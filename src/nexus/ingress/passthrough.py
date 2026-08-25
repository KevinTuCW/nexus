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
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException

from nexus.config import get_settings
from nexus.ingress.auth import AuthError, authenticate
from nexus.ledger.book import Entry
from nexus.ledger.usage import Usage, cost_nanousd
from nexus.registry.families import family_of
from nexus.upstream import EMBEDDING_PRICES
from nexus.state import get_state

#: Endpoints forwarded verbatim. Grow this list deliberately, one measured
#: tenant need at a time.
PASSTHROUGH_PATHS = ("/rerank", "/v1/rerank", "/v1/embeddings")

router = APIRouter()


def _post(url: str, json: dict, headers: dict, timeout: int):
    """Seam for tests. Real HTTP lives behind this one function."""
    import httpx

    return httpx.post(url, json=json, headers=headers, timeout=timeout)


@router.post("/rerank")
@router.post("/v1/rerank")
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


@router.post("/v1/embeddings")
async def embeddings(payload: dict, authorization: str = Header(default="")) -> dict:
    """Forward an embedding request, and bill it.

    The difference from `/rerank` is that the provider reports token counts
    here, so there is a real figure to put in the ledger instead of an
    invented one. Not billing it would leave helpmate's entire corpus
    ingestion invisible in the cost breakdown -- for the tenant whose cost
    attribution is the reason this platform exists.
    """
    state = get_state()
    try:
        tenant = authenticate(authorization, state.key_index)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    model = payload.get("model", "")
    price = EMBEDDING_PRICES.get(model)
    if price is None:
        # Same rule as chat completions: an unpriced model would write a
        # zero-cost row that reconciles perfectly against nothing.
        raise HTTPException(
            status_code=400, detail=f"no price on file for embedding model '{model}'"
        )

    settings = get_settings()
    provider_key = os.environ.get(settings.rerank_api_key_env, "").strip()
    if not provider_key:
        raise HTTPException(
            status_code=503,
            detail=f"{settings.rerank_api_key_env} is not set; refusing to embed",
        )

    try:
        response = _post(
            url=f"{settings.rerank_base_url.rstrip('/')}/embeddings",
            json=payload,
            headers={"Authorization": f"Bearer {provider_key}"},
            timeout=settings.upstream_timeout_s,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"embeddings upstream failed: {exc}"
        ) from exc
    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"embeddings upstream returned {response.status_code}",
        )

    body = response.json()
    usage = Usage(
        prompt_tokens=int((body.get("usage") or {}).get("prompt_tokens", 0)),
        completion_tokens=0,
    )
    call_id = f"emb-{uuid.uuid4().hex[:12]}"
    state.ledger.record(
        Entry(
            entry_id=f"e-{uuid.uuid4().hex[:12]}",
            call_id=call_id,
            tenant=tenant,
            workload="embeddings",
            trace_root=None,
            span_id=call_id,
            parent_span_id=None,
            model=model,
            family=family_of(model),
            usage=usage,
            cost_nanousd=cost_nanousd(usage, price),
            status="ok",
            ts=datetime.now(timezone.utc),
        )
    )
    return body
