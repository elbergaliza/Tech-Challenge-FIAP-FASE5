"""
Memória conversacional do agente SDR.

Duas camadas, porque conversa de WhatsApp não cabe inteira num prompt:

  JANELA CURTA   as últimas N mensagens, literais. É o que dá naturalidade: o
                 agente lembra o que foi dito há dois turnos.

  MEMÓRIA LONGA  o perfil consolidado do lead, um resumo incremental da conversa
                 e os imóveis já apresentados. É o que dá continuidade entre
                 sessões, inclusive depois de dias de silêncio, que é exatamente
                 o cenário 3 do PDF do desafio (follow-up).

O ganho concreto sobre o agente sozinho: a extração da Pessoa 1 é SEM ESTADO.
Ela roda regex sobre a conversa concatenada a cada chamada, então um campo já
descoberto pode voltar a "undefined" quando o texto cresce e o padrão deixa de
casar. Aqui o perfil é MONOTÔNICO: uma vez conhecido, um campo só muda para
outro valor conhecido (o lead se corrigiu), nunca de volta para desconhecido. A
correção fica registrada, porque "o lead mudou de ideia sobre o orçamento" é
informação que o corretor quer ver.

Privacidade (LGPD): o que sai daqui rumo ao LLM passa por pseudonimização, e o
mapa de apelidos fica guardado na sessão para que o mesmo e-mail tenha sempre o
mesmo apelido. O módulo também implementa retenção por prazo (princípio da
Limitação de Armazenamento), `forget()` (direito de exclusão) e `export()`
(direito de acesso e portabilidade).

IDIOMA: identificadores, chaves de estado e chaves de perfil estão em inglês. O
`dados_coletados` em português da Pessoa 1 é traduzido uma vez, na borda, pelo
`lead_profile.from_agent()`. O texto de contexto do prompt fica em português
porque o LLM o lê ao lado de um prompt em português.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

import lead_profile
from lead_profile import (
    DODGE_THRESHOLD, PII_FIELDS, PROFILE_FIELDS, is_known, next_to_collect,
)
from privacy import PATTERNS, Pseudonymizer, detect_names, mask_for_log

STATE_VERSION = 1

# Espelha o corte que a Pessoa 1 já faz em `historico[-10:]`.
DEFAULT_WINDOW = 10

# A partir daqui a conversa antiga deveria virar resumo, para o prompt não
# crescer sem limite. Quem produz o resumo é o summarizer; a memória só avisa.
SUMMARY_THRESHOLD = 20

# Retenção padrão. A LGPD não fixa prazo, exige que seja o necessário para a
# finalidade; seis meses é o que se costuma praticar para lead comercial frio.
DEFAULT_RETENTION_DAYS = 180

_VALID_LEAD_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _utc_now():
    return datetime.now(timezone.utc)


def _iso(moment):
    return moment.isoformat()


def validate_lead_id(lead_id):
    """O lead_id vira nome de arquivo, então não pode vir cru da API.

    Sem isto, um lead_id como "../../.env" faria o store ler e escrever fora do
    diretório de dados.
    """
    if not isinstance(lead_id, str) or not _VALID_LEAD_ID.match(lead_id):
        raise ValueError(
            "lead_id inválido: %r. Aceito: letras, dígitos, '_' e '-', até 64 chars."
            % (lead_id,)
        )

    return lead_id


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

class InMemoryStore:
    """Store volátil. Usado nos testes e em demo."""

    def __init__(self):
        self._data = {}

    def read(self, lead_id):
        state = self._data.get(lead_id)
        return json.loads(json.dumps(state)) if state else None

    def write(self, lead_id, state):
        self._data[lead_id] = json.loads(json.dumps(state))

    def delete(self, lead_id):
        return self._data.pop(lead_id, None) is not None

    def list_ids(self):
        return sorted(self._data)


class JsonFileStore:
    """Um arquivo JSON por lead.

    Um arquivo por lead, e não um arquivo único com todos, por dois motivos:
    `forget()` vira `os.remove` e o dado some de verdade, e escrever um lead não
    reescreve a base inteira.

    Para produção a Pessoa 3 implementa a mesma interface sobre o banco; nada
    mais no módulo precisa mudar.
    """

    def __init__(self, directory):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def _path(self, lead_id):
        return os.path.join(self.directory, "%s.json" % validate_lead_id(lead_id))

    def read(self, lead_id):
        path = self._path(lead_id)
        if not os.path.isfile(path):
            return None

        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def write(self, lead_id, state):
        path = self._path(lead_id)
        # Escrita atômica: um Ctrl+C no meio não pode deixar JSON truncado no
        # lugar do histórico do lead.
        temporary = path + ".tmp"

        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)

        os.replace(temporary, path)

    def delete(self, lead_id):
        path = self._path(lead_id)
        if not os.path.isfile(path):
            return False

        os.remove(path)
        return True

    def list_ids(self):
        if not os.path.isdir(self.directory):
            return []

        return sorted(
            name[:-5] for name in os.listdir(self.directory) if name.endswith(".json")
        )


# ---------------------------------------------------------------------------
# Turno
# ---------------------------------------------------------------------------

class PreparedTurn:
    """O que o agente precisa para responder um turno, já pseudonimizado.

    Existe para que mascarar e restaurar sejam sempre um par. Chamar o LLM com
    texto mascarado e esquecer de restaurar faria o lead receber "[NOME_1]" na
    tela; o par `start_turn` / `finish_turn` torna isso difícil de errar.
    """

    def __init__(self, lead_id, message, original_message, history, context, mapping):
        self.lead_id = lead_id
        self.message = message
        self.original_message = original_message
        self.history = history
        self.context = context
        self.mapping = mapping


# ---------------------------------------------------------------------------
# Memória
# ---------------------------------------------------------------------------

class ConversationMemory:

    def __init__(self, store=None, clock=None, pseudonymizer=None,
                 retention_days=DEFAULT_RETENTION_DAYS):
        self.store = store if store is not None else InMemoryStore()
        # Relógio injetável: sem isto não dá para testar retenção sem esperar
        # 180 dias.
        self.clock = clock or _utc_now
        self.pseudo = pseudonymizer or Pseudonymizer()
        self.retention_days = retention_days

    # -- estado -------------------------------------------------------------

    def _new_state(self, lead_id):
        now = _iso(self.clock())
        return {
            "version": STATE_VERSION,
            "lead_id": lead_id,
            "created_at": now,
            "updated_at": now,
            # Duas datas diferentes de propósito. `last_interaction_at` inclui
            # as mensagens do agente e serve à retenção. `last_lead_message_at`
            # só avança quando o LEAD escreve, e é a que mede silêncio: sem essa
            # separação, mandar um follow-up zeraria o próprio contador que
            # decide se o follow-up deve ser mandado.
            "last_interaction_at": now,
            "last_lead_message_at": now,
            "consent": None,
            "profile": {},
            "profile_meta": {},
            "messages": [],
            "summary": None,
            "summarized_up_to": 0,
            "shown_properties": [],
            "alias_map": {},
            # Por campo, quantas mensagens do lead chegaram enquanto ele
            # continuava desconhecido. É o proxy para "já perguntei isso e
            # não obtive resposta": só a memória tem esse estado, porque o
            # agente vê uma janela de 10 mensagens e nenhum contador.
            "unanswered": {},
            # Consecutivos sem resposta do lead: zera quando ele volta a falar.
            # É esse que decide a cadência e o alerta de "insistindo demais".
            "followups_sent": 0,
            # Total histórico, nunca zera. Serve a relatório, não a decisão.
            "followups_total": 0,
            "last_followup_at": None,
        }

    def state(self, lead_id):
        """Estado do lead, criando um vazio se ainda não existir."""
        validate_lead_id(lead_id)
        return self.store.read(lead_id) or self._new_state(lead_id)

    def _save(self, lead_id, state):
        state["updated_at"] = _iso(self.clock())
        self.store.write(lead_id, state)
        return state

    def exists(self, lead_id):
        validate_lead_id(lead_id)
        return self.store.read(lead_id) is not None

    def leads(self):
        return self.store.list_ids()

    # -- mensagens ----------------------------------------------------------

    def record_message(self, lead_id, role, content):
        """Grava uma mensagem. `role` é 'user' ou 'assistant'."""
        if role not in ("user", "assistant"):
            raise ValueError("role inválido: %r" % (role,))

        state = self.state(lead_id)
        now = _iso(self.clock())

        state["messages"].append({"role": role, "content": content, "at": now})
        state["last_interaction_at"] = now

        if role == "user":
            state["last_lead_message_at"] = now
            # O lead voltou a falar: a régua de follow-up recomeça. Ele está
            # conversando, não fugindo, e a próxima retomada deve usar o
            # intervalo curto de novo. `followups_total` guarda o histórico.
            state["followups_sent"] = 0

            # Uma mensagem chegou e o campo que o agente estava perseguindo
            # continua desconhecido. Conta só ESSE campo, não todos os que
            # faltam: um SDR pergunta uma coisa por vez, e marcar tudo de uma
            # vez fazia o contexto dizer "desista de todos os assuntos", que é
            # instrução pior do que o problema original.
            #
            # Contar aqui, e não no `update_profile`, garante exatamente um
            # incremento por mensagem do lead: o `update_profile` pode ser
            # chamado mais de uma vez no mesmo turno.
            pursued = next_to_collect(state["profile"])
            if pursued:
                unanswered = state.setdefault("unanswered", {})
                unanswered[pursued] = unanswered.get(pursued, 0) + 1

        return self._save(lead_id, state)

    def history(self, lead_id, window=DEFAULT_WINDOW, mask=False):
        """Histórico no formato que o `chamar_agente()` espera.

        A Pessoa 1 consome `[{"role": ..., "content": ...}]`, então é esse o
        formato devolvido, e não o interno.
        """
        state = self.state(lead_id)
        messages = state["messages"]

        if window:
            messages = messages[-window:]

        if not mask:
            return [{"role": m["role"], "content": m["content"]} for m in messages]

        output, mapping = self._mask_messages(
            messages, state.get("alias_map", {}), self._known_names(state)
        )
        # Apelidos novos precisam ser persistidos, senão o mesmo e-mail ganha
        # apelidos diferentes a cada turno e o LLM acha que são pessoas
        # diferentes.
        state["alias_map"] = mapping
        self._save(lead_id, state)

        return output

    def _mask_messages(self, messages, mapping, names):
        output = []
        for message in messages:
            text, mapping = self.pseudo.mask(message["content"], mapping, names)
            output.append({"role": message["role"], "content": text})

        return output, mapping

    def message_count(self, lead_id):
        return len(self.state(lead_id)["messages"])

    # -- perfil -------------------------------------------------------------

    def _known_names(self, state):
        name = state.get("profile", {}).get("name")
        return [name] if is_known(name) else []

    def update_profile(self, lead_id, collected_data):
        """Funde o `dados_coletados` da Pessoa 1 no perfil acumulado.

        Devolve a lista de alterações, cada uma com `kind` 'new' ou
        'correction'. O corretor quer ver as correções: "o lead subiu o
        orçamento de 500k para 800k" é sinal de compra, não ruído.

        Valores desconhecidos NUNCA sobrescrevem valores conhecidos. É a razão
        de a memória existir: a extração da Pessoa 1 é sem estado e pode
        regredir entre chamadas.
        """
        state = self.state(lead_id)
        now = _iso(self.clock())
        changes = []

        # As chaves em português da Pessoa 1 são traduzidas aqui, uma vez, na
        # borda. O `from_agent` também deixa passar as nossas próprias chaves,
        # então um perfil já traduzido pode ser fundido de novo com segurança.
        for field, value in lead_profile.from_agent(collected_data).items():
            previous = state["profile"].get(field)

            if not is_known(previous):
                kind = "new"
            elif str(previous) != str(value):
                kind = "correction"
            else:
                continue

            state["profile"][field] = value
            # Respondeu: o contador zera. Se o lead voltar a esconder o campo
            # depois de uma correção, a contagem recomeça do zero.
            state.setdefault("unanswered", {}).pop(field, None)
            meta = state["profile_meta"].setdefault(field, {"revisions": 0})
            meta["updated_at"] = now
            meta["revisions"] += 1

            changes.append({
                "field": field, "from": previous, "to": value, "kind": kind,
            })

        self._save(lead_id, state)
        return changes

    def profile(self, lead_id):
        return dict(self.state(lead_id)["profile"])

    def alias_map(self, lead_id):
        """Mapa de apelidos da sessão, para quem precisa restaurar texto.

        Usado pelo summarizer e pelo gerador de follow-up, que mandam a conversa
        mascarada ao LLM e precisam desfazer os apelidos na volta.
        """
        return dict(self.state(lead_id).get("alias_map", {}))

    def known_fields(self, lead_id):
        return sorted(f for f, v in self.profile(lead_id).items() if is_known(v))

    def dodged_fields(self, lead_id, threshold=DODGE_THRESHOLD):
        """Campos que o lead vem deixando sem resposta, e há quantas mensagens.

        Serve a dois consumidores: o contexto do prompt, para o agente parar de
        insistir, e o alerta do dashboard, para o corretor saber que o lead está
        se esquivando de um ponto específico.

        Só a memória consegue calcular isto. O agente recebe uma janela de dez
        mensagens e nenhum contador, então ele repetiria a mesma pergunta
        indefinidamente sem perceber.
        """
        unanswered = self.state(lead_id).get("unanswered", {})
        return {f: n for f, n in unanswered.items() if n >= threshold}

    # -- imóveis já apresentados --------------------------------------------

    def record_shown_properties(self, lead_id, ids):
        """Guarda o que já foi mostrado.

        Serve a dois consumidores: o follow-up, que precisa dizer "aquele
        apartamento em Botafogo que te mandei", e a busca, que não deveria
        repetir as mesmas três opções a cada turno.
        """
        state = self.state(lead_id)
        already_seen = state["shown_properties"]

        for property_id in ids:
            if property_id not in already_seen:
                already_seen.append(property_id)

        return self._save(lead_id, state)

    def shown_properties(self, lead_id):
        return list(self.state(lead_id)["shown_properties"])

    # -- resumo incremental -------------------------------------------------

    def needs_summary(self, lead_id):
        """Verdadeiro quando há conversa antiga demais fora do resumo."""
        state = self.state(lead_id)
        unsummarized = len(state["messages"]) - state["summarized_up_to"]
        return unsummarized > SUMMARY_THRESHOLD

    def messages_to_summarize(self, lead_id):
        """As mensagens ainda não cobertas pelo resumo, menos a janela viva."""
        state = self.state(lead_id)
        return state["messages"][state["summarized_up_to"]:-DEFAULT_WINDOW]

    def set_summary(self, lead_id, text, up_to_index):
        """Guarda o resumo produzido pelo summarizer."""
        state = self.state(lead_id)
        state["summary"] = text
        state["summarized_up_to"] = max(0, min(up_to_index, len(state["messages"])))
        return self._save(lead_id, state)

    def summary(self, lead_id):
        return self.state(lead_id)["summary"]

    # -- follow-up ----------------------------------------------------------

    def hours_of_silence(self, lead_id):
        """Horas desde a última mensagem DO LEAD.

        Mensagens do agente não contam: o que interessa é há quanto tempo o lead
        não responde. `last_lead_message_at` usa `.get` com fallback para que
        estados gravados antes deste campo existir continuem legíveis.
        """
        state = self.state(lead_id)
        last = datetime.fromisoformat(
            state.get("last_lead_message_at") or state["created_at"]
        )
        return (self.clock() - last).total_seconds() / 3600.0

    def record_followup(self, lead_id):
        state = self.state(lead_id)
        state["followups_sent"] += 1
        state["followups_total"] = state.get("followups_total", 0) + 1
        state["last_followup_at"] = _iso(self.clock())
        return self._save(lead_id, state)

    # -- contexto para o prompt ---------------------------------------------

    def build_context(self, lead_id, mask=True):
        """Bloco de texto com a memória longa, para injetar no prompt.

        Complementa o histórico curto, não o substitui. É o que o agente da
        Pessoa 1 receberia como `contexto_extra`.
        """
        state = self.state(lead_id)
        text = self._context_text(state)

        if not (mask and text):
            return text

        text, mapping = self.pseudo.mask(
            text, state.get("alias_map", {}), self._known_names(state)
        )
        state["alias_map"] = mapping
        self._save(lead_id, state)

        return text

    def _context_text(self, state):
        """Monta o bloco em claro. A pseudonimização é aplicada por quem chama.

        Separado de `build_context` para que `start_turn` consiga mascarar
        histórico, mensagem e contexto contra UM único mapa acumulado.

        O texto é em português: o LLM o lê ao lado de um prompt em português.
        """
        profile = state["profile"]
        lines = []

        known = {f: v for f, v in profile.items() if is_known(v)}
        if known:
            lines.append("O QUE VOCÊ JÁ SABE SOBRE ESTE LEAD (não pergunte de novo):")
            for field in PROFILE_FIELDS:
                if field in known:
                    lines.append("- %s: %s" % (
                        lead_profile.FIELD_LABELS[field],
                        lead_profile.label(field, known[field]),
                    ))

        dodged = {f: n for f, n in state.get("unanswered", {}).items()
                  if n >= DODGE_THRESHOLD}

        # Só faz sentido apontar o que falta quando já se sabe alguma coisa.
        # Num lead novo isso seria a lista inteira, redundante com o system
        # prompt da Pessoa 1 e puro custo de token.
        #
        # Campos esquivados saem desta lista de propósito: mandar "descubra o
        # orçamento" e "não insista no orçamento" no mesmo prompt é instrução
        # contraditória, e o modelo escolhe uma das duas ao acaso.
        if known:
            missing = [lead_profile.FIELD_LABELS[f].lower() for f in PROFILE_FIELDS
                       if f not in known and f not in ("email", "phone")
                       and f not in dodged]
            if missing:
                lines.append("AINDA FALTA DESCOBRIR: " + ", ".join(missing) + ".")

        if dodged:
            detalhe = ", ".join(
                "%s (%d mensagens)" % (lead_profile.FIELD_LABELS[f].lower(), n)
                for f, n in sorted(dodged.items())
            )
            lines.append(
                "O LEAD NÃO RESPONDEU SOBRE: " + detalhe + ". "
                "Pare de perguntar isso. Siga para outro assunto ou ofereça "
                "agendar com o que você já tem."
            )

        if state["summary"]:
            lines.append("RESUMO DA CONVERSA ATÉ AQUI:")
            lines.append(state["summary"])

        if state["shown_properties"]:
            lines.append(
                "IMÓVEIS JÁ APRESENTADOS A ESTE LEAD: "
                + ", ".join(state["shown_properties"])
                + ". Não os apresente como novidade."
            )

        if state["followups_sent"]:
            lines.append(
                "Este lead já recebeu %d follow-up(s) sem responder. "
                "Não insista de forma agressiva." % state["followups_sent"]
            )

        return "\n".join(lines)

    # -- ciclo de um turno --------------------------------------------------

    def start_turn(self, lead_id, message, window=DEFAULT_WINDOW):
        """Registra a mensagem do lead e devolve tudo pronto e pseudonimizado.

        Histórico, mensagem e contexto são mascarados contra o MESMO mapa
        acumulado, e o mapa completo volta no turno. Mascarar cada peça com um
        mapa próprio faria a resposta do LLM voltar com apelidos que o
        `finish_turn` não saberia desfazer, e o lead veria "[EMAIL_2]".
        """
        self.record_message(lead_id, "user", message)

        state = self.state(lead_id)
        mapping = state.get("alias_map", {})

        # Nomes já conhecidos MAIS os declarados nesta mensagem. Sem a segunda
        # metade, o nome do lead iria em claro para o LLM exatamente na mensagem
        # em que ele se apresenta.
        names = self._known_names(state) + detect_names(message)

        previous = state["messages"][:-1]
        if window:
            previous = previous[-window:]

        history, mapping = self._mask_messages(previous, mapping, names)
        masked_message, mapping = self.pseudo.mask(message, mapping, names)
        context, mapping = self.pseudo.mask(
            self._context_text(state), mapping, names
        )

        state["alias_map"] = mapping
        self._save(lead_id, state)

        return PreparedTurn(
            lead_id=lead_id,
            message=masked_message,
            original_message=message,
            history=history,
            context=context,
            mapping=dict(mapping),
        )

    def finish_turn(self, lead_id, turn, reply, collected_data=None):
        """Restaura a resposta, grava e funde o perfil. Devolve o texto ao lead.

        Detalhe de integração que não é óbvio: como o LLM recebeu texto
        mascarado, a extração da Pessoa 1 roda sobre placeholders e devolve
        `"[EMAIL_1]"` no campo e-mail, ou `"undefined"` porque o regex dela não
        reconhece o placeholder. Então aqui fazemos duas coisas: desfazemos os
        apelidos nos valores extraídos, e extraímos e-mail, telefone e nome
        diretamente do texto ORIGINAL, que só nós temos.
        """
        restored_reply = self.pseudo.restore(reply, turn.mapping)
        self.record_message(lead_id, "assistant", restored_reply)

        data = dict(collected_data or {})
        for field, value in list(data.items()):
            if isinstance(value, str):
                data[field] = self.pseudo.restore(value, turn.mapping)

        for field, value in extract_pii(turn.original_message).items():
            if not is_known(data.get(field)):
                data[field] = value

        changes = self.update_profile(lead_id, data)

        return restored_reply, changes

    # -- LGPD ---------------------------------------------------------------

    def record_consent(self, lead_id, granted, purpose):
        """Consentimento explícito e informado (LGPD art. 8º)."""
        state = self.state(lead_id)
        state["consent"] = {
            "granted": bool(granted),
            "purpose": purpose,
            "at": _iso(self.clock()),
        }
        return self._save(lead_id, state)

    def has_consent(self, lead_id):
        consent = self.state(lead_id).get("consent")
        return bool(consent and consent.get("granted"))

    def forget(self, lead_id):
        """Direito de exclusão. Apaga tudo do lead, mapa de apelidos incluído."""
        validate_lead_id(lead_id)
        return self.store.delete(lead_id)

    def export(self, lead_id):
        """Direito de acesso e portabilidade: tudo que guardamos, legível."""
        if not self.exists(lead_id):
            return None

        state = self.state(lead_id)
        return {
            "lead_id": lead_id,
            "created_at": state["created_at"],
            "last_interaction_at": state["last_interaction_at"],
            "consent": state["consent"],
            "profile": state["profile"],
            "profile_meta": state["profile_meta"],
            "messages": state["messages"],
            "summary": state["summary"],
            "shown_properties": state["shown_properties"],
        }

    def expired(self, days=None):
        """Leads cuja última interação passou do prazo de retenção."""
        days = self.retention_days if days is None else days
        cutoff = self.clock() - timedelta(days=days)

        stale = []
        for lead_id in self.leads():
            state = self.store.read(lead_id)
            if not state:
                continue
            if datetime.fromisoformat(state["last_interaction_at"]) < cutoff:
                stale.append(lead_id)

        return stale

    def purge_expired(self, days=None):
        """Limitação de Armazenamento: descarta o que passou do prazo.

        Feito para rodar como job agendado pela Pessoa 3, ao lado do scheduler
        de follow-up.
        """
        removed = []
        for lead_id in self.expired(days):
            if self.forget(lead_id):
                removed.append(lead_id)

        return removed

    def log_summary(self, lead_id):
        """Uma linha de log sem nenhum dado pessoal."""
        state = self.state(lead_id)
        return "lead=%s messages=%d fields=%d followups=%d" % (
            lead_id,
            len(state["messages"]),
            len([v for v in state["profile"].values() if is_known(v)]),
            state["followups_sent"],
        )


def extract_pii(text):
    """E-mail, telefone e nome a partir do texto em claro.

    Necessário porque a extração da Pessoa 1 roda sobre o texto já mascarado e
    fica cega justamente para estes campos: o regex dela não reconhece
    "[EMAIL_1]" como e-mail.
    """
    found = {}

    for kind, pattern in PATTERNS:
        if kind not in ("EMAIL", "TELEFONE"):
            continue
        match = pattern.search(text or "")
        if match:
            found["email" if kind == "EMAIL" else "phone"] = match.group(0)

    names = detect_names(text)
    if names:
        # Só o primeiro nome, capitalizado, para casar com a convenção que a
        # Pessoa 1 usa em `extrair_nome`.
        found["name"] = names[0].split()[0].capitalize()

    return found


__all__ = [
    "ConversationMemory",
    "extract_pii",
    "InMemoryStore",
    "JsonFileStore",
    "PreparedTurn",
    "mask_for_log",
    "validate_lead_id",
]
