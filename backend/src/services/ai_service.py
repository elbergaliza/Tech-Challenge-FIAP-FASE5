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

import io
import os
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
    from llm import get_client, is_transient, retry_transient
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
            # O gerador do JOB nasce sem LLM quando FOLLOWUP_USA_LLM e falso,
            # que e o padrao. Ele rodava a cada 30 minutos gastando a MESMA
            # cota diaria de 20 requisicoes que o chat usa, em segundo plano e
            # sem nada na tela: o testador envelhecia alguns leads seguindo o
            # proprio roteiro e, meia hora depois, o chat comecava a responder
            # em mock sem explicacao. O texto heuristico ja usa o perfil do
            # lead e ja funciona.
            followup=FollowUpGenerator(
                client=client if config.FOLLOWUP_USA_LLM
                else get_client("unavailable")),
            agente=agente,
            extrair=extrair or _extracao_simples,
            modo="gemini" if agente else "mock",
            detalhe="",
            index=None,       # so na primeira busca
            embedder=None,
            index_tentado=False,
            # O que aconteceu DE VERDADE no ultimo turno. `modo` diz so o que
            # foi possivel montar no boot, e isso nao muda mais: com chave
            # invalida, projeto bloqueado ou cota diaria estourada, `modo`
            # continua "gemini" enquanto todo turno e respondido pelo mock, e o
            # selo do cabecalho fica verde mentindo. Isto e o que o /health
            # passa a reportar.
            ultima_origem=None,
            ultimo_erro="",
            ultima_busca_ok=None,
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


def aquecer_indice() -> None:
    # Constroi o indice fora do caminho do primeiro visitante.
    #
    # Com o embedder do Gemini, indexar os 140 imoveis leva dezenas de segundos
    # na primeira vez, e quem pagava era a primeira mensagem do chat: a pessoa
    # mandava "oi" e esperava. Chamado numa thread no start do app, o custo sai
    # do turno e vai para o boot, onde ninguem esta olhando.
    #
    # O `_lock` do `_get_index` ja cobre a corrida com uma mensagem que chegue
    # antes de terminar: a segunda espera a primeira, em vez de indexar duas
    # vezes.
    try:
        _get_index()
    except Exception as erro:  # pragma: no cover
        print("[rag] Aquecimento falhou: %s" % _uma_linha(erro))


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
        try:
            embedder = get_embedder(prefer=None if config.HAS_LLM else "hashing")

            # Um arquivo de cache POR EMBEDDER.
            #
            # Com um caminho unico, os dois embedders gravavam no mesmo lugar:
            # bastava UMA subida com a cota de embedding fora para o fallback
            # lexical sobrescrever o indice semantico do Gemini no disco. Na
            # subida seguinte, com a cota de volta, o cache nao servia mais e o
            # projeto reindexava os 140 imoveis pagando a API outra vez.
            caminho = os.path.join(
                os.path.dirname(indexer.INDEX_PATH),
                "imoveis_%s.json" % re.sub(r"[^a-zA-Z0-9_.-]", "_", embedder.name),
            )
            # Aproveita o cache ANTIGO, de caminho unico, enquanto ele
            # existir e for do mesmo embedder. Sem isto, a mudanca de nome do
            # arquivo faria cada pessoa do grupo reindexar os 140 imoveis uma
            # vez, pagando embedding por uma migracao que nao precisa custar
            # nada. O `get_index` grava no caminho novo e o antigo vira
            # inofensivo.
            if not os.path.exists(caminho):
                antigo = indexer.INDEX_PATH
                if os.path.exists(antigo):
                    try:
                        import json
                        with io.open(antigo, encoding="utf-8") as arquivo:
                            if json.load(arquivo).get("embedder") == embedder.name:
                                caminho = antigo
                    except Exception:
                        pass

            index, origem = indexer.get_index(
                embedder, indexer.load_properties(), index_path=caminho)

            # O embedder pode ter mudado no caminho: se o Gemini falhou ao
            # indexar, o `get_index` cai para o lexical, e continuar usando o
            # embedder do Gemini para a QUERY compararia vetores de espacos
            # diferentes, devolvendo lixo com cara de resultado.
            if index.embedder_name != embedder.name:
                embedder = HashingEmbedder()

            estado["index"] = index
            estado["embedder"] = embedder
            # Marcado SO no sucesso. Antes ficava marcado antes do `try`, e uma
            # falha passageira no aquecimento do boot (arquivo de imoveis
            # ocupado, rede fora por um segundo) desligava o RAG pelo resto da
            # vida do processo: nenhuma mensagem posterior retentava, e nem
            # devolver o arquivo resolvia sem reiniciar.
            estado["index_tentado"] = True
            print("[rag] %d imoveis indexados (%s, %s)."
                  % (len(index), embedder.name, origem))
        except Exception as erro:
            print("[rag] Indice indisponivel (vai retentar no proximo turno): %s"
                  % _uma_linha(erro))
            estado["index"] = None
            estado["embedder"] = None

    return estado.get("index"), estado.get("embedder")


