"""The OpenAI-compatible surface.

Phase 1 implements `/v1/chat/completions` against a fake upstream, wired
through auth, family lookup and the ledger. Routing, diversity enforcement
and fallback arrive in P2; this file exists now so those land on a path that
already meters and reconciles.

Note what is deliberately absent: streaming. It needs the real upstream to
be meaningful, and the hard part of streaming — settling an aborted stream —
is already covered deterministically by `nexus.ledger.session`.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request

from nexus.ingress.auth import AuthError, authenticate
from nexus.ledger.book import Entry
from nexus.ledger.session import meter
from nexus.registry.families import family_of
from nexus.state import get_state
from nexus.upstream import PRICES, UnpricedModel

router = APIRouter()


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str = Header(default="")):
    state = get_state()
    try:
        tenant = authenticate(authorization, state.key_index)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    body = await request.json()
    model = body.get("model", "")
    messages = body.get("messages", [])

    price = PRICES.get(model)
    if price is None:
        raise HTTPException(status_code=400, detail=f"no price on file for model '{model}'")

    call_id = f"call-{uuid.uuid4().hex[:12]}"
    settlements: list = []

    try:
        with meter(call_id, price, settlements.append) as session:
            completion = state.upstream.complete(call_id, model, messages)
            session.observe(completion.usage)
    except UnpricedModel as exc:  # pragma: no cover - guarded above
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    settlement = settlements[0]
    state.ledger.record(
        Entry(
            entry_id=f"e-{uuid.uuid4().hex[:12]}",
            call_id=call_id,
            tenant=tenant,
            # Zero-touch tenants send no workload label; "default" is
            # honest about that rather than inventing a breakdown.
            workload=body.get("nexus_workload", "default"),
            trace_root=body.get("nexus_trace_root"),
            span_id=call_id,
            parent_span_id=None,
            model=model,
            family=family_of(model),
            usage=settlement.usage,
            cost_nanousd=settlement.cost_nanousd,
            status=settlement.status,
            ts=datetime.now(timezone.utc),
        )
    )

    return {
        "id": call_id,
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": completion.content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": settlement.usage.prompt_tokens,
            "completion_tokens": settlement.usage.completion_tokens,
            "total_tokens": settlement.usage.prompt_tokens
            + settlement.usage.completion_tokens,
        },
    }
