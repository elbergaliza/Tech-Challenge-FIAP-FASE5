# A cola com a IA. Aqui o backend deixa de ser CRUD.
#
# Este modulo importa o `agent.py` da Pessoa 1 e os modulos da Pessoa 2 (memoria,
# RAG, resumo, follow-up) e os orquestra num turno de conversa. Ele e a traducao
# para servidor do `ai-memory-rag/scripts/run_chat.py`, que e o unico lugar onde
# esse fluxo ja tinha sido montado ponta a ponta; a ordem das operacoes abaixo
# segue a de la, inclusive nas partes que existem por causa de bug ja pago.
#
# DEGRADACAO
# ----------
# Nada aqui pode derrubar o backend. Sem GEMINI_API_KEY o chat responde com um
# agente mock, o RAG usa embedder lexical e o resumo usa heuristica. A API sobe
# igual, com todas as rotas funcionando: e o que permite a Parte 4 (front)
# desenvolver contra este backend antes de existir chave, e o que evita que uma
# cota estourada no meio da apresentacao mate a demo.
#
# O QUE E CARO E POR ISSO E CACHEADO
# ----------------------------------
# Indexar os 140 imoveis custa uma chamada de embedding por imovel. Por isso o
# indice e construido UMA vez, na primeira necessidade, e guardado em disco pelo
# proprio `indexer.get_index`. Reindexar a cada request torraria a cota do plano
# gratuito antes da terceira mensagem.

import re
import threading

import bootstrap  # noqa: F401
import config

# --- Parte 2 (ai-memory-rag) -----------------------------------------------
# Import com fallback: se a pasta nao estiver no checkout, o backend ainda sobe
# e o chat cai inteiro no mock. Melhor uma demo degradada do que um 500 no boot.
try:
    import lead_profile
    from followup import FollowUpGenerator, evaluate_followup, leads_due_for_followup
    from llm import get_client, retry_transient
    from memory.conversation_memory import ConversationMemory, extract_pii
    from rag import indexer, retriever
    from rag import schema as rag_schema
    from rag.embeddings import HashingEmbedder, get_embedder, normalize
    from summarizer import Summarizer
    PARTE2_OK = True
    PARTE2_ERRO = ""
except Exception as erro:  # pragma: no cover
    PARTE2_OK = False
    PARTE2_ERRO = str(erro)

from database.memory_store import SqlAlchemyStore

_lock = threading.Lock()
_estado = {}


# ---------------------------------------------------------------------------
# Inicializacao preguicosa
# ---------------------------------------------------------------------------

def _init():
    # Monta os singletons na primeira necessidade, uma vez so.
    #
    # Preguicoso e nao no `startup` do FastAPI porque construir o indice pode
    # levar dezenas de segundos com o embedder do Gemini, e um healthcheck que
    # expira antes disso derruba o container antes de a API existir. Quem paga o
    # custo e a primeira mensagem de chat, nao o boot.
    if _estado:
        return _estado

    with _lock:
        if _estado:  # outra thread chegou primeiro enquanto esperavamos
            return _estado

        if not PARTE2_OK:
            _estado.update(
                memory=None, index=None, embedder=None, client=None,
                summarizer=None, followup=None, agente=None, extrair=None,
                modo="mock", detalhe="ai-memory-rag indisponivel: %s" % PARTE2_ERRO,
            )
            return _estado

        memory = ConversationMemory(SqlAlchemyStore())
        client = get_client(None if config.HAS_LLM else "unavailable")

        agente, extrair = _carregar_agente_real()

        _estado.update(
            memory=memory,
            client=client,
            summarizer=Summarizer(client=client),
            followup=FollowUpGenerator(client=client),
            agente=agente,
            extrair=extrair or _extracao_simples,
            modo="gemini" if agente else "mock",
            detalhe="",
            index=None,       # so na primeira busca
            embedder=None,
            index_tentado=False,
        )

        return _estado