def purgar_expirados():
    """Apaga da memoria da IA os leads fora do prazo de retencao.

    Devolve a lista de ids removidos. Nao levanta: a purga nao pode impedir a
    API de subir.
    """
    estado = _init()
    memory = estado.get("memory")
    if memory is None:
        return []

    # Os dois lados.
    #
    # `purge_expired` limpa so a memoria da IA (o `estado_json`). A linha do
    # lead, as mensagens e os agendamentos ficavam no banco relacional, entao a
    # "retencao" apagava a metade que ninguem ve e mantinha a metade com nome e
    # telefone. Aqui os dois caminham juntos, que e o que a politica promete.
    apagados = memory.purge_expired()

    if apagados:
        try:
            from database.db import SessionLocal
            from models.lead import Lead

            with SessionLocal() as db:
                for lead_id in apagados:
                    linha = db.get(Lead, lead_id)
                    if linha is not None:
                        # Mensagens e agendamentos vao junto, por cascade.
                        db.delete(linha)
                db.commit()
        except Exception as erro:
            print("[lgpd] Memoria purgada, tabelas relacionais nao: %s"
                  % _uma_linha(erro))

    return apagados


def status() -> dict:
    # O que esta ligado de verdade. Vai no `GET /health`.
    estado = _init()
    index = estado.get("index")

    # "o agente esta em mock" e a primeira coisa que alguem precisa conseguir
    # descobrir sem ler log. Por isso o que sai aqui e a origem do ULTIMO turno
    # quando ja houve algum, e so na ausencia dela o modo decidido no boot.
    ultima = estado.get("ultima_origem")
    detalhe = estado.get("detalhe", "")
    if ultima and ultima != estado.get("modo"):
        detalhe = ("Configurado como %s, mas o ultimo turno foi respondido em "
                   "%s. %s" % (estado.get("modo"), ultima,
                               estado.get("ultimo_erro", ""))).strip()

    return {
        "parte2": PARTE2_OK,
        "agente": ultima or estado.get("modo", "mock"),
        "modo_configurado": estado.get("modo", "mock"),
        "llm": getattr(estado.get("client"), "name", "unavailable"),
        "rag": {
            "carregado": index is not None,
            "imoveis": len(index) if index is not None else 0,
            "embedder": getattr(estado.get("embedder"), "name", None),
            # `carregado` diz que o objeto existe; isto diz se a ultima BUSCA
            # funcionou, que e a pergunta que importa quando a cota de
            # embedding acaba com o backend ja no ar.
            "ultima_busca_ok": estado.get("ultima_busca_ok"),
        },
        "detalhe": detalhe,
    }


# ---------------------------------------------------------------------------
# Resposta curta no contexto da pergunta
# ---------------------------------------------------------------------------

# A extracao da Pessoa 1 e regex sobre a mensagem isolada: ela entende
# "3 quartos" e nao entende "3". So que "3" e exatamente o que se responde a
# "quantos quartos voce precisa?", e o efeito de perder isso e grave: o campo
# nunca era gravado, o agente continuava perseguindo o mesmo dado e a conversa
# entrava em looping ("Quantos quartos?" / "3" / "Quantos quartos?").
#
# A solucao e devolver a pergunta ao texto antes de extrair: quem respondeu "3"
# disse "3 quartos". Reescrever e reusar a extracao existente e melhor do que
# escrever uma segunda extracao que sairia de sincronia com a primeira.
#
# So os campos em que o molde foi VERIFICADO. Urgencia ficou de fora porque
# qualquer molde devolvia "baixa", que e o default da Pessoa 1 e seria pior que
# nao responder.
MOLDE_DA_RESPOSTA = {
    "bedrooms": "%s quartos",
    "region": "no bairro %s",
    "price_range": "orcamento de %s",
    # Perfil investidor: a Pessoa 1 nao extrai estes dois, entao a captura e
    # nossa. Os dois sao valor em reais, e o molde reusa o extrator de preco.
    "investor_ticket": "orcamento de %s",
    "expected_return": "orcamento de %s",
}

