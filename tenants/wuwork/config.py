"""wuwork's own settings.

Deliberately not `nexus.config`. wuwork is a tenant: it knows a base URL and
a key, and nothing whatsoever about how the gateway behind them works. The
duplication is the point — sharing a settings object with the platform would
be the first step towards sharing everything else, and the "cost to onboard"
figure would stop measuring onboarding.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WuworkSettings:
    nexus_base_url: str
    nexus_api_key: str
    answer_model: str = "glm-4.6"
    timeout_s: int = 60

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "WuworkSettings":
        return cls(
            nexus_base_url=env.get("NEXUS_BASE_URL", "http://127.0.0.1:8000/v1"),
            nexus_api_key=env.get("NEXUS_API_KEY", ""),
            answer_model=env.get("WUWORK_ANSWER_MODEL", "glm-4.6"),
            timeout_s=int(env.get("WUWORK_TIMEOUT_S", "60")),
        )
