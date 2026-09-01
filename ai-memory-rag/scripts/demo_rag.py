"""
Demonstração do RAG nos cenários do PDF do desafio.

    python ai-memory-rag/scripts/demo_rag.py
    python ai-memory-rag/scripts/demo_rag.py --embedder gemini

Cada cenário entra pelo formato exato de `dados_coletados` que o agente da
Pessoa 1 devolve, e a saída mostra o bloco de texto que será injetado no prompt
do LLM. É essa a costura entre a Parte 1 e a Parte 2.
"""

import argparse
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from rag import indexer, retriever                    # noqa: E402
from rag.embeddings import get_embedder, load_env     # noqa: E402

SCENARIOS = [
    {
        "name": "Cenário 1 do PDF: compra na zona sul",
        "message": "Estou procurando apartamento na zona sul, 3 quartos, é urgente",
        "data": {
            "nome": "João",
            "intencao": "COMPRA",
            "preco_faixa": "800k-1.5m",
            "regiao": "Zona Sul",
            "quartos": "3",
            "urgencia": "alta",
            "email": "undefined",
            "telefone": "undefined",
        },
        "extra": {},
    },
    {
        "name": "Cenário 2 do PDF: perfil investidor",
        "message": "Quero investir em imóveis para renda, tenho cerca de 1 milhão",
        "data": {
            "nome": "Marina",
            "intencao": "INVESTIMENTO",
            "preco_faixa": "1m",
            "regiao": "undefined",
            "quartos": "undefined",
            "urgencia": "media",
        },
        "extra": {"expected_return": "espero uns 6% ao ano"},
    },
    {
        "name": "Aluguel com busca por raio",
        "message": "Procuro para alugar perto do Leblon, 2 quartos, até 6 mil",
        "data": {
            "intencao": "ALUGUEL",
            "preco_faixa": "6000",
            "regiao": "Leblon",
            "quartos": "2",
            "urgencia": "alta",
        },
        "extra": {"radius_km": 4},
    },
    {
        "name": "Perfil impossível: 4 quartos no Leblon por 300 mil",
        "message": "Quero comprar 4 quartos no Leblon, meu limite é 300 mil",
        "data": {
            "intencao": "COMPRA",
            "preco_faixa": "300k",
            "regiao": "Leblon",
            "quartos": "4",
        },
        "extra": {},
    },
    {
        "name": "Primeira mensagem, nada extraído ainda",
        "message": "Oi, tudo bem?",
        "data": {
            "nome": "undefined",
            "intencao": "undefined",
            "preco_faixa": "undefined",
            "regiao": "undefined",
            "quartos": "undefined",
        },
        "extra": {},
    },
]


def _main():
    parser = argparse.ArgumentParser(description="Demo do RAG de imóveis.")
    parser.add_argument("--embedder", choices=["gemini", "hashing"], default="hashing")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    load_env()
    embedder = get_embedder(prefer=args.embedder)

    properties = indexer.load_properties()
    index = indexer.build_index(properties, embedder)

    print("=" * 78)
    print("RAG DE IMÓVEIS  |  %d imóveis  |  embedder=%s  |  dim=%d"
          % (len(index), index.embedder_name, index.dim))
    print("=" * 78)

    for scenario in SCENARIOS:
        filters = retriever.filters_from_profile(scenario["data"], **scenario["extra"])
        result = retriever.search(
            index, filters, scenario["message"],
            embedder=embedder, top_k=args.top_k,
        )

        print()
        print("### %s" % scenario["name"])
        print("Lead diz: %s" % scenario["message"])
        print("Filtros derivados: %r" % filters)
        print("Candidatos após o filtro: %d" % result.candidate_count)

        if result.relaxations:
            print("Relaxamentos aplicados:")
            for description in result.relaxations:
                print("   - %s" % description)

        print()
        print(retriever.format_for_prompt(result))
        print("-" * 78)

    return 0


if __name__ == "__main__":
    sys.exit(_main())