# Chave da extracao da Pessoa 1 para cada campo do perfil da Parte 2.
CHAVE_DA_PESSOA_1 = {
    "bedrooms": "quartos",
    "region": "regiao",
    "price_range": "preco_faixa",
    "investor_ticket": "preco_faixa",
    "expected_return": "preco_faixa",
}

# Resposta curta e resposta A UMA PERGUNTA. Num texto longo, um numero solto e
# quase sempre outra coisa ("moro ha 3 anos no Leblon"), e reinterpretar viraria
# invencao.
MAX_PALAVRAS_RESPOSTA = 5

# O investidor ganha folga, e nao e generosidade: a resposta tipica dele nao
# cabe em cinco palavras. "quero uns 12 mil por mes" tem SEIS, caia fora da
# reinterpretacao, e o efeito era o pior possivel: `expected_return` ficava
# pendente para sempre, `next_to_collect` nunca chegava a None, o convite para
# a conversa com o especialista nunca era liberado, e o valor ainda ia parar em
# `price_range`, que para investidor nem existe.
#
# A folga so vale quando o campo pendente e de investidor E a frase tem cara de
# dinheiro, entao ela nao afrouxa a interpretacao de mais nada.
MAX_PALAVRAS_RESPOSTA_INVESTIDOR = 10


# Dinheiro tem marca propria: cifrao, "mil", "reais", ou um numero grande
# demais para ser outra coisa. Reconhecer a FORMA antes da ordem de coleta e o
# que impede o defeito que gravou "550 quartos" a partir de "R$ 8.550": a
# pergunta em aberto era quartos, e o texto era claramente orcamento.
MARCAS_DE_DINHEIRO = ("r$", "reais", "mil", "conto")

# Acima disto nao e quantidade de quarto, e valor. O catalogo vai ate 4.
MAIOR_QUARTO_CRIVEL = 10


def _parece_dinheiro(texto):
    baixo = texto.lower()
    if any(marca in baixo for marca in MARCAS_DE_DINHEIRO):
        return True

    cru = re.sub(r"[.\s]", "", texto)
    return cru.isdigit() and int(cru) > MAIOR_QUARTO_CRIVEL


