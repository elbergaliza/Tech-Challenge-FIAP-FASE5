"""
Demonstração da memória conversacional com pseudonimização e RAG.

    python ai-memory-rag/scripts/demo_memory.py

Simula uma conversa de várias sessões sem chamar o Gemini: as respostas do
agente são fixas e escritas com apelidos, exatamente como o modelo as produziria
depois de receber o texto mascarado. O objetivo é tornar visível o que
normalmente fica escondido:

  * o que o lead escreveu (em claro, só nosso);
  * o que de fato sai para o LLM (pseudonimizado);
  * o que o lead recebe de volta (restaurado);
  * como o perfil se acumula sem regredir;
  * o que a memória injeta no prompt em cada turno.
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

# Por turno: o que o lead diz, o que a extração da Pessoa 1 devolveria, e a
# resposta que o LLM produziria a partir do texto MASCARADO.
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
        # A extração da Pessoa 1 regride aqui: o nome sai do texto recente e
        # volta a "undefined". É exatamente disto que a memória protege.
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
    print("MEMÓRIA CONVERSACIONAL + PSEUDONIMIZAÇÃO + RAG")
    print("=" * 78)

    memory.record_consent(LEAD, True, "atendimento imobiliário e follow-up")
    print("\nConsentimento registrado: %s" % memory.has_consent(LEAD))

    for number, spec in enumerate(TURNS, start=1):
        rule("TURNO %d" % number)

        print("\n[1] O lead escreveu (em claro, nunca sai daqui):")
        print("    %s" % spec["lead"])

        turn = memory.start_turn(LEAD, spec["lead"])

        print("\n[2] O que SAI para o Gemini (pseudonimizado):")
        print("    mensagem: %s" % turn.message)
        if turn.context:
            for line in turn.context.splitlines():
                print("    contexto| %s" % line)
        else:
            print("    contexto| (vazio, primeiro turno)")

        if spec.get("search"):
            profile = memory.profile(LEAD)
            result = retriever.search_for_lead(
                index, profile, spec["lead"], embedder=embedder, top_k=3
            )
            memory.record_shown_properties(LEAD, [r.id for r in result])
            print("    rag     | %d imóveis encontrados: %s"
                  % (len(result), ", ".join(r.id for r in result)))

        print("\n[3] O Gemini responde (ainda com apelidos):")
        print("    %s" % spec["llm"])

        reply, changes = memory.finish_turn(LEAD, turn, spec["llm"], spec["extracted"])

        print("\n[4] O que o LEAD recebe (restaurado):")
        print("    %s" % reply)

        if changes:
            print("\n[5] Perfil atualizado:")
            for change in changes:
                if change["kind"] == "correction":
                    print("    ~ %s: %s -> %s (lead se corrigiu)"
                          % (change["field"], change["from"], change["to"]))
                else:
                    print("    + %s: %s" % (change["field"], change["to"]))

    # -- o que a memória preservou -----------------------------------------

    rule("PERFIL ACUMULADO")
    for field, value in memory.profile(LEAD).items():
        print("  %-12s %s" % (field + ":", value))

    print("\nO campo 'name' sobreviveu aos turnos 2, 3 e 4, nos quais a extração")
    print("da Pessoa 1 devolveu 'undefined'. Sem memória, o agente teria")
    print("perguntado o nome do lead outra vez.")

    rule("IMÓVEIS JÁ APRESENTADOS")
    print("  %s" % ", ".join(memory.shown_properties(LEAD)))

    rule("LOG (sem dado pessoal)")
    print("  %s" % memory.log_summary(LEAD))

    # -- LGPD ---------------------------------------------------------------

    rule("LGPD")
    package = memory.export(LEAD)
    print("  Direito de acesso: %d mensagens, %d campos de perfil, consentimento em %s"
          % (len(package["messages"]), len(package["profile"]),
             package["consent"]["at"][:10]))

    memory.forget(LEAD)
    print("  Direito de exclusão: apagado. Perfil agora: %r" % memory.profile(LEAD))
    print("  Mapa de pseudônimos (guardava PII em claro): %r"
          % memory.state(LEAD)["alias_map"])

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
