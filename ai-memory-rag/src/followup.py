"""
Automatic follow-up: when to reach back out, and what to say.

Responsibility split agreed with Person 3: she schedules and dispatches, this
module decides IF a message should go out and writes WHAT it says.
`leads_due_for_followup()` is the query `backend/src/jobs/followup_scheduler.py`
runs.

Scenario 3 of the challenge brief: the lead started a conversation and vanished.
The agent must reach back out on its own, keeping context and re-engaging. The
two hard parts are silence and excess.

WHEN (escalating cadence). One attempt after 24h, another after 3 days, a last
one after 7 days, then stop. A lead who declared urgency gets a shorter cadence,
because someone who needs to move in two weeks will not wait three days for a
reply.

WHEN NOT. This is the half that usually gets forgotten, and it is what separates
follow-up from spam:
  * no consent recorded, no message (LGPD art. 8);
  * if the lead asked to stop, never again;
  * if the lead replied after the last follow-up, the counter resets;
  * once the attempt ceiling is reached, close out and hand the lead back to the
    broker.

WHAT TO SAY. Three tones, one per attempt: light re-opener, offer of something
new, polite sign-off with the door left open. Each uses memory to cite what the
lead actually said, rather than generic filler. With an LLM the text is written
on the spot; without one, a template filled from the profile. Either way,
`source` records the origin.

NOTE ON LANGUAGE: identifiers, field names and enum values are in English.
Prompts, templates and every message the lead reads stay in Portuguese.
"""

import lead_profile
from lead_profile import is_known
from llm import get_client
from privacy import Pseudonymizer

# ---------------------------------------------------------------------------
# Cadence
# ---------------------------------------------------------------------------
# Hours of silence required before attempts 1, 2 and 3. The tuple length is the
# attempt ceiling.
DEFAULT_CADENCE = (24, 72, 168)
URGENT_CADENCE = (4, 24, 72)

TONES = ("reopen", "offer", "signoff")

# Opt-out phrases. Any of them blocks follow-up forever: pushing someone who
# asked you to stop destroys the brand and, under the LGPD, is processing with
# no legal basis once consent is withdrawn.
OPT_OUT_PHRASES = (
    "não quero", "nao quero", "pare de", "para de me", "não me manda",
    "nao me manda", "não tenho interesse", "nao tenho interesse",
    "me tira", "descadastr", "sair da lista", "não insista", "nao insista",
    "desisti", "já comprei", "ja comprei", "já aluguei", "ja aluguei",
    "resolvi por outro", "não precisa mais", "nao precisa mais",
)

# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

def detect_opt_out(memory, lead_id):
    """True if the lead ever asked not to be contacted again.

    Scans only the lead's own messages. An agent line such as "me avisa se não
    quiser mais receber" must not trip the block.
    """
    for message in memory.state(lead_id)["messages"]:
        if message["role"] != "user":
            continue

        text = message["content"].lower()
        if any(phrase in text for phrase in OPT_OUT_PHRASES):
            return True

    return False


def cadence_for(profile):
    if (profile or {}).get("urgency") == "high":
        return URGENT_CADENCE

    return DEFAULT_CADENCE


class FollowUpDecision:

    def __init__(self, send, reason, attempt=0, tone=None, hours_of_silence=0.0,
                 hours_remaining=0.0):
        self.send = send
        self.reason = reason
        self.attempt = attempt
        self.tone = tone
        self.hours_of_silence = hours_of_silence
        self.hours_remaining = hours_remaining

    def to_dict(self):
        return {
            "send": self.send,
            "reason": self.reason,
            "attempt": self.attempt,
            "tone": self.tone,
            "hours_of_silence": round(self.hours_of_silence, 1),
            "hours_remaining": round(self.hours_remaining, 1),
        }

    def __repr__(self):
        return "<FollowUpDecision send=%s %s>" % (self.send, self.reason)


