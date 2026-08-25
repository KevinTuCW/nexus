import json
from pathlib import Path

from tenants.wuwork.minutes import ActionItem, summarise

SAMPLES = Path(__file__).resolve().parent.parent / "tenants" / "wuwork" / "samples"


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.seen = None

    def chat(self, messages, model=None):
        self.seen = messages
        return json.dumps(self.payload, ensure_ascii=False)


def _payload():
    return {
        "summary": "评审了 Q3 结果并决定更换供应商。",
        "action_items": [
            {"owner": "张三", "task": "给出迁移方案", "due": "下周三"},
            {"owner": None, "task": "再评估一次成本", "due": None},
        ],
    }


def test_summary_and_action_items_are_parsed():
    result = summarise("会议转写……", _FakeClient(_payload()))
    assert result.summary
    assert len(result.action_items) == 2
    assert isinstance(result.action_items[0], ActionItem)


def test_an_item_without_an_owner_keeps_no_owner():
    # The tempting fix is to assign the meeting organiser. That invents a
    # commitment nobody made, and the person finds out when they are chased
    # for it.
    result = summarise("会议转写……", _FakeClient(_payload()))
    assert result.action_items[1].owner is None


def test_malformed_model_output_is_refused_not_patched():
    client = _FakeClient({})
    client.chat = lambda messages, model=None: "这不是 JSON"
    result = summarise("会议转写……", client)
    assert result.summary == ""
    assert result.action_items == []
    assert result.parse_failed is True


def test_long_transcripts_are_not_silently_truncated():
    # Dropping the tail of a transcript loses exactly the part where
    # decisions get made, and produces a summary that reads complete.
    long_text = "讨论。\n" * 5000
    client = _FakeClient(_payload())
    summarise(long_text, client)
    sent = " ".join(m["content"] for m in client.seen)
    assert sent.count("讨论。") == 5000


def test_the_real_samples_contain_an_unowned_item():
    # Guards the material, not the code. The no-invented-owner rule can only
    # be exercised end-to-end if a transcript actually contains an item
    # nobody volunteered for -- and a sample rewritten later to look tidier
    # would remove that case without breaking anything else.
    texts = [p.read_text(encoding="utf-8") for p in SAMPLES.glob("meeting-*.txt")]
    assert len(texts) == 2
    vague = ("再看看", "再评估", "回头", "再说")
    assert any(any(v in t for v in vague) for t in texts)
