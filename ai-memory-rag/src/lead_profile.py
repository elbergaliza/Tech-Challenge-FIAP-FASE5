"""
O perfil do lead, e a fronteira com o agente da Pessoa 1.

Este módulo é o único lugar onde chaves de dado em português podem existir, e
ele existe justamente para que elas não vazem para o resto do código.

O `ai-core/src/agent.py` produz um dicionário com chaves e enums em português:

    {"nome": "João", "intencao": "COMPRA", "preco_faixa": "500k-800k",
     "regiao": "Copacabana", "quartos": "3", "urgencia": "alta",
     "email": "undefined", "telefone": "undefined"}

Esse formato é contrato da Pessoa 1 e não cabe a nós mudar. Em vez de deixá-lo
se espalhar pelo módulo inteiro, `from_agent()` traduz uma vez, na borda, para
o perfil que o resto do código usa:

    {"name": "João", "intent": "BUY", "price_range": "500k-800k",
     "region": "Copacabana", "bedrooms": "3", "urgency": "high"}

Valores desconhecidos ("undefined", vazio, None) são descartados em vez de
carregados, para que o código lá na frente nunca precise lembrar que
"undefined" é uma string mágica.

O caminho inverso, os mapas `*_LABELS`, existe porque o produto fala português:
o código decide com `intent == "BUY"`, o agente escreve "compra".
"""

# O que o resto do módulo usa.
PROFILE_FIELDS = (
    "name", "intent", "price_range", "region", "bedrooms",
    "urgency", "email", "phone",
    # Casa, apartamento, cobertura, studio ou sala comercial.
    #
    # NAO entra na ordem de coleta: o agente nunca pergunta o tipo, ele so
    # aproveita quando o lead diz ("quero uma casa"). Perguntar seria mais uma
    # rodada no funil para um dado que na maioria das conversas vem de graca.
    # O que ele evita e a base oferecer sala comercial a quem vai morar.
    "property_type",
    # Perfil investidor. O desafio pede ticket e expectativa de retorno, e o
    # agente ja pergunta os dois; sem lugar para guardar, a resposta ficava so
    # na conversa e o corretor recebia um investidor sem os numeros que
    # definem o negocio dele.
    "investor_ticket", "expected_return",
)

# Campos que so fazem sentido para quem investe. Ficam fora da coleta de quem
# procura para morar: perguntar "qual seu ticket" a um locatario e ruido.
INVESTOR_FIELDS = ("investor_ticket", "expected_return")

# Campos que carregam dado pessoal direto.
PII_FIELDS = ("name", "email", "phone")

# Chave da Pessoa 1 -> a nossa. Aceita também as nossas próprias chaves, para
# que um perfil já traduzido possa ser reprocessado sem uma segunda conversão.
AGENT_FIELD_MAP = {
    "nome": "name",
    "intencao": "intent",
    "preco_faixa": "price_range",
    "regiao": "region",
    "quartos": "bedrooms",
    "urgencia": "urgency",
    "email": "email",
    "telefone": "phone",
    "tipo_imovel": "property_type",
}

AGENT_INTENTS = {
    "COMPRA": "BUY",
    "ALUGUEL": "RENT",
    "INVESTIMENTO": "INVEST",
}

AGENT_URGENCIES = {
    "alta": "high",
    "media": "medium",
    "média": "medium",
    "baixa": "low",
}

# Rótulos em português para tudo que um lead ou um corretor lê.
INTENT_LABELS = {"BUY": "compra", "RENT": "aluguel", "INVEST": "investimento"}

URGENCY_LABELS = {"high": "alta", "medium": "média", "low": "baixa"}

FIELD_LABELS = {
    "name": "Nome",
    "investor_ticket": "Ticket de investimento",
    "expected_return": "Retorno esperado",
    "intent": "Intenção",
    "price_range": "Faixa de preço",
    "region": "Região",
    "bedrooms": "Quartos",
    "urgency": "Urgência",
    "email": "E-mail",
    "phone": "Telefone",
    "property_type": "Tipo de imóvel",
}

