"""Cross-business-line operations digest.

The one wuwork capability that needs data belonging to other tenants, and
therefore the one that had to be authorised rather than assumed. It reads
usage through nexus under an explicit `cross_tenant_read` grant.

**A refused read produces a refusal, not a smaller digest.** A group digest
that silently covers one business line instead of five is still titled
"group", and it gets taken into a meeting. An error does not.

Amounts pass through untouched. wuwork formats nano-USD itself — it may not
import `nexus.money`, and the isolation test enforces that — but formatting
is not rounding: a digest that disagrees with the ledger it came from is
worse than no digest, because someone will reconcile the two and trust the
wrong one.
"""

from dataclasses import dataclass, field

NANO_PER_USD = 1_000_000_000


def format_usd(nanousd: int) -> str:
    """Same rendering as the platform's, reimplemented rather than imported."""
    return f"${nanousd / NANO_PER_USD:.6f}"


@dataclass(frozen=True)
class Digest:
    summary: str
    by_tenant: dict[str, int] = field(default_factory=dict)
    refused: bool = False


def build_digest(usage_client, chat_client, tenants: tuple[str, ...]) -> Digest:
    try:
        payload = usage_client.get_usage(tenants)
    except PermissionError:
        return Digest(summary="", by_tenant={}, refused=True)

    # Every business line asked for appears, including the ones that spent
    # nothing. `/v1/usage` builds `by_tenant` from ledger rows, so a line with
    # no traffic today is simply absent from the payload -- and the digest
    # then printed four bullets under a heading that says five. That is the
    # failure `refused` exists to prevent, arriving through the door nobody
    # was watching: silence and zero are not the same fact, and only one of
    # them is worth reporting to a group meeting as "no spend".
    by_tenant = {name: 0 for name in tenants}
    by_tenant.update(payload.get("by_tenant", {}))
    lines = "\n".join(
        f"- {name}: {format_usd(cost)}" for name, cost in sorted(by_tenant.items())
    )
    summary = chat_client.chat(
        [
            {
                "role": "system",
                "content": "你是集团运营日报助手。只根据给出的用量数据写摘要，"
                "不要推测原因，不要补充数据里没有的业务线。",
            },
            {"role": "user", "content": f"各业务线今日 AI 支出：\n{lines}"},
        ]
    )
    return Digest(summary=summary, by_tenant=dict(by_tenant))
