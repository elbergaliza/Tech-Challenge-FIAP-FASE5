"""
Demo of the broker summary and the automatic follow-up.

    python ai-memory-rag/scripts/demo_broker.py

Runs offline: with no GEMINI_API_KEY both modules take the heuristic path, which
is exactly what needs to be seen working, because it is what shows up if the API
goes down during the presentation.

Shows three things:
  1. the pipeline ordered by temperature, which is the dashboard list;
  2. the full card for one lead;
  3. the follow-up ladder for a lead who went quiet, over two weeks.
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

    print("LLM client: %s" % ("available: " + client.name if client.available
                              else "unavailable (%s)" % client.reason))

    # -- properties for the hottest lead -----------------------------------

    embedder = HashingEmbedder()
    index = indexer.build_index(indexer.load_properties(), embedder)
    result = retriever.search_for_lead(
        index, memory.profile("lead-ana"), "3 quartos em Copacabana",
        embedder=embedder, top_k=3,
    )
    memory.record_shown_properties("lead-ana", [r.id for r in result])

    # -- 1. dashboard list --------------------------------------------------

    rule("1. BROKER PIPELINE (ordered by temperature)")
    for summary in summarize_pipeline(memory, summarizer):
        print("\n%-8s %-14s score %3d   %s" % (
            TEMPERATURE_LABELS[summary.temperature], summary.lead_id,
            summary.score, summary.next_action,
        ))
        for alert in summary.alerts:
            print("         ! %s" % alert)

    # -- 2. one lead's card -------------------------------------------------

    rule("2. CARD FOR THE HOTTEST LEAD")
    summary = summarizer.summarize_for_broker(memory, "lead-ana")
    print()
    print(summary.to_markdown())

    print("\nHow the score was formed:")
    for factor in summary.factors:
        print("  %s" % factor)

    # -- 3. follow-up ladder -------------------------------------------------

    rule("3. FOLLOW-UP FOR A LEAD WHO WENT QUIET (lead-carlos, 2 weeks)")
    generator = FollowUpGenerator(client=client)

    for day in range(0, 15):
        clock.advance(days=1)
        decision = evaluate_followup(memory, "lead-carlos")

        if not decision.send:
            continue

        followup = generator.send(memory, "lead-carlos")
        print("\nDay %d  ·  attempt %d  ·  tone '%s'  ·  channel %s  ·  %s"
              % (day + 1, followup.attempt, followup.tone, followup.channel,
                 followup.source))
        print("   \"%s\"" % followup.text)

    print("\nAfter the third one, the agent stops on its own:")
    print("   %s" % evaluate_followup(memory, "lead-carlos").reason)

    # -- 4. what blocks a send ----------------------------------------------

    rule("4. WHEN THE AGENT DOES NOT SEND")
    clock.advance(days=10)

    print("\nlead-anonimo (never gave consent):")
    print("   %s" % evaluate_followup(memory, "lead-anonimo").reason)

    memory.record_message("lead-ana", "user", "já comprei outro, obrigado")
    print("\nlead-ana (said they already bought):")
    print("   %s" % evaluate_followup(memory, "lead-ana").reason)

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