# Ordem em que um SDR persegue os campos, e é a ordem do fluxo do PDF do
# desafio: primeiro entender a intenção, depois onde, depois o quê, depois
# quanto, e o contato por último.
#
# `name` e `email` ficam de fora de propósito. Nome vem de graça quando o lead
# se apresenta, e e-mail é alternativa ao telefone; nenhum dos dois é
# perseguido, então nenhum dos dois pode ser contado como esquivado.
COLLECTION_ORDER = ("intent", "region", "bedrooms", "price_range", "urgency", "phone")

# A ordem de quem investe e outra, e nao e detalhe de apresentacao: quem
# investe decide por ticket e retorno, nao por numero de quartos. Perguntar
# quartos a um investidor, como o agente fazia, e a pergunta errada e queima a
# credibilidade da conversa.
INVESTOR_COLLECTION_ORDER = (
    "intent", "investor_ticket", "expected_return", "region", "urgency", "phone",
)


def collection_order_for(profile):
    """A lista de coleta deste lead, conforme a intencao dele."""
    if (profile or {}).get("intent") == "INVEST":
        return INVESTOR_COLLECTION_ORDER

    return COLLECTION_ORDER


def next_to_collect(profile):
    """O campo que o agente está perseguindo agora, ou None se já tem tudo.

    Um SDR pergunta uma coisa por vez. Saber QUAL é a pergunta em aberto é o
    que permite contar esquiva de forma justa: campo que ainda nem foi
    perguntado não pode contar como ignorado.
    """
    profile = profile or {}
    for field in collection_order_for(profile):
        if not is_known(profile.get(field)):
            return field

    return None


# Quantas mensagens do lead um campo pode ficar sem resposta antes de ser
# considerado esquivado. Um SDR humano tenta duas vezes e segue em frente; na
# terceira, insistir vira interrogatório.
DODGE_THRESHOLD = 3

# Pistas de urgência BAIXA de verdade.
#
# Existem porque a `extrair_urgencia` da Pessoa 1 não tem ramo "undefined": ela
# devolve "alta" se achar palavra de pressa, "media" se achar palavra de prazo,
# e "baixa" em TODO o resto. Um "oi, meu nome é Marcos" vira urgência baixa.
#
# É a única das nove funções de extração dela sem esse ramo; as outras oito
# devolvem "undefined" quando não sabem. Como a lista de urgência baixa dela é
# vazia, "baixa" nunca é evidência: é sempre o default.
#
# O estrago é do nosso lado, não do dela: o perfil é monotônico, então a
# urgência trava no primeiro turno, o `next_to_collect` para de perseguir o
# prazo, e o score do lead nasce deflacionado.
#
# A defesa fica aqui, na camada anticorrupção, e não no código dela, que é de
# outra pessoa. Estas pistas são nossas: com elas a urgência baixa passa a ser
# reconhecida quando o lead realmente a expressa, o que hoje não acontece.
LOW_URGENCY_CUES = (
    "sem pressa", "nao tenho pressa", "não tenho pressa", "sem urgencia",
    "sem urgência", "sem correria", "com calma", "tranquilo", "no meu tempo",
    "ano que vem", "so olhando", "só olhando", "so pesquisando",
    "só pesquisando", "apenas pesquisando", "dando uma olhada",
    "sem compromisso", "a longo prazo", "nao tem pressa", "não tem pressa",
)


def mentions_low_urgency(text):
    """O lead disse alguma coisa que sustente urgência baixa?"""
    if not text:
        return False

    lowered = str(text).lower()
    return any(cue in lowered for cue in LOW_URGENCY_CUES)


