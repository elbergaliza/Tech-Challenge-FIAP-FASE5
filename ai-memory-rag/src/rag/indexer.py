"""
Indexador da base de imóveis.

Transforma `shared/data/imoveis.json` num índice consultável: para cada imóvel,
monta um texto de busca ponderado e calcula seu embedding.

Equivalente conceitual no Azure Cognitive Search (Aula 04):

    create_index()       -> schema.AZURE_FIELD_MAP
    upload_documents()   -> build_index()
    indexador agendado   -> reindex() chamado por um job

Linha de comando:

    python -m src.rag.indexer                    # usa o que estiver disponível
    python -m src.rag.indexer --embedder hashing # força o caminho offline
"""

import hashlib
import json
import os

from . import schema
from .embeddings import HashingEmbedder, cosine, get_embedder, load_env

# Caminhos relativos à raiz do repositório.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PROPERTIES_PATH = os.path.join(ROOT, "shared", "data", "imoveis.json")
INDEX_PATH = os.path.join(ROOT, "ai-memory-rag", "data", "index", "imoveis_index.json")

INDEX_VERSION = 1


def build_search_text(prop):
    """Monta o texto que representa o imóvel no espaço vetorial.

    Campos mais discriminantes entram repetidos, o que é o análogo local do
    boosting por campo do Azure (`search_fields=["title^3", "description"]`).
    Repetir o token aumenta sua massa no vetor.
    """
    parts = []

    for field, weight in schema.TEXT_WEIGHTS.items():
        value = prop.get(field)

        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        if not value:
            continue

        parts.extend([str(value)] * weight)

    # A zona entra sempre, para que "zona sul" recupere imóveis de Copacabana
    # mesmo quando o anúncio nunca escreve "zona sul".
    if prop.get("zone"):
        parts.append(str(prop["zone"]))

    # Quartos em forma textual: o lead escreve "3 quartos", não "quartos: 3".
    bedrooms = prop.get("bedrooms")
    if bedrooms:
        parts.append("%d quartos" % bedrooms)

    if prop.get("deal_type") == "RENTAL":
        parts.append("aluguel alugar locacao")
    else:
        parts.append("venda comprar compra")

    return " . ".join(parts)


class Index:
    """Índice em memória. Pequeno de propósito: a base tem dezenas de itens."""

    def __init__(self, embedder_name, dim, documents, vectors, version=INDEX_VERSION,
                 source_hash=None):
        if len(documents) != len(vectors):
            raise ValueError("documents e vectors com tamanhos diferentes")

        self.embedder_name = embedder_name
        self.dim = dim
        self.documents = documents
        self.vectors = vectors
        self.version = version
        # Impressão digital da base que gerou estes vetores. É o que permite
        # detectar que `imoveis.json` mudou e o índice em disco ficou velho.
        self.source_hash = source_hash
        self._by_id = {d["id"]: d for d in documents}

    def __len__(self):
        return len(self.documents)

    def by_id(self, property_id):
        return self._by_id.get(property_id)

    def similarities(self, query_vector, allowed_ids=None):
        """Devolve [(imóvel, similaridade)] para os ids permitidos.

        `allowed_ids` é o resultado dos filtros estruturados. Restringir antes
        de calcular o cosseno evita trabalho e, mais importante, garante que um
        filtro rígido nunca seja vencido por semelhança textual.
        """
        results = []

        for document, vector in zip(self.documents, self.vectors):
            if allowed_ids is not None and document["id"] not in allowed_ids:
                continue
            results.append((document, cosine(query_vector, vector)))

        results.sort(key=lambda pair: pair[1], reverse=True)
        return results

    def facets(self, field):
        """Contagem por valor de um campo, como as facetas do Azure.

        Alimenta direto o dashboard do corretor (Parte 4).
        """
        counts = {}

        for document in self.documents:
            value = document.get(field)
            values = value if isinstance(value, list) else [value]
            for v in values:
                if v is None:
                    continue
                counts[v] = counts.get(v, 0) + 1

        return dict(sorted(counts.items(), key=lambda pair: -pair[1]))

    def to_dict(self):
        return {
            "version": self.version,
            "embedder": self.embedder_name,
            "dim": self.dim,
            "total": len(self.documents),
            "source_hash": self.source_hash,
            "documents": self.documents,
            # 6 casas decimais bastam para cosseno e cortam o arquivo em ~40%.
            "vectors": [[round(v, 6) for v in vector] for vector in self.vectors],
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            embedder_name=data["embedder"],
            dim=data["dim"],
            documents=data["documents"],
            vectors=data["vectors"],
            version=data.get("version", INDEX_VERSION),
            source_hash=data.get("source_hash"),
        )


