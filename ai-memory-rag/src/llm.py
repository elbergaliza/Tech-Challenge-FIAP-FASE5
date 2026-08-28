"""
LLM access, with graceful degradation.

Two modules depend on this: the summarizer and the follow-up generator. Both
follow the same rule: **never fail for lack of an API key**. With no
`GEMINI_API_KEY`, no network or an API outage, callers fall back to a
deterministic heuristic path.

That is not fussiness. The summary shows up on the broker dashboard: if the
Gemini call fails during the presentation, a poorer rule-built summary beats an
error screen. It also lets the tests run offline without burning quota.

Every result records where it came from (`source`), so the interface never
presents rule-written text as if it were AI-written.

On the model id: `ai-core/src/agent.py` uses `gemini-3.6-flash`. I could not
confirm that id exists. Here the default is configurable through `GEMINI_MODEL`,
and the recommendation to the team is to pin ONE verified id in that variable
and have both modules read it.
"""

import json
import os
import re

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class UnavailableClient:
    """No LLM configured. Callers use the heuristic path."""

    name = "unavailable"
    available = False

    def __init__(self, reason="LLM not configured"):
        self.reason = reason

    def generate(self, prompt, temperature=0.4):
        raise RuntimeError(self.reason)


class GeminiClient:

    available = True

    def __init__(self, api_key=None, model=None):
        self.model = model or DEFAULT_MODEL
        self.name = self.model

        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not found")

        from google import genai  # lazy import: offline needs no package

        self._client = genai.Client(api_key=api_key)

    def generate(self, prompt, temperature=0.4):
        try:
            from google.genai import types

            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=temperature),
            )
        except ImportError:
            response = self._client.models.generate_content(
                model=self.model, contents=prompt
            )

        return (response.text or "").strip()


class StubClient:
    """Returns predefined replies. For tests and demos."""

    name = "stub"
    available = True

    def __init__(self, replies):
        self.replies = list(replies) if isinstance(replies, (list, tuple)) else [replies]
        self.calls = []

    def generate(self, prompt, temperature=0.4):
        self.calls.append(prompt)
        if not self.replies:
            raise RuntimeError("StubClient ran out of replies")

        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def get_client(prefer=None, api_key=None, model=None):
    """Return the best available client, without raising."""
    if prefer == "unavailable":
        return UnavailableClient("forced by caller")

    try:
        return GeminiClient(api_key=api_key, model=model)
    except Exception as error:
        return UnavailableClient(str(error))


def extract_json(text):
    """Extract the JSON object from a model reply.

    Models routinely wrap JSON in markdown fences, or preface it with "Here is
    the summary:". Failing over that would be waste, so extraction is tolerant:
    find the first braced block and try to parse it. Returns None when there is
    no valid JSON, and the caller decides the fallback.
    """
    if not text:
        return None

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except (ValueError, TypeError):
        pass

    match = _JSON_BLOCK.search(cleaned)
    if not match:
        return None

    try:
        return json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