# O lado espelhado do problema acima. A lista de pressa da Pessoa 1 tem oito
# expressões ("urgente", "rápido", "logo", "já", "essa semana"...), e um lead
# que diz "preciso mudar esse mês" ou "o quanto antes" não casa com nenhuma:
# cai no default "baixa", que o guarda abaixo descarta, e a urgência fica
# eternamente desconhecida. Na prática o agente perguntava o prazo, o lead
# respondia, e o agente perguntava de novo.
#
# Mesma decisão do LOW: a defesa mora aqui, na camada anticorrupção, e não no
# código da outra pessoa.
HIGH_URGENCY_CUES = (
    "esse mes", "esse mês", "este mes", "este mês", "ainda esse mes",
    "ainda esse mês", "mes que vem", "mês que vem", "proxima semana",
    "próxima semana", "proximas semanas", "próximas semanas",
    "o quanto antes", "quanto antes", "para ontem", "pra ontem",
    "com pressa", "estou com pressa", "imediato", "imediatamente",
    "assim que possivel", "assim que possível", "preciso mudar",
    "tenho que sair", "meu contrato acaba", "meu contrato vence",
    "estou de mudanca", "estou de mudança",
)


# Só uma menção a MORAR desfaz uma intenção de investimento já registrada.
MORAR_CUES = (
    "morar", "moradia", "residir", "pra mim", "para mim", "pra minha",
    "para minha", "minha familia", "minha família", "meu filho", "minha filha",
    "sair do aluguel", "primeiro imovel", "primeiro imóvel",
)


def mentions_living_intent(text):
    """O lead disse que o imóvel é para morar?"""
    if not text:
        return False

    lowered = str(text).lower()
    return any(cue in lowered for cue in MORAR_CUES)


def substituicao_valida(field, previous, value, message=None):
    """Vale trocar um valor JÁ CONHECIDO por este novo?

    O perfil aceita correção de propósito: o lead que sobe o orçamento de 500k
    para 800k é sinal de compra, e o corretor quer ver isso. Mas uma correção
    às cegas tem um caso patológico.

    Depois que o lead diz "quero investir em imóveis para alugar", a conversa
    inteira fala em alugar. O agente pergunta se ele prefere "pronto para
    alugar", ele responde "pronto para alugar", e a extração, que é sem estado,
    lê ALUGUEL e rebaixa o investidor a locatário. O ticket vira orçamento, o
    fluxo vai para visita em vez de consultoria com especialista, e o que o
    lead disse no primeiro turno é perdido.

    Para quem investe, "alugar" e "comprar" descrevem a OPERAÇÃO, não a
    intenção. Só uma menção explícita a morar desfaz o investimento.
    """
    if field == "intent" and previous == "INVEST" and value in ("RENT", "BUY"):
        return mentions_living_intent(message)

    return True


# A extração da Pessoa 1 aceita QUALQUER número antes de "quartos": "R$ 8.550
# quartos" vira 550 quartos. E o perfil é monotônico, então o valor errado
# entra e não sai mais, some com o campo verdadeiro e infla o score.
#
# O catálogo do projeto vai de 0 (kitnet) a 4. O teto aqui é folgado de
# propósito: barra o absurdo sem discutir com quem procura casa grande.
MAX_QUARTOS_PLAUSIVEL = 10


def bedrooms_plausiveis(valor):
    """O número de quartos cabe num imóvel de verdade?"""
    try:
        quantidade = int(str(valor).strip())
    except (TypeError, ValueError):
        return False

    return 0 <= quantidade <= MAX_QUARTOS_PLAUSIVEL


def mentions_high_urgency(text):
    """O lead disse alguma coisa que sustente urgência alta?"""
    if not text:
        return False

    lowered = str(text).lower()
    return any(cue in lowered for cue in HIGH_URGENCY_CUES)


# Literalmente o que a Pessoa 1 emite para um campo que não conseguiu extrair.
AGENT_UNKNOWN = "undefined"

UNKNOWN_VALUES = (AGENT_UNKNOWN, "", None)


def is_known(value):
    """Verdadeiro quando um valor de perfil carrega informação de verdade."""
    return value not in UNKNOWN_VALUES and str(value).strip() != ""


