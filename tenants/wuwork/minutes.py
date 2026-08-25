"""Meeting minutes and action-item extraction.

Three rules, each about not inventing things.

**An action item with no owner keeps no owner.** The tempting fix is to
assign the meeting organiser; that manufactures a commitment nobody made,
and the person discovers it when they are chased for it.

**Malformed model output is refused, not repaired.** A half-parsed summary
is indistinguishable from a real one once it is in someone's inbox.

**Transcripts are not truncated to fit.** The tail of a meeting is where
decisions get made, and a summary built from the first half reads exactly as
complete as one built from all of it. If a transcript is too long for the
model, that is a failure to report, not a slice to take quietly.
"""

import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActionItem:
    owner: str | None
    task: str
    due: str | None


@dataclass(frozen=True)
class Minutes:
    summary: str
    action_items: list[ActionItem] = field(default_factory=list)
    parse_failed: bool = False


_SYSTEM = (
    "你是会议纪要助手。只输出 JSON，字段为 summary 与 action_items；"
    "action_items 每项含 owner、task、due。转写里没有明确责任人的，"
    "owner 必须为 null，不要推断，不要指派给会议召集人。"
)


def summarise(transcript: str, client) -> Minutes:
    raw = client.chat(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": transcript},
        ]
    )
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return Minutes(summary="", action_items=[], parse_failed=True)
    items = [
        ActionItem(
            owner=item.get("owner"),
            task=item.get("task", ""),
            due=item.get("due"),
        )
        for item in data.get("action_items", [])
    ]
    return Minutes(summary=data.get("summary", ""), action_items=items)
