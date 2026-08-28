"""
Indexer for the property base.

Turns `shared/data/imoveis.json` into a searchable index: for each property it
builds a weighted search text and computes its embedding.

Conceptual equivalent in Azure Cognitive Search (lesson 04):

    create_index()       -> schema.AZURE_FIELD_MAP
    upload_documents()   -> build_index()
    scheduled indexer    -> reindex() driven by a job

Command line:

    python -m src.rag.indexer                    # use whatever is available
    python -m src.rag.indexer --embedder hashing # force the offline path
"""

import json
import os

from . import schema
from .embeddings import cosine, get_embedder, load_env

# Paths relative to the repository root.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PROPERTIES_PATH = os.path.join(ROOT, "shared", "data", "imoveis.json")
INDEX_PATH = os.path.join(ROOT, "ai-memory-rag", "data", "index", "imoveis_index.json")

INDEX_VERSION = 1


def build_search_text(prop):
    """Build the text that represents a property in vector space.

    More discriminating fields are repeated, which is the local analogue of
    Azure's per-field boosting (`search_fields=["title^3", "description"]`).
    Repeating a token increases its mass in the vector.
    """
    parts = []

    for field, weight in schema.TEXT_WEIGHTS.items():
        value = prop.get(field)

        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        if not value:
            continue

        parts.extend([str(value)] * weight)

    # The zone is always added so that "zona sul" retrieves properties in
    # Copacabana even when the listing never writes "zona sul".
    if prop.get("zone"):
        parts.append(str(prop["zone"]))

    # Bedrooms in textual form: a lead writes "3 quartos", not "quartos: 3".
    bedrooms = prop.get("bedrooms")
    if bedrooms:
        parts.append("%d quartos" % bedrooms)

    if prop.get("deal_type") == "RENTAL":
        parts.append("aluguel alugar locacao")
    else:
        parts.append("venda comprar compra")

    return " . ".join(parts)


class Index:
    """In-memory index. Small on purpose: the base holds dozens of items."""

    def __init__(self, embedder_name, dim, documents, vectors, version=INDEX_VERSION):
        if len(documents) != len(vectors):
            raise ValueError("documents and vectors have different lengths")

        self.embedder_name = embedder_name
        self.dim = dim
        self.documents = documents
        self.vectors = vectors
        self.version = version
        self._by_id = {d["id"]: d for d in documents}

    def __len__(self):
        return len(self.documents)

    def by_id(self, property_id):
        return self._by_id.get(property_id)

    def similarities(self, query_vector, allowed_ids=None):
        """Return [(property, similarity)] for the allowed ids.

        `allowed_ids` is the output of the structured filters. Restricting
        before computing cosine avoids wasted work and, more importantly,
        guarantees a hard filter is never overridden by textual similarity.
        """
        results = []

        for document, vector in zip(self.documents, self.vectors):
            if allowed_ids is not None and document["id"] not in allowed_ids:
                continue
            results.append((document, cosine(query_vector, vector)))

        results.sort(key=lambda pair: pair[1], reverse=True)
        return results

    def facets(self, field):
        """Count by field value, like Azure's facets.

        Feeds the broker dashboard directly (Part 4).
        """
        counts = {}

        for document in self.documents:
            value = document.get(field)
            values = value if isinstance(value, list) else [value]
            for v in values:
                if v is None:
                    continue
                counts[v] = counts.get(v, 0) + 1

        return dict(sorted(counts.items(), key=lambda pair: -pair[1]))

    def to_dict(self):
        return {
            "version": self.version,
            "embedder": self.embedder_name,
            "dim": self.dim,
            "total": len(self.documents),
            "documents": self.documents,
            # Six decimals are plenty for cosine and cut the file by ~40%.
            "vectors": [[round(v, 6) for v in vector] for vector in self.vectors],
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            embedder_name=data["embedder"],
            dim=data["dim"],
            documents=data["documents"],
            vectors=data["vectors"],
            version=data.get("version", INDEX_VERSION),
        )


def load_properties(path=None):
    path = path or PROPERTIES_PATH

    if not os.path.isfile(path):
        raise FileNotFoundError(
            "Property base not found at %s. "
            "Run: python ai-memory-rag/scripts/generate_properties.py" % path
        )

    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    properties = data["properties"] if isinstance(data, dict) else data

    problems = []
    for prop in properties:
        for problem in schema.validate_property(prop):
            problems.append("%s: %s" % (prop.get("id", "?"), problem))

    if problems:
        raise ValueError(
            "Invalid property base (%d problems):\n  %s"
            % (len(problems), "\n  ".join(problems[:20]))
        )

    return properties


def build_index(properties, embedder):
    texts = [build_search_text(prop) for prop in properties]
    vectors = embedder.embed_documents(texts)

    return Index(
        embedder_name=embedder.name,
        dim=len(vectors[0]) if vectors else embedder.dim,
        documents=list(properties),
        vectors=vectors,
    )


def save_index(index, path=None):
    path = path or INDEX_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(index.to_dict(), handle, ensure_ascii=False)

    return path


def load_index(path=None):
    path = path or INDEX_PATH

    if not os.path.isfile(path):
        raise FileNotFoundError(
            "Index not found at %s. Run: python -m src.rag.indexer" % path
        )

    with open(path, "r", encoding="utf-8") as handle:
        return Index.from_dict(json.load(handle))


def reindex(embedder=None, properties_path=None, index_path=None):
    """Rebuild the index from scratch. This is the hook for a scheduled job."""
    embedder = embedder or get_embedder()
    properties = load_properties(properties_path)
    index = build_index(properties, embedder)
    destination = save_index(index, index_path)

    return index, destination


def _main():
    import argparse

    parser = argparse.ArgumentParser(description="Index the property base.")
    parser.add_argument(
        "--embedder",
        choices=["gemini", "hashing"],
        default=None,
        help="force an embedder; by default tries Gemini and falls back to hashing",
    )
    args = parser.parse_args()

    load_env()
    embedder = get_embedder(prefer=args.embedder)

    index, destination = reindex(embedder=embedder)

    print("Index built: %d properties, embedder=%s, dim=%d"
          % (len(index), index.embedder_name, index.dim))
    print("Saved to: %s" % destination)
    print("Facets by zone: %s" % index.facets("zone"))
    print("Facets by deal type: %s" % index.facets("deal_type"))


if __name__ == "__main__":
    _main()