def _carregar_agente_real():
    # Importa o agente da Pessoa 1, ou devolve (None, None).
    #
    # O import fica dentro do try de proposito: o `agent.py` levanta ValueError
    # ja no topo do modulo quando nao ha GEMINI_API_KEY, entao nao da para
    # verificar antes de importar.
    try:
        from agent import chamar_agente, extrair_dados_estruturados
        return chamar_agente, extrair_dados_estruturados
    except Exception as erro:
        print("[ai] Agente real indisponivel (%s). Usando mock." % _uma_linha(erro))
        return None, None


def _get_index():
    # Indice do RAG, construido no primeiro uso. None se nao der.
    estado = _init()

    # Sem a Parte 2 os nomes `get_embedder` e `indexer` nem existem: sair aqui
    # e o que impede um NameError disfarcado de erro de RAG.
    if not PARTE2_OK:
        return None, None

    if estado.get("index") is not None or estado.get("index_tentado"):
        return estado.get("index"), estado.get("embedder")

    with _lock:
        estado["index_tentado"] = True
        try:
            embedder = get_embedder(prefer=None if config.HAS_LLM else "hashing")
            index, origem = indexer.get_index(embedder, indexer.load_properties())

            # O embedder pode ter mudado no caminho: se o Gemini falhou ao
            # indexar, o `get_index` cai para o lexical, e continuar usando o
            # embedder do Gemini para a QUERY compararia vetores de espacos
            # diferentes, devolvendo lixo com cara de resultado.
            if index.embedder_name != embedder.name:
                embedder = HashingEmbedder()

            estado["index"] = index
            estado["embedder"] = embedder
            print("[rag] %d imoveis indexados (%s, %s)."
                  % (len(index), embedder.name, origem))
        except Exception as erro:
            print("[rag] Indice indisponivel: %s" % _uma_linha(erro))
            estado["index"] = None
            estado["embedder"] = None

    return estado.get("index"), estado.get("embedder")


def status() -> dict:
    # O que esta ligado de verdade. Vai no `GET /health`.
    estado = _init()
    index = estado.get("index")

    return {
        "parte2": PARTE2_OK,
        "agente": estado.get("modo", "mock"),
        "llm": getattr(estado.get("client"), "name", "unavailable"),
        "rag": {
            "carregado": index is not None,
            "imoveis": len(index) if index is not None else 0,
            "embedder": getattr(estado.get("embedder"), "name", None),
        },
        "detalhe": estado.get("detalhe", ""),
    }


# ---------------------------------------------------------------------------
# Agente mock
# ---------------------------------------------------------------------------

PERGUNTAS_MOCK = {
    "intent": "Legal! Você está procurando para comprar, alugar ou investir?",
    "region": "Perfeito. Qual região você tem em mente?",
    "bedrooms": "Entendi. Quantos quartos você precisa?",
    "price_range": "Show. Qual faixa de preço faz sentido pra você?",
    "urgency": "E a urgência, precisa para logo ou dá pra ir com calma?",
    "phone": "Me passa um telefone pra eu te enviar as opções?",
}


def _agente_mock(memory, lead_id):
    # SDR de mentira: pergunta o proximo campo que a memoria ainda nao tem.
    #
    # Nao substitui o agente da Pessoa 1 e nao tenta: ele nao conversa, so segue
    # a lista de coleta. Existe para o backend inteiro ser testavel e demonstravel
    # sem chave de API, e para o front nao ficar bloqueado esperando a Parte 1.
    def chamar(mensagem, historico, lead=None):
        # Perfil lido DEPOIS de a memoria ja ter absorvido a mensagem atual: o
        # `processar_mensagem` faz a absorcao antes de chamar o agente. Sem
        # isso, o mock repetiria a pergunta que o lead acabou de responder.
        profile = memory.profile(lead_id)
        faltando = lead_profile.next_to_collect(profile)

        if faltando:
            resposta = PERGUNTAS_MOCK.get(faltando, "Me conta um pouco mais?")
        else:
            nome = profile.get("name")
            resposta = ("Perfeito%s, já tenho o que preciso. "
                        "Quer agendar uma visita?" % (", " + nome if nome else ""))

        # `dados_coletados` vazio de proposito: quem extrai e o
        # `processar_mensagem`, sobre a mensagem limpa. Devolver algo aqui seria
        # extrair duas vezes.
        return {"resposta": resposta, "dados_coletados": {},
                "status_qualificacao": "em_andamento", "confianca": 0.0}

    return chamar


