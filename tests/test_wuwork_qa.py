from tenants.wuwork.config import WuworkSettings  # noqa: F401  (import shape check)
from tenants.wuwork.qa import answer


class _FakeClient:
    def __init__(self, reply="按标准报销，需直属主管审批。"):
        self.reply = reply
        self.seen = None

    def chat(self, messages, model=None):
        self.seen = messages
        return self.reply


def test_answer_grounds_on_retrieved_documents():
    client = _FakeClient()
    result = answer("出差住宿费怎么报销", client)
    assert result.answer
    assert result.sources[0] == "expense-reimbursement"
    # The retrieved text must actually reach the model. A "RAG" pipeline
    # that retrieves and then ignores the result is a plain chat call with
    # extra latency, and nothing downstream would notice.
    joined = " ".join(m["content"] for m in client.seen)
    assert "报销" in joined


def test_answer_reports_which_documents_it_used():
    result = answer("VPN 连不上怎么办", _FakeClient())
    assert "vpn-access" in result.sources


def test_no_relevant_document_is_said_so_not_guessed():
    # Below the overlap floor, wuwork says it does not know. Answering
    # anyway from the model's own priors would produce confident HR advice
    # with no policy behind it -- the failure mode an internal assistant is
    # most likely to be trusted through.
    result = answer("公司股价明天会涨吗", _FakeClient())
    assert result.sources == []
    assert "没有找到" in result.answer


def test_a_question_about_a_competitor_is_also_refused():
    # This one scored *higher* than every relevant question under the
    # hashed cosine (0.2718). It is the case that killed the score-threshold
    # design, so it gets its own test rather than living only in a commit
    # message.
    result = answer("竞争对手给多少薪资", _FakeClient())
    assert result.sources == []


def test_refusal_does_not_call_the_model():
    # Nothing to ground on means nothing to ask. Sending the question
    # anyway would bill the tenant for an answer that must be discarded.
    client = _FakeClient()
    answer("巴西的首都是哪里", client)
    assert client.seen is None
