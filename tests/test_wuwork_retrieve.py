import pytest

from tenants.wuwork.retrieve import Corpus, hash_embed


def test_hash_embedding_is_deterministic():
    # The gate must produce the same numbers on every machine and every run.
    # A retrieval score that moves with the weather cannot be a baseline.
    assert hash_embed("报销标准") == hash_embed("报销标准")


def test_different_text_gives_different_vectors():
    assert hash_embed("报销标准") != hash_embed("年假天数")


def test_embedding_has_the_declared_dimension():
    assert len(hash_embed("x")) == 256


@pytest.fixture
def corpus():
    return Corpus.load()


def test_corpus_loads_every_document(corpus):
    assert len(corpus.documents) == 6


def test_retrieval_finds_the_right_document(corpus):
    hits = corpus.search("出差住宿费怎么报销", top_k=2)
    assert hits[0].doc_id == "expense-reimbursement"


def test_retrieval_is_not_trivially_correct(corpus):
    # If every query returned the same document, the test above would pass
    # by accident. This one fails the moment retrieval degenerates.
    assert corpus.search("VPN 连不上怎么办", top_k=1)[0].doc_id == "vpn-access"
    assert corpus.search("年假能不能跨年", top_k=1)[0].doc_id == "annual-leave"


def test_top_k_is_respected(corpus):
    assert len(corpus.search("审批人是谁", top_k=3)) == 3
