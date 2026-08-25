"""Canonical token accounting.

`Usage` holds **disjoint** counts: `prompt_tokens` covers only tokens billed
at the base input rate, and cache reads and cache writes are counted apart
from it, never inside it.

This normalisation exists because the two dominant vendor conventions
disagree about that very question:

  - OpenAI-style — `prompt_tokens` is the total, and
    `prompt_tokens_details.cached_tokens` is a subset already contained in it.
  - Anthropic-style — `input_tokens` excludes both
    `cache_creation_input_tokens` and `cache_read_input_tokens`.

Feeding an OpenAI payload to the Anthropic-shaped adapter double-counts
every cached token; the reverse under-counts. Neither raises, both leave a
ledger that reconciles against itself, and gate G2 stays green while the
invoice drifts. Keeping the conventions apart in code, and asserting they
converge in tests, is the only defence.
"""

from dataclasses import dataclass

from nexus.money import Price


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    def __post_init__(self) -> None:
        for name in (
            "prompt_tokens",
            "completion_tokens",
            "cache_write_tokens",
            "cache_read_tokens",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} is negative: {getattr(self, name)}")


def from_openai_payload(payload: dict) -> Usage:
    """Normalise an OpenAI-style `usage` object.

    `cached_tokens` is subtracted out of `prompt_tokens` because it is
    already inside it.
    """
    total_prompt = int(payload.get("prompt_tokens", 0))
    details = payload.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens", 0))
    if cached > total_prompt:
        raise ValueError(
            f"cached_tokens ({cached}) exceeds prompt_tokens ({total_prompt}); "
            "this payload is not OpenAI-shaped — clamping would hide the "
            "wrong adapter and under-bill silently"
        )
    return Usage(
        prompt_tokens=total_prompt - cached,
        completion_tokens=int(payload.get("completion_tokens", 0)),
        cache_read_tokens=cached,
    )


def from_anthropic_payload(payload: dict) -> Usage:
    """Normalise an Anthropic-style `usage` object.

    The cache fields are already disjoint from `input_tokens`, so nothing is
    subtracted. Doing so here is the mirror-image bug of not doing it above.
    """
    return Usage(
        prompt_tokens=int(payload.get("input_tokens", 0)),
        completion_tokens=int(payload.get("output_tokens", 0)),
        cache_write_tokens=int(payload.get("cache_creation_input_tokens", 0)),
        cache_read_tokens=int(payload.get("cache_read_input_tokens", 0)),
    )


def cost_nanousd(usage: Usage, price: Price) -> int:
    """Integer nano-USD cost. Each token kind bills at its own rate."""
    return (
        usage.prompt_tokens * price.prompt
        + usage.completion_tokens * price.completion
        + usage.cache_write_tokens * price.cache_write
        + usage.cache_read_tokens * price.cache_read
    )
