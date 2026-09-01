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
)

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
    "intent": "Intenção",
    "price_range": "Faixa de preço",
    "region": "Região",
    "bedrooms": "Quartos",
    "urgency": "Urgência",
    "email": "E-mail",
    "phone": "Telefone",
}

# Ordem em que um SDR persegue os campos, e é a ordem do fluxo do PDF do
# desafio: primeiro entender a intenção, depois onde, depois o quê, depois
# quanto, e o contato por último.
#
# `name` e `email` ficam de fora de propósito. Nome vem de graça quando o lead
# se apresenta, e e-mail é alternativa ao telefone; nenhum dos dois é
# perseguido, então nenhum dos dois pode ser contado como esquivado.
COLLECTION_ORDER = ("intent", "region", "bedrooms", "price_range", "urgency", "phone")


def next_to_collect(profile):
    """O campo que o agente está perseguindo agora, ou None se já tem tudo.

    Um SDR pergunta uma coisa por vez. Saber QUAL é a pergunta em aberto é o
    que permite contar esquiva de forma justa: campo que ainda nem foi
    perguntado não pode contar como ignorado.
    """
    profile = profile or {}
    for field in COLLECTION_ORDER:
        if not is_known(profile.get(field)):
            return field

    return None


# Quantas mensagens do lead um campo pode ficar sem resposta antes de ser
# considerado esquivado. Um SDR humano tenta duas vezes e segue em frente; na
# terceira, insistir vira interrogatório.
DODGE_THRESHOLD = 3

# Literalmente o que a Pessoa 1 emite para um campo que não conseguiu extrair.
AGENT_UNKNOWN = "undefined"

UNKNOWN_VALUES = (AGENT_UNKNOWN, "", None)


def is_known(value):
    """Verdadeiro quando um valor de perfil carrega informação de verdade."""
    return value not in UNKNOWN_VALUES and str(value).strip() != ""


def from_agent(collected_data):
    """Traduz o `dados_coletados` da Pessoa 1 para o nosso perfil.

    Tolera chaves já traduzidas, então chamar duas vezes é seguro. Valores
    desconhecidos são descartados em vez de carregados como "undefined".

    >>> from_agent({"intencao": "COMPRA", "regiao": "undefined"})
    {'intent': 'BUY'}
    """
    profile = {}

    for raw_key, raw_value in (collected_data or {}).items():
        key = AGENT_FIELD_MAP.get(raw_key, raw_key)
        if key not in PROFILE_FIELDS:
            continue
        if not is_known(raw_value):
            continue

        value = raw_value

        if key == "intent":
            upper = str(value).upper()
            # Aceita tanto o enum em português da Pessoa 1 quanto o nosso.
            value = AGENT_INTENTS.get(upper, upper)
        elif key == "urgency":
            lower = str(value).lower()
            value = AGENT_URGENCIES.get(lower, lower)

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

    return value


def has_contact(profile):
    profile = profile or {}
    return is_known(profile.get("email")) or is_known(profile.get("phone"))