def _resposta_em_contexto(memory, lead_id, mensagem, extrair, ja_extraido):
    # Devolve o que a mensagem responde, ou {}.
    texto = (mensagem or "").strip()
    if not texto:
        return {}

    palavras = len(texto.split())
    if palavras > MAX_PALAVRAS_RESPOSTA_INVESTIDOR:
        return {}

    perfil = memory.profile(lead_id)
    pendente = lead_profile.next_to_collect(perfil)

    # O teto maior so se aplica a quem investe respondendo com um valor.
    folga_de_investidor = (
        pendente in lead_profile.INVESTOR_FIELDS and _parece_dinheiro(texto)
    )
    if palavras > MAX_PALAVRAS_RESPOSTA and not folga_de_investidor:
        return {}

    # A forma da resposta manda mais que a ordem da coleta. Quem escreve
    # "R$ 8.550" falou de orcamento, mesmo que a proxima pergunta da lista
    # fosse quartos, e consumir isso como quartos perde as duas informacoes de
    # uma vez: grava um numero absurdo e joga fora o valor.
    if pendente in lead_profile.INVESTOR_FIELDS and lead_profile.is_known(
        (ja_extraido or {}).get("preco_faixa")
    ):
        # A extracao da Pessoa 1 nao conhece ticket nem retorno: ela joga todo
        # valor em reais no orcamento. Para um investidor com a pergunta de
        # ticket em aberto, "tenho 800 mil" e o ticket, e deixar isso virar
        # orcamento mistura o quanto ele TEM com o quanto ele quer gastar por
        # imovel. O valor e redirecionado, e o orcamento volta a ser
        # desconhecido para nao gravar os dois.
        return {
            pendente: (ja_extraido or {})["preco_faixa"],
            "preco_faixa": "undefined",
        }

    if pendente in lead_profile.INVESTOR_FIELDS and _parece_dinheiro(texto):
        # Para quem investe, o numero em reais responde a pergunta em aberto:
        # primeiro o ticket, depois o retorno esperado. Mandar isso para
        # `price_range` misturaria o quanto ele TEM com o quanto ele QUER.
        campo = pendente
    elif _parece_dinheiro(texto) and not lead_profile.is_known(perfil.get("price_range")):
        campo = "price_range"
    elif pendente in MOLDE_DA_RESPOSTA:
        campo = pendente
    else:
        return {}

    chave = CHAVE_DA_PESSOA_1[campo]

    # Ja veio na mensagem: nao ha o que reinterpretar.
    if lead_profile.is_known((ja_extraido or {}).get(chave)):
        return {}

    if campo == "bedrooms":
        # O numero tem que ABRIR a resposta e terminar ali: "3" e "3, urgente"
        # valem, "2000" nao (o \b impede casar so os dois primeiros digitos) e
        # "moro ha 3 anos" tambem nao, porque nao comeca com o numero.
        #
        # O caso "3, urgente" e comum: a pessoa responde duas perguntas de uma
        # vez, e antes a resposta inteira era descartada.
        achado = re.match(r"^(\d{1,2})\b", texto)
        if not achado or int(achado.group(1)) > MAIOR_QUARTO_CRIVEL:
            return {}
        texto = achado.group(1)

    if campo == "price_range" or campo in lead_profile.INVESTOR_FIELDS:
        # Os tres campos sao valor em reais e passam pelo mesmo extrator, que
        # le "5 mil" e nao le "5000" nem "R$ 8.550".
        cru = re.sub(r"[^\d]", "", texto)
        if cru and int(cru) >= 1000:
            texto = "%g mil" % (int(cru) / 1000)

    reinterpretado = extrair(MOLDE_DA_RESPOSTA[campo] % texto) or {}
    valor = reinterpretado.get(chave)

    if not lead_profile.is_known(valor):
        return {}

    # `from_agent` aceita tanto as chaves dela quanto as nossas. Os campos de
    # investidor saem com o nome NOSSO, senao o ticket viraria orcamento.
    return {campo if campo in lead_profile.INVESTOR_FIELDS else chave: valor}


# ---------------------------------------------------------------------------
# Quando o dado chega e nao da para usar
# ---------------------------------------------------------------------------

# Oito ou nove digitos seguidos, sem DDD na frente. E um telefone de verdade
# escrito pela metade, e o `extract_pii` recusa, com razao: sem DDD ele nao
# serve para ligar.
_RE_TELEFONE_SEM_DDD = re.compile(r"(?<!\d)(\d{4}[-. ]?\d{4,5})(?!\d)")

# Numero solto grande demais para ser quantidade de quarto.
_RE_NUMERO_SOLTO = re.compile(r"^\s*(\d{1,6})\s*$")


def _nao_entendi(mensagem: str, perfil: dict, pendente: str) -> str | None:
    """O que o lead mandou que ficou pelo caminho, em uma instrucao.

    Sem isto, o silencio: a extracao recusa o dado, o agente nao fica sabendo,
    seguir adiante como se tivesse recebido. Foi o que aconteceu numa conversa
    real, em que o agente escreveu "te ligaremos no 3341-0549" enquanto o
    sistema nao tinha telefone nenhum gravado, e a proxima acao do corretor
    continuava sendo "pedir telefone".
    """
    texto = (mensagem or "").strip()
    if not texto:
        return None

    if not lead_profile.has_contact(perfil):
        achado = _RE_TELEFONE_SEM_DDD.search(texto.replace("(", "").replace(")", ""))
        if achado and not lead_profile.is_known(perfil.get("phone")):
            return ("O lead mandou %s, que parece telefone mas esta sem DDD, e "
                    "sem DDD nao da para ligar. Peca o numero de novo COM o DDD, "
                    "citando um exemplo como 21 99999-0000. Nao diga que vai "
                    "ligar para ele." % achado.group(1))

    if pendente == "bedrooms":
        solto = _RE_NUMERO_SOLTO.match(texto)
        if solto and int(solto.group(1)) > MAIOR_QUARTO_CRIVEL:
            return ("O lead respondeu %s para a pergunta de quartos, e esse "
                    "numero nao e quantidade de quarto. Pergunte de novo, "
                    "confirmando se ele quis dizer outra coisa." % solto.group(1))

    return None


# ---------------------------------------------------------------------------
# Agente mock
# ---------------------------------------------------------------------------

