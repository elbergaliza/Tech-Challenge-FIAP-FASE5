"""
Intelligent summary for the broker, and incremental summary for memory.

Two different products, with different readers:

  BROKER SUMMARY       goes on the dashboard. In ten seconds of reading it must
                       answer: who is this lead, what do they want, how hot are
                       they, and what do I do now.

  INCREMENTAL SUMMARY  goes into the agent prompt. It compresses the old
                       conversation into a few lines so the context does not
                       grow without bound, preserving whatever changes a
                       decision.

Both degrade gracefully: with no `GEMINI_API_KEY`, no network or an API outage,
a deterministic heuristic path produces a poorer but correct and immediate
summary. The `source` field says where it came from ("llm" or "heuristic"), so
the interface never presents rule-written text as if it were AI-written.

The temperature score is DELIBERATELY rule based rather than a model. With
dozens of synthetic leads, an Isolation Forest would give the appearance of
rigour without the rigour: the Anomaly Detection lessons themselves insist the
central problem is threshold definition and validation against realistic data.
A seven-factor rule is auditable, the broker understands why a lead is hot, and
it is defensible in front of the panel. Swapping in a model later means
replacing `compute_score`.

NOTE ON LANGUAGE: identifiers, field names and enum values are in English.
Prompts and every string a broker or lead reads stay in Portuguese; the
*_LABELS maps carry that rendering.
"""

import lead_profile
from lead_profile import has_contact, is_known
from llm import extract_json, get_client
from privacy import Pseudonymizer

# ---------------------------------------------------------------------------
# Lead temperature score
# ---------------------------------------------------------------------------
# These sum to 100. Contact weighs as much as intent because a perfectly
# qualified lead with no phone or e-mail is a lead the broker cannot reach: for
# an SDR, contact is the final product.
SCORE_WEIGHTS = {
    "intent": 20,
    "contact": 20,
    "price_range": 15,
    "high_urgency": 15,
    "region": 10,
    "bedrooms": 10,
    "engagement": 10,
}

# A lead that scored 80 and vanished a month ago is not hot. The silence
# penalty keeps the dashboard from sending the broker after a dead lead.
SILENCE_PENALTIES = (
    (48, 0),
    (24 * 7, 10),
    (24 * 30, 25),
)
MAX_SILENCE_PENALTY = 40

HOT_THRESHOLD = 70
WARM_THRESHOLD = 40

# Portuguese labels for the temperature shown on the dashboard.
TEMPERATURE_LABELS = {"HOT": "QUENTE", "WARM": "MORNO", "COLD": "FRIO"}

TEMPERATURE_ICONS = {"HOT": "🔥", "WARM": "🌤️", "COLD": "❄️"}

MESSAGES_FOR_ENGAGED = 3


def _silence_penalty(hours):
    for limit, penalty in SILENCE_PENALTIES:
        if hours <= limit:
            return penalty

    return MAX_SILENCE_PENALTY


