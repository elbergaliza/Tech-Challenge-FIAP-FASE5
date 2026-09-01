"""
Camada de embeddings do RAG.

Duas implementações, uma interface:

  GeminiEmbedder   embeddings semânticos de verdade, via `gemini-embedding-001`.
                   Precisa de GEMINI_API_KEY e de rede.

  HashingEmbedder  fallback determinístico, 100% offline, sem dependências.
                   NÃO é semântico: é uma projeção lexical (hashing de
                   unigramas e bigramas, no espírito do HashingVectorizer).
                   Existe para que os testes e a demo rodem sem chave e sem
                   internet, e para que ninguém do grupo precise instalar nada
                   para ver o módulo funcionar. Acerta bem em "apartamento 3
                   quartos na zona sul", porque o vocabulário do domínio é
                   pequeno e repetitivo; erra em sinônimos ("perto do mar" x
                   "vista para a praia"), e é aí que o Gemini se justifica.

`get_embedder()` escolhe entre os dois, preferindo o Gemini quando há chave.

Por que a interface separa documento de consulta: o `gemini-embedding-001`
aceita um `task_type`, e usar RETRIEVAL_DOCUMENT ao indexar e RETRIEVAL_QUERY
ao buscar melhora a qualidade da recuperação de forma mensurável. Um embedder
com um único método `embed()` não conseguiria fazer essa distinção.
"""

import hashlib
import math
import os
import re
import unicodedata

# ---------------------------------------------------------------------------
# Utilitários de texto
# ---------------------------------------------------------------------------

_NON_ALPHANUM = re.compile(r"[^a-z0-9]+")

# Palavras que aparecem em quase todo anúncio e por isso não discriminam nada.
# Ficam em português porque é a língua do corpus.
_STOPWORDS = {
    "a", "ao", "aos", "as", "com", "da", "das", "de", "do", "dos", "e",
    "em", "na", "nas", "no", "nos", "o", "os", "para", "por", "que", "se",
    "sem", "sob", "sobre", "um", "uma", "uns", "umas", "ou", "the",
}


def normalize(text):
    """Minúsculas, sem acento. Faz "Gávea" e "gavea" virarem o mesmo token."""
    if not text:
        return ""

    decomposed = unicodedata.normalize("NFKD", str(text))
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_accents.lower()


def tokenize(text):
    """Devolve os unigramas e bigramas úteis de um texto."""
    cleaned = _NON_ALPHANUM.sub(" ", normalize(text))
    words = [w for w in cleaned.split() if len(w) > 1 and w not in _STOPWORDS]

    tokens = list(words)
    # Bigramas capturam expressões do domínio que perdem sentido separadas:
    # "zona sul", "vista mar", "portaria 24h", "3 quartos".
    for previous, following in zip(words, words[1:]):
        tokens.append(previous + "_" + following)

    return tokens


# ---------------------------------------------------------------------------
# Álgebra de vetores (Python puro; a base é pequena, não precisa de numpy)
# ---------------------------------------------------------------------------

def l2_normalize(vector):
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector

    return [v / norm for v in vector]


def cosine(a, b):
    """Similaridade de cosseno. Robusta a vetores não normalizados."""
    if len(a) != len(b):
        raise ValueError("vetores de dimensões diferentes: %d != %d" % (len(a), len(b)))

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


# ---------------------------------------------------------------------------
# Embedder lexical offline
# ---------------------------------------------------------------------------

class HashingEmbedder:
    """Projeção lexical determinística, sem rede e sem dependências.

    Usa hashlib em vez do `hash()` embutido porque o hash de strings do Python
    é randomizado por processo (PYTHONHASHSEED), o que faria o índice salvo em
    disco não bater com as consultas de uma execução seguinte.
    """

    name = "hashing-v1"

    def __init__(self, dim=512):
        self.dim = dim

    def _vectorize(self, text):
        counts = {}
        for token in tokenize(text):
            counts[token] = counts.get(token, 0) + 1

        vector = [0.0] * self.dim
        for token, n in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            h = int.from_bytes(digest, "big")
            index = h % self.dim
            sign = 1.0 if (h >> 17) & 1 else -1.0
            # tf sublinear: a décima menção de "varanda" não vale dez vezes a
            # primeira.
            vector[index] += sign * (1.0 + math.log(n))

        return l2_normalize(vector)

    def embed_documents(self, texts):
        return [self._vectorize(t) for t in texts]

    def embed_query(self, text):
        return self._vectorize(text)


