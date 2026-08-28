"""
Pseudonymisation of personal data before it reaches the LLM.

The concrete problem this solves: Gemini is a third party. Every piece of text
the agent sends leaves our control. Today `ai-core/src/agent.py` concatenates
the whole conversation and ships it, lead e-mail and phone number included.
Under the LGPD that is processing personal data with transfer to a third party,
and the Necessity and Minimisation principles say only the minimum required for
the purpose should leave.

The technique is PSEUDONYMISATION, not anonymisation, and the difference
matters. Lesson 04 of "Privacidade e Proteção de Dados" warns that AI can
re-identify anonymised data by combining datasets; and practically, simply
deleting the name would ruin the humanised conversation, which is a grading
criterion. So:

    "Oi, meu nome é João, meu email é joao@x.com"
        -> mask ->
    "Oi, meu nome é [NOME_1], meu email é [EMAIL_1]"        (this goes to the LLM)
        -> LLM replies ->
    "Claro, [NOME_1]! Vou enviar as opções para [EMAIL_1]."
        -> restore ->
    "Claro, João! Vou enviar as opções para joao@x.com."    (this goes to the lead)

The substitution map never leaves our machine. The LLM works with opaque
references and the lead is still addressed by name.

Usage:

    pseudo = Pseudonymizer()
    safe, mapping = pseudo.mask(lead_text)
    reply = call_llm(safe)
    final = pseudo.restore(reply, mapping)

To keep the same alias across conversation turns, pass the previous map:

    safe, mapping = pseudo.mask(new_message, mapping=session_mapping)

NOTE ON LANGUAGE: identifiers and comments are in English; the alias tags
([NOME_1], [EMAIL_1]) stay in Portuguese because they appear inside Portuguese
prompts and the model reads them as part of that text.
"""

import re

# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------
# Order matters: the most specific pattern matches first, otherwise an e-mail
# becomes half a phone number. E-mail comes first because it contains digits;
# CPF and CEP come before phone because their shapes are more constrained.
#
# What is deliberately NOT detected:
#   * an unformatted CPF (11 bare digits) is ambiguous with a mobile number
#     including area code. It is treated as a phone, the likelier reading in a
#     real estate conversation.
#   * CEP requires the hyphen. Without it, 8 bare digits would match property
#     prices.
PATTERNS = (
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("CPF", re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")),
    ("CEP", re.compile(r"\b\d{5}-\d{3}\b")),
    ("TELEFONE", re.compile(
        r"(?:\+55\s?)?\(?\d{2}\)?[\s.-]?9?\d{4}[\s.-]?\d{4}\b"
    )),
)

_ALIAS = re.compile(r"\[([A-Z]+)_(\d+)\]")

# Very short names are not masked: the risk of replacing "Ana" inside
# "Ananindeua" is real, but the bigger risk is masking two-letter particles and
# shredding the whole text.
MIN_NAME_LENGTH = 3

# A proper name is not detectable by regex in general, but in a service
# conversation it almost always arrives DECLARED: "meu nome é X", "sou o X".
# Requiring a capital initial in the capture discards most false positives
# ("sou casado", "sou de niterói" in lowercase do not match).
#
# This exists for a specific reason: the lead's name only becomes known AFTER
# they introduce themselves, and without detection on the way in it would go to
# the LLM in the clear in exactly the message that reveals it, which is the one
# that most needs protecting.
#
# The trigger ("meu nome é", "sou o") matches case-insensitively via the scoped
# `(?i:...)` flag; the capture does NOT, because the capital initial is what
# separates "Sou Marina" from "sou casado". Compiling the whole pattern with
# IGNORECASE would lose that distinction; compiling with no ignore-case at all
# would miss "Sou João" at the start of a sentence, the most common case.
_NAME_CAPTURE = r"([A-ZÀ-Ý][\wÀ-ÿ]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ]+)*)"

_NAME_DECLARATIONS = (
    re.compile(r"(?i:(?:meu\s+)?nome\s+[ée])\s+" + _NAME_CAPTURE),
    re.compile(r"(?i:\bchamo[-\s]me)\s+" + _NAME_CAPTURE),
    re.compile(r"(?i:\b(?:eu\s+)?sou\s+(?:[oa]\s+)?)" + _NAME_CAPTURE),
    re.compile(r"(?i:\baqui\s+[ée]\s+(?:[oa]\s+)?)" + _NAME_CAPTURE),
)


def detect_names(text):
    """Proper names declared in the text.

    Returns the full name and also each part with 3+ letters, so that a later
    mention of just the first name is masked too. The first name comes first in
    the list, because that is the form used when restoring: "Prazer, João!"
    reads better than "Prazer, João Pereira!" in a WhatsApp conversation.
    """
    if not text:
        return []

    found = []
    for pattern in _NAME_DECLARATIONS:
        for match in pattern.finditer(text):
            full = match.group(1).strip()
            if len(full) < MIN_NAME_LENGTH:
                continue

            parts = full.split()
            for candidate in [parts[0], full] + parts[1:]:
                if len(candidate) >= MIN_NAME_LENGTH and candidate not in found:
                    found.append(candidate)

    return found


def _next_alias(mapping, kind):
    """Generate the next free alias of a kind, based on the existing map."""
    used = []
    for alias in mapping:
        match = _ALIAS.fullmatch(alias)
        if match and match.group(1) == kind:
            used.append(int(match.group(2)))

    return "[%s_%d]" % (kind, max(used) + 1 if used else 1)


class Pseudonymizer:
    """Replaces personal data with stable, reversible aliases."""

    def mask(self, text, mapping=None, names=None):
        """Return `(masked_text, mapping)`.

        `mapping` is `{alias: original_value}`. Pass the session map so the same
        e-mail gets the same alias on every turn, otherwise the LLM sees
        [EMAIL_1] and [EMAIL_2] for one person and gets confused.

        `names` is the list of known proper names for the lead. Names are not
        reliably detectable by regex, but we already have them in the profile,
        so they are masked by exact match.
        """
        if text is None:
            return "", dict(mapping or {})

        mapping = dict(mapping or {})
        # Reverse index so an already-seen value reuses its alias.
        by_value = {value: alias for alias, value in mapping.items()}
        result = text

        for kind, pattern in PATTERNS:
            def substitute(match, kind=kind):
                value = match.group(0)
                if value in by_value:
                    return by_value[value]

                alias = _next_alias(mapping, kind)
                mapping[alias] = value
                by_value[value] = alias
                return alias

            result = pattern.sub(substitute, result)

        # Every variant received belongs to the SAME person: memory is
        # per-lead, and `names` carries the profile name plus whatever was
        # declared in the message. Without this, "João Pereira", "João" and
        # "Pereira" would become [NOME_1], [NOME_2] and [NOME_3], and the LLM
        # would conclude they are three different people.
        variants = [
            name for name in (names or [])
            if name and len(name) >= MIN_NAME_LENGTH and name != "undefined"
        ]

        if variants:
            alias = next((by_value[v] for v in variants if v in by_value), None)

            if alias is None:
                alias = _next_alias(mapping, "NOME")
                # The first variant is the preferred form when restoring, and
                # `detect_names` puts the first name at the front.
                mapping[alias] = variants[0]

            for variant in variants:
                by_value[variant] = alias

            # Longest first, otherwise "João" would consume the first half and
            # leave "[NOME_1] Pereira" behind.
            for variant in sorted(set(variants), key=lambda n: (-len(n), n)):
                result = re.sub(
                    r"\b%s\b" % re.escape(variant), alias, result, flags=re.IGNORECASE
                )

        return result, mapping

    def restore(self, text, mapping):
        """Swap the aliases back for the real values.

        Applied to the LLM reply before it reaches the lead. Aliases the model
        invented that are not in the map are stripped, so the lead never sees a
        stray "[EMAIL_7]" on screen.
        """
        if text is None:
            return ""

        result = text
        for alias, value in (mapping or {}).items():
            result = result.replace(alias, value)

        return _ALIAS.sub("", result)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def contains_pii(text):
    """Kinds of personal data present in the text. Useful in tests and audits."""
    if not text:
        return []

    return sorted({kind for kind, pattern in PATTERNS if pattern.search(text)})


def mask_for_log(text, names=None):
    """IRREVERSIBLE masking, for logs and telemetry.

    Logs are the classic leak: they sit on disk, ship to an aggregator, and
    nobody applies retention to them. There is no map to undo this, on purpose.
    """
    if not text:
        return ""

    result = text
    for kind, pattern in PATTERNS:
        result = pattern.sub("[%s]" % kind, result)

    for name in (names or []):
        if name and len(name) >= MIN_NAME_LENGTH and name != "undefined":
            result = re.sub(
                r"\b%s\b" % re.escape(name), "[NOME]", result, flags=re.IGNORECASE
            )

    return result
