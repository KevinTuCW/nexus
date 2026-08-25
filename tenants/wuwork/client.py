"""A plain OpenAI-compatible client pointed at nexus.

Thirty lines, no SDK, no platform imports. That is the whole integration
surface, and its size is the number this phase reports.
"""

import httpx

from tenants.wuwork.config import WuworkSettings


class NexusClient:
    def __init__(self, settings: WuworkSettings) -> None:
        if not settings.nexus_api_key.strip():
            raise RuntimeError(
                "NEXUS_API_KEY is empty; refusing to call the gateway. An "
                "anonymous call would be rejected at the far end and read "
                "like an outage rather than a missing credential."
            )
        self._s = settings

    def chat(self, messages: list[dict], model: str | None = None) -> str:
        r = httpx.post(
            f"{self._s.nexus_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {self._s.nexus_api_key}"},
            json={"model": model or self._s.answer_model, "messages": messages},
            timeout=self._s.timeout_s,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
