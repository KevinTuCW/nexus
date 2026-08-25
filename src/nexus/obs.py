"""Langfuse tracing, optional and non-fatal.

Two rules.

**Observability never takes the gateway down.** A tracing backend that is
slow, unreachable or misconfigured must cost a trace, never an answer. Every
failure inside this module is swallowed and the caller proceeds untraced —
including a failure while closing the span, which happens after the request
has already succeeded and must not retroactively fail it.

**Traces carry metadata, not prompts.** A trace is a copy of tenant data in a
third-party system, and these tenants include a financial-advisory service
and a customer-support desk. What gets recorded is the routing decision, the
model, the tenant and the cost — enough to answer "why did this cost that",
which is what the platform exists to answer. Message content is not on the
list, and `SAFE_ATTRS` is the list.
"""

import os
from contextlib import contextmanager
from typing import Iterator, Optional

#: The only attribute names this module will send. Adding one is a decision
#: about what leaves the building, so it is made here and nowhere else.
SAFE_ATTRS = (
    "tenant",
    "workload",
    "requested_model",
    "served_model",
    "family",
    "substituted",
    "fallback_from",
    "status",
    "cost_nanousd",
)


def tracing_enabled() -> bool:
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")
    )


def _start_span(name: str, attrs: dict):
    from langfuse import get_client

    return get_client().start_span(name=name, metadata=attrs)


@contextmanager
def span(name: str, **attrs) -> Iterator[Optional[object]]:
    """Open a trace span, or yield None if tracing is off or broken."""
    if not tracing_enabled():
        yield None
        return
    filtered = {k: v for k, v in attrs.items() if k in SAFE_ATTRS}
    handle = None
    try:
        handle = _start_span(name, filtered)
    except Exception:  # noqa: BLE001 - see module docstring
        yield None
        return
    try:
        yield handle
    finally:
        try:
            handle.end()
        except Exception:  # noqa: BLE001
            pass
