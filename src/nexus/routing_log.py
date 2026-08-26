"""A bounded record of routing decisions, including the refused ones.

Gate G1 refuses an impermissible substitution by raising, and the request
ends with a 500. Without this log that refusal leaves no trace anywhere a
person will look — and a gate whose interventions are invisible is a gate
that only exists in a logfile, which is to say nowhere.

Bounded on purpose: an unbounded in-memory log is a slow leak that looks
like a feature until the process is old enough to matter.

Vetoes are held apart from accepted routes so eviction cannot take them.
Accepted routes are constant and dull; vetoes are rare and are the only
reason to open this panel. A single ring buffer would drop exactly the
interesting ones and leave behind a tidy record of everything that went
right.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class RoutingEvent:
    tenant: str
    requested: str
    routed: str
    vetoed: bool
    reason: str
    ts: datetime


class RoutingLog:
    def __init__(self, capacity: int = 500) -> None:
        self._capacity = capacity
        self._accepted: deque[RoutingEvent] = deque(maxlen=capacity)
        self._vetoed: deque[RoutingEvent] = deque(maxlen=capacity)

    def record(
        self, tenant: str, requested: str, routed: str, vetoed: bool, reason: str
    ) -> None:
        event = RoutingEvent(
            tenant=tenant,
            requested=requested,
            routed=routed,
            vetoed=vetoed,
            reason=reason,
            ts=datetime.now(timezone.utc),
        )
        (self._vetoed if vetoed else self._accepted).append(event)

    def events(self) -> list[RoutingEvent]:
        """Newest-last, vetoes and accepted routes interleaved by time.

        Each deque already enforces its own `capacity` independently, which
        is the whole point of keeping the two apart -- re-slicing the merged
        result down to a single `capacity` here would undo that: with
        `capacity` accepted events and `capacity` vetoed events both in
        range, the accepted ones are newer by construction (vetoes are rare
        and old), so a further `[-capacity:]` truncation would silently
        drop every veto again.
        """
        return sorted(list(self._accepted) + list(self._vetoed), key=lambda e: e.ts)
