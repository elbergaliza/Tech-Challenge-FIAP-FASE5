"""
Chat interativo no terminal: a Parte 2 rodando de ponta a ponta.

    python ai-memory-rag/scripts/run_chat.py

É o mais perto de "rodar o projeto" que dá hoje, porque o backend (Parte 3) e o
frontend (Parte 4) ainda estão vazios. Este script faz o papel dos dois: recebe
a mensagem digitada, chama a Parte 2, chama o agente da Parte 1 e devolve a
resposta na tela.

MODO SEM CHAVE (padrão quando não há GEMINI_API_KEY)
    Usa um agente simulado que decide a próxima pergunta a partir do que a
    MEMÓRIA já sabe. Não é o agente da Pessoa 1, e o script avisa isso na tela.
    Serve para exercitar memória, RAG, pseudonimização, resumo e follow-up.

MODO COM CHAVE
    Importa o `chamar_agente` de verdade do `ai-core/src/agent.py` e conversa
    com o Gemini. Precisa de `pip install google-genai` e de `GEMINI_API_KEY`.

Comandos durante a conversa:
    /perfil     o que a memória já sabe deste lead
    /prompt     o que exatamente saiu para o LLM no último turno
    /imoveis    roda a busca agora e mostra o bloco que vai para o prompt
    /resumo     o card do corretor para este lead
    /followup   avalia se caberia follow-up e mostra o texto que seria enviado
    /exportar   o pacote do direito de acesso (LGPD)
    /esquecer   apaga tudo do lead (direito de exclusão)
    /sair       encerra

Passe --simulado para forçar o modo sem IA mesmo tendo chave, útil para
comparar os dois lado a lado.
"""

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, os.path.join(REPO_ROOT, "ai-core", "src"))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
try:
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass

from followup import FollowUpGenerator, evaluate_followup                 # noqa: E402
from lead_profile import FIELD_LABELS, PROFILE_FIELDS, is_known           # noqa: E402
from llm import get_client                                               # noqa: E402
from memory.conversation_memory import (                                 # noqa: E402
    ConversationMemory, JsonFileStore, extract_pii,
)
from privacy import mask_for_log                                         # noqa: E402
from rag import indexer, retriever, schema                               # noqa: E402
from rag.embeddings import (                                             # noqa: E402
    HashingEmbedder, get_embedder, load_env, normalize,
)
from summarizer import Summarizer                                        # noqa: E402

DATA_DIR = os.path.join(HERE, "..", "data", "conversas")


# ---------------------------------------------------------------------------
# Agente
# ---------------------------------------------------------------------------

def carregar_agente_real():
    """Importa o agente da Pessoa 1, ou devolve (None, None) se não der.

    O `agent.py` levanta ValueError já no import quando não há GEMINI_API_KEY,
    então o import fica dentro do try de propósito.

    Devolve também o `extrair_dados_estruturados` dela, e a razão está no
    comentário longo do loop de conversa: a extração precisa rodar sobre a
    mensagem limpa do lead, não sobre o texto enriquecido que vai ao LLM.
    """
    try:
        from agent import chamar_agente, extrair_dados_estruturados
        return chamar_agente, extrair_dados_estruturados
    except Exception as erro:
        print("[agente real indisponível: %s]" % erro)
        return None, None


# Ordem em que o SDR simulado persegue os campos que faltam.
ORDEM_DE_COLETA = ("intent", "region", "bedrooms", "price_range", "urgency", "phone")

PERGUNTAS = {
    "intent": "Você está procurando para comprar, alugar ou investir?",
    "region": "Qual região você tem em mente?",
    "bedrooms": "Quantos quartos você precisa?",
    "price_range": "Qual sua faixa de preço?",
    "urgency": "Como está a urgência? Precisa para logo?",
    "phone": "Me passa um telefone para eu te enviar as opções?",
}


_QUARTOS = re.compile(r"(\d+)\s*(?:quartos?|dorm)", re.IGNORECASE)
_PRECO = re.compile(
    r"\d[\d.,]*\s*(?:mil|milh(?:ao|ão|oes|ões)|mi|[km])\b|\bR\$\s*\d[\d.,]*",
    re.IGNORECASE,
)


