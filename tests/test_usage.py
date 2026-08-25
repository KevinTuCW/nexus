import pytest

from nexus.ledger.usage import (
    Usage,
    cost_nanousd,
    from_anthropic_payload,
    from_openai_payload,
)
from nexus.money import Price

PRICE = Price(prompt=600, completion=2200, cache_write=750, cache_read=60)


def test_openai_cached_tokens_are_a_subset_and_get_split_out():
    # OpenAI-style: prompt_tokens is the TOTAL; cached_tokens is inside it.
    payload = {
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "prompt_tokens_details": {"cached_tokens": 400},
    }
    u = from_openai_payload(payload)
    assert u.prompt_tokens == 600      # 1000 total - 400 cached
    assert u.cache_read_tokens == 400
    assert u.completion_tokens == 200


def test_anthropic_cache_fields_are_already_disjoint():
    # Anthropic-style: input_tokens EXCLUDES both cache fields.
    payload = {
        "input_tokens": 600,
        "output_tokens": 200,
        "cache_creation_input_tokens": 300,
        "cache_read_input_tokens": 400,
    }
    u = from_anthropic_payload(payload)
    assert u.prompt_tokens == 600      # unchanged, not 600-700
    assert u.cache_write_tokens == 300
    assert u.cache_read_tokens == 400


def test_the_two_conventions_agree_after_normalisation():
    # Same real call, reported by two vendor conventions, must produce the
    # same canonical Usage. Feeding an OpenAI payload through the Anthropic
    # adapter double-counts every cached token and nothing raises; this
    # test is what makes that visible.
    openai = from_openai_payload(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 400},
        }
    )
    anthropic = from_anthropic_payload(
        {"input_tokens": 600, "output_tokens": 200, "cache_read_input_tokens": 400}
    )
    assert openai == anthropic


def test_openai_payload_with_more_cached_than_prompt_is_refused():
    # Physically impossible; almost certainly the wrong adapter was used.
    # Clamping to zero would hide the mistake and under-bill forever.
    with pytest.raises(ValueError):
        from_openai_payload(
            {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "prompt_tokens_details": {"cached_tokens": 400},
            }
        )


def test_cost_bills_each_token_kind_at_its_own_rate():
    u = Usage(
        prompt_tokens=600, completion_tokens=200, cache_write_tokens=300, cache_read_tokens=400
    )
    expected = 600 * 600 + 200 * 2200 + 300 * 750 + 400 * 60
    assert cost_nanousd(u, PRICE) == expected


def test_cost_is_an_int_not_a_float():
    u = Usage(prompt_tokens=1, completion_tokens=1)
    assert isinstance(cost_nanousd(u, PRICE), int)


def test_negative_counts_are_refused():
    with pytest.raises(ValueError):
        Usage(prompt_tokens=-1, completion_tokens=0)
