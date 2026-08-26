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

Phase 3c — the code repo is complete.

Routing, gate G1's diversity guard, gate G4's fallback chain, streaming
metering, non-standard endpoint passthrough, a Postgres ledger and Langfuse
tracing are in. Real providers are reached through LiteLLM and are
**opt-in** (`UPSTREAM=litellm`); the default stays on a deterministic fake,
so a fresh clone cannot bill anyone for running `make test` — and the
console carries a banner saying which one is in use, because a console
pointed at the fake looks exactly like one pointed at real providers.

**All four gates run under `python -m nexus.eval` and exit 2 on any
violation.** Each has a `--fail-demo` that injects the failure it exists to
catch, so "this gate can fail" is a command you can run rather than a claim
in a commit message.

Two incumbent tenants have been integrated and measured without a single
line changed in their repos — see `docs/integration-helpmate.md` and
`docs/integration-shopscout.md`. The latter documents a deliberate attack on
G1: unpinning three jury models collapsed a three-lab jury into three copies
of one model, cut the bill by 91%, and produced no error of any kind.

The conformance runner has been pointed at all four incumbent repos: two
yield full metric baselines, one yields pass/fail only, and one exceeds a
conformance-pass time budget. `docs/wuwork.md` records what each one did,
failures included.

## Costs, and why they are three numbers

| | lines | the question it answers |
|---|---|---|
| onboarding | **57** | what a new business line pays to reach the gateway at all |
| reuse, tenant side | **73** | what the *next* team pays to build on another line's data |
| reuse, platform side | **121** | paid once; the second tenant to want this pays none of it |

Adding them together answers none of the three. The platform figure is also
the honest price of making reuse safe rather than merely convenient: without
the grant and the audit trail, crossing a tenant boundary would have cost a
tenant nothing and cost the group its isolation.

## Running tests

`make test` is offline and hermetic. `make test-live` loads `.env` into the
shell and additionally runs the tests marked `live`, which reach real
providers and a real database. Reaching real providers should be something
you typed, not something you inherited.

## Running

```bash
python3.12 -m venv .venv
make install
make test          # offline, hermetic
make run           # dev server on :8000
python -m nexus.eval           # the four gates; exit 2 on any violation
python -m nexus.eval --fail-demo g1   # ...and proof that one can fail
```

Or with Postgres:

```bash
docker compose up --build
# console at http://localhost:8000/console?key=dev-wuwork
```

The image runs its own test suite at build time in a stage that has `git`
and `make` — the isolation and conformance tests shell out to both — and
ships a runtime stage that has neither.

## Stated blanks

- aura's on-device token usage never reaches the ledger: the edge never crosses this gateway, and back-filling it would require editing the tenant repo, which the zero-touch contract forbids.
- For zero-touch tenants, attribution is tenant/workload level — call-chain-level attribution holds only for the native tenant.
- Reconciliation against a real provider is not independent. The provider's response is the only figure available, so `charges()` prices the **raw** payload while the ledger prices the normalised one — enough to catch a normalisation bug, not enough to catch a provider that under-reports. A genuinely independent source would be the provider's billing API, which is not integrated.
- For streams the client abandons, no usage frame arrives, so the ledger books the number of content deltas seen. A delta is not a token: rows with `status = 'aborted'` are a **lower bound**, and reconciliation asserts a bound rather than an equality for them. Over-billing an aborted call is still reported.
- Passthrough endpoints (`/rerank`) are forwarded but not billed. Rerank is not priced per token; putting it through the token ledger would mean inventing a figure, and reconciliation would then confirm the invention. The gap is recorded here instead.
- The audit trail lives in memory. `cross_tenant_read_audit` exists in `db/schema.sql` but nothing writes to it yet, so the record of who read whose usage dies with the process. An audit trail that answers questions only until the next restart is worth saying out loud.
- helpmate's gate belongs to G3's live arm, not the offline one. It runs 53 golden cases through real retrieval and generation against a live database, which is longer than a conformance pass should block for. The offline arm does not cover it.
- The conformance runner cannot leave a tenant checkout untouched, only put it back. Running helpmate's gate rewrites `eval/report.md`; the runner refuses to start against an already-dirty checkout, restores tracked files afterwards, and lists untracked ones without deleting them. See `## The zero-touch contract` for what the claim narrowed to and why.
