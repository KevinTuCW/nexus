"""Short-form question answering over the internal corpus.

Three decisions worth naming.

**Retrieved text is put in front of the model, and the sources come back
with the answer.** A pipeline that retrieves and then ignores what it
retrieved is a plain chat call with extra latency; nothing downstream would
notice, and the answer would look exactly as authoritative.

**With too little lexical overlap, wuwork says it does not know.** An
internal assistant answering HR questions from a model's own priors produces
confident policy advice with no policy behind it — and staff have no way to
tell the difference. Refusing is the cheaper failure.

**That refusal is decided on raw word overlap, not on the retrieval score.**
The hashed cosine ranks documents well and calibrates not at all: measured
on this corpus, irrelevant questions outscored relevant ones — "竞争对手给多
少薪资" reached 0.2718 against a relevant-question floor of 0.1689. Hash
collisions are why: they let bigrams that never appear in a document
contribute to its score. Counting literal overlap keeps exactly the
information the hash discards.
"""

import re
from dataclasses import dataclass

from tenants.wuwork.retrieve import Corpus

#: How many distinct query bigrams must literally occur in a document before
#: wuwork is willing to answer from it. Measured on the shipped corpus:
#: relevant questions match 2-4, irrelevant ones 0-1.
MIN_QUERY_BIGRAM_HITS = 2

_CORPUS: Corpus | None = None


def _corpus() -> Corpus:
    global _CORPUS
    if _CORPUS is None:
        _CORPUS = Corpus.load()
    return _CORPUS


def _lexical_hits(question: str, text: str) -> int:
    """How many distinct query bigrams literally occur in `text`."""
    cleaned = re.sub(r"\s+", "", question)
    grams = {cleaned[i : i + 2] for i in range(max(len(cleaned) - 1, 0))}
    return sum(1 for g in grams if g in text)


@dataclass(frozen=True)
class Answer:
    answer: str
    sources: list[str]


def answer(question: str, client, top_k: int = 2) -> Answer:
    hits = [
        h
        for h in _corpus().search(question, top_k=top_k)
        if _lexical_hits(question, h.text) >= MIN_QUERY_BIGRAM_HITS
    ]
    if not hits:
        return Answer(answer="没有找到相关的内部制度文档。", sources=[])
    context = "\n\n".join(f"[{h.doc_id}]\n{h.text}" for h in hits)
    reply = client.chat(
        [
            {
                "role": "system",
                "content": "你是集团内部制度助手。只根据提供的制度文档回答，"
                "文档没写的就说没写。",
            },
            {"role": "user", "content": f"制度文档：\n{context}\n\n问题：{question}"},
        ]
    )
    return Answer(answer=reply, sources=[h.doc_id for h in hits])
