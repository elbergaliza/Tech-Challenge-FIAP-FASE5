"""
Demonstração do resumo para o corretor e do follow-up automático.

    python ai-memory-rag/scripts/demo_broker.py

Roda offline: sem GEMINI_API_KEY os dois módulos usam o caminho heurístico, que
é justamente o que precisa ser visto funcionando, porque é o que aparece se a
API cair durante a apresentação.

Mostra três coisas:
  1. a carteira ordenada por temperatura, que é a lista do dashboard;
  2. o card completo de um lead;
  3. a escada de follow-up de um lead que sumiu, ao longo de duas semanas.
"""

import io
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from followup import FollowUpGenerator, evaluate_followup                 # noqa: E402
from llm import get_client                                               # noqa: E402
from memory.conversation_memory import ConversationMemory, InMemoryStore  # noqa: E402
from rag import indexer, retriever                                       # noqa: E402
from rag.embeddings import HashingEmbedder                               # noqa: E402
from summarizer import (                                              # noqa: E402
    TEMPERATURE_LABELS, Summarizer, summarize_pipeline,
)


class Clock:
    def __init__(self):
        self.moment = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.moment

    def advance(self, **delta):
        self.moment = self.moment + timedelta(**delta)


LEADS = [
    {
        "id": "lead-ana",
        "consent": True,
        "messages": [
            "Oi, sou a Ana. Quero comprar apartamento em Copacabana",
            "Preciso de 3 quartos, até 1.5 milhão",
            "É urgente, preciso mudar até dezembro. Meu zap é (21) 98765-4321",
        ],
        "profile": {
            "nome": "Ana", "intencao": "COMPRA", "preco_faixa": "1.5m",
            "regiao": "Copacabana", "quartos": "3", "urgencia": "alta",
            "telefone": "(21) 98765-4321",
        },
    },
    {
        "id": "lead-carlos",
        "consent": True,
        "messages": [
            "bom dia, queria alugar algo na Tijuca",
            "2 quartos serve",
        ],
        "profile": {
            "nome": "Carlos", "intencao": "ALUGUEL", "regiao": "Tijuca",
            "quartos": "2", "urgencia": "baixa",
        },
    },
    {
        "id": "lead-anonimo",
        "consent": False,
        "messages": ["oi"],
        "profile": {},
    },
]


def rule(title=""):
    print("\n" + "=" * 78)
    if title:
        print(title)
        print("=" * 78)


def build_pipeline(clock):
    memory = ConversationMemory(InMemoryStore(), clock=clock)

    for lead in LEADS:
        for message in lead["messages"]:
            memory.record_message(lead["id"], "user", message)
            memory.record_message(lead["id"], "assistant", "entendi, me conta mais")
        if lead["profile"]:
            memory.update_profile(lead["id"], lead["profile"])
        if lead["consent"]:
            memory.record_consent(lead["id"], True, "atendimento imobiliário")

    return memory


def main():
    clock = Clock()
    memory = build_pipeline(clock)

    client = get_client()
    summarizer = Summarizer(client=client)

    print("Cliente LLM: %s" % ("disponível: " + client.name if client.available
                                else "indisponível (%s)" % client.reason))

    # -- imóveis para o lead mais quente -----------------------------------

    embedder = HashingEmbedder()
    index = indexer.build_index(indexer.load_properties(), embedder)
    result = retriever.search_for_lead(
        index, memory.profile("lead-ana"), "3 quartos em Copacabana",
        embedder=embedder, top_k=3,
    )
    memory.record_shown_properties("lead-ana", [r.id for r in result])

    # -- 1. lista do dashboard ----------------------------------------------

    rule("1. CARTEIRA DO CORRETOR (ordenada por temperatura)")
    for summary in summarize_pipeline(memory, summarizer):
        print("\n%-8s %-14s score %3d   %s" % (
            TEMPERATURE_LABELS[summary.temperature], summary.lead_id,
            summary.score, summary.next_action,
        ))
        for alert in summary.alerts:
            print("         ! %s" % alert)

    # -- 2. card de um lead -------------------------------------------------

    rule("2. CARD DO LEAD MAIS QUENTE")
    summary = summarizer.summarize_for_broker(memory, "lead-ana")
    print()
    print(summary.to_markdown())

    print("\nComo o score foi formado:")
    for factor in summary.factors:
        print("  %s" % factor)

    # -- 3. escada de follow-up ----------------------------------------------

    rule("3. FOLLOW-UP DE UM LEAD QUE SUMIU (lead-carlos, 2 semanas)")
    generator = FollowUpGenerator(client=client)

    for day in range(0, 15):
        clock.advance(days=1)
        decision = evaluate_followup(memory, "lead-carlos")

        if not decision.send:
            continue

        followup = generator.send(memory, "lead-carlos")
        print("\nDia %d  ·  tentativa %d  ·  tom '%s'  ·  canal %s  ·  %s"
              % (day + 1, followup.attempt, followup.tone, followup.channel,
                 followup.source))
        print("   \"%s\"" % followup.text)

    print("\nDepois da terceira, o agente para sozinho:")
    print("   %s" % evaluate_followup(memory, "lead-carlos").reason)

    # -- 4. o que impede o envio ---------------------------------------------

    rule("4. QUANDO O AGENTE NÃO ENVIA")
    clock.advance(days=10)

    print("\nlead-anonimo (nunca deu consentimento):")
    print("   %s" % evaluate_followup(memory, "lead-anonimo").reason)

    memory.record_message("lead-ana", "user", "já comprei outro, obrigado")
    print("\nlead-ana (disse que já comprou):")
    print("   %s" % evaluate_followup(memory, "lead-ana").reason)

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
