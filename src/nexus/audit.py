"""Who read whose usage, and when.

Deliberately records no amounts. An audit trail exists to answer "who looked
at what", and a copy of the figures themselves would be the same tenant data
again, sitting behind whatever access control the audit table happens to
have — which is usually looser than the original's, because audit tables get
read by more people.

Denied attempts are recorded alongside successful ones. An investigation
starts from the refusals, and a trail containing only successes is one in
which nothing was ever refused.
"""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class AuditRecord:
    caller: str
    targets: tuple[str, ...]
    ts: datetime
    denied: bool = False


class InMemoryAudit:
    """Phase 3c's audit sink. Postgres persistence is not wired yet, and the
    README records that as a stated gap: an audit trail that dies with the
    process answers questions only until the next restart."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def record_cross_tenant_read(self, caller: str, targets: tuple[str, ...]) -> None:
        self._records.append(
            AuditRecord(caller=caller, targets=targets, ts=datetime.now(timezone.utc))
        )

    def record_cross_tenant_denial(
        self, caller: str, targets: tuple[str, ...]
    ) -> None:
        self._records.append(
            AuditRecord(
                caller=caller,
                targets=targets,
                ts=datetime.now(timezone.utc),
                denied=True,
            )
        )

    def records(self) -> list[AuditRecord]:
        return list(self._records)
