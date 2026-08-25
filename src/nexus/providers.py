"""Model id -> transport. Deliberately one-way.

A model id like `zai/glm-4.6` is the key gate G1 counts diversity by: it is
what `nexus.registry.families` maps to a weight family. Transport details —
which host answers, which key opens it — must never feed back into that id.
Moving a checkpoint to a different provider should change `api_base` and
nothing else; if it changed the id, `family_of()` would return something
different and a diversity verdict would move with it, silently.

That is why this table exists separately from both FAMILIES and PRICES, and
why `tests/test_providers.py` asserts the three stay in step: a model that
can be billed but not reached is a routing candidate the gateway will pick
and then fail on.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoint:
    #: What LiteLLM should be told to call. The `openai/` prefix selects
    #: LiteLLM's OpenAI-compatible transport, which is what every provider
    #: here speaks; the part after it is the provider's own model name.
    litellm_model: str
    api_base: str
    api_key_env: str


ENDPOINTS: dict[str, Endpoint] = {
    "zai/glm-4.6": Endpoint(
        litellm_model="openai/glm-4.6",
        api_base="https://api.z.ai/api/paas/v4",
        api_key_env="GLM_API_KEY",
    ),
    "zai/glm-4.7": Endpoint(
        litellm_model="openai/glm-4.7",
        api_base="https://api.z.ai/api/paas/v4",
        api_key_env="GLM_API_KEY",
    ),
    "siliconflow/Qwen/Qwen3-8B": Endpoint(
        litellm_model="openai/Qwen/Qwen3-8B",
        api_base="https://api.siliconflow.com/v1",
        api_key_env="SILICONFLOW_API_KEY",
    ),
    "siliconflow/Qwen/Qwen3-235B-A22B": Endpoint(
        litellm_model="openai/Qwen/Qwen3-235B-A22B",
        api_base="https://api.siliconflow.com/v1",
        api_key_env="SILICONFLOW_API_KEY",
    ),
    "dashscope/qwen3-235b-a22b": Endpoint(
        litellm_model="openai/qwen3-235b-a22b",
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
    ),
    "siliconflow/deepseek-ai/DeepSeek-V3": Endpoint(
        litellm_model="openai/deepseek-ai/DeepSeek-V3",
        api_base="https://api.siliconflow.com/v1",
        api_key_env="SILICONFLOW_API_KEY",
    ),
}


#: Tenant-facing model names -> canonical ids.
#:
#: Zero-touch integration means the tenants cannot be asked to rename
#: anything. helpmate says `glm-4.7` because that is what z.ai calls it, and
#: `Qwen/Qwen3-8B` because that is what SiliconFlow calls it. If nexus
#: demanded canonical ids on the wire, "integration costs zero lines in the
#: tenant repo" would simply be false -- so speaking the tenant's dialect is
#: the gateway's job, not theirs.
#:
#: Resolution happens at the door, before routing, so everything downstream
#: -- the policy layer, gate G1, the ledger -- only ever sees canonical ids.
#: Two hostings of one checkpoint therefore stay one weight family no matter
#: which name arrived.
ALIASES: dict[str, str] = {
    # helpmate's answer and router models.
    "glm-4.7": "zai/glm-4.7",
    "glm-4.6": "zai/glm-4.6",
    "Qwen/Qwen3-8B": "siliconflow/Qwen/Qwen3-8B",
    # shopscout / wealthwise jury members, as those repos name them.
    "Qwen/Qwen3-235B-A22B": "siliconflow/Qwen/Qwen3-235B-A22B",
    "deepseek-ai/DeepSeek-V3": "siliconflow/deepseek-ai/DeepSeek-V3",
}


def canonical_model(model: str) -> str:
    """Resolve a tenant-facing model name to a canonical id.

    Unknown names come back untouched: the price check downstream produces a
    better error, one that names the model the tenant actually sent rather
    than something this table invented.
    """
    return ALIASES.get(model, model)


def endpoint_for(model: str) -> Endpoint:
    """Transport details for a model id. Raises KeyError if unregistered."""
    return ENDPOINTS[model]