def compute_score(profile, lead_messages=0, hours_of_silence=0.0):
    """Score from 0 to 100, plus the factors that make it up.

    Returns the factors and not just the number, because the broker needs to
    know WHY a lead is hot in order to decide what to say on the call.

    Factor strings are Portuguese: they surface on the dashboard.
    """
    profile = profile or {}
    factors = []
    score = 0

    for field in ("intent", "price_range", "region", "bedrooms"):
        if is_known(profile.get(field)):
            score += SCORE_WEIGHTS[field]
            factors.append("+%d %s informado"
                           % (SCORE_WEIGHTS[field],
                              lead_profile.FIELD_LABELS[field].lower()))

    if has_contact(profile):
        score += SCORE_WEIGHTS["contact"]
        factors.append("+%d contato disponível" % SCORE_WEIGHTS["contact"])

    if profile.get("urgency") == "high":
        score += SCORE_WEIGHTS["high_urgency"]
        factors.append("+%d urgência alta" % SCORE_WEIGHTS["high_urgency"])

    if lead_messages >= MESSAGES_FOR_ENGAGED:
        score += SCORE_WEIGHTS["engagement"]
        factors.append("+%d engajado (%d mensagens)"
                       % (SCORE_WEIGHTS["engagement"], lead_messages))

    penalty = _silence_penalty(hours_of_silence)
    if penalty:
        score -= penalty
        factors.append("-%d sem resposta há %d dia(s)"
                       % (penalty, int(hours_of_silence // 24)))

    return max(0, min(100, score)), factors


def classify_temperature(score):
    if score >= HOT_THRESHOLD:
        return "HOT"
    if score >= WARM_THRESHOLD:
        return "WARM"
    return "COLD"


# ---------------------------------------------------------------------------
# Actionable alerts
# ---------------------------------------------------------------------------

def build_alerts(profile, state):
    """What is blocking this lead. Becomes a badge on the dashboard."""
    alerts = []

    if not has_contact(profile):
        alerts.append("Sem contato: não dá para retomar fora do chat")

    if not is_known(profile.get("intent")):
        alerts.append("Intenção não identificada (compra, aluguel ou investimento)")

    if not is_known(profile.get("price_range")):
        alerts.append("Orçamento não informado")

    if state.get("followups_sent", 0) >= 3:
        alerts.append("%d follow-ups sem resposta: considerar encerrar"
                      % state["followups_sent"])

    if not state.get("consent"):
        alerts.append("Consentimento LGPD não registrado")

    return alerts


def suggest_next_action(profile, temperature, state):
    """Rule-based next action. The field the broker uses most."""
    if not is_known(profile.get("intent")):
        return "Descobrir se é compra, aluguel ou investimento"

    if not has_contact(profile):
        return "Pedir telefone ou e-mail antes de avançar"

    if temperature == "HOT":
        if profile.get("intent") == "INVEST":
            return "Encaminhar para o especialista em investimentos hoje"
        return "Ligar hoje e agendar visita"

    if temperature == "WARM":
        missing = [lead_profile.FIELD_LABELS[f].lower()
                   for f in ("price_range", "region", "bedrooms")
                   if not is_known(profile.get(f))]
        if missing:
            return "Retomar para descobrir: " + ", ".join(missing)
        return "Enviar opções e propor visita"

    if state.get("followups_sent", 0) >= 3:
        return "Lead frio após vários contatos: mover para nutrição de longo prazo"

    return "Reengajar com follow-up automático"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
# Prompt decisions, since the prompt discipline has no written material:
#   * role and task stated explicitly on the first line;
#   * output format declared as JSON and repeated at the end, which is where
#     the model pays most attention;
#   * explicit ban on inventing, because a summary is where hallucination does
#     the most damage: the broker calls believing a fact the lead never said;
#   * instruction to leave a field empty when unsure, rather than guessing;
#   * the [NOME_1] and [EMAIL_1] aliases are explained, otherwise the model
#     tries to "fix" them into invented names.
#
# Prompts are written in Portuguese so prompt and output share the language of
# the product, and so the team can review the text the agent will say.

PROMPT_BROKER_SUMMARY = """Você é um analista de vendas de uma imobiliária. \
Leia a conversa entre um agente SDR e um lead e produza um resumo para o \
corretor que vai assumir o atendimento.

REGRAS:
- Use SOMENTE o que está na conversa. Não invente fatos, preferências ou nomes.
- Se algo não foi dito, deixe o campo vazio ou a lista vazia. Não chute.
- Trechos como [NOME_1], [EMAIL_1] ou [TELEFONE_1] são dados pessoais \
protegidos. Mantenha-os exatamente como estão, sem substituir por nomes reais.
- Escreva em português do Brasil, direto, sem floreio comercial.

PERFIL JÁ EXTRAÍDO:
{profile}

CONVERSA:
{conversation}

Responda APENAS com um JSON neste formato, sem texto antes ou depois. As CHAVES \
ficam em inglês, porque são contrato de máquina; os VALORES vêm em português, \
porque são lidos por pessoas:
{{
  "summary": "2 a 4 frases sobre quem é o lead e o que ele procura",
  "main_interest": "uma frase",
  "buying_signals": ["trechos que indicam intenção real de fechar"],
  "objections": ["dúvidas, hesitações ou obstáculos que o lead levantou"],
  "next_action": "o que o corretor deve fazer agora, em uma frase"
}}"""

PROMPT_INCREMENTAL_SUMMARY = """Comprima o trecho de conversa abaixo em no \
máximo 5 linhas, para servir de memória de um agente SDR imobiliário.

REGRAS:
- Preserve o que muda decisão: preferências, restrições, objeções, compromissos \
assumidos e mudanças de ideia do lead.
- Descarte saudações, confirmações e cortesias.
- Não invente nada.
- Mantenha apelidos como [NOME_1] intactos.
- Escreva em português do Brasil, em terceira pessoa.

{previous_summary}TRECHO DA CONVERSA:
{conversation}

Responda apenas com o texto comprimido."""


# ---------------------------------------------------------------------------
# Broker summary
# ---------------------------------------------------------------------------

class BrokerSummary:

    def __init__(self, lead_id, profile, score, temperature, factors, alerts,
                 next_action, summary, main_interest="", buying_signals=None,
                 objections=None, shown_properties=None, source="heuristic",
                 generated_at=None, message_count=0, hours_of_silence=0.0):
        self.lead_id = lead_id
        self.profile = profile
        self.score = score
        self.temperature = temperature
        self.factors = factors
        self.alerts = alerts
        self.next_action = next_action
        self.summary = summary
        self.main_interest = main_interest
        self.buying_signals = buying_signals or []
        self.objections = objections or []
        self.shown_properties = shown_properties or []
        self.source = source
        self.generated_at = generated_at
        self.message_count = message_count
        self.hours_of_silence = hours_of_silence

    def to_dict(self):
        return {
            "lead_id": self.lead_id,
            "profile": self.profile,
            "score": self.score,
            "temperature": self.temperature,
            # Portuguese rendering shipped alongside the enum, so the dashboard
            # does not have to carry its own translation table.
            "temperature_label": TEMPERATURE_LABELS[self.temperature],
            "factors": self.factors,
            "alerts": self.alerts,
            "next_action": self.next_action,
            "summary": self.summary,
            "main_interest": self.main_interest,
            "buying_signals": self.buying_signals,
            "objections": self.objections,
            "shown_properties": self.shown_properties,
            "source": self.source,
            "generated_at": self.generated_at,
            "message_count": self.message_count,
            "hours_of_silence": round(self.hours_of_silence, 1),
        }

    def to_markdown(self):
        """Block ready for the dashboard card (Part 4)."""
        lines = [
            "### %s %s  ·  score %d/100" % (
                TEMPERATURE_ICONS[self.temperature],
                TEMPERATURE_LABELS[self.temperature],
                self.score,
            ),
            "",
            self.summary,
            "",
            "**Próxima ação:** %s" % self.next_action,
        ]

        if self.buying_signals:
            lines += ["", "**Sinais de compra:**"]
            lines += ["- %s" % s for s in self.buying_signals]

        if self.objections:
            lines += ["", "**Objeções:**"]
            lines += ["- %s" % o for o in self.objections]

        if self.alerts:
            lines += ["", "**Atenção:**"]
            lines += ["- %s" % a for a in self.alerts]

        if self.shown_properties:
            lines += ["", "**Imóveis já apresentados:** "
                      + ", ".join(self.shown_properties)]

        lines += ["", "_Resumo gerado por %s._"
                  % ("IA" if self.source == "llm" else "regras (IA indisponível)")]

        return "\n".join(lines)


class Summarizer:

    def __init__(self, client=None, pseudonymizer=None):
        self.client = client if client is not None else get_client()
        self.pseudo = pseudonymizer or Pseudonymizer()

    # -- broker summary -----------------------------------------------------

    def summarize_for_broker(self, memory, lead_id, use_llm=True):
        state = memory.state(lead_id)
        profile = memory.profile(lead_id)

        lead_messages = sum(1 for m in state["messages"] if m["role"] == "user")
        hours = memory.hours_of_silence(lead_id)

        score, factors = compute_score(profile, lead_messages, hours)
        temperature = classify_temperature(score)

        base = dict(
            lead_id=lead_id,
            profile=profile,
            score=score,
            temperature=temperature,
            factors=factors,
            alerts=build_alerts(profile, state),
            next_action=suggest_next_action(profile, temperature, state),
            shown_properties=list(state["shown_properties"]),
            generated_at=state["updated_at"],
            message_count=len(state["messages"]),
            hours_of_silence=hours,
        )

        content = None
        if use_llm and self.client.available and state["messages"]:
            content = self._summary_via_llm(memory, lead_id, profile)

        if content:
            # The LLM's suggested next action is used only if it returned one;
            # the rule is the floor, because this field can never be empty.
            base["next_action"] = content.get("next_action") or base["next_action"]
            return BrokerSummary(
                summary=content.get("summary") or _heuristic_summary(profile, state),
                main_interest=content.get("main_interest", ""),
                buying_signals=content.get("buying_signals") or [],
                objections=content.get("objections") or [],
                source="llm",
                **base
            )

        return BrokerSummary(
            summary=_heuristic_summary(profile, state),
            main_interest=_heuristic_interest(profile),
            source="heuristic",
            **base
        )

    def _summary_via_llm(self, memory, lead_id, profile):
        """Call the LLM with the pseudonymised conversation; return dict or None.

        Silent failure is a product decision: if Gemini goes down, the dashboard
        shows the rule-built summary instead of an error screen.
        """
        conversation = _format_conversation(
            memory.history(lead_id, window=None, mask=True)
        )
        masked_profile, _ = self.pseudo.mask(
            _format_profile(profile), memory.alias_map(lead_id)
        )

        prompt = PROMPT_BROKER_SUMMARY.format(
            profile=masked_profile, conversation=conversation
        )

        try:
            raw = self.client.generate(prompt, temperature=0.2)
        except Exception:
            return None

        content = extract_json(raw)
        if not isinstance(content, dict):
            return None

        # The broker is an authorised recipient: aliases become real data again
        # before reaching the dashboard.
        return _restore_deep(content, self.pseudo, memory.alias_map(lead_id))

    # -- incremental summary for memory -------------------------------------

    def compress_memory(self, memory, lead_id, force=False):
        """Summarise the old middle of the conversation and store it.

        Returns the summary text, or None when there was nothing to compress.
        The live window of recent messages is never touched.
        """
        if not force and not memory.needs_summary(lead_id):
            return None

        chunk = memory.messages_to_summarize(lead_id)
        if not chunk:
            return None

        state = memory.state(lead_id)
        up_to_index = state["summarized_up_to"] + len(chunk)
        previous = state.get("summary")

        text = None
        if self.client.available:
            text = self._compress_via_llm(memory, lead_id, chunk, previous)

        if not text:
            text = _heuristic_compression(chunk, previous, memory.profile(lead_id))

        memory.set_summary(lead_id, text, up_to_index)
        return text

    def _compress_via_llm(self, memory, lead_id, chunk, previous):
        mapping = memory.alias_map(lead_id)
        profile = memory.profile(lead_id)
        names = [profile["name"]] if is_known(profile.get("name")) else []

        lines = []
        for message in chunk:
            text, mapping = self.pseudo.mask(message["content"], mapping, names)
            lines.append("%s: %s" % (
                "Lead" if message["role"] == "user" else "Agente", text
            ))

        header = ""
        if previous:
            masked, mapping = self.pseudo.mask(previous, mapping, names)
            header = "RESUMO ANTERIOR (incorpore ao novo):\n%s\n\n" % masked

        prompt = PROMPT_INCREMENTAL_SUMMARY.format(
            previous_summary=header, conversation="\n".join(lines)
        )

        try:
            raw = self.client.generate(prompt, temperature=0.2)
        except Exception:
            return None

        return self.pseudo.restore(raw.strip(), mapping) if raw else None


# ---------------------------------------------------------------------------
# Heuristic path
# ---------------------------------------------------------------------------

_INTENT_VERB = {
    "BUY": "quer comprar", "RENT": "quer alugar", "INVEST": "quer investir em",
}


def _heuristic_interest(profile):
    verb = _INTENT_VERB.get(profile.get("intent"), "está procurando")

    parts = ["Lead %s imóvel" % verb]
    if is_known(profile.get("bedrooms")):
        parts.append("de %s quartos" % profile["bedrooms"])
    if is_known(profile.get("region")):
        parts.append("em %s" % profile["region"])
    if is_known(profile.get("price_range")):
        parts.append("na faixa de %s" % profile["price_range"])

    return " ".join(parts) + "."


def _heuristic_summary(profile, state):
    """Rule-built summary, no LLM. Poorer, but always correct."""
    sentences = []

    interest = _heuristic_interest(profile)
    name = profile.get("name")
    if is_known(name):
        # "Lead quer comprar..." becomes "João quer comprar...".
        interest = "%s %s" % (name, interest.replace("Lead ", "", 1))
    sentences.append(interest)

    missing = [lead_profile.FIELD_LABELS[f].lower()
               for f in ("intent", "price_range", "region", "bedrooms")
               if not is_known(profile.get(f))]
    if missing:
        sentences.append("Ainda não informou: %s." % ", ".join(missing))

    if profile.get("urgency") == "high":
        sentences.append("Declarou urgência alta.")

    exchanges = sum(1 for m in state["messages"] if m["role"] == "user")
    sentences.append("Trocou %d mensagem(ns) com o agente." % exchanges)

    if state.get("followups_sent"):
        sentences.append("Recebeu %d follow-up(s) sem responder."
                         % state["followups_sent"])

    return " ".join(sentences)


def _heuristic_compression(chunk, previous, profile):
    """Compression without an LLM: keep the profile, count what was dropped.

    It does not try to imitate a written summary. It says what it knows and
    admits what it does not have, which beats producing invented text with no
    model behind it.
    """
    from_lead = [m for m in chunk if m["role"] == "user"]

    lines = []
    if previous:
        lines.append(previous)

    lines.append(_heuristic_interest(profile))
    lines.append(
        "Trecho de %d mensagens do lead resumido automaticamente sem IA; "
        "detalhes da conversa antiga não foram preservados." % len(from_lead)
    )

    if from_lead:
        first = from_lead[0]["content"]
        lines.append("Primeira mensagem do trecho: \"%s\"."
                     % (first[:120] + ("..." if len(first) > 120 else "")))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_conversation(history):
    return "\n".join(
        "%s: %s" % ("Lead" if m["role"] == "user" else "Agente", m["content"])
        for m in history
    )


def _format_profile(profile):
    known = [(f, v) for f, v in (profile or {}).items() if is_known(v)]
    if not known:
        return "(nada extraído ainda)"

    return "\n".join(
        "- %s: %s" % (lead_profile.FIELD_LABELS.get(f, f), lead_profile.label(f, v))
        for f, v in known
    )


def _restore_deep(value, pseudo, mapping):
    if isinstance(value, str):
        return pseudo.restore(value, mapping)
    if isinstance(value, list):
        return [_restore_deep(v, pseudo, mapping) for v in value]
    if isinstance(value, dict):
        return {k: _restore_deep(v, pseudo, mapping) for k, v in value.items()}

    return value


def summarize_pipeline(memory, summarizer=None, use_llm=False):
    """Summarise every lead, ordered by temperature.

    This is the query that feeds the dashboard list. `use_llm=False` by default
    because sweeping the whole pipeline with one Gemini call per lead is slow
    and expensive; the rich summary is generated when a specific lead is opened.
    """
    summarizer = summarizer or Summarizer(client=get_client("unavailable"))

    summaries = [
        summarizer.summarize_for_broker(memory, lead_id, use_llm=use_llm)
        for lead_id in memory.leads()
    ]
    summaries.sort(key=lambda s: s.score, reverse=True)

    return summaries
