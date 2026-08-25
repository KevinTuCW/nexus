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

from fastapi import APIRouter, Header, HTTPException

from nexus.ingress.auth import AuthError, authenticate
from nexus.state import get_state

#: Endpoints forwarded verbatim. Grow this list deliberately, one measured
#: tenant need at a time.
PASSTHROUGH_PATHS = ("/rerank",)

router = APIRouter()


@router.post("/rerank")
async def rerank(payload: dict, authorization: str = Header(default="")) -> dict:
    state = get_state()
    try:
        authenticate(authorization, state.key_index)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    # Phase 2a has no real provider; the shape is what matters here, and the
    # LiteLLM-backed forward lands in P2b.
    documents = payload.get("documents", [])
    return {
        "results": [
            {"index": i, "relevance_score": 1.0 / (i + 1)}
            for i in range(len(documents))
        ]
    }
