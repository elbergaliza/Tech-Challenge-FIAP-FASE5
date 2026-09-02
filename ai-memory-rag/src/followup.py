"""
Follow-up automático: quando retomar o contato e o que dizer.

Divisão de responsabilidade combinada com a Pessoa 3: ela agenda e dispara,
este módulo decide SE deve enviar e escreve O QUE dizer. O
`leads_due_for_followup()` é a consulta que o
`backend/src/jobs/followup_scheduler.py` roda.

Cenário 3 do PDF do desafio: o lead começou a conversa e sumiu. O agente deve
retomar sozinho, mantendo o contexto e reengajando. As duas partes difíceis são
o silêncio e o excesso.

QUANDO (cadência com escalonamento). Uma tentativa depois de 24h, outra depois
de 3 dias, a última depois de 7 dias, e para. Lead com urgência declarada recebe
cadência mais curta, porque quem precisa mudar em duas semanas não espera três
dias por uma resposta.

QUANDO NÃO. Esta é a metade que costuma ser esquecida, e é a que separa
follow-up de spam:
  * sem consentimento registrado, não envia (LGPD art. 8º);
  * se o lead pediu para parar, não envia nunca mais;
  * se o lead respondeu depois do último follow-up, o contador zera;
  * atingido o teto de tentativas, encerra e devolve o lead ao corretor.

O QUE DIZER. Três tons, um por tentativa: retomada leve, oferta de novidade,
despedida com porta aberta. Cada um usa a memória para citar o que o lead disse,
não um texto genérico. Com LLM o texto é escrito na hora; sem LLM, um molde
preenchido com o perfil. Nos dois casos, `source` registra a origem.

IDIOMA: identificadores, nomes de campo e valores de enum estão em inglês.
Prompts, moldes e toda mensagem que o lead lê ficam em português.
"""

import lead_profile
from lead_profile import is_known
from llm import get_client
from privacy import Pseudonymizer

# ---------------------------------------------------------------------------
# Cadência
# ---------------------------------------------------------------------------
# Horas de silêncio exigidas antes da 1ª, 2ª e 3ª tentativa. O tamanho da tupla
# é o teto de tentativas.
DEFAULT_CADENCE = (24, 72, 168)
URGENT_CADENCE = (4, 24, 72)

TONES = ("reopen", "offer", "signoff")

# Frases de recusa. Qualquer uma bloqueia follow-up para sempre: insistir com
# quem pediu para parar destrói a marca e, sob a LGPD, é tratamento sem base
# legal depois da revogação do consentimento.
OPT_OUT_PHRASES = (
    "não quero", "nao quero", "pare de", "para de me", "não me manda",
    "nao me manda", "não tenho interesse", "nao tenho interesse",
    "me tira", "descadastr", "sair da lista", "não insista", "nao insista",
    "desisti", "já comprei", "ja comprei", "já aluguei", "ja aluguei",
    "resolvi por outro", "não precisa mais", "nao precisa mais",
)

# ---------------------------------------------------------------------------
# Decisão
# ---------------------------------------------------------------------------

def detect_opt_out(memory, lead_id):
    """Verdadeiro se o lead pediu, em algum momento, para não ser mais contatado.

    Varre só as mensagens do lead. Uma frase do agente como "me avisa se não
    quiser mais receber" não pode disparar o bloqueio.
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
    """Decide se cabe follow-up agora. Nunca levanta exceção.

    Devolve sempre um FollowUpDecision com o motivo em texto, para que o job da
    Pessoa 3 possa logar por que pulou um lead sem precisar reimplementar a
    regra.

    Os motivos ficam em português: acabam em logs que o time lê.
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

    # `followups_sent` conta só as tentativas consecutivas sem resposta: a
    # memória o zera quando o lead volta a falar. Então este índice é sempre a
    # próxima tentativa do ciclo de silêncio atual.
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
    """Leads que devem receber follow-up agora.

    Ponto de entrada do job agendado da Pessoa 3. Devolve pares
    `(lead_id, decision)` já filtrados, ordenados do mais silencioso ao menos.
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
# Decisões de prompt para follow-up, que é o texto de maior risco do sistema:
# chega sem ser pedido, e um texto ruim faz o lead bloquear a imobiliária.
#   * limite de tamanho explícito e agressivo, porque o canal é WhatsApp;
#   * proibição de saudação genérica: o valor está em citar o que o lead disse;
#   * no máximo UMA pergunta, para o lead ter uma resposta fácil de dar;
#   * proibição de inventar imóvel ou condição comercial;
#   * o tom entra como instrução completa por tentativa, e não como adjetivo
#     solto, para o modelo não escrever três vezes a mesma mensagem.

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
# Geração
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
    """Por onde falar com o lead. Telefone antes de e-mail: é WhatsApp."""
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
        """Produz a mensagem de retomada, ou None se não for hora de enviar.

        `new_properties` são recomendações do RAG obtidas depois da última
        conversa: é o que dá substância à segunda tentativa. Sem elas, o
        follow-up de "oferta" degenera no mesmo "ainda tem interesse?" de
        sempre.
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
        """Gera e registra o follow-up na memória.

        Registrar é o que faz a cadência avançar. A mensagem entra no histórico
        como fala do agente, então NÃO zera o contador de silêncio do lead.
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
# Contexto para o prompt
# ---------------------------------------------------------------------------

# Campos que vale colocar no prompt. E-mail e telefone ficam de fora de
# propósito: o texto do follow-up nunca deve recitar de volta ao lead os
# próprios dados de contato dele.
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
# Caminho heurístico
# ---------------------------------------------------------------------------
# Moldes usados quando não há LLM. Propositalmente curtos e concretos: um molde
# genérico ("ainda tem interesse?") seria pior do que não enviar nada.

def _profile_fragment(profile):
    """Trecho que prova ao lead que a mensagem não é disparo em massa."""
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

    # despedida
    return ("%s Não quero insistir, então vou parar por aqui. "
            "Se voltar a procurar imóvel, é só me chamar que retomo de onde "
            "paramos. Sucesso!" % greeting)
