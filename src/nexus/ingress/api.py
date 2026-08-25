"""The OpenAI-compatible surface.

`/v1/chat/completions`, wired through auth, routing, the diversity guard,
the fallback chain, the ledger and a trace span — in that order, and the
order is load-bearing: the guard runs before any provider is called, and the
trace is opened only after the ledger row exists, so a trace can never
describe a call the books do not.

Streaming is implemented in `nexus.upstream_litellm` and metered by
`nexus.ingress.streaming`, but it is not exposed on this endpoint yet: no
tenant asks for `stream: true` through nexus today, and an untested response
path is worse than an absent one.
"""

import uuid
from dataclasses import replace
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request

from nexus.ingress.auth import AuthError, authenticate
from nexus.ledger.book import Entry
from nexus.ledger.session import meter
from nexus.obs import span
from nexus.policy.diversity import DiversityExhausted, DiversityViolation, guard
from nexus.policy.fallback import fallback_chain
from nexus.policy.routing import choose
from nexus.registry.families import family_of
from nexus.state import get_state
from nexus.upstream import PRICES, UpstreamUnavailable

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

    if model not in PRICES:
        raise HTTPException(status_code=400, detail=f"no price on file for model '{model}'")

    policy = state.policies[tenant]
    decision = choose(policy, model, PRICES)
    try:
        guard(policy, decision)
    except DiversityViolation as exc:
        # The router proposed something the tenant pinned. Refusing the
        # request is the honest outcome: serving the requested model anyway
        # would hide a routing bug behind a correct-looking answer.
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    group_id = body.get("nexus_diversity_group")
    if group_id is not None:
        if policy.integration != "native":
            raise HTTPException(
                status_code=400,
                detail=(
                    "diversity groups require native integration; a zero-touch "
                    "tenant's call structure is not visible to nexus, and "
                    "accepting a group id would imply otherwise"
                ),
            )
        candidates = (decision.model, *fallback_chain(policy, decision, PRICES))
        try:
            served = state.groups.reserve(group_id, list(candidates))
        except DiversityExhausted as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        decision = replace(
            decision, model=served, substituted=served != decision.requested
        )
        # Re-guarded on purpose. Group selection is itself a substitution,
        # and skipping the guard here would open a G1 bypass along the
        # "group" path specifically.
        #
        # Honest note: this call cannot fail today. Candidates are drawn
        # from `fallback_chain`, which only yields models the policy already
        # permits, so `guard` has nothing to reject -- deleting this line
        # turns no test red. It stays as defence against a future change to
        # how candidates are built, which is exactly the kind of change that
        # happens quietly. It is an assertion, not a gate, and is not
        # dressed up as one.
        guard(policy, decision)

    attempts = (decision.model, *fallback_chain(policy, decision, PRICES))
    last_error: Exception | None = None
    for served_model in attempts:
        call_id = f"call-{uuid.uuid4().hex[:12]}"
        settlements: list = []
        try:
            with meter(call_id, PRICES[served_model], settlements.append) as session:
                completion = state.upstream.complete(call_id, served_model, messages)
                session.observe(completion.usage)
        except UpstreamUnavailable as exc:
            # Not recorded in the ledger: a failed attempt produced no
            # upstream charge, so a row for it would surface immediately as
            # an orphan_entry and turn gate G2 red.
            last_error = exc
            continue
        break
    else:
        raise HTTPException(
            status_code=503,
            detail=(
                f"no usable model for '{model}': {last_error}. "
                + (
                    "This tenant forbids fallback."
                    if not policy.allow_fallback
                    else "Every permitted alternative failed."
                )
            ),
        )

    settlement = settlements[0]
    fallback_from = decision.model if served_model != decision.model else None

    state.ledger.record(
        Entry(
            entry_id=f"e-{uuid.uuid4().hex[:12]}",
            call_id=call_id,
            tenant=tenant,
            workload=body.get("nexus_workload", "default"),
            trace_root=body.get("nexus_trace_root"),
            span_id=call_id,
            parent_span_id=None,
            model=served_model,
            family=family_of(served_model),
            usage=settlement.usage,
            cost_nanousd=settlement.cost_nanousd,
            status=settlement.status,
            ts=datetime.now(timezone.utc),
            fallback_from=fallback_from,
        )
    )

    # Traced after the ledger row exists, so a trace can never describe a
    # call the books do not. Metadata only -- `messages` is never passed,
    # and SAFE_ATTRS would drop it if someone added it here later.
    with span(
        "chat.completion",
        tenant=tenant,
        workload=body.get("nexus_workload", "default"),
        requested_model=model,
        served_model=served_model,
        family=family_of(served_model),
        substituted=decision.substituted,
        fallback_from=fallback_from,
        status=settlement.status,
        cost_nanousd=settlement.cost_nanousd,
    ):
        pass

    payload = {
        "id": call_id,
        "object": "chat.completion",
        "model": served_model,
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
    if fallback_from is not None:
        # Gate G4: present on the response *and* in the ledger row above.
        payload["nexus_fallback"] = {
            "from": fallback_from,
            "to": served_model,
            "reason": str(last_error),
        }
    return payload
