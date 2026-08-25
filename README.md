# nexus

An OpenAI-compatible AI gateway for the (fictional) Wanmart retail group — 阵 06 of the 武道AI "以阵制胜" series.

## Tenants

| tenant     | integration | what it is                                  |
|------------|-------------|----------------------------------------------|
| helpmate   | zero-touch  | graded routing / support triage              |
| shopscout  | zero-touch  | cross-border product selection + shopping agent |
| wealthwise | zero-touch  | multi-agent financial advisory                |
| aura       | zero-touch  | on-device AI hardware assistant               |
| wuwork     | native      | in-repo tenant, arriving in P3                |

A sixth sibling project, `medscope` (chest-X-ray reading), is deliberately **not** integrated: a system whose hard gates are specialist clinical semantics is one the platform should keep its hands off.

## The zero-touch contract

Incumbent tenant checkouts must have an empty `git status --porcelain` before and after any conformance run; anything that requires editing a tenant repo is not a nexus capability.

## Gates

- **G1** — routing must not collapse declared model diversity.
- **G2** — attribution error is zero.
- **G3** — a tenant's own gates must not regress after integration.
- **G4** — fallback is never silent.

Principle: any gate that can fail the delivery must have a failure mode that has been demonstrated.

## Status

Phase 1: deterministic foundation. No LLM, `FakeUpstream` only, ledger in memory. Routing, diversity enforcement, fallback, streaming and Postgres are P2–P3.

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