PERGUNTAS_MOCK = {
    "intent": "Legal! Você está procurando para comprar, alugar ou investir?",
    "investor_ticket": "Entendi, investimento. Qual ticket você tem disponível?",
    "expected_return": "E que retorno mensal você espera desse investimento?",
    "region": "Perfeito. Qual região você tem em mente?",
    "bedrooms": "Entendi. Quantos quartos você precisa?",
    "price_range": "Show. Qual faixa de preço faz sentido pra você?",
    "urgency": "E a urgência, precisa para logo ou dá pra ir com calma?",
    "phone": "Me passa um telefone pra eu te enviar as opções?",
}


# Segunda tentativa de cada pergunta, com exemplo do formato. Existe porque a
# primeira pode nao ter sido entendida pela extracao, e repetir a mesma frase
# palavra por palavra poe a conversa em looping: o lead responde, o agente
# pergunta igual, o lead responde de novo. Pedir o formato quebra o ciclo, e
# quem le percebe que precisa escrever diferente.
EXEMPLOS_MOCK = {
    "intent": "Desculpa, nao peguei: e para comprar, alugar ou investir?",
    "investor_ticket": "Nao peguei o valor do ticket. Pode escrever assim: "
                       "800 mil?",
    "expected_return": "Nao peguei o retorno. Pode escrever o valor mensal, "
                       "tipo: 5 mil?",
    "region": "Nao consegui identificar o bairro. Pode escrever so o nome? "
              "Por exemplo: Botafogo.",
    "bedrooms": "Nao peguei o numero. Pode escrever assim: 3 quartos?",
    "price_range": "Nao peguei o valor. Pode escrever assim: 5 mil?",
    "urgency": "Sobre o prazo: e para logo ou da para ir com calma?",
    "phone": "Pode mandar o telefone com DDD? Por exemplo: 21 99999-0000.",
}


def _ultima_do_agente(historico):
    # A ultima fala do agente no historico, ou "".
    for item in reversed(historico or []):
        if (item or {}).get("role") in ("assistant", "model"):
            return item.get("content") or ""
    return ""