# Imitacao simplificada da extracao da Pessoa 1, para o modo sem chave. O
# `agent.py` dela faz isso melhor e com mais casos; aqui o objetivo e so o
# backend ter o que alimentar na memoria sem depender de API.
#
# Devolve no dialeto DELA (chaves e enums em portugues), porque e esse o formato
# que o `lead_profile.from_agent()` sabe traduzir. Emitir o dialeto ja traduzido
# faria a memoria receber um perfil que ela nao reconhece.
_RE_QUARTOS = re.compile(r"(\d+)\s*(?:quartos?|dorm)", re.IGNORECASE)
_RE_PRECO = re.compile(
    r"\d[\d.,]*\s*(?:mil|milh(?:ao|ão|oes|ões)|mi|[km])\b|\bR\$\s*\d[\d.,]*",
    re.IGNORECASE,
)


def _extracao_simples(texto):
    # Extracao minima para quando a Pessoa 1 nao esta disponivel.
    #
    # Nome, e-mail e telefone NAO saem daqui: quem os extrai e o `extract_pii` da
    # Parte 2, que roda sempre, sobre o texto original e nao sobre o mascarado.
    baixo = normalize(texto) if PARTE2_OK else texto.lower()
    dados = {}

    if any(p in baixo for p in ("investir", "investimento", "renda", "rentab")):
        dados["intencao"] = "INVESTIMENTO"
    elif any(p in baixo for p in ("alugar", "aluguel", "locacao")):
        dados["intencao"] = "ALUGUEL"
    elif any(p in baixo for p in ("comprar", "compra", "adquirir")):
        dados["intencao"] = "COMPRA"

    # Bairros e zonas vem do schema do RAG, e nao de uma lista escrita a mao
    # aqui: uma lista propria sairia de sincronia com a base de imoveis, e a
    # regiao extraida deixaria de casar com qualquer imovel na busca.
    if PARTE2_OK:
        for lugar in list(rag_schema.NEIGHBORHOODS) + list(rag_schema.ZONES):
            if normalize(lugar) in baixo:
                dados["regiao"] = lugar
                break

    quartos = _RE_QUARTOS.search(texto)
    if quartos:
        dados["quartos"] = quartos.group(1)

    preco = _RE_PRECO.search(texto)
    if preco:
        dados["preco_faixa"] = preco.group(0).strip()

    if any(p in baixo for p in ("urgente", "urgencia", "rapido", "logo",
                                "essa semana")):
        dados["urgencia"] = "alta"

    return dados


# ---------------------------------------------------------------------------
# Retentativa
# ---------------------------------------------------------------------------

# Texto exato que o agente da Pessoa 1 devolve quando a chamada ao Gemini falha.
# Acoplamento a uma string dela; se ela mudar a mensagem, isto para de detectar
# e a retentativa simplesmente nao acontece (nao quebra nada).
FALHA_DO_AGENTE = "tive um problema"


class _Sobrecarregado(Exception):
    def __init__(self, resultado):
        super().__init__((resultado or {}).get("resposta", ""))
        self.resultado = resultado