def evaluate_followup(memory, lead_id, require_consent=True):
    """Decide whether a follow-up is due now. Never raises.

    Always returns a FollowUpDecision with a plain-text reason, so Person 3's
    job can log why it skipped a lead without reimplementing the rule.

    Reason strings are Portuguese: they end up in logs the team reads.
    """
    state = memory.state(lead_id)
    profile = memory.profile(lead_id)
    silence = memory.hours_of_silence(lead_id)
    sent = state.get("followups_sent", 0)

    if not state["messages"]:
        return FollowUpDecision(False, "lead sem conversa iniciada")

    if detect_opt_out(memory, lead_id):
        return FollowUpDecision(False, "lead pediu para não ser mais contatado")

    if require_consent and not memory.has_consent(lead_id):
        return FollowUpDecision(False, "sem consentimento registrado (LGPD)")

    cadence = cadence_for(profile)

    if sent >= len(cadence):
        return FollowUpDecision(
            False, "teto de %d tentativas atingido" % len(cadence), attempt=sent
        )

    # `followups_sent` counts only consecutive attempts with no reply:
    # memory resets it when the lead speaks again. So this index is always the
    # next attempt of the current silence cycle.
    wait = cadence[sent]

    if silence < wait:
        return FollowUpDecision(
            False,
            "aguardando: %.0fh de silêncio, faltam %.0fh" % (silence, wait - silence),
            attempt=sent + 1,
            hours_of_silence=silence,
            hours_remaining=wait - silence,
        )

    return FollowUpDecision(
        True,
        "%.0fh de silêncio, tentativa %d de %d" % (silence, sent + 1, len(cadence)),
        attempt=sent + 1,
        tone=TONES[min(sent, len(TONES) - 1)],
        hours_of_silence=silence,
    )


def leads_due_for_followup(memory, require_consent=True):
    """Leads that should receive a follow-up right now.

    Entry point for Person 3's scheduled job. Returns filtered
    `(lead_id, decision)` pairs, ordered from most to least silent.
    """
    due = []

    for lead_id in memory.leads():
        decision = evaluate_followup(memory, lead_id, require_consent)
        if decision.send:
            due.append((lead_id, decision))

    due.sort(key=lambda pair: pair[1].hours_of_silence, reverse=True)
    return due


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
# Prompt decisions for follow-up, the highest-risk text in the system: it
# arrives unrequested, and a bad one gets the agency blocked.
#   * explicit and aggressive length limit, because the channel is WhatsApp;
#   * no generic greeting: the value is in citing what the lead actually said;
#   * at most ONE question, so the lead has an easy answer to give;
#   * ban on inventing properties or commercial terms;
#   * the tone enters as a full instruction per attempt rather than a loose
#     adjective, so the model does not write the same message three times.

TONE_INSTRUCTIONS = {
    "reopen": (
        "Primeira retomada, após pouco tempo de silêncio. Seja leve e natural. "
        "Retome exatamente o assunto onde a conversa parou e faça UMA pergunta "
        "fácil de responder. Não cobre resposta nem mencione que ele não "
        "respondeu."
    ),
    "offer": (
        "Segunda tentativa. O lead não respondeu à primeira. Reconheça o "
        "silêncio de leve, sem cobrança, e traga algo de valor concreto: uma "
        "opção nova que encaixa no perfil dele. Ofereça uma saída fácil, do "
        "tipo 'se preferir, te aviso quando surgir algo melhor'."
    ),
    "signoff": (
        "Última tentativa. Encerre o ciclo com elegância. Deixe claro que você "
        "não vai insistir mais, agradeça o contato e deixe a porta aberta para "
        "quando fizer sentido. Não faça pergunta que pressione. Não soe magoado."
    ),
}

PROMPT_FOLLOWUP = """Você é um SDR de uma imobiliária retomando contato com um \
lead que parou de responder. Escreva UMA mensagem de WhatsApp.

{tone_instruction}

REGRAS:
- No máximo 3 linhas curtas. É WhatsApp, não e-mail.
- No máximo UMA pergunta.
- Use o que o lead já disse. Nada de "passando para saber se ainda tem interesse".
- Não invente imóveis, preços, descontos nem condições. Use só o que está abaixo.
- Não repita imóveis que já foram apresentados.
- Trechos como [NOME_1] são dados protegidos: mantenha-os exatamente assim.
- Português do Brasil, tom de pessoa, não de robô. Sem "prezado cliente".

O QUE SE SABE DO LEAD:
{profile}
{context}
Responda apenas com o texto da mensagem, sem aspas e sem comentários."""


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

