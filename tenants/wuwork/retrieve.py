"""Retrieval over wuwork's own corpus, with an offline embedder.

The embedder hashes character n-grams into a fixed-width vector. It is not
good, and it is not meant to be: what it is, is **deterministic and
offline**. wuwork's gate is the first thing the conformance runner will be
pointed at, and a gate whose numbers move between machines cannot serve as a
baseline for anything.

Real embeddings through nexus are an upgrade, not a prerequisite. Keeping
the default offline also means the gate stays runnable in CI without
credentials, which is the difference between a gate that runs and a gate
that gets skipped.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

DIM = 256
CORPUS_DIR = Path(__file__).resolve().parent / "corpus"


def _ngrams(text: str, n: int = 2) -> list[str]:
    cleaned = re.sub(r"\s+", "", text)
    return [cleaned[i : i + n] for i in range(max(len(cleaned) - n + 1, 0))]


def hash_embed(text: str) -> list[float]:
    """Hash character bigrams into a fixed-width bag-of-ngrams vector."""
    vec = [0.0] * DIM
    for gram in _ngrams(text):
        h = int(hashlib.sha256(gram.encode("utf-8")).hexdigest(), 16)
        vec[h % DIM] += 1.0
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec] if norm else vec


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass(frozen=True)
class Hit:
    doc_id: str
    score: float
    text: str


@dataclass(frozen=True)
class Document:
    doc_id: str
    text: str
    vector: list[float]


@dataclass
class Corpus:
    documents: list[Document]

    @classmethod
    def load(cls, directory: Path = CORPUS_DIR) -> "Corpus":
        docs = []
        for path in sorted(directory.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            docs.append(Document(path.stem, text, hash_embed(text)))
        return cls(documents=docs)

    def search(self, query: str, top_k: int = 3) -> list[Hit]:
        q = hash_embed(query)
        scored = [
            Hit(d.doc_id, _cosine(q, d.vector), d.text) for d in self.documents
        ]
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]
