"""
Conversational memory for the SDR agent.

Two layers, because a WhatsApp conversation does not fit whole into a prompt:

  SHORT WINDOW   the last N messages, verbatim. This is what makes the agent
                 sound natural: it remembers what was said two turns ago.

  LONG MEMORY    the consolidated lead profile, an incremental conversation
                 summary and the properties already shown. This is what gives
                 continuity across sessions, including after days of silence,
                 which is exactly scenario 3 of the challenge brief (follow-up).

The concrete gain over the agent alone: Person 1's extraction is STATELESS. It
runs regex over the concatenated conversation on every call, so an already
discovered field can fall back to "undefined" once the text grows and the
pattern stops matching. Here the profile is MONOTONIC: once known, a field only
changes to another known value (the lead corrected themselves), never back to
unknown. The correction is recorded, because "the lead changed their mind about
the budget" is information the broker wants to see.

Privacy (LGPD): whatever leaves here for the LLM goes through pseudonymisation,
and the alias map is kept in the session so the same e-mail always gets the same
alias. The module also implements time-based retention (Storage Limitation
principle), `forget()` (right to erasure) and `export()` (right of access and
portability).

NOTE ON LANGUAGE: identifiers, state keys and profile keys are in English.
Person 1's Portuguese `dados_coletados` is translated once, at the edge, by
`lead_profile.from_agent()`. The prompt context text stays in Portuguese
because the LLM reads it alongside a Portuguese prompt.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

import lead_profile
from lead_profile import PII_FIELDS, PROFILE_FIELDS, is_known
from privacy import PATTERNS, Pseudonymizer, detect_names, mask_for_log

STATE_VERSION = 1

# Mirrors the cut Person 1 already applies in `historico[-10:]`.
DEFAULT_WINDOW = 10

# Past this point the older conversation should become a summary, so the prompt
# does not grow without bound. The summarizer produces it; memory only signals.
SUMMARY_THRESHOLD = 20

# Default retention. The LGPD sets no fixed term, it requires no longer than
# necessary for the purpose; six months is common practice for a cold sales lead.
DEFAULT_RETENTION_DAYS = 180

_VALID_LEAD_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _utc_now():
    return datetime.now(timezone.utc)


def _iso(moment):
    return moment.isoformat()


def validate_lead_id(lead_id):
    """The lead id becomes a filename, so it cannot arrive raw from the API.

    Without this, a lead id like "../../.env" would make the store read and
    write outside the data directory.
    """
    if not isinstance(lead_id, str) or not _VALID_LEAD_ID.match(lead_id):
        raise ValueError(
            "invalid lead_id: %r. Allowed: letters, digits, '_' and '-', up to 64 chars."
            % (lead_id,)
        )

    return lead_id


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class InMemoryStore:
    """Volatile store. Used in tests and demos."""

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
    """One JSON file per lead.

    One file per lead rather than a single file with all of them, for two
    reasons: `forget()` becomes `os.remove` and the data is genuinely gone, and
    writing one lead does not rewrite the whole base.

    For production Person 3 implements the same interface over the database;
    nothing else in the module changes.
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
        # Atomic write: a Ctrl+C halfway through must not leave truncated JSON
        # where the lead's history used to be.
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
# Turn
# ---------------------------------------------------------------------------

class PreparedTurn:
    """Everything the agent needs to answer one turn, already pseudonymised.

    It exists so masking and restoring are always a pair. Calling the LLM with
    masked text and forgetting to restore would show the lead "[NOME_1]" on
    screen; the `start_turn` / `finish_turn` pair makes that hard to get wrong.
    """

    def __init__(self, lead_id, message, original_message, history, context, mapping):
        self.lead_id = lead_id
        self.message = message
        self.original_message = original_message
        self.history = history
        self.context = context
        self.mapping = mapping


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