def _agente_mock(memory, lead_id):
    # SDR de mentira: pergunta o proximo campo que a memoria ainda nao tem.
    #
    # Nao substitui o agente da Pessoa 1 e nao tenta: ele nao conversa, so segue
    # a lista de coleta. Existe para o backend inteiro ser testavel e demonstravel
    # sem chave de API, e para o front nao ficar bloqueado esperando a Parte 1.
    # `contexto_extra` e ignorado de proposito: o mock nao interpreta texto,
    # ele le a memoria direto. O parametro existe para a assinatura bater com a
    # do agente real, porque quando nao ha chave e ESTE o `chamar_agente`.
    def chamar(mensagem, historico, lead=None, contexto_extra=""):
        # Perfil lido DEPOIS de a memoria ja ter absorvido a mensagem atual: o
        # `processar_mensagem` faz a absorcao antes de chamar o agente. Sem
        # isso, o mock repetiria a pergunta que o lead acabou de responder.
        profile = memory.profile(lead_id)
        faltando = lead_profile.next_to_collect(profile)

        if faltando:
            resposta = PERGUNTAS_MOCK.get(faltando, "Me conta um pouco mais?")

            # Se a ultima coisa que o agente disse ja foi esta pergunta, o lead
            # respondeu e a extracao nao entendeu. Repetir identico e o looping
            # que o testador viu: "Quantos quartos?" / "3" / "Quantos quartos?".
            #
            # O mesmo vale quando o dado veio pela metade (telefone sem DDD):
            # ai nem espera repetir, ja pede no formato.
            repetiu = bool(resposta) and resposta in _ultima_do_agente(historico)
            if repetiu or _nao_entendi(mensagem, profile, faltando):
                resposta = EXEMPLOS_MOCK.get(faltando, resposta)
        else:
            nome = profile.get("name")
            saudacao = ", " + nome if nome else ""

            # Quem investe fecha com especialista, nao com visita a imovel: e
            # a regra que o proprio contrato da API declara ao mandar
            # CONSULTORIA em vez de VISITA para INVEST.
            if profile.get("intent") == "INVEST":
                resposta = ("Perfeito%s, já tenho o que preciso. Quer agendar uma "
                            "conversa com um especialista em investimento?" % saudacao)
            else:
                resposta = ("Perfeito%s, já tenho o que preciso. "
                            "Quer agendar uma visita?" % saudacao)

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

    # Mesma leitura do `extrair_tipo_imovel` da Parte 1, so que reaproveitando
    # a tabela da Parte 2 em vez de manter uma terceira copia da lista.
    if PARTE2_OK:
        tipo = retriever.tipo_de_imovel_no_texto(texto)
        if tipo:
            dados["tipo_imovel"] = tipo

    quartos = _RE_QUARTOS.search(texto)
    if quartos:
        dados["quartos"] = quartos.group(1)

    preco = _RE_PRECO.search(texto)
    if preco:
        dados["preco_faixa"] = preco.group(0).strip()

    # Espelha o `extrair_urgencia` da Parte 1, inclusive a negação.
    #
    # "logo" saiu daqui pelo mesmo motivo que saiu de lá: "moro logo ali no
    # Leblon" é endereço, não prazo. E a ausência das pistas de urgência BAIXA
    # era um buraco próprio deste caminho: sem chave de API, quem respondia
    # "sem pressa, ano que vem" não gravava urgência nenhuma, `next_to_collect`
    # travava em `urgency` e o seletor de data nunca aparecia na demo mock,
    # que é justamente a que roda sem depender de cota.
    if any(p in baixo for p in ("sem pressa", "nao tenho pressa",
                                "não tenho pressa", "sem urgencia",
                                "sem urgência", "com calma", "ano que vem",
                                "so pesquisando", "só pesquisando",
                                "so olhando", "só olhando", "sem data")):
        dados["urgencia"] = "baixa"
    elif any(p in baixo for p in ("urgente", "urgencia", "urgência", "rapido",
                                  "rápido", "o quanto antes", "essa semana",
                                  "esta semana", "para ontem", "pra ontem",
                                  "com pressa", "imediato")):
        dados["urgencia"] = "alta"
    elif any(p in baixo for p in ("em breve", "logo logo", "proximos meses",
                                  "próximos meses", "poucos meses")):
        dados["urgencia"] = "media"

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
        # `or ""` depois do `.get`, e nao como default: quando a chave existe
        # com valor None (resposta do Gemini sem parte de texto), o default do
        # `.get` nao entra e o `in` estoura com TypeError. Esse TypeError nao e
        # `_Sobrecarregado`, entao escapava do fallback para o mock e virava
        # "tive um problema tecnico" sem marca nenhuma na tela.
        texto = (resultado or {}).get("resposta") or ""
        if not texto.strip() or FALHA_DO_AGENTE in texto:
            raise _Sobrecarregado(resultado)
        return resultado

    if not PARTE2_OK:
        return uma_vez()

    def vale_insistir(erro):
        if not isinstance(erro, _Sobrecarregado):
            return False
        # A Pessoa 1 passou a devolver o motivo junto do texto de falha. Sem
        # ele (agente antigo), mantem o comportamento de antes e retenta.
        causa = (erro.resultado or {}).get("erro")
        return is_transient(causa) if causa else True

    # `_Sobrecarregado` sobe quando as tentativas acabam. Engolir aqui e
    # devolver o texto de desculpa da Pessoa 1 era perder o turno: quem decide
    # o plano B e o chamador, que tem o mock em maos.
    return retry_transient(uma_vez, retryable=vale_insistir)


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
    dados.update(_resposta_em_contexto(memory, lead_id, turno.message, extrair, dados))
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

    # O que o lead mandou e nao deu para usar vira instrucao explicita para o
    # agente. Sem isso ele segue como se tivesse recebido o dado.
    faltando = lead_profile.next_to_collect(perfil)
    avisos = []

    recado = _nao_entendi(mensagem, perfil, faltando)
    if recado:
        avisos.append("NAO CONSEGUI USAR O QUE O LEAD MANDOU: " + recado)

    # Quem decide se da para agendar e o backend, nao o modelo. O seletor de
    # data so aparece na tela quando o perfil fecha; sem este aviso, o agente
    # convidava para "escolher o dia ai embaixo" com a tela ainda sem seletor
    # nenhum, e o cliente ficava procurando um botao que nao existia.
    if faltando is None:
        avisos.append(
            "O SELETOR DE DATA ESTA VISIVEL na tela do cliente agora. Pode "
            "convidar para escolher dia e hora ali."
        )
    else:
        avisos.append(
            "AINDA NAO OFERECA AGENDAMENTO: o seletor de data so aparece "
            "quando o perfil fecha, e ainda falta "
            + lead_profile.FIELD_LABELS.get(faltando, faltando).lower()
            + ". Convidar agora manda o cliente procurar um botao que nao "
            "esta na tela."
        )

    if avisos:
        separador = "\n\n"
        turno.context = (
            (turno.context + separador if turno.context else "")
            + separador.join(avisos)
        )

    # 4. RAG, so quando ja se sabe algo do lead.
    busca = None
    bloco_imoveis = ""
    if any(lead_profile.is_known(perfil.get(c)) for c in CAMPOS_QUE_LIBERAM_BUSCA):
        # `turno.message`, e nao `mensagem`: a busca embeda o texto no Gemini,
        # que e terceiro, e a versao crua leva nome, e-mail e telefone junto.
        # Era o unico caminho que furava a pseudonimizacao do `privacy.py`, e o
        # mascarado nao perde nada para buscar imovel: "[NOME_1] quer 3 quartos
        # em Botafogo" tem o mesmo sinal semantico que a frase original.
        busca = _buscar(memory, lead_id, perfil, turno.message)
        bloco_imoveis = retriever.format_for_prompt(busca) if busca else ""

    # 5. O contexto vai pelo parametro proprio, nao mais prefixado na mensagem.
    #    Prefixado, ele chegava ao modelo sob o titulo "NOVA MENSAGEM DO
    #    CLIENTE", ou seja, o agente lia "AINDA NAO OFERECA AGENDAMENTO" e a
    #    lista de imoveis como se o LEAD tivesse dito aquilo.
    contexto = "\n\n".join(p for p in (turno.context, bloco_imoveis) if p)

    try:
        resultado = _chamar_com_retentativa(
            chamar_agente, turno.message, turno.history, lead_id,
            contexto_extra=contexto,
        )
    except _Sobrecarregado:
        # 503 insistente do Gemini, depois das retentativas. Em vez de pedir
        # desculpa e parar a qualificacao, o mock pergunta o proximo campo que
        # falta: robotico, mas o funil continua andando e o lead continua
        # sendo aproveitado. A memoria e o RAG nao dependem do agente e seguem
        # normais, entao o turno perde o texto bonito e nada mais.
        #
        # `origem` viraria "mock" na resposta, e o front marca a bolha: a tela
        # nunca deixa de dizer quem respondeu.
        print("[ai] Gemini indisponivel apos as retentativas: caindo no mock.")
        resultado = _agente_mock(memory, lead_id)(
            turno.message, turno.history, lead_id,
        )
        origem = "mock"
    except Exception as erro:
        # Mesma politica do bloco acima, e pelo mesmo motivo: pedir desculpa
        # PARA a qualificacao, e quem esta testando fica sem saber o que fazer,
        # porque repetir a mensagem da o mesmo resultado. O mock pergunta o
        # proximo campo que falta, entao o funil continua andando, e a bolha
        # sai marcada para a tela nunca esconder quem respondeu.
        print("[ai] Agente falhou: %s" % _uma_linha(erro))
        try:
            resultado = _agente_mock(memory, lead_id)(
                turno.message, turno.history, lead_id,
            )
            origem = "mock"
        except Exception as erro_do_mock:
            # O mock nao fala com a rede; se ate ele falhar, o problema e
            # outro e a desculpa e mesmo o unico caminho.
            print("[ai] Ate o mock falhou: %s" % _uma_linha(erro_do_mock))
            resultado = {"resposta": "Desculpe, tive um problema técnico aqui. "
                                     "Pode repetir?"}
            origem = "erro"

    # O /health le isto. Sem registrar a origem REAL do turno, o selo do
    # cabecalho continuava verde dizendo "gemini" enquanto toda resposta vinha
    # do mock, que e exatamente a situacao em que quem esta testando mais
    # precisa saber o que esta acontecendo.
    estado["ultima_origem"] = origem
    if origem != "gemini":
        estado["ultimo_erro"] = _uma_linha(
            (resultado or {}).get("erro") or "") or estado.get("ultimo_erro", "")

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

    # Compressao incremental da memoria.
    #
    # O `compress_memory` existe na Parte 2 desde o comeco e NUNCA era chamado
    # por ninguem fora dos testes: `summarized_up_to` ficava em 0 e `summary`
    # em null em todos os leads do banco, inclusive num com 22 mensagens, acima
    # do limiar de 20. A funcionalidade que o desafio pede (memoria que nao
    # cresce sem limite numa conversa longa) existia e nao era exercitada pela
    # API, o que e o mesmo que nao existir na hora de demonstrar.
    #
    # `needs_summary` ja segura o custo: so passa do limiar, e so uma vez a
    # cada bloco. Falhar aqui nao pode custar o turno, que ja esta pronto.
    try:
        estado["summarizer"].compress_memory(memory, lead_id)
    except Exception as erro:
        print("[memoria] Compressao adiada: %s" % _uma_linha(erro))

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
        # O contexto que foi ao agente. Nao vai para a API: existe para o teste
        # conseguir afirmar sobre as INSTRUCOES enviadas, em vez de depender de
        # o modelo ter obedecido naquela execucao.
        "contexto": turno.context,
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

    estado = _init()
    try:
        resultado = retriever.search_for_lead(
            index, perfil, mensagem, embedder=embedder, top_k=3,
        )
        estado["ultima_busca_ok"] = True
        return resultado
    except Exception as erro:
        print("[rag] Busca indisponivel: %s" % _uma_linha(erro))
        estado["ultima_busca_ok"] = False

    # Uma cota de embedding que acaba no MEIO da sessao desligava os imoveis
    # pelo resto da conversa, em silencio: o que falha e o embedding da
    # CONSULTA, e o indice em memoria continua intacto e inutil. Reconstruir
    # com o embedder lexical custa poucos segundos e devolve resultados
    # aproximados, que e infinitamente melhor do que nunca mais mostrar imovel.
    if not PARTE2_OK:
        return None

    try:
        lexical = HashingEmbedder()
        caminho = os.path.join(os.path.dirname(indexer.INDEX_PATH),
                               "imoveis_%s.json" % lexical.name)
        index_lexical, _ = indexer.get_index(
            lexical, indexer.load_properties(), index_path=caminho)
        resultado = retriever.search_for_lead(
            index_lexical, perfil, mensagem, embedder=lexical, top_k=3,
        )
        print("[rag] Busca semantica fora: seguindo com o indice lexical.")
        estado["index"] = index_lexical
        estado["embedder"] = lexical
        estado["ultima_busca_ok"] = True
        return resultado
    except Exception as erro:
        print("[rag] Nem o indice lexical respondeu: %s" % _uma_linha(erro))
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


