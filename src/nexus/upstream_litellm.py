"""The real provider adapter, satisfying the `Upstream` protocol.

Two design points carry gate G2 across the move from a fake provider to a
real one.

**`charges()` is derived from the raw response payload, not from the
normalised `Usage`.** With the fake upstream, reconciliation compared two
genuinely independent numbers. A real provider gives only one — the usage
block in its response — so if both the ledger and the charge went through
`from_openai_payload()`, a bug in that function would cancel out on both
sides and G2 would stay green while every invoice drifted. Pricing the raw
fields separately keeps the most likely real bug — the OpenAI/Anthropic
cache-token convention mix-up — detectable.

What this does *not* buy: independence from the provider itself. If the
provider under-reports, nexus reproduces the under-report faithfully. A
genuinely independent source would be the provider's billing API, which this
phase does not integrate. That gap is recorded in the README.

**Provider failures become `UpstreamUnavailable`.** The policy layer already
knows how to walk a fallback chain on that exception; leaking a
provider-specific error type would make each new provider a change to the
request handler.
"""

import os
from typing import Callable, Iterator, Optional

from nexus.ledger.book import UpstreamCharge
from nexus.ledger.usage import from_openai_payload
from nexus.providers import endpoint_for
from nexus.upstream import PRICES, Completion, UpstreamUnavailable


def _default_completion_fn(**kwargs):
    # Imported lazily so that `import nexus.app` does not pull in LiteLLM.
    # `tests/test_no_network.py` asserts exactly that.
    import litellm

    return litellm.completion(**kwargs)


def _default_stream_fn(**kwargs):
    import litellm

    return litellm.completion(stream=True, **kwargs)


class LiteLLMUpstream:
    """Talks to real providers through LiteLLM's OpenAI-compatible transport."""

    def __init__(
        self,
        completion_fn: Optional[Callable] = None,
        stream_fn: Optional[Callable] = None,
        timeout_s: int = 60,
    ) -> None:
        self._completion_fn = completion_fn or _default_completion_fn
        self._stream_fn = stream_fn or _default_stream_fn
        self._timeout_s = timeout_s
        self._charges: list[UpstreamCharge] = []

    def _call_kwargs(self, model: str, messages: list[dict]) -> dict:
        endpoint = endpoint_for(model)
        key = os.environ.get(endpoint.api_key_env, "").strip()
        if not key:
            raise RuntimeError(
                f"{endpoint.api_key_env} is not set; refusing to call "
                f"'{model}'. An unauthenticated attempt would fail at the "
                "provider and look like an outage, sending the request down "
                "the fallback chain for the wrong reason."
            )
        return {
            "model": endpoint.litellm_model,
            "messages": messages,
            "api_base": endpoint.api_base,
            "api_key": key,
            "timeout": self._timeout_s,
        }

    def _book(self, call_id: str, model: str, raw_usage: dict) -> None:
        """Price the provider's own figures, straight from the raw payload."""
        price = PRICES[model]
        details = raw_usage.get("prompt_tokens_details") or {}
        cached = int(details.get("cached_tokens", 0))
        base_prompt = int(raw_usage.get("prompt_tokens", 0)) - cached
        cost = (
            base_prompt * price.prompt
            + int(raw_usage.get("completion_tokens", 0)) * price.completion
            + cached * price.cache_read
        )
        self._charges.append(
            UpstreamCharge(call_id=call_id, model=model, cost_nanousd=cost)
        )

    def complete(self, call_id: str, model: str, messages: list[dict]) -> Completion:
        kwargs = self._call_kwargs(model, messages)
        try:
            response = self._completion_fn(**kwargs)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
            raise UpstreamUnavailable(f"{model}: {exc}") from exc
        payload = response.model_dump()
        raw_usage = payload.get("usage") or {}
        self._book(call_id, model, raw_usage)
        content = payload["choices"][0]["message"].get("content") or ""
        return Completion(content=content, usage=from_openai_payload(raw_usage))

    def stream(self, call_id: str, model: str, messages: list[dict]) -> Iterator[str]:
        """Yield content deltas, booking whatever the provider actually gave.

        Two accounting paths, because the provider only offers one of them
        reliably. A stream that runs to completion carries a final usage
        frame; that is authoritative and is what gets booked. A stream the
        client abandons carries no such frame, so the fallback is the number
        of deltas seen — explicitly a lower bound, since a delta is not a
        token. `reconcile()` knows the difference and asserts a bound rather
        than an equality for those rows.
        """
        kwargs = self._call_kwargs(model, messages)
        kwargs["stream_options"] = {"include_usage": True}
        try:
            stream = self._stream_fn(**kwargs)
        except Exception as exc:  # noqa: BLE001 - see complete()
            raise UpstreamUnavailable(f"{model}: {exc}") from exc

        prompt_tokens = sum(len(m.get("content", "")) for m in messages) or 1
        seen_deltas = 0
        final_usage: dict | None = None
        try:
            for chunk in stream:
                payload = chunk.model_dump()
                if payload.get("usage"):
                    final_usage = payload["usage"]
                    continue
                delta = (payload.get("choices") or [{}])[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    seen_deltas += 1
                    yield content
        finally:
            self._book(
                call_id,
                model,
                final_usage
                or {"prompt_tokens": prompt_tokens, "completion_tokens": seen_deltas},
            )

    def charges(self) -> list[UpstreamCharge]:
        return list(self._charges)