def _extracao_de_mentira(texto):
    """Imitação MUITO simplificada da extração da Pessoa 1.

    Existe só para o modo sem chave ter o que alimentar na memória. O
    `agent.py` de verdade faz isso melhor e com mais casos; aqui o objetivo é
    apenas exercitar o meu módulo, não substituir o trabalho dela.

    Devolve no dialeto dela (chaves e enums em português), porque é justamente
    esse formato que o `lead_profile.from_agent()` precisa saber traduzir.
    """
    baixo = normalize(texto)
    dados = {}

    if any(p in baixo for p in ("investir", "investimento", "renda", "rentab")):
        dados["intencao"] = "INVESTIMENTO"
    elif any(p in baixo for p in ("alugar", "aluguel", "locacao")):
        dados["intencao"] = "ALUGUEL"
    elif any(p in baixo for p in ("comprar", "compra", "adquirir")):
        dados["intencao"] = "COMPRA"

    for lugar in list(schema.NEIGHBORHOODS) + list(schema.ZONES):
        if normalize(lugar) in baixo:
            dados["regiao"] = lugar
            break

    quartos = _QUARTOS.search(texto)
    if quartos:
        dados["quartos"] = quartos.group(1)

    preco = _PRECO.search(texto)
    if preco:
        dados["preco_faixa"] = preco.group(0).strip()

    if any(p in baixo for p in ("urgente", "urgencia", "rapido", "logo", "essa semana")):
        dados["urgencia"] = "alta"

    return dados


def agente_simulado(memory, lead_id):
    """SDR de mentira que pergunta o próximo campo que a memória não tem.

    Existe para o script rodar sem chave de API. Não substitui o agente da
    Pessoa 1: ele não conversa, só segue a lista. Mas é suficiente para ver a
    memória guiando o diálogo, que é o ponto.
    """
    def chamar(mensagem, historico, lead=None):
        # Lê o perfil DEPOIS de a memória já ter absorvido a mensagem atual. O
        # loop faz essa absorção antes de chamar o agente, justamente para que
        # este `profile` não esteja defasado em um turno: sem isso, o agente
        # perguntava de novo exatamente o que o lead acabara de responder.
        profile = memory.profile(lead_id)
        faltando = [c for c in ORDEM_DE_COLETA if not is_known(profile.get(c))]

        if faltando:
            resposta = PERGUNTAS[faltando[0]]
        else:
            resposta = "Perfeito, tenho tudo que preciso. Quer agendar uma visita?"

        # `dados_coletados` vazio de propósito: neste script quem extrai é o
        # loop, sobre a mensagem limpa. Devolver algo aqui seria extrair duas
        # vezes.
        return {
            "resposta": resposta,
            "dados_coletados": {},
            "status_qualificacao": "em_andamento",
            "extracoes_completas": [],
            "confianca": 0.0,
        }

    return chamar


# ---------------------------------------------------------------------------
# Apresentação
# ---------------------------------------------------------------------------

def linha(titulo=""):
    print("\n" + "-" * 74)
    if titulo:
        print(titulo)
        print("-" * 74)


def mostrar_perfil(memory, lead_id):
    profile = memory.profile(lead_id)
    if not profile:
        print("  (nada coletado ainda)")
        return

    for campo in PROFILE_FIELDS:
        if is_known(profile.get(campo)):
            print("  %-16s %s" % (FIELD_LABELS[campo] + ":", profile[campo]))


