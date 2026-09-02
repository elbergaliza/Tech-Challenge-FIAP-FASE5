"""
Resumo inteligente para o corretor, e resumo incremental para a memória.

São dois produtos diferentes, com leitores diferentes:

  RESUMO PARA O CORRETOR   vai para o dashboard. Precisa responder, em dez
                           segundos de leitura: quem é este lead, o que ele
                           quer, quão quente está e o que eu faço agora.

  RESUMO INCREMENTAL       vai para o prompt do agente. Precisa comprimir a
                           conversa antiga em poucas linhas para o contexto não
                           crescer sem limite, preservando o que muda decisão.

Ambos degradam com elegância: sem `GEMINI_API_KEY`, sem rede ou com a API fora
do ar, um caminho heurístico determinístico produz um resumo mais pobre, porém
correto e imediato. O campo `source` diz de onde veio ("llm" ou "heuristic"),
para que a interface nunca apresente texto de regra como se fosse de IA.

O score de temperatura é DELIBERADAMENTE baseado em regras, e não num modelo.
Com dezenas de leads sintéticos, um Isolation Forest daria a aparência de rigor
sem o rigor: as próprias aulas de Detecção de Anomalias insistem que o problema
central é a definição do limiar e a validação com dados realistas. Uma regra de
sete fatores é auditável, o corretor entende por que o lead está quente, e é
defensável na banca. Trocar por um modelo depois é substituir `compute_score`.

IDIOMA: identificadores, nomes de campo e valores de enum estão em inglês.
Prompts e toda string que um corretor ou lead lê ficam em português; os mapas
*_LABELS carregam essa renderização.
"""

import lead_profile
from lead_profile import DODGE_THRESHOLD, has_contact, is_known
from llm import extract_json, get_client
from privacy import Pseudonymizer

# ---------------------------------------------------------------------------
# Score de temperatura do lead
# ---------------------------------------------------------------------------
# Somam 100. Contato pesa tanto quanto intenção porque um lead perfeitamente
# qualificado sem telefone nem e-mail é um lead que o corretor não consegue
# atender: para um SDR, contato é o produto final.
SCORE_WEIGHTS = {
    "intent": 20,
    "contact": 20,
    "price_range": 15,
    "high_urgency": 15,
    "region": 10,
    "bedrooms": 10,
    "engagement": 10,
}

# Um lead que pontuou 80 e sumiu há um mês não está quente. A penalidade por
# silêncio evita que o dashboard mande o corretor atrás de lead morto.
SILENCE_PENALTIES = (
    (48, 0),
    (24 * 7, 10),
    (24 * 30, 25),
)
MAX_SILENCE_PENALTY = 40

HOT_THRESHOLD = 70
WARM_THRESHOLD = 40

# Rótulos em português para a temperatura mostrada no dashboard.
TEMPERATURE_LABELS = {"HOT": "QUENTE", "WARM": "MORNO", "COLD": "FRIO"}

TEMPERATURE_ICONS = {"HOT": "🔥", "WARM": "🌤️", "COLD": "❄️"}

MESSAGES_FOR_ENGAGED = 3


def _silence_penalty(hours):
    for limit, penalty in SILENCE_PENALTIES:
        if hours <= limit:
            return penalty

    return MAX_SILENCE_PENALTY


