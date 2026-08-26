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

    def get_usage(self, tenants: tuple[str, ...]) -> dict:
        """Read usage for the named tenants through the gateway.

        A 403 becomes `PermissionError` rather than an HTTP detail: the
        digest's decision is "may I see all of this or not", and leaking the
        transport's vocabulary into that decision would invite someone to
        handle 403 and 404 differently when they mean the same thing here.
        """
        r = httpx.get(
            f"{self._s.nexus_base_url.rstrip('/')}/usage",
            params={"tenants": ",".join(tenants)},
            headers={"Authorization": f"Bearer {self._s.nexus_api_key}"},
            timeout=self._s.timeout_s,
        )
        if r.status_code == 403:
            raise PermissionError(r.text)
        r.raise_for_status()
        return r.json()
