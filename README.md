# nexus

An OpenAI-compatible AI gateway for the (fictional) Wanmart retail group — 阵 06 of the 武道AI "以阵制胜" series.

## Tenants

| tenant     | integration | what it is                                  |
|------------|-------------|----------------------------------------------|
| helpmate   | zero-touch  | graded routing / support triage              |
| shopscout  | zero-touch  | cross-border product selection + shopping agent |
| wealthwise | zero-touch  | multi-agent financial advisory                |
| aura       | zero-touch  | on-device AI hardware assistant               |
| wuwork     | native      | internal-office assistant; onboarding cost = 57 lines |

A sixth sibling project, `medscope` (chest-X-ray reading), is deliberately **not** integrated: a system whose hard gates are specialist clinical semantics is one the platform should keep its hands off.

## The zero-touch contract

Integrating a tenant requires **no edit to its code**: base URLs come from
the environment, and model names are resolved by the gateway rather than
imposed on the caller. That is the claim, and `scripts/verify_tenant.py`
checks it.

It is not a claim that running a tenant's own gate has no side effects.
Evals write reports; helpmate's rewrites `eval/report.md` every run. The
conformance runner therefore refuses to start against an already-dirty
checkout, restores tracked files afterwards, and lists any untracked files
the gate created. An earlier version of this section claimed the checkout
was byte-identical before and after — that was false, and gate G3 is what
proved it.

## Gates

- **G1** — routing must not collapse declared model diversity.
- **G2** — attribution error is zero.
- **G3** — a tenant's own gates must not regress after integration.
- **G4** — fallback is never silent.

Principle: any gate that can fail the delivery must have a failure mode that has been demonstrated.

**G3 has two arms, and they prove different things.** The offline arm runs
`wuwork`'s gate, which never crosses the gateway: it proves wuwork still
works, not that integration changed nothing. The live arm points a real
tenant's gate at nexus and compares against the baseline captured before
integration — that is the actual claim, and it needs real credentials, so it
runs under `make test-live` and is skipped without them.

## Status

Phase 2b. Routing, gate G1's diversity guard, gate G4's fallback chain,
streaming metering, non-standard endpoint passthrough, a Postgres ledger and
Langfuse tracing are all in. Real providers are reached through LiteLLM and
are **opt-in** (`UPSTREAM=litellm`); the default stays on a deterministic
fake so a fresh clone cannot bill anyone for running `make test`.

Two tenants have been integrated and measured without a single line changed
in their repos — see `docs/integration-helpmate.md` and
`docs/integration-shopscout.md`. The latter also documents a deliberate
attack on G1: unpinning three jury models collapsed a three-lab jury into
three copies of one model, cut the bill by 91%, and produced no error of any
kind.

Phase 3a is in: the `wuwork` tenant, its own offline gate, the G3
conformance runner and baseline comparison. The runner has been pointed at
all four incumbent repos — two yield full metric baselines, one yields
pass/fail only, and one exceeds a conformance-pass time budget. See
`docs/wuwork.md`; the failures are recorded rather than tidied away.

Still to come in Phase 3b: the four gates wired to `exit 2` with a
falsification test each, the cross-business-line digest (measured as *reuse*
cost, separately from onboarding), and the FinOps console.

## Running tests

`make test` is offline and hermetic. `make test-live` loads `.env` into the
shell and additionally runs the tests marked `live`, which reach real
providers and a real database. Reaching real providers should be something
you typed, not something you inherited.

## Running

```bash
python3.12 -m venv .venv
make install
make test
make run
```

## Stated blanks

- aura's on-device token usage never reaches the ledger: the edge never crosses this gateway, and back-filling it would require editing the tenant repo, which the zero-touch contract forbids.
- For zero-touch tenants, attribution is tenant/workload level — call-chain-level attribution holds only for the native tenant.
- Reconciliation against a real provider is not independent. The provider's response is the only figure available, so `charges()` prices the **raw** payload while the ledger prices the normalised one — enough to catch a normalisation bug, not enough to catch a provider that under-reports. A genuinely independent source would be the provider's billing API, which is not integrated.
- For streams the client abandons, no usage frame arrives, so the ledger books the number of content deltas seen. A delta is not a token: rows with `status = 'aborted'` are a **lower bound**, and reconciliation asserts a bound rather than an equality for them. Over-billing an aborted call is still reported.
- Passthrough endpoints (`/rerank`) are forwarded but not billed. Rerank is not priced per token; putting it through the token ledger would mean inventing a figure, and reconciliation would then confirm the invention. The gap is recorded here instead.
