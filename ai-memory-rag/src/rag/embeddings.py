"""
Embedding layer for the RAG.

Two implementations, one interface:

  GeminiEmbedder   real semantic embeddings via `gemini-embedding-001`.
                   Needs GEMINI_API_KEY and network access.

  HashingEmbedder  deterministic fallback, fully offline, zero dependencies.
                   It is NOT semantic: it is a lexical projection (hashing of
                   unigrams and bigrams, in the spirit of HashingVectorizer).
                   It exists so tests and demos run with no API key and no
                   internet, and so nobody on the team has to install anything
                   to see the module work. It does well on "apartamento 3
                   quartos na zona sul", because the domain vocabulary is small
                   and repetitive; it fails on synonyms ("perto do mar" vs
                   "vista para a praia"), and that is where Gemini earns its
                   place.

`get_embedder()` picks between them, preferring Gemini when a key is available.

Why the interface separates documents from queries: `gemini-embedding-001`
accepts a `task_type`, and using RETRIEVAL_DOCUMENT when indexing and
RETRIEVAL_QUERY when searching measurably improves retrieval quality. An
embedder with a single `embed()` method could not make that distinction.
"""

import hashlib
import math
import os
import re
import unicodedata

# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

_NON_ALPHANUM = re.compile(r"[^a-z0-9]+")

# Words that show up in almost every listing and therefore discriminate
# nothing. Kept in Portuguese because that is the language of the corpus.
_STOPWORDS = {
    "a", "ao", "aos", "as", "com", "da", "das", "de", "do", "dos", "e",
    "em", "na", "nas", "no", "nos", "o", "os", "para", "por", "que", "se",
    "sem", "sob", "sobre", "um", "uma", "uns", "umas", "ou", "the",
}


def normalize(text):
    """Lowercase, strip accents. Makes "Gávea" and "gavea" the same token."""
    if not text:
        return ""

    decomposed = unicodedata.normalize("NFKD", str(text))
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_accents.lower()


def tokenize(text):
    """Return the useful unigrams and bigrams of a text."""
    cleaned = _NON_ALPHANUM.sub(" ", normalize(text))
    words = [w for w in cleaned.split() if len(w) > 1 and w not in _STOPWORDS]

    tokens = list(words)
    # Bigrams capture domain phrases that lose meaning when split:
    # "zona sul", "vista mar", "portaria 24h", "3 quartos".
    for previous, following in zip(words, words[1:]):
        tokens.append(previous + "_" + following)

    return tokens


# ---------------------------------------------------------------------------
# Vector algebra (pure Python; the base is small, numpy is not needed)
# ---------------------------------------------------------------------------

def l2_normalize(vector):
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector

    return [v / norm for v in vector]


def cosine(a, b):
    """Cosine similarity. Robust to non-normalized vectors."""
    if len(a) != len(b):
        raise ValueError("vectors of different dimensions: %d != %d" % (len(a), len(b)))

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


# ---------------------------------------------------------------------------
# Offline lexical embedder
# ---------------------------------------------------------------------------

class HashingEmbedder:
    """Deterministic lexical projection, no network and no dependencies.

    Uses hashlib rather than the built-in `hash()` because Python randomises
    string hashing per process (PYTHONHASHSEED), which would make an index
    saved to disk disagree with queries from a later run.
    """

    name = "hashing-v1"

    def __init__(self, dim=512):
        self.dim = dim

    def _vectorize(self, text):
        counts = {}
        for token in tokenize(text):
            counts[token] = counts.get(token, 0) + 1

        vector = [0.0] * self.dim
        for token, n in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            h = int.from_bytes(digest, "big")
            index = h % self.dim
            sign = 1.0 if (h >> 17) & 1 else -1.0
            # Sublinear tf: the tenth mention of "varanda" is not worth ten
            # times the first.
            vector[index] += sign * (1.0 + math.log(n))

        return l2_normalize(vector)

    def embed_documents(self, texts):
        return [self._vectorize(t) for t in texts]

    def embed_query(self, text):
        return self._vectorize(text)


# ---------------------------------------------------------------------------
# Gemini embedder
# ---------------------------------------------------------------------------

class GeminiEmbedder:
    """Semantic embeddings via the Gemini API.

    The SDK import is lazy on purpose: someone who only wants to run the
    offline tests should not need the package installed.
    """

    name = "gemini-embedding-001"

    def __init__(self, api_key=None, model="gemini-embedding-001", dim=768, batch=32):
        self.model = model
        self.dim = dim
        self.batch = batch

        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not found. Use HashingEmbedder to run offline."
            )

        from google import genai  # lazy import

        self._genai = genai
        self._client = genai.Client(api_key=api_key)

    def _embed(self, texts, task_type):
        vectors = []

        for start in range(0, len(texts), self.batch):
            chunk = texts[start:start + self.batch]
            vectors.extend(self._call(chunk, task_type))

        return vectors

    def _call(self, chunk, task_type):
        # Different google-genai versions accept `config` differently. Try with
        # task_type (better quality) and fall back to the minimal call if the
        # installed SDK does not support it.
        try:
            from google.genai import types

            response = self._client.models.embed_content(
                model=self.model,
                contents=chunk,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=self.dim,
                ),
            )
        except Exception:
            response = self._client.models.embed_content(
                model=self.model,
                contents=chunk,
            )

        return [l2_normalize(list(e.values)) for e in response.embeddings]

    def embed_documents(self, texts):
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text):
        return self._embed([text], "RETRIEVAL_QUERY")[0]


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def load_env(path=".env"):
    """Read a simple .env file without depending on python-dotenv.

    Does not overwrite variables already present in the environment, which is
    what dotenv does and what makes sense in CI.
    """
    if not os.path.isfile(path):
        return

    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def get_embedder(prefer=None, api_key=None):
    """Return the best available embedder.

    prefer="gemini"   force Gemini and fail loudly if unavailable.
    prefer="hashing"  force the offline path.
    prefer=None       try Gemini, fall back to hashing without breaking.
    """
    if prefer == "hashing":
        return HashingEmbedder()

    if prefer == "gemini":
        return GeminiEmbedder(api_key=api_key)

    try:
        return GeminiEmbedder(api_key=api_key)
    except Exception as error:
        print("[rag] Gemini unavailable (%s). Falling back to HashingEmbedder." % error)
        return HashingEmbedder()