def perfil_completo(perfil: dict) -> bool:
    """Se nao falta mais nenhum campo da ordem de coleta deste lead.

    E o mesmo criterio que libera o seletor de data no turno normal. Existe
    aqui para a conversa REABERTA chegar na tela no mesmo estado em que
    parou: sem isto, quem recarregava a pagina no meio do agendamento perdia
    o seletor e so o trazia de volta mandando outra mensagem.
    """
    if not PARTE2_OK:
        return False

    return lead_profile.next_to_collect(perfil or {}) is None


def rotulo_da_temperatura(temperatura: str) -> str:
    """"HOT" -> "QUENTE". A tabela e a mesma que o card do corretor usa."""
    if not PARTE2_OK:
        return temperatura

    from summarizer import TEMPERATURE_LABELS
    return TEMPERATURE_LABELS.get(temperatura, temperatura)


def horas_de_silencio(lead_id: str) -> float:
    estado = _init()
    if not PARTE2_OK or not estado["memory"].exists(lead_id):
        return 0.0
    return estado["memory"].hours_of_silence(lead_id)


def registrar_consentimento(lead_id: str, granted: bool = True,
                            origem: str = "chat") -> None:
    # O proposito e so a RETOMADA. Responder quem chegou perguntando sobre
    # imovel e atendimento pedido pelo proprio titular e nao depende de aceite;
    # puxar conversa dias depois depende. Empacotar os dois num consentimento
    # so e o tipo de aceite generico que a LGPD nao aceita, por nao ser para
    # finalidade determinada.
    #
    # `origem` entra no proposito porque o onus de provar o consentimento e de
    # quem coletou: um aceite dado por telefone e registrado pelo corretor nao
    # pode ficar indistinguivel de um aceite dado pelo titular na tela.
    estado = _init()
    if PARTE2_OK:
        proposito = "retomada de contato sobre imoveis"
        if origem != "chat":
            proposito += " (aceite coletado por %s)" % origem

        estado["memory"].record_consent(lead_id, granted, proposito)


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
