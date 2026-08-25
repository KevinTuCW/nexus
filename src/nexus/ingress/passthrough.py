"""Non-standard endpoints, forwarded by explicit whitelist.

"OpenAI compatible" is not the same as "every endpoint compatible". helpmate
reranks through `{embed_base_url}/rerank`, a SiliconFlow extension that has
no place in the OpenAI surface — and because helpmate is integrated
zero-touch, it cannot be asked to stop calling it. A gateway that implements
only `/v1/chat/completions` breaks that tenant's retrieval on integration
day, which is the sort of thing that gets a platform reverted.

The whitelist is explicit rather than a catch-all proxy. A gateway that
forwards whatever it is handed is an open relay into the provider account it
holds credentials for.

Passthrough calls are **not billed**. Rerank is not priced per token, so
putting it through the token ledger would mean inventing a figure — and an
invented figure is worse than a documented gap, because reconciliation would
then agree with it. The gap is stated in the README.
"""

import os

from fastapi import APIRouter, Header, HTTPException

from nexus.config import get_settings
from nexus.ingress.auth import AuthError, authenticate
from nexus.state import get_state

#: Endpoints forwarded verbatim. Grow this list deliberately, one measured
#: tenant need at a time.
PASSTHROUGH_PATHS = ("/rerank",)

router = APIRouter()


def _post(url: str, json: dict, headers: dict, timeout: int):
    """Seam for tests. Real HTTP lives behind this one function."""
    import httpx

    return httpx.post(url, json=json, headers=headers, timeout=timeout)


@router.post("/rerank")
async def rerank(payload: dict, authorization: str = Header(default="")) -> dict:
    state = get_state()
    try:
        authenticate(authorization, state.key_index)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    settings = get_settings()
    provider_key = os.environ.get(settings.rerank_api_key_env, "").strip()
    if not provider_key:
        # Never fabricate a ranking. helpmate would take a made-up order as
        # a real one and its retrieval would degrade with nothing to show
        # for it -- worse than a visible outage, because a visible outage
        # gets fixed.
        raise HTTPException(
            status_code=503,
            detail=(
                f"{settings.rerank_api_key_env} is not set; "
                "refusing to invent a ranking"
            ),
        )

    try:
        response = _post(
            url=f"{settings.rerank_base_url.rstrip('/')}/rerank",
            # Forwarded verbatim, model included: which reranker to use is
            # the tenant's decision. Rerank has no price table, so a
            # substitution here would be both unbilled and invisible.
            json=payload,
            # The tenant authenticates to nexus; nexus authenticates to the
            # provider. Forwarding the tenant's key would hand every tenant
            # a provider credential.
            headers={"Authorization": f"Bearer {provider_key}"},
            timeout=settings.upstream_timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 - any transport failure is a 502
        raise HTTPException(
            status_code=502, detail=f"rerank upstream failed: {exc}"
        ) from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"rerank upstream returned {response.status_code}",
        )
    return response.json()
