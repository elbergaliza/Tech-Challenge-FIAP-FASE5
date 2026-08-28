"""
RAG demo over the three scenarios from the challenge brief.

    python ai-memory-rag/scripts/demo_rag.py
    python ai-memory-rag/scripts/demo_rag.py --embedder gemini

Each scenario enters through the exact `dados_coletados` shape Person 1's agent
returns, and the output shows the text block that gets injected into the LLM
prompt. That block is the seam between Part 1 and Part 2.
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
        "name": "Scenario 1 from the brief: buying in the south zone",
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
        "name": "Scenario 2 from the brief: investor profile",
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
        "name": "Rental with a radius search",
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
        "name": "Impossible profile: 4 bedrooms in Leblon for 300k",
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
        "name": "First message, nothing extracted yet",
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
    parser = argparse.ArgumentParser(description="Property RAG demo.")
    parser.add_argument("--embedder", choices=["gemini", "hashing"], default="hashing")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    load_env()
    embedder = get_embedder(prefer=args.embedder)

    properties = indexer.load_properties()
    index = indexer.build_index(properties, embedder)

    print("=" * 78)
    print("PROPERTY RAG  |  %d properties  |  embedder=%s  |  dim=%d"
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
        print("Lead says: %s" % scenario["message"])
        print("Derived filters: %r" % filters)
        print("Candidates after filtering: %d" % result.candidate_count)

        if result.relaxations:
            print("Relaxation steps applied:")
            for description in result.relaxations:
                print("   - %s" % description)

        print()
        print(retriever.format_for_prompt(result))
        print("-" * 78)

    return 0


if __name__ == "__main__":
    sys.exit(_main())