class ConversationMemory:

    def __init__(self, store=None, clock=None, pseudonymizer=None,
                 retention_days=DEFAULT_RETENTION_DAYS):
        self.store = store if store is not None else InMemoryStore()
        # Injectable clock: without it there is no way to test retention
        # without waiting 180 days.
        self.clock = clock or _utc_now
        self.pseudo = pseudonymizer or Pseudonymizer()
        self.retention_days = retention_days

    # -- state --------------------------------------------------------------

    def _new_state(self, lead_id):
        now = _iso(self.clock())
        return {
            "version": STATE_VERSION,
            "lead_id": lead_id,
            "created_at": now,
            "updated_at": now,
            # Two different dates on purpose. `ultima_interacao_em` includes the
            # agent's messages and serves retention. `ultima_mensagem_lead_em`
            # only advances when the LEAD writes, and it is what measures
            # silence: without that separation, sending a follow-up would reset
            # the very counter that decides whether to send one.
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
            # Consecutive without a lead reply: resets when they speak again.
            # This is what drives cadence and the "pushing too hard" alert.
            "followups_sent": 0,
            # Lifetime total, never resets. For reporting, not decisions.
            "followups_total": 0,
            "last_followup_at": None,
        }

    def state(self, lead_id):
        """The lead's state, creating an empty one if it does not exist yet."""
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

    # -- messages -----------------------------------------------------------

    def record_message(self, lead_id, role, content):
        """Store a message. `role` is 'user' or 'assistant'."""
        if role not in ("user", "assistant"):
            raise ValueError("invalid role: %r" % (role,))

        state = self.state(lead_id)
        now = _iso(self.clock())

        state["messages"].append({"role": role, "content": content, "at": now})
        state["last_interaction_at"] = now

        if role == "user":
            state["last_lead_message_at"] = now
            # The lead is talking again: the follow-up ladder restarts. They are
            # conversing, not running away, and the next nudge should use the
            # short interval again. `followups_total` keeps the history.
            state["followups_sent"] = 0

        return self._save(lead_id, state)

    def history(self, lead_id, window=DEFAULT_WINDOW, mask=False):
        """History in the shape `chamar_agente()` expects.

        Person 1 consumes `[{"role": ..., "content": ...}]`, so that is the
        shape returned, not the internal one.
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
        # New aliases must be persisted, otherwise the same e-mail gets a
        # different alias each turn and the LLM thinks they are different people.
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

    # -- profile ------------------------------------------------------------

    def _known_names(self, state):
        name = state.get("profile", {}).get("name")
        return [name] if is_known(name) else []

    def update_profile(self, lead_id, collected_data):
        """Merge Person 1's `dados_coletados` into the accumulated profile.

        Returns the list of changes, each with `tipo` 'novo' or 'correcao'. The
        broker wants to see corrections: "the lead raised the budget from 500k
        to 800k" is a buying signal, not noise.

        Unknown values NEVER overwrite known ones. That is the reason memory
        exists: Person 1's extraction is stateless and can regress between calls.
        """
        state = self.state(lead_id)
        now = _iso(self.clock())
        changes = []

        # Person 1's Portuguese keys are translated here, once, at the edge.
        # `from_agent` also passes our own keys through, so an already
        # translated profile can be merged again safely.
        for field, value in lead_profile.from_agent(collected_data).items():
            previous = state["profile"].get(field)

            if not is_known(previous):
                kind = "new"
            elif str(previous) != str(value):
                kind = "correction"
            else:
                continue

            state["profile"][field] = value
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
        """Session alias map, for callers that need to restore text.

        Used by the summarizer and the follow-up generator, which send masked
        conversation to the LLM and must undo the aliases on the way back.
        """
        return dict(self.state(lead_id).get("alias_map", {}))

    def known_fields(self, lead_id):
        return sorted(f for f, v in self.profile(lead_id).items() if is_known(v))

    # -- properties already shown -------------------------------------------

    def record_shown_properties(self, lead_id, ids):
        """Store what has already been presented.

        Serves two consumers: follow-up, which needs to say "that apartment in
        Botafogo I sent you", and search, which should not repeat the same three
        options every turn.
        """
        state = self.state(lead_id)
        already_seen = state["shown_properties"]

        for property_id in ids:
            if property_id not in already_seen:
                already_seen.append(property_id)

        return self._save(lead_id, state)

    def shown_properties(self, lead_id):
        return list(self.state(lead_id)["shown_properties"])

    # -- incremental summary ------------------------------------------------

    def needs_summary(self, lead_id):
        """True when too much old conversation sits outside the summary."""
        state = self.state(lead_id)
        unsummarized = len(state["messages"]) - state["summarized_up_to"]
        return unsummarized > SUMMARY_THRESHOLD

    def messages_to_summarize(self, lead_id):
        """Messages not yet covered by the summary, minus the live window."""
        state = self.state(lead_id)
        return state["messages"][state["summarized_up_to"]:-DEFAULT_WINDOW]

    def set_summary(self, lead_id, text, up_to_index):
        """Store the summary produced by the summarizer."""
        state = self.state(lead_id)
        state["summary"] = text
        state["summarized_up_to"] = max(0, min(up_to_index, len(state["messages"])))
        return self._save(lead_id, state)

    def summary(self, lead_id):
        return self.state(lead_id)["summary"]

    # -- follow-up ----------------------------------------------------------

    def hours_of_silence(self, lead_id):
        """Hours since the LEAD's last message.

        Agent messages do not count: what matters is how long the lead has been
        quiet. `ultima_mensagem_lead_em` uses `.get` with a fallback so states
        written before this field existed remain readable.
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

    # -- context for the prompt ---------------------------------------------

    def build_context(self, lead_id, mask=True):
        """Long-memory text block to inject into the prompt.

        Complements the short history, it does not replace it. This is what
        Person 1's agent would receive as `contexto_extra`.
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
        """Build the block in the clear. Callers apply pseudonymisation.

        Split out from `build_context` so `start_turn` can mask history,
        message and context against ONE accumulated map.

        The text is Portuguese: it is read by the LLM next to a Portuguese
        prompt.
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

        # Pointing out what is missing only helps once something is known. On a
        # brand new lead it would be the whole list, redundant with Person 1's
        # system prompt and pure token cost.
        if known:
            missing = [lead_profile.FIELD_LABELS[f].lower() for f in PROFILE_FIELDS
                       if f not in known and f not in ("email", "phone")]
            if missing:
                lines.append("AINDA FALTA DESCOBRIR: " + ", ".join(missing) + ".")

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

    # -- one turn -----------------------------------------------------------

    def start_turn(self, lead_id, message, window=DEFAULT_WINDOW):
        """Record the lead's message and return everything pseudonymised.

        History, message and context are masked against the SAME accumulated
        map, and the complete map comes back in the turn. Masking each piece
        with its own map would make the LLM reply come back with aliases that
        `finish_turn` could not undo, and the lead would see "[EMAIL_2]".
        """
        self.record_message(lead_id, "user", message)

        state = self.state(lead_id)
        mapping = state.get("alias_map", {})

        # Known names PLUS the ones declared in this message. Without the second
        # half, the lead's name would go to the LLM in the clear in exactly the
        # message where they introduce themselves.
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
        """Restore the reply, store it and merge the profile. Returns the text.

        A non-obvious integration detail: because the LLM received masked text,
        Person 1's extraction runs over placeholders and returns `"[EMAIL_1]"`
        in the e-mail field, or `"undefined"` because its regex does not
        recognise the placeholder. So two things happen here: aliases are undone
        in the extracted values, and e-mail, phone and name are extracted
        directly from the ORIGINAL text, which only we hold.
        """
        restored_reply = self.pseudo.restore(reply, turn.mapping)
        self.record_message(lead_id, "assistant", restored_reply)

        data = dict(collected_data or {})
        for field, value in list(data.items()):
            if isinstance(value, str):
                data[field] = self.pseudo.restore(value, turn.mapping)

        for field, value in _extract_pii(turn.original_message).items():
            if not is_known(data.get(field)):
                data[field] = value

        changes = self.update_profile(lead_id, data)

        return restored_reply, changes

    # -- LGPD ---------------------------------------------------------------

    def record_consent(self, lead_id, granted, purpose):
        """Explicit, informed consent (LGPD art. 8)."""
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
        """Right to erasure. Wipes everything, alias map included."""
        validate_lead_id(lead_id)
        return self.store.delete(lead_id)

    def export(self, lead_id):
        """Right of access and portability: everything we hold, readable."""
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
        """Leads whose last interaction is past the retention window."""
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
        """Storage Limitation: discard what is past the retention window.

        Built to run as a scheduled job by Person 3, alongside the follow-up
        scheduler.
        """
        removed = []
        for lead_id in self.expired(days):
            if self.forget(lead_id):
                removed.append(lead_id)

        return removed

    def log_summary(self, lead_id):
        """One log line with no personal data."""
        state = self.state(lead_id)
        return "lead=%s messages=%d fields=%d followups=%d" % (
            lead_id,
            len(state["messages"]),
            len([v for v in state["profile"].values() if is_known(v)]),
            state["followups_sent"],
        )


def _extract_pii(text):
    """E-mail, phone and name from the text in the clear.

    Needed because Person 1's extraction runs over already-masked text and is
    blind to exactly these fields: its regex does not recognise "[EMAIL_1]" as
    an e-mail.
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
        # First name only, capitalised, to match the convention Person 1 uses in
        # `extrair_nome`.
        found["name"] = names[0].split()[0].capitalize()

    return found


__all__ = [
    "ConversationMemory",
    "InMemoryStore",
    "JsonFileStore",
    "PreparedTurn",
    "mask_for_log",
    "validate_lead_id",
]
