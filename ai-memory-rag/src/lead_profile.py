"""
The lead profile, and the boundary with Person 1's agent.

This module is the single place where Portuguese data keys are allowed to
exist, and it exists precisely so they do not leak anywhere else.

`ai-core/src/agent.py` produces a dict with Portuguese keys and Portuguese enum
values:

    {"nome": "João", "intencao": "COMPRA", "preco_faixa": "500k-800k",
     "regiao": "Copacabana", "quartos": "3", "urgencia": "alta",
     "email": "undefined", "telefone": "undefined"}

That shape is Person 1's contract and we do not get to change it. Instead of
letting it spread through the whole module, `from_agent()` translates it once,
at the edge, into the English profile the rest of the code works with:

    {"name": "João", "intent": "BUY", "price_range": "500k-800k",
     "region": "Copacabana", "bedrooms": "3", "urgency": "high"}

Unknown values ("undefined", empty, None) are dropped rather than carried, so
downstream code never has to remember that "undefined" is a magic string.

The reverse direction, `*_LABELS`, exists because the product speaks
Portuguese: the code branches on `intent == "BUY"`, the agent says "compra".
"""

# What the rest of the module works with.
PROFILE_FIELDS = (
    "name", "intent", "price_range", "region", "bedrooms",
    "urgency", "email", "phone",
)

# Fields carrying personal data directly.
PII_FIELDS = ("name", "email", "phone")

# Person 1's key -> ours. Also accepts our own keys, so an already-translated
# profile can be fed back in without a second conversion.
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

# Portuguese labels for everything a lead or a broker reads.
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

# Literally what Person 1 emits for a field it could not extract.
AGENT_UNKNOWN = "undefined"

UNKNOWN_VALUES = (AGENT_UNKNOWN, "", None)


def is_known(value):
    """True when a profile value carries actual information."""
    return value not in UNKNOWN_VALUES and str(value).strip() != ""


def from_agent(collected_data):
    """Translate Person 1's `dados_coletados` into our English profile.

    Tolerates keys that are already translated, so calling it twice is safe.
    Unknown values are dropped instead of being carried as "undefined".

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
            # Accept both Person 1's Portuguese enum and our own.
            value = AGENT_INTENTS.get(upper, upper)
        elif key == "urgency":
            lower = str(value).lower()
            value = AGENT_URGENCIES.get(lower, lower)

        profile[key] = value

    return profile


def to_agent(profile):
    """Translate back into Person 1's shape.

    Only needed if some other part of the project wants to hand our profile to
    code that speaks the agent's dialect. Fields we do not know are filled with
    the agent's own "undefined" marker, because that is what it expects.
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
    """Portuguese rendering of a profile value, for text the lead reads."""
    if field == "intent":
        return INTENT_LABELS.get(value, value)
    if field == "urgency":
        return URGENCY_LABELS.get(value, value)

    return value


def has_contact(profile):
    profile = profile or {}
    return is_known(profile.get("email")) or is_known(profile.get("phone"))