class FollowUp:

    def __init__(self, lead_id, text, attempt, tone, source, channel, decision):
        self.lead_id = lead_id
        self.text = text
        self.attempt = attempt
        self.tone = tone
        self.source = source
        self.channel = channel
        self.decision = decision

    def to_dict(self):
        return {
            "lead_id": self.lead_id,
            "text": self.text,
            "attempt": self.attempt,
            "tone": self.tone,
            "source": self.source,
            "channel": self.channel,
            "decision": self.decision.to_dict(),
        }

    def __repr__(self):
        return "<FollowUp %s attempt=%d tone=%s>" % (self.lead_id, self.attempt, self.tone)


def suggested_channel(profile):
    """Where to reach the lead. Phone before e-mail: this is WhatsApp."""
    if is_known((profile or {}).get("phone")):
        return "whatsapp"
    if is_known((profile or {}).get("email")):
        return "email"

    return "chat"


class FollowUpGenerator:

    def __init__(self, client=None, pseudonymizer=None):
        self.client = client if client is not None else get_client()
        self.pseudo = pseudonymizer or Pseudonymizer()

    def generate(self, memory, lead_id, new_properties=None, require_consent=True,
                 decision=None):
        """Produce the re-engagement message, or None if it is not time yet.

        `new_properties` are RAG recommendations found since the last
        conversation: they are what gives the second attempt substance. Without
        them the "offer" follow-up degenerates into the usual "still
        interested?".
        """
        decision = decision or evaluate_followup(memory, lead_id, require_consent)
        if not decision.send:
            return None

        profile = memory.profile(lead_id)
        text = None

        if self.client.available:
            text = self._generate_via_llm(memory, lead_id, decision.tone, new_properties)

        source = "llm" if text else "heuristic"
        if not text:
            text = _heuristic_text(profile, decision.tone, new_properties)

        return FollowUp(
            lead_id=lead_id,
            text=text,
            attempt=decision.attempt,
            tone=decision.tone,
            source=source,
            channel=suggested_channel(profile),
            decision=decision,
        )

    def send(self, memory, lead_id, new_properties=None, require_consent=True):
        """Generate the follow-up and record it in memory.

        Recording is what advances the cadence. The message enters the history
        as an agent turn, so it does NOT reset the lead's silence counter.
        """
        followup = self.generate(memory, lead_id, new_properties, require_consent)
        if not followup:
            return None

        memory.record_message(lead_id, "assistant", followup.text)
        memory.record_followup(lead_id)

        if new_properties:
            memory.record_shown_properties(
                lead_id, [_property_id(p) for p in new_properties]
            )

        return followup

    def _generate_via_llm(self, memory, lead_id, tone, new_properties):
        mapping = memory.alias_map(lead_id)
        profile = memory.profile(lead_id)
        names = [profile["name"]] if is_known(profile.get("name")) else []

        profile_text, mapping = self.pseudo.mask(_format_profile(profile), mapping, names)
        context, mapping = self.pseudo.mask(
            _format_context(memory, lead_id, new_properties), mapping, names
        )

        prompt = PROMPT_FOLLOWUP.format(
            tone_instruction=TONE_INSTRUCTIONS[tone],
            profile=profile_text,
            context=context,
        )

        try:
            raw = self.client.generate(prompt, temperature=0.7)
        except Exception:
            return None

        if not raw or not raw.strip():
            return None

        return self.pseudo.restore(raw.strip().strip('"'), mapping)


# ---------------------------------------------------------------------------
# Prompt context
# ---------------------------------------------------------------------------