def from_agent(collected_data, message=None):
    """Traduz o `dados_coletados` da Pessoa 1 para o nosso perfil.

    Tolera chaves já traduzidas, então chamar duas vezes é seguro. Valores
    desconhecidos são descartados em vez de carregados como "undefined".

    Com `message`, a urgência baixa só é aceita se a mensagem realmente a
    sustentar; ver `LOW_URGENCY_CUES`. Sem `message` nada é descartado, porque
    aí não há como julgar e um chamador programático pode estar remontando um
    perfil já validado.

    >>> from_agent({"intencao": "COMPRA", "regiao": "undefined"})
    {'intent': 'BUY'}
    >>> from_agent({"urgencia": "baixa"}, message="oi, tudo bem?")
    {}
    >>> from_agent({"urgencia": "baixa"}, message="sem pressa, é para 2027")
    {'urgency': 'low'}
    """
    profile = {}

    for raw_key, raw_value in (collected_data or {}).items():
        key = AGENT_FIELD_MAP.get(raw_key, raw_key)
        if key not in PROFILE_FIELDS:
            continue
        if not is_known(raw_value):
            continue

        value = raw_value

        if key == "bedrooms":
            # Ver `bedrooms_plausiveis`: sem isto, um valor de dinheiro que caiu
            # no campo errado trava o perfil para sempre.
            if not bedrooms_plausiveis(value):
                continue
        elif key == "intent":
            upper = str(value).upper()
            # Aceita tanto o enum em português da Pessoa 1 quanto o nosso.
            value = AGENT_INTENTS.get(upper, upper)
        elif key == "urgency":
            lower = str(value).lower()
            value = AGENT_URGENCIES.get(lower, lower)
            # "baixa" sem nada na mensagem que a sustente é o default da
            # Pessoa 1, não informação. Deixar passar trava o campo para
            # sempre, porque o perfil é monotônico.
            #
            # Antes de descartar, vale olhar o contrário: a lista de pressa
            # dela é curta, e "preciso mudar esse mês" chega aqui como "baixa"
            # quando a mensagem diz exatamente o oposto.
            if value == "low" and message is not None:
                if mentions_high_urgency(message):
                    value = "high"
                elif not mentions_low_urgency(message):
                    continue

        profile[key] = value

    return profile


def to_agent(profile):
    """Traduz de volta para o formato da Pessoa 1.

    Só é necessário se alguma outra parte do projeto quiser entregar o nosso
    perfil a um código que fala o dialeto do agente. Campos que não conhecemos
    são preenchidos com o marcador "undefined" dela, porque é o que ela espera.
    """
    reverse_fields = {ours: theirs for theirs, ours in AGENT_FIELD_MAP.items()}
    reverse_intents = {ours: theirs for theirs, ours in AGENT_INTENTS.items()}
    reverse_urgencies = {"high": "alta", "medium": "media", "low": "baixa"}

    result = {}
    for field in PROFILE_FIELDS:
        # Campo que nao existe no dialeto dela nao tem para onde ser
        # traduzido. E o caso do perfil investidor, que e nosso: mandar uma
        # chave que ela nao conhece nao ajuda ninguem e suja o dicionario.
        if field not in reverse_fields:
            continue

        value = (profile or {}).get(field)

        if not is_known(value):
            result[reverse_fields[field]] = AGENT_UNKNOWN
            continue

        if field == "intent":
            value = reverse_intents.get(value, value)
        elif field == "urgency":
            value = reverse_urgencies.get(value, value)

        result[reverse_fields[field]] = value

    return result


def label(field, value):
    """Renderização em português de um valor de perfil, para o lead ler."""
    if field == "intent":
        return INTENT_LABELS.get(value, value)
    if field == "urgency":
        return URGENCY_LABELS.get(value, value)
    if field == "expected_return":
        # Retorno vem como valor mensal em reais, e o sufixo evita a leitura
        # errada de "2000" como percentual.
        return "%s por mês" % value

    return value


def has_contact(profile):
    profile = profile or {}
    return is_known(profile.get("email")) or is_known(profile.get("phone"))
