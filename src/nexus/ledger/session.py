"""Metering sessions: settle exactly once, including when the call blows up.

Settlement runs in `finally`, never on the success path. When a client
disconnects mid-stream the upstream vendor still bills for every token it
already generated; settling only on completion means nexus absorbs that cost
invisibly. The resulting shortfall scales with traffic, so it reads like a
pricing mistake rather than a missing code path — which is why gate G2's
falsification test is exactly "move settlement out of `finally`".
"""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Literal

from nexus.ledger.usage import Usage, cost_nanousd
from nexus.money import Price

Status = Literal["ok", "aborted", "failed"]


@dataclass(frozen=True)
class Settlement:
    call_id: str
    usage: Usage
    cost_nanousd: int
    status: Status


class MeterSession:
    """Accumulates token counts for one upstream call and settles once."""

    def __init__(self, call_id: str, price: Price, sink: Callable[[Settlement], None]) -> None:
        self._call_id = call_id
        self._price = price
        self._sink = sink
        self._usage = Usage(prompt_tokens=0, completion_tokens=0)
        self._settled = False
        self._produced = False

    def observe(self, usage: Usage) -> None:
        """Record the usage reported by the upstream (non-streaming, or the
        final usage frame of a stream)."""
        if self._settled:
            raise RuntimeError(f"call {self._call_id} already settled")
        self._usage = usage
        if usage.completion_tokens or usage.prompt_tokens:
            self._produced = True

    def add_completion_tokens(self, n: int) -> None:
        """Count tokens seen on the wire during streaming.

        Streams often carry no usage frame until the end — and an aborted
        stream never gets one at all, which is the case this method exists
        for.
        """
        if self._settled:
            raise RuntimeError(f"call {self._call_id} already settled")
        u = self._usage
        self._usage = Usage(
            prompt_tokens=u.prompt_tokens,
            completion_tokens=u.completion_tokens + n,
            cache_write_tokens=u.cache_write_tokens,
            cache_read_tokens=u.cache_read_tokens,
        )
        if n:
            self._produced = True

    def settle(self, status: Status = "ok") -> None:
        """Emit exactly one settlement. Repeat calls are no-ops."""
        if self._settled:
            return
        self._settled = True
        self._sink(
            Settlement(
                call_id=self._call_id,
                usage=self._usage,
                cost_nanousd=cost_nanousd(self._usage, self._price),
                status=status,
            )
        )

    @property
    def produced_tokens(self) -> bool:
        return self._produced


@contextmanager
def meter(
    call_id: str, price: Price, sink: Callable[[Settlement], None]
) -> Iterator[MeterSession]:
    """Run a block with metering that survives exceptions.

    `aborted` and `failed` are kept apart: tokens were produced and billed
    upstream in the first case, and nothing was in the second. Collapsing
    them loses the ability to tell a lost-revenue bug from a connectivity
    blip.
    """
    session = MeterSession(call_id, price, sink)
    try:
        yield session
    except BaseException:
        session.settle("aborted" if session.produced_tokens else "failed")
        raise
    else:
        session.settle("ok")