# Fields worth putting in the prompt. E-mail and phone are left out on
# purpose: the follow-up text should never recite the lead's own contact
# details back at them.
_PROMPT_FIELDS = ("name", "intent", "price_range", "region", "bedrooms", "urgency")


def _format_profile(profile):
    lines = [
        "- %s: %s" % (
            lead_profile.FIELD_LABELS[field],
            lead_profile.label(field, (profile or {})[field]),
        )
        for field in _PROMPT_FIELDS
        if is_known((profile or {}).get(field))
    ]
    return "\n".join(lines) if lines else "- (quase nada foi coletado ainda)"


def _property_id(prop):
    return prop.get("id") if isinstance(prop, dict) else getattr(prop, "id", None)


def _describe_property(prop):
    if not isinstance(prop, dict):
        return getattr(prop, "reason", str(prop))

    return "%s: %s em %s por R$ %s" % (
        prop.get("id", "?"),
        prop.get("title", "imóvel"),
        prop.get("neighborhood", "?"),
        "{:,.0f}".format(prop.get("price", 0)).replace(",", "."),
    )


def _format_context(memory, lead_id, new_properties):
    state = memory.state(lead_id)
    parts = []

    last = _last_lead_message(state)
    if last:
        parts.append("ÚLTIMA COISA QUE O LEAD DISSE:\n\"%s\"" % last)

    if state.get("summary"):
        parts.append("RESUMO DA CONVERSA:\n%s" % state["summary"])

    if state["shown_properties"]:
        parts.append("IMÓVEIS JÁ APRESENTADOS (não repita): %s"
                     % ", ".join(state["shown_properties"]))

    if new_properties:
        lines = [_describe_property(p) for p in new_properties]
        parts.append("OPÇÕES NOVAS QUE VOCÊ PODE CITAR:\n" + "\n".join(lines))

    parts.append("HÁ QUANTO TEMPO NÃO RESPONDE: %.0f horas"
                 % memory.hours_of_silence(lead_id))

    return "\n\n" + "\n\n".join(parts) + "\n"


def _last_lead_message(state):
    for message in reversed(state["messages"]):
        if message["role"] == "user":
            return message["content"]

    return None


# ---------------------------------------------------------------------------
# Heuristic path
# ---------------------------------------------------------------------------
# Templates used when there is no LLM. Deliberately short and concrete: a
# generic template ("ainda tem interesse?") would be worse than sending nothing.

def _profile_fragment(profile):
    """The snippet that proves to the lead this is not a mass blast."""
    parts = []
    if is_known(profile.get("bedrooms")):
        parts.append("%s quartos" % profile["bedrooms"])
    if is_known(profile.get("region")):
        parts.append("em %s" % profile["region"])
    if is_known(profile.get("price_range")):
        parts.append("até %s" % profile["price_range"])

    return " ".join(parts)


def _heuristic_text(profile, tone, new_properties=None):
    name = profile.get("name")
    greeting = "Oi, %s!" % name if is_known(name) else "Oi!"
    looking_for = _profile_fragment(profile)

    if tone == "reopen":
        if looking_for:
            return ("%s Continuo de olho em opções de %s pra você. "
                    "Ainda faz sentido seguir com a busca?" % (greeting, looking_for))
        return ("%s Ficamos no meio da conversa outro dia. "
                "Me conta: você procura pra comprar ou pra alugar?" % greeting)

    if tone == "offer":
        if new_properties:
            highlight = new_properties[0]
            return ("%s Apareceu algo novo que combina com o que você procura: "
                    "%s. Quer que eu te mande os detalhes?"
                    % (greeting, _describe_property(highlight)))
        if looking_for:
            return ("%s Entraram opções novas de %s essa semana. "
                    "Quer que eu separe algumas pra você?" % (greeting, looking_for))
        return ("%s Sei que a rotina corre. Se quiser, eu separo algumas opções "
                "e te mando sem compromisso. Topa?" % greeting)

    # signoff
    return ("%s Não quero insistir, então vou parar por aqui. "
            "Se voltar a procurar imóvel, é só me chamar que retomo de onde "
            "paramos. Sucesso!" % greeting)
