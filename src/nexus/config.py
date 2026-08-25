"""Runtime configuration for nexus, loaded from environment / .env.

Deliberately does NOT hold the model-family table. Its single source of
truth is `nexus.registry.families` — two copies would drift in spelling and
silently weaken gate G1 (routing must not collapse a tenant's declared
model diversity). This is the same reason medscope keeps CRITICAL_LABELS
out of its Settings.

There is no `env_prefix`: the four incumbent tenant repos have none either,
and matching their convention keeps the integration story uniform.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    policies_dir: Path = Path("policies")
    upstream_timeout_s: int = 60
    # "fake" | "litellm". Opt-in on purpose: a default of "litellm" would
    # mean a fresh clone running `make test` could reach real providers and
    # bill someone real money.
    upstream: str = "fake"
    # Rerank is a provider extension, not part of the OpenAI surface, so it
    # gets its own base and key rather than riding on the model registry.
    rerank_base_url: str = "https://api.siliconflow.com/v1"
    rerank_api_key_env: str = "SILICONFLOW_API_KEY"
    # Empty means the in-memory ledger. Persisting is opt-in for the same
    # reason reaching real providers is: a fresh clone should not need a
    # database to run its tests.
    database_url: str = ""
    # Money is stored as integer nano-USD everywhere; see nexus.money for
    # why cents cannot be used. Exposed as a setting only so the value is
    # discoverable, not so it can be changed.
    default_currency_unit: str = "nanousd"


def get_settings() -> Settings:
    return Settings()