def buscar_sem_quebrar(fn, *args, **kwargs):
    """Roda uma busca e devolve [] se a camada de embedding falhar.

    O chat e a unica forma de demonstrar a Parte 2 enquanto nao existe backend.
    Uma cota estourada no meio de uma conversa nao pode derrubar a sessao e
    levar junto a memoria do turno.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as error:
        texto = str(error).strip().replace(chr(10), " ")
        print("[rag] Busca indisponivel: %s" % (texto[:140],))
        return []


def mostrar_imoveis(index, embedder, memory, lead_id, mensagem):
    resultado = buscar_sem_quebrar(
        retriever.search_for_lead,
        index, memory.profile(lead_id), mensagem, embedder=embedder, top_k=3,
    )
    memory.record_shown_properties(lead_id, [r.id for r in resultado])
    print(retriever.format_for_prompt(resultado))
    return resultado


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------

def main():
    # O .env é procurado na RAIZ DO REPOSITÓRIO, não no diretório de onde o
    # script foi chamado. Sem isso, rodar de dentro de ai-memory-rag/ não
    # encontraria a chave e o modo real falharia sem explicação. Carregar aqui
    # também resolve para o `agent.py` da Pessoa 1: ele lê `os.getenv` no
    # import, e a variável já vai estar no ambiente.
    load_env(os.path.join(REPO_ROOT, ".env"))

    argumentos = [a for a in sys.argv[1:] if not a.startswith("--")]
    forcar_simulado = "--simulado" in sys.argv

    lead_id = argumentos[0] if argumentos else "lead-terminal"

    embedder = get_embedder(prefer="hashing" if forcar_simulado else None)
    # `get_index` e nao `build_index`: indexar custa uma chamada de API por
    # imovel, e reconstruir os 140 a cada inicializacao estourava a cota do
    # plano gratuito antes mesmo da primeira mensagem. O indice fica em disco
    # e so e refeito quando o embedder ou a base mudam.
    index, origem = indexer.get_index(embedder, indexer.load_properties())
    # O embedder pode ter mudado no caminho: se o Gemini falhou ao indexar, o
    # `get_index` cai para o lexical, e o cabecalho precisa dizer a verdade.
    if index.embedder_name != embedder.name:
        embedder = HashingEmbedder()
    client = get_client("unavailable" if forcar_simulado else None)
    summarizer = Summarizer(client=client)
    gerador = FollowUpGenerator(client=client)

    memory = ConversationMemory(JsonFileStore(DATA_DIR))

    chamar_agente, extrair_dados = (
        (None, None) if forcar_simulado else carregar_agente_real()
    )
    modo_real = chamar_agente is not None
    if not modo_real:
        chamar_agente = agente_simulado(memory, lead_id)
        extrair_dados = _extracao_de_mentira

    print("=" * 74)
    print("AGENTE SDR IMOBILIÁRIO  ·  Parte 2 rodando de ponta a ponta")
    print("=" * 74)
    print("lead_id:   %s" % lead_id)
    if modo_real:
        rotulo_agente = "Pessoa 1 + Gemini"
    elif forcar_simulado:
        rotulo_agente = "SIMULADO (forçado por --simulado)"
    else:
        rotulo_agente = "SIMULADO (agente real indisponível)"
    print("agente:    %s" % rotulo_agente)
    print("resumo:    %s" % (client.name if client.available
                             else "heurístico (sem LLM)"))
    print("embedder:  %s  ·  %d imóveis indexados (%s)"
          % (embedder.name, len(index), "cache" if origem == "cache" else "recém-indexado"))
    print("memória:   %s" % os.path.normpath(DATA_DIR))

    if memory.exists(lead_id):
        print("\nConversa anterior encontrada: %d mensagens. Retomando."
              % memory.message_count(lead_id))
    else:
        memory.record_consent(lead_id, True, "atendimento imobiliário e follow-up")
        print("\nConsentimento registrado (LGPD).")

    print("\nDigite sua mensagem. /sair para encerrar, /perfil, /imoveis,")
    print("/prompt, /resumo, /followup, /exportar, /esquecer.")

    ultimo_turno = None

    while True:
        try:
            entrada = input("\nvocê > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not entrada:
            continue

        # -- comandos --------------------------------------------------------

        if entrada in ("/sair", "/quit", "/exit"):
            break

        if entrada == "/perfil":
            linha("O QUE A MEMÓRIA SABE")
            mostrar_perfil(memory, lead_id)
            print("\n  mensagens: %d  ·  imóveis mostrados: %d  ·  follow-ups: %d"
                  % (memory.message_count(lead_id),
                     len(memory.shown_properties(lead_id)),
                     memory.state(lead_id)["followups_sent"]))
            continue

        if entrada == "/prompt":
            linha("O QUE SAIU PARA O LLM NO ÚLTIMO TURNO")
            if not ultimo_turno:
                print("  (nenhum turno ainda)")
            else:
                print("mensagem:\n  %s" % ultimo_turno.message)
                print("\ncontexto:")
                print("  " + (ultimo_turno.context or "(vazio)").replace("\n", "\n  "))
                print("\nmapa de apelidos (NUNCA sai daqui):")
                print("  %r" % ultimo_turno.mapping)
            continue

        if entrada == "/imoveis":
            linha("BUSCA AGORA")
            mostrar_imoveis(index, embedder, memory, lead_id, "")
            continue

        if entrada == "/resumo":
            linha("CARD DO CORRETOR")
            print(summarizer.summarize_for_broker(memory, lead_id).to_markdown())
            continue

        if entrada == "/followup":
            linha("FOLLOW-UP")
            decisao = evaluate_followup(memory, lead_id)
            print("  enviar: %s" % decisao.send)
            print("  motivo: %s" % decisao.reason)
            if decisao.send:
                fup = gerador.generate(memory, lead_id)
                print("  canal:  %s" % fup.channel)
                print("  texto:  \"%s\"" % fup.text)
            continue

        if entrada == "/exportar":
            linha("DIREITO DE ACESSO (LGPD)")
            pacote = memory.export(lead_id)
            print("  mensagens: %d" % len(pacote["messages"]))
            print("  perfil:    %r" % pacote["profile"])
            print("  consentimento em: %s" % pacote["consent"]["at"])
            continue

        if entrada == "/esquecer":
            memory.forget(lead_id)
            print("  Apagado. Perfil agora: %r" % memory.profile(lead_id))
            continue

        # Comando escrito errado NÃO pode virar mensagem de chat. Sem esta
        # guarda, um "/promp" com typo entrava no histórico como fala do lead,
        # sujava a contagem de mensagens e aparecia no resumo do corretor.
        if entrada.startswith("/"):
            print("  Comando desconhecido: %s" % entrada)
            print("  Disponíveis: /perfil /prompt /imoveis /resumo /followup "
                  "/exportar /esquecer /sair")
            continue

        # -- turno de conversa -----------------------------------------------

        turno = memory.start_turn(lead_id, entrada)
        ultimo_turno = turno

        # A memória absorve o que a mensagem trouxe ANTES de qualquer um
        # raciocinar sobre ela: o agente, o RAG e o contexto do prompt.
        #
        # A ordem importa e custou um bug. Com a absorção só no `finish_turn`,
        # o agente decidia a próxima pergunta lendo um perfil defasado em um
        # turno, e repetia a pergunta que o lead acabara de responder.
        #
        # `extrair_dados` roda sobre a mensagem MASCARADA, que é o que a Pessoa
        # 1 veria. `extract_pii` roda sobre o texto ORIGINAL, porque nome,
        # e-mail e telefone estão mascarados na outra versão.
        dados = extrair_dados(turno.message)
        dados.update(extract_pii(entrada))
        alteracoes = memory.update_profile(lead_id, dados)

        # Contexto refeito depois da absorção, senão o prompt diria "ainda
        # falta descobrir quartos" na mesma mensagem em que o lead os informou.
        # O mapa de apelidos pode ter crescido, então o turno é reconciliado
        # para a restauração continuar cobrindo tudo.
        turno.context = memory.build_context(lead_id)
        turno.mapping.update(memory.alias_map(lead_id))

        # Só busca imóveis quando já se sabe algo do lead. Nos primeiros
        # turnos o RAG devolveria três imóveis ao acaso, o que confunde mais do
        # que ajuda.
        perfil = memory.profile(lead_id)
        vale_buscar = any(is_known(perfil.get(c))
                          for c in ("intent", "region", "bedrooms", "price_range"))

        busca = None
        bloco_imoveis = ""
        if vale_buscar:
            busca = buscar_sem_quebrar(
                retriever.search_for_lead,
                index, perfil, entrada, embedder=embedder, top_k=3,
            )
            bloco_imoveis = retriever.format_for_prompt(busca) if busca else ""

        # A Pessoa 1 ainda não aceita `contexto_extra`, então o contexto vai
        # prefixado na mensagem. É contorno temporário: com o parâmetro, isto
        # seria `chamar_agente(turno.message, turno.history, lead_id,
        # contexto_extra=turno.context + bloco_imoveis)`.
        contexto = "\n\n".join(p for p in (turno.context, bloco_imoveis) if p)
        mensagem_para_o_agente = (
            "%s\n\nMENSAGEM DO CLIENTE:\n%s" % (contexto, turno.message)
            if contexto else turno.message
        )

        try:
            resultado = chamar_agente(mensagem_para_o_agente, turno.history, lead_id)
        except Exception as erro:
            print("agente > [falhou: %s]" % erro)
            continue

        # `dados_coletados` do agente é ignorado de propósito. A extração dela
        # é regex sobre o texto recebido, e o contexto e o bloco do RAG foram
        # prefixados na mensagem (contorno até existir `contexto_extra`). Ela
        # extrairia dados dos IMÓVEIS como se o lead os tivesse dito: uma
        # descrição com "renda de aluguel" virava intenção INVESTIMENTO, o
        # bairro do imóvel virava a região desejada, o preço do imóvel virava o
        # orçamento. A extração correta já aconteceu lá em cima, sobre a
        # mensagem limpa.
        #
        # Isto é a prova de que `contexto_extra` precisa existir de verdade no
        # `chamar_agente`: enriquecer a mensagem é ativamente perigoso enquanto
        # a extração for regex sobre o texto.
        resposta, _ = memory.finish_turn(lead_id, turno, resultado["resposta"])

        if busca and busca.recommendations:
            memory.record_shown_properties(lead_id, [r.id for r in busca])

        print("\nagente > %s" % resposta)

        if alteracoes:
            for a in alteracoes:
                marca = "~" if a["kind"] == "correction" else "+"
                print("         [%s %s: %s]" % (marca, FIELD_LABELS[a["field"]], a["to"]))

        if busca and busca.recommendations:
            print("         [rag: %s]" % ", ".join(r.id for r in busca))

        # Log sem dado pessoal, como iria para um agregador de verdade.
        print("         [log: %s]" % memory.log_summary(lead_id))

    linha("FIM")
    print("Conversa salva em %s" % os.path.normpath(DATA_DIR))
    print("Rode de novo com o mesmo lead_id para ver a memória entre sessões:")
    print("  python ai-memory-rag/scripts/run_chat.py %s" % lead_id)
    print("\nLog final, sem PII: %s" % mask_for_log(memory.log_summary(lead_id)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
