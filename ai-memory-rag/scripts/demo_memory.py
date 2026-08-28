"""
Demo of conversational memory with pseudonymisation and RAG.

    python ai-memory-rag/scripts/demo_memory.py

Simulates a multi-session conversation without calling Gemini: the agent
replies are canned and written with aliases, exactly as the model would produce
them after receiving masked text. The point is to make visible what is normally
hidden:

  * what the lead wrote (in the clear, ours only);
  * what actually leaves for the LLM (pseudonymised);
  * what the lead gets back (restored);
  * how the profile accumulates without regressing;
  * what memory injects into the prompt on each turn.
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from memory.conversation_memory import ConversationMemory, InMemoryStore  # noqa: E402
from rag import indexer, retriever                                        # noqa: E402
from rag.embeddings import HashingEmbedder                                # noqa: E402

LEAD = "lead-demo-01"

# Per turn: what the lead says, what Person 1's extraction would return, and the
# reply the LLM would produce from the MASKED text.
TURNS = [
    {
        "lead": "Oi! Meu nome é João Pereira, quero comprar um apartamento",
        "extracted": {
            "nome": "João", "intencao": "COMPRA", "preco_faixa": "undefined",
            "regiao": "undefined", "quartos": "undefined", "urgencia": "baixa",
        },
        "llm": "Prazer, [NOME_1]! Qual região você está procurando?",
    },
    {
        "lead": "Estou olhando Copacabana, preciso de 3 quartos",
        # Person 1's extraction regresses here: the name is no longer in the
        # recent text and falls back to "undefined". This is exactly what
        # memory protects against.
        "extracted": {
            "nome": "undefined", "intencao": "COMPRA", "preco_faixa": "undefined",
            "regiao": "Copacabana", "quartos": "3", "urgencia": "baixa",
        },
        "llm": "Ótima escolha, [NOME_1]! Qual sua faixa de preço?",
    },
    {
        "lead": "Até 1.5 milhão. Meu email é joao.pereira@exemplo.com e o zap (21) 98765-4321",
        "extracted": {
            "nome": "undefined", "intencao": "COMPRA", "preco_faixa": "1.5m",
            "regiao": "Copacabana", "quartos": "3", "urgencia": "baixa",
        },
        "llm": "Anotado, [NOME_1]. Confirmo o envio para [EMAIL_1] e o contato [TELEFONE_1].",
        "search": True,
    },
    {
        "lead": "Na verdade consigo ir até 2 milhões, e é urgente",
        "extracted": {
            "nome": "undefined", "intencao": "COMPRA", "preco_faixa": "2m",
            "regiao": "Copacabana", "quartos": "3", "urgencia": "alta",
        },
        "llm": "Perfeito, [NOME_1]! Com esse orçamento abre bastante opção. Vamos agendar uma visita?",
        "search": True,
    },
]


def rule(title=""):
    print("\n" + ("- " * 39))
    if title:
        print(title)


def main():
    memory = ConversationMemory(InMemoryStore())
    embedder = HashingEmbedder()
    index = indexer.build_index(indexer.load_properties(), embedder)

    print("=" * 78)
    print("CONVERSATIONAL MEMORY + PSEUDONYMISATION + RAG")
    print("=" * 78)

    memory.record_consent(LEAD, True, "atendimento imobiliário e follow-up")
    print("\nConsent recorded: %s" % memory.has_consent(LEAD))

    for number, spec in enumerate(TURNS, start=1):
        rule("TURN %d" % number)

        print("\n[1] The lead wrote (in the clear, never leaves here):")
        print("    %s" % spec["lead"])

        turn = memory.start_turn(LEAD, spec["lead"])

        print("\n[2] What LEAVES for Gemini (pseudonymised):")
        print("    message: %s" % turn.message)
        if turn.context:
            for line in turn.context.splitlines():
                print("    context| %s" % line)
        else:
            print("    context| (empty, first turn)")

        if spec.get("search"):
            profile = memory.profile(LEAD)
            result = retriever.search_for_lead(
                index, profile, spec["lead"], embedder=embedder, top_k=3
            )
            memory.record_shown_properties(LEAD, [r.id for r in result])
            print("    rag     | %d properties found: %s"
                  % (len(result), ", ".join(r.id for r in result)))

        print("\n[3] Gemini replies (still with aliases):")
        print("    %s" % spec["llm"])

        reply, changes = memory.finish_turn(LEAD, turn, spec["llm"], spec["extracted"])

        print("\n[4] What the LEAD receives (restored):")
        print("    %s" % reply)

        if changes:
            print("\n[5] Profile updated:")
            for change in changes:
                if change["kind"] == "correction":
                    print("    ~ %s: %s -> %s (lead corrected themselves)"
                          % (change["field"], change["from"], change["to"]))
                else:
                    print("    + %s: %s" % (change["field"], change["to"]))

    # -- what memory preserved ---------------------------------------------

    rule("ACCUMULATED PROFILE")
    for field, value in memory.profile(LEAD).items():
        print("  %-12s %s" % (field + ":", value))

    print("\nThe 'name' field survived turns 2, 3 and 4, where Person 1's")
    print("extraction returned 'undefined'. Without memory the agent would")
    print("have asked the lead's name all over again.")

    rule("PROPERTIES ALREADY SHOWN")
    print("  %s" % ", ".join(memory.shown_properties(LEAD)))

    rule("LOG (no personal data)")
    print("  %s" % memory.log_summary(LEAD))

    # -- LGPD ---------------------------------------------------------------

    rule("LGPD")
    package = memory.export(LEAD)
    print("  Right of access: %d messages, %d profile fields, consent on %s"
          % (len(package["messages"]), len(package["profile"]),
             package["consent"]["at"][:10]))

    memory.forget(LEAD)
    print("  Right to erasure: wiped. Profile now: %r" % memory.profile(LEAD))
    print("  Alias map (used to hold PII in the clear): %r"
          % memory.state(LEAD)["alias_map"])

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