# ---------------------------------------------------------------------------
# Embedder Gemini
# ---------------------------------------------------------------------------

class GeminiEmbedder:
    """Embeddings semânticos via API do Gemini.

    O import do SDK é lazy de propósito: quem só quer rodar os testes offline
    não deveria precisar do pacote instalado.
    """

    name = "gemini-embedding-001"

    def __init__(self, api_key=None, model="gemini-embedding-001", dim=768, batch=32):
        self.model = model
        self.dim = dim
        self.batch = batch

        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY não encontrada. Use HashingEmbedder para rodar offline."
            )

        from google import genai  # import lazy

        self._genai = genai
        self._client = genai.Client(api_key=api_key)

    def _embed(self, texts, task_type):
        vectors = []

        for start in range(0, len(texts), self.batch):
            chunk = texts[start:start + self.batch]
            vectors.extend(self._call(chunk, task_type))

        return vectors

    def _call(self, chunk, task_type):
        # Versões diferentes do google-genai aceitam o `config` de formas
        # diferentes. Tenta com task_type (melhor qualidade) e cai para a
        # chamada mínima se o SDK instalado não suportar.
        try:
            from google.genai import types

            response = self._client.models.embed_content(
                model=self.model,
                contents=chunk,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=self.dim,
                ),
            )
        except Exception:
            response = self._client.models.embed_content(
                model=self.model,
                contents=chunk,
            )

        return [l2_normalize(list(e.values)) for e in response.embeddings]

    def embed_documents(self, texts):
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text):
        return self._embed([text], "RETRIEVAL_QUERY")[0]


# ---------------------------------------------------------------------------
# Seleção
# ---------------------------------------------------------------------------

def load_env(path=".env"):
    """Lê um .env simples sem depender do python-dotenv.

    Não sobrescreve variáveis já presentes no ambiente, que é o comportamento
    do dotenv e o que faz sentido em CI.
    """
    if not os.path.isfile(path):
        return

    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def get_embedder(prefer=None, api_key=None):
    """Devolve o melhor embedder disponível.

    prefer="gemini"   força o Gemini e falha alto se não der.
    prefer="hashing"  força o modo offline.
    prefer=None       tenta Gemini e cai para hashing sem quebrar.

    No modo automático a checagem inclui uma chamada de sonda. Construir o
    `GeminiEmbedder` NÃO valida a chave: o SDK só fala com a API na primeira
    requisição. Sem a sonda, uma chave inválida passava pela construção e
    explodia lá adiante, dentro do `build_index`, derrubando o processo em vez
    de cair para o modo offline. Uma chamada minúscula no início é barata perto
    de um traceback no meio de uma apresentação.
    """
    if prefer == "hashing":
        return HashingEmbedder()

    if prefer == "gemini":
        return GeminiEmbedder(api_key=api_key)

    try:
        embedder = GeminiEmbedder(api_key=api_key)
        embedder.embed_query("teste")
        return embedder
    except Exception as error:
        print("[rag] Gemini indisponível (%s)." % _resumo_do_erro(error))
        print("[rag] Usando HashingEmbedder offline.")
        return HashingEmbedder()


def _resumo_do_erro(error):
    """Primeira linha do erro, para o aviso caber na tela.

    Erros de API vêm com JSON e stack de várias linhas; despejar tudo isso
    antes do cabeçalho do programa esconde a informação que importa.
    """
    texto = str(error).strip().replace("\n", " ")
    return texto[:160] + ("..." if len(texto) > 160 else "")