def compute_score(profile, lead_messages=0, hours_of_silence=0.0):
    """Score de 0 a 100, e a lista de fatores que o compõem.

    Devolve os fatores, e não só o número, porque o corretor precisa saber POR
    QUE o lead está quente para decidir o que dizer na ligação.

    Os fatores ficam em português: aparecem no dashboard.
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
# Alertas acionáveis
# ---------------------------------------------------------------------------

def build_alerts(profile, state):
    """O que está travando este lead. Vira badge no dashboard."""
    alerts = []

    if not has_contact(profile):
        alerts.append("Sem contato: não dá para retomar fora do chat")

    if not is_known(profile.get("intent")):
        alerts.append("Intenção não identificada (compra, aluguel ou investimento)")

    if not is_known(profile.get("price_range")):
        alerts.append("Orçamento não informado")

    dodged = {f: n for f, n in state.get("unanswered", {}).items()
              if n >= DODGE_THRESHOLD}
    if dodged:
        alerts.append(
            "Lead se esquiva de: %s"
            % ", ".join(lead_profile.FIELD_LABELS[f].lower()
                        for f in sorted(dodged))
        )

    if state.get("followups_sent", 0) >= 3:
        alerts.append("%d follow-ups sem resposta: considerar encerrar"
                      % state["followups_sent"])

    if not state.get("consent"):
        alerts.append("Consentimento LGPD não registrado")

    return alerts


def suggest_next_action(profile, temperature, state):
    """Próxima ação por regra. É o campo que o corretor mais usa."""
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
# Decisões de prompt, já que a disciplina de prompts não tem material escrito:
#   * papel e tarefa explícitos na primeira linha;
#   * formato de saída declarado como JSON e repetido no fim, que é onde o
#     modelo mais presta atenção;
#   * proibição explícita de inventar, porque resumo é onde alucinação causa
#     mais estrago: o corretor liga acreditando num dado que o lead nunca disse;
#   * instrução para deixar o campo vazio quando não souber, em vez de chutar;
#   * os apelidos [NOME_1] e [EMAIL_1] são explicados, senão o modelo tenta
#     "corrigi-los" para nomes inventados.
#
# Os prompts são escritos em português para que prompt e saída compartilhem o
# idioma do produto, e para que o time consiga revisar o texto que o agente vai
# dizer ao lead.

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
# Resumo para o corretor
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
            # Renderização em português junto do enum, para o dashboard não
            # precisar carregar a própria tabela de tradução.
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
        """Bloco pronto para o card do dashboard (Parte 4)."""
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

    # -- resumo para o corretor ---------------------------------------------

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
            # A próxima ação sugerida pelo LLM só entra se ele devolveu uma; a
            # regra é o piso, porque este campo nunca pode ficar vazio.
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
        """Chama o LLM com a conversa pseudonimizada e devolve o dict, ou None.

        Falha silenciosa por decisão de produto: se o Gemini cair, o dashboard
        mostra o resumo por regra em vez de uma tela de erro.
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

        # O corretor é destinatário autorizado: os apelidos voltam a ser dados
        # reais antes de chegar ao dashboard.
        return _restore_deep(content, self.pseudo, memory.alias_map(lead_id))

    # -- resumo incremental para a memória ----------------------------------

    def compress_memory(self, memory, lead_id, force=False):
        """Resume o miolo antigo da conversa e grava na memória.

        Devolve o texto do resumo, ou None quando não havia o que comprimir. A
        janela viva de mensagens recentes nunca é tocada.
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
# Caminho heurístico
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
    """Resumo montado por regra, sem LLM. Mais pobre, porém sempre correto."""
    sentences = []

    interest = _heuristic_interest(profile)
    name = profile.get("name")
    if is_known(name):
        # "Lead quer comprar..." vira "João quer comprar...".
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
    """Compressão sem LLM: guarda o perfil e conta o volume descartado.

    Não tenta imitar um resumo escrito. Diz o que sabe e admite o que não tem,
    o que é preferível a produzir texto inventado sem modelo por trás.
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
# Auxiliares
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
    """Resume todos os leads, ordenados por temperatura.

    É a consulta que alimenta a lista do dashboard. `use_llm=False` por padrão
    porque varrer a carteira inteira chamando o Gemini por lead é caro e lento;
    o resumo rico é gerado ao abrir um lead específico.
    """
    summarizer = summarizer or Summarizer(client=get_client("unavailable"))

    summaries = [
        summarizer.summarize_for_broker(memory, lead_id, use_llm=use_llm)
        for lead_id in memory.leads()
    ]
    summaries.sort(key=lambda s: s.score, reverse=True)

    return summaries
