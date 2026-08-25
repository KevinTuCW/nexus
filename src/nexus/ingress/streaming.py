"""Metering a stream, including the streams that do not finish.

Tested as a generator rather than through HTTP on purpose. The behaviour
that matters — a client that walks away mid-stream — is the awkward one to
provoke through a test client, and testing it at the transport layer would
make the most important case the least reliably exercised.
"""

from typing import Callable, Iterator

from nexus.ledger.session import Settlement, meter
from nexus.ledger.usage import Usage
from nexus.upstream import PRICES, Upstream


def metered_stream(
    upstream: Upstream,
    call_id: str,
    model: str,
    messages: list[dict],
    sink: Callable[[Settlement], None],
) -> Iterator[str]:
    """Yield upstream chunks while counting them into a settlement.

    The prompt is counted once, before the first chunk. Counting it per
    chunk would multiply a long-context call by the number of chunks: a
    large error, entirely plausible-looking in isolation, and detectable
    only against the provider's own figures.
    """
    prompt_tokens = sum(len(m.get("content", "")) for m in messages) or 1
    with meter(call_id, PRICES[model], sink) as session:
        session.observe(Usage(prompt_tokens=prompt_tokens, completion_tokens=0))
        for chunk in upstream.stream(call_id, model, messages):
            session.add_completion_tokens(1)
            yield chunk