def _chamar_com_retentativa(chamar_agente, *args, **kwargs):
    # Insiste quando o Gemini responde 503 por demanda alta.
    #
    # O 503 "high demand" e capacidade momentanea do lado do Google, nao defeito
    # do pedido: em horario de pico chega a ~1 em 3 chamadas. O agente da Pessoa 1
    # ja captura a excecao internamente e devolve o texto de erro, entao nao ha
    # excecao para pegar aqui: a deteccao e pelo texto. E feio, e e o preco de nao
    # mexer no codigo dela.
    def uma_vez():
        resultado = chamar_agente(*args, **kwargs)
        if FALHA_DO_AGENTE in (resultado or {}).get("resposta", ""):
            raise _Sobrecarregado(resultado)
        return resultado

    if not PARTE2_OK:
        return uma_vez()

    try:
        return retry_transient(
            uma_vez, retryable=lambda e: isinstance(e, _Sobrecarregado),
        )
    except _Sobrecarregado as desistencia:
        # Acabaram as tentativas: devolve a ultima resposta dela sem gastar mais
        # uma chamada. O turno e gravado do mesmo jeito e a memoria nao se perde.
        return desistencia.resultado


# ---------------------------------------------------------------------------
# O turno
# ---------------------------------------------------------------------------

# Campos que, uma vez conhecidos, justificam buscar imovel. Nos primeiros turnos
# o RAG devolveria tres imoveis ao acaso, o que confunde mais do que ajuda.
CAMPOS_QUE_LIBERAM_BUSCA = ("intent", "region", "bedrooms", "price_range")


def processar_mensagem(lead_id: str, mensagem: str) -> dict:
    # Um turno completo: memoria -> RAG -> agente -> memoria.
    #
    # Devolve um dict com resposta, perfil, novidades, imoveis e o card do
    # corretor recalculado. NAO toca no banco relacional: quem sincroniza lead e
    # mensagens e o `chat_service`, para que a fronteira "IA" / "persistencia"
    # continue visivel.
    estado = _init()

    if not PARTE2_OK:
        return _turno_degradado(lead_id, mensagem, estado)

    memory = estado["memory"]
    extrair = estado["extrair"]
    chamar_agente = estado["agente"] or _agente_mock(memory, lead_id)
    origem = "gemini" if estado["agente"] else "mock"

    # 1. A memoria registra a mensagem e devolve tudo pseudonimizado.
    turno = memory.start_turn(lead_id, mensagem, window=config.HISTORY_WINDOW)

    # 2. A memoria absorve o que a mensagem trouxe ANTES de qualquer um
    #    raciocinar sobre ela. A ordem custou um bug no run_chat: com a absorcao
    #    so no `finish_turn`, o agente decidia a proxima pergunta lendo um perfil
    #    defasado em um turno e repetia o que o lead acabara de responder.
    #
    #    `extrair` roda sobre a mensagem MASCARADA, que e o que a Pessoa 1 veria.
    #    `extract_pii` roda sobre o texto ORIGINAL, porque nome, e-mail e
    #    telefone estao mascarados na outra versao.
    dados = extrair(turno.message)
    dados.update(extract_pii(mensagem))
    # A mensagem original vai junto para a memoria poder descartar a urgencia
    # "baixa" que a Pessoa 1 emite por default em qualquer texto.
    novidades = memory.update_profile(lead_id, dados, message=mensagem)

    # 3. Contexto refeito DEPOIS da absorcao, senao o prompt diria "ainda falta
    #    descobrir quartos" na mesma mensagem em que o lead os informou. O mapa
    #    de apelidos pode ter crescido, entao o turno e reconciliado para a
    #    restauracao continuar cobrindo tudo.
    turno.context = memory.build_context(lead_id)
    turno.mapping.update(memory.alias_map(lead_id))

    perfil = memory.profile(lead_id)

    # 4. RAG, so quando ja se sabe algo do lead.
    busca = None
    bloco_imoveis = ""
    if any(lead_profile.is_known(perfil.get(c)) for c in CAMPOS_QUE_LIBERAM_BUSCA):
        busca = _buscar(memory, lead_id, perfil, mensagem)
        bloco_imoveis = retriever.format_for_prompt(busca) if busca else ""

    # 5. A Pessoa 1 ainda nao aceita `contexto_extra`, entao o contexto vai
    #    prefixado na mensagem. Contorno temporario: com o parametro, isto seria
    #    `chamar_agente(turno.message, turno.history, lead_id,
    #    contexto_extra=turno.context + bloco_imoveis)`.
    contexto = "\n\n".join(p for p in (turno.context, bloco_imoveis) if p)
    mensagem_para_o_agente = (
        "%s\n\nMENSAGEM DO CLIENTE:\n%s" % (contexto, turno.message)
        if contexto else turno.message
    )

    try:
        resultado = _chamar_com_retentativa(
            chamar_agente, mensagem_para_o_agente, turno.history, lead_id,
        )
    except Exception as erro:
        print("[ai] Agente falhou: %s" % _uma_linha(erro))
        resultado = {"resposta": "Desculpe, tive um problema técnico aqui. "
                                 "Pode repetir?"}
        origem = "erro"

    # 6. `dados_coletados` do agente e IGNORADO de proposito. A extracao dela e
    #    regex sobre o texto recebido, e nos prefixamos o contexto e o bloco do
    #    RAG na mensagem. Ela extrairia dados dos IMOVEIS como se o lead os
    #    tivesse dito: "renda de aluguel" na descricao virava intencao
    #    INVESTIMENTO, o bairro do imovel virava a regiao desejada, o preco do
    #    imovel virava o orcamento. A extracao correta ja aconteceu no passo 2,
    #    sobre a mensagem limpa.
    resposta, mais_novidades = memory.finish_turn(
        lead_id, turno, resultado.get("resposta", ""),
    )
    novidades = list(novidades) + list(mais_novidades or [])

    if busca and busca.recommendations:
        memory.record_shown_properties(lead_id, [r.id for r in busca])

    # 7. Card do corretor recalculado. `use_llm=False`: e uma chamada de LLM por
    #    turno so para atualizar um score que a heuristica ja calcula bem. O
    #    resumo com IA e gerado sob demanda no GET /leads/{id}?resumo_ia=true.
    card = estado["summarizer"].summarize_for_broker(memory, lead_id, use_llm=False)

    perfil = memory.profile(lead_id)

    return {
        "resposta": resposta,
        "perfil": perfil,
        "novidades": [dict(n) for n in novidades],
        "imoveis": _imoveis_para_dto(busca),
        "card": card.to_dict(),
        "origem": origem,
        # Tudo coletado = hora de oferecer visita. E o mesmo criterio que o
        # prompt da Pessoa 1 usa ("quando tiver 4+ dados, ofereca agendar"),
        # mas medido sobre a memoria, que nao esquece o que saiu da janela.
        "sugerir_agendamento": lead_profile.next_to_collect(perfil) is None,
    }


