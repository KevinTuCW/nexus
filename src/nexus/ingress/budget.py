"""Turning the budget policy into an HTTP refusal, in one place.

Both billed surfaces — `/v1/chat/completions` and `/v1/embeddings` — check
the same budget the same way. Embeddings are included deliberately: helpmate
ingests its whole corpus through that endpoint, so leaving it unchecked would
mean the single largest way to spend a tenant's money is the one the budget
does not cover.

`429` rather than `402` or `403`: the tenant is not unauthorised and nothing
is owed, the allowance for this window is used up and returns at the next
one. `Retry-After` carries that window, so a client can wait the right amount
instead of hammering.
"""

from datetime import datetime, timezone

from fastapi import HTTPException

from nexus.ledger.book import Ledger
from nexus.policy.quota import (
    MIN_CALL_NANOUSD,
    check_budget,
    day_start,
    next_day_start,
)
from nexus.registry.tenants import TenantPolicy


def enforce_budget(ledger: Ledger, policy: TenantPolicy) -> None:
    """Refuse the call if this tenant has spent its day's allowance.

    Raises `HTTPException(429)`; returns None when the call may proceed.
    """
    now = datetime.now(timezone.utc)
    spent = ledger.spent_since(policy.tenant, day_start(now))
    decision = check_budget(
        spent_today=spent,
        incoming_estimate=MIN_CALL_NANOUSD,
        budget=policy.budget_nanousd_per_day,
    )
    if decision.allowed:
        return
    retry_after = int((next_day_start(now) - now).total_seconds())
    raise HTTPException(
        status_code=429,
        detail=(
            f"tenant '{policy.tenant}': {decision.reason}. "
            + (
                "This tenant's budget is 0, which means switched off, not "
                "unlimited."
                if policy.budget_nanousd_per_day == 0
                else "The allowance resets at the next UTC day."
            )
        ),
        headers={"Retry-After": str(retry_after)},
    )