def load_properties(path=None):
    path = path or PROPERTIES_PATH

    if not os.path.isfile(path):
        raise FileNotFoundError(
            "Base de imóveis não encontrada em %s. "
            "Rode: python ai-memory-rag/scripts/generate_properties.py" % path
        )

    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    properties = data["properties"] if isinstance(data, dict) else data

    problems = []
    for prop in properties:
        for problem in schema.validate_property(prop):
            problems.append("%s: %s" % (prop.get("id", "?"), problem))

    if problems:
        raise ValueError(
            "Base de imóveis inválida (%d problemas):\n  %s"
            % (len(problems), "\n  ".join(problems[:20]))
        )

    return properties


def properties_hash(properties):
    """Impressão digital estável da base indexada.

    Só entra o que muda o vetor: o id e o texto de busca. Assim, editar um
    campo que não é indexado não invalida o índice inteiro à toa.
    """
    digest = hashlib.blake2b(digest_size=16)
    for prop in properties:
        digest.update(str(prop.get("id", "")).encode("utf-8"))
        digest.update(b"|")
        digest.update(build_search_text(prop).encode("utf-8"))
        digest.update(b"\n")

    return digest.hexdigest()


def build_index(properties, embedder):
    texts = [build_search_text(prop) for prop in properties]
    vectors = embedder.embed_documents(texts)

    return Index(
        embedder_name=embedder.name,
        dim=len(vectors[0]) if vectors else embedder.dim,
        documents=list(properties),
        vectors=vectors,
        source_hash=properties_hash(properties),
    )


def save_index(index, path=None):
    path = path or INDEX_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(index.to_dict(), handle, ensure_ascii=False)

    return path


def load_index(path=None):
    path = path or INDEX_PATH

    if not os.path.isfile(path):
        raise FileNotFoundError(
            "Índice não encontrado em %s. Rode: python -m src.rag.indexer" % path
        )

    with open(path, "r", encoding="utf-8") as handle:
        return Index.from_dict(json.load(handle))


def get_index(embedder, properties=None, index_path=None, fallback=True):
    """Devolve um índice utilizável, reaproveitando o disco sempre que der.

    Existe porque `build_index` custa uma chamada de API por imóvel. Chamá-lo a
    cada inicialização, como o runner fazia, torra a cota do plano gratuito em
    uma execução e derruba o processo. O índice só é reconstruído quando o
    embedder, a dimensão, a versão do formato ou a própria base mudam.

    Com `fallback`, uma falha ao construir com o Gemini (cota, rede, chave) cai
    para o índice lexical offline em vez de matar o processo. O RAG continua
    respondendo; o que piora é a qualidade semântica, e isso é dito em voz alta.
    """
    properties = properties if properties is not None else load_properties()
    esperado = properties_hash(properties)

    try:
        cached = load_index(index_path)
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        cached = None

    if cached is not None and _cache_serve(cached, embedder, esperado):
        return cached, "cache"

    try:
        index = build_index(properties, embedder)
    except Exception as error:
        if not fallback or isinstance(embedder, HashingEmbedder):
            raise

        print("[rag] Falha ao indexar com %s: %s" % (embedder.name, _uma_linha(error)))
        print("[rag] Reindexando offline com HashingEmbedder.")
        index = build_index(properties, HashingEmbedder())

    save_index(index, index_path)
    return index, "rebuild"


def _cache_serve(cached, embedder, esperado):
    """O índice em disco pode ser usado com este embedder e esta base?"""
    if cached.version != INDEX_VERSION:
        return False
    if cached.embedder_name != embedder.name:
        return False
    # Índices gravados antes do `source_hash` existir não têm como ser
    # validados; reconstruir uma vez é mais barato que servir vetor errado.
    if cached.source_hash != esperado:
        return False

    return True


def _uma_linha(error):
    texto = str(error).strip().replace("\n", " ")
    return texto[:160] + ("..." if len(texto) > 160 else "")


def reindex(embedder=None, properties_path=None, index_path=None):
    """Reconstrói o índice do zero. É o gancho para um job agendado."""
    embedder = embedder or get_embedder()
    properties = load_properties(properties_path)
    index = build_index(properties, embedder)
    destination = save_index(index, index_path)

    return index, destination


def _main():
    import argparse

    parser = argparse.ArgumentParser(description="Indexa a base de imóveis.")
    parser.add_argument(
        "--embedder",
        choices=["gemini", "hashing"],
        default=None,
        help="força um embedder; por padrão tenta Gemini e cai para hashing",
    )
    args = parser.parse_args()

    load_env()
    embedder = get_embedder(prefer=args.embedder)

    index, destination = reindex(embedder=embedder)

    print("Índice criado: %d imóveis, embedder=%s, dim=%d"
          % (len(index), index.embedder_name, index.dim))
    print("Salvo em: %s" % destination)
    print("Facetas por zona: %s" % index.facets("zone"))
    print("Facetas por tipo de negócio: %s" % index.facets("deal_type"))


if __name__ == "__main__":
    _main()