def _turno_degradado(lead_id, mensagem, estado):
    # Sem a Parte 2 nao ha memoria: responde algo util e diz a verdade.
    return {
        "resposta": "Oi! Estou com o cérebro parcialmente offline por aqui "
                    "(módulo de memória indisponível), mas registrei sua "
                    "mensagem e um corretor vai te responder.",
        "perfil": {}, "novidades": [], "imoveis": [], "card": None,
        "origem": "degradado", "sugerir_agendamento": False,
    }


def _buscar(memory, lead_id, perfil, mensagem):
    # Busca imoveis, devolvendo None se a camada de embedding falhar.
    #
    # Uma cota estourada no meio de uma conversa nao pode derrubar o turno e levar
    # junto a memoria: sem imovel a conversa continua, sem resposta ela morre.
    index, embedder = _get_index()
    if index is None:
        return None

    try:
        return retriever.search_for_lead(
            index, perfil, mensagem, embedder=embedder, top_k=3,
        )
    except Exception as erro:
        print("[rag] Busca indisponivel: %s" % _uma_linha(erro))
        return None


def _imoveis_para_dto(busca):
    if not busca:
        return []

    saida = []
    for rec in busca:
        prop = rec.property
        saida.append({
            "id": prop["id"],
            "title": prop["title"],
            "neighborhood": prop["neighborhood"],
            "zone": prop.get("zone"),
            "deal_type": prop["deal_type"],
            "price": prop["price"],
            "bedrooms": prop["bedrooms"],
            "area_m2": prop.get("area_m2"),
            "score": round(rec.score, 4),
            "reason": rec.reason,
        })
    return saida


# ---------------------------------------------------------------------------
# Consultas usadas pelas rotas de leitura
# ---------------------------------------------------------------------------

def card_do_corretor(lead_id: str, use_llm: bool = False) -> dict | None:
    # O resumo do lead para o dashboard. `use_llm=True` gasta uma chamada.
    estado = _init()
    if not PARTE2_OK or not estado["memory"].exists(lead_id):
        return None

    card = estado["summarizer"].summarize_for_broker(
        estado["memory"], lead_id, use_llm=use_llm and config.HAS_LLM,
    )
    return card.to_dict()


def perfil(lead_id: str) -> dict:
    estado = _init()
    if not PARTE2_OK:
        return {}
    return estado["memory"].profile(lead_id)


def horas_de_silencio(lead_id: str) -> float:
    estado = _init()
    if not PARTE2_OK or not estado["memory"].exists(lead_id):
        return 0.0
    return estado["memory"].hours_of_silence(lead_id)


def registrar_consentimento(lead_id: str, granted: bool = True) -> None:
    estado = _init()
    if PARTE2_OK:
        estado["memory"].record_consent(
            lead_id, granted, "atendimento imobiliário e follow-up",
        )


def esquecer(lead_id: str) -> bool:
    # Direito de exclusao (LGPD): apaga a memoria da IA.
    #
    # A linha do lead no banco relacional e apagada pelo `lead_service`; sao dois
    # passos porque sao duas representacoes, e a rota chama os dois.
    estado = _init()
    if not PARTE2_OK:
        return False
    return estado["memory"].forget(lead_id)


def exportar(lead_id: str) -> dict | None:
    # Direito de acesso (LGPD): tudo que a memoria guarda deste lead.
    estado = _init()
    if not PARTE2_OK or not estado["memory"].exists(lead_id):
        return None
    return estado["memory"].export(lead_id)


def followups_pendentes(gerar_texto: bool = False) -> list[dict]:
    # Leads que merecem follow-up agora, do mais silencioso ao menos.
    #
    # `gerar_texto=False` por padrao: e uma chamada de LLM por lead, e o dashboard
    # so precisa da lista. O texto vem quando o corretor abre o item.
    estado = _init()
    if not PARTE2_OK:
        return []

    memory = estado["memory"]
    pendentes = []

    for lead_id, decisao in leads_due_for_followup(memory):
        item = {
            "lead_id": lead_id,
            "horas_de_silencio": round(decisao.hours_of_silence, 1),
            "tentativa": decisao.attempt,
            "tom": decisao.tone,
            "motivo": decisao.reason,
            "canal": None,
            "texto_sugerido": None,
        }

        if gerar_texto:
            try:
                fup = estado["followup"].generate(memory, lead_id)
                item["canal"] = fup.channel
                item["texto_sugerido"] = fup.text
            except Exception as erro:
                item["motivo"] += " (texto indisponivel: %s)" % _uma_linha(erro)

        pendentes.append(item)

    return pendentes


def gerar_followup(lead_id: str) -> dict | None:
    # Gera E REGISTRA um follow-up: a memoria conta a tentativa.
    #
    # `send()` e nao `generate()` porque so o primeiro incrementa o contador de
    # tentativas. Gerar sem registrar faria o mesmo lead reaparecer na lista de
    # pendentes para sempre, e a cadencia nunca avancaria de "reopen" para
    # "signoff".
    estado = _init()
    if not PARTE2_OK:
        return None

    decisao = evaluate_followup(estado["memory"], lead_id)
    if not decisao.send:
        return {"enviado": False, "motivo": decisao.reason}

    fup = estado["followup"].send(estado["memory"], lead_id)
    return {"enviado": True, **fup.to_dict()}


def _uma_linha(erro):
    return str(erro).strip().replace("\n", " ")[:160]
