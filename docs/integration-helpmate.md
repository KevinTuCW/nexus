# Integrating helpmate, zero-touch

Measured on 2026-08-25 against `helpmate` at `~/ai_projects/helpmate`.
No file in that repo was created, modified or deleted at any point.

## What integration consists of

Two environment variables, supplied to helpmate's process from outside:

```
LLM_BASE_URL={NEXUS}/v1
SILICONFLOW_BASE_URL={NEXUS}/v1
```

That is the whole change. helpmate reads bare-named settings with no
`env_prefix`, so every base URL it uses is overridable from the environment.
**Lines changed in the tenant repo: 0.**

The first covers the answer model. The second covers three separate chains
that all hang off SiliconFlow in helpmate's config — the router model, the
embedding client, and rerank.

## First attempt: all four chains failed

Before any of this worked, a probe sent the four request shapes helpmate
actually produces. Every one failed, for three unrelated reasons:

| helpmate sends | nexus answered | root cause |
|---|---|---|
| `model: "glm-4.7"` | `400 no price on file` | nexus only knew `zai/glm-4.7` |
| `model: "Qwen/Qwen3-8B"` | `400 no price on file` | nexus only knew `siliconflow/Qwen/Qwen3-8B` |
| `POST /v1/embeddings` | `404` | not implemented |
| `POST /v1/rerank` | `404` | implemented at `/rerank` |

The last one is the easiest to miss by reading code instead of running it.
helpmate builds its rerank URL as `{embed_base_url} + "/rerank"`, and that
base already ends in `/v1` — so pointing it at nexus makes it call
`/v1/rerank`, not `/rerank`.

The naming failure is the one that mattered. **Zero-touch means the gateway
speaks the tenant's dialect, not the reverse.** A gateway that requires
canonical model ids on the wire is a gateway that requires a tenant edit,
and "integration costs zero lines" would have been false while every unit
test stayed green — nothing in the suite sends a tenant-shaped model name.

Fixes: `providers.ALIASES` resolves tenant names at the door (before
routing, so gate G1 still only ever sees canonical ids); `/v1/rerank` was
added alongside `/rerank`; `/v1/embeddings` was implemented and **billed**.

## Second attempt: all four chains answer

| chain | endpoint | result |
|---|---|---|
| answer model | `/v1/chat/completions` `glm-4.7` | `200`, served as `zai/glm-4.7` |
| router model | `/v1/chat/completions` `Qwen/Qwen3-8B` | `200`, served as `siliconflow/Qwen/Qwen3-8B` |
| embeddings | `/v1/embeddings` `Qwen/Qwen3-Embedding-8B` | `200`, real vector from SiliconFlow, billed |
| rerank | `/v1/rerank` `Qwen/Qwen3-Reranker-8B` | `200`, real scores from SiliconFlow, unbilled |

## Observable side effects a tenant might notice

- **The `model` field in responses is the canonical id**, not the name the
  tenant sent: helpmate asks for `glm-4.7` and gets `"model": "zai/glm-4.7"`.
  OpenAI clients do not generally validate this echo, and helpmate does not,
  but it is a real difference and is recorded here rather than smoothed over.
- **Rerank is forwarded but not billed** (no per-token price exists for it),
  so helpmate's rerank spend does not appear in the ledger. Embeddings *are*
  billed, so corpus ingestion does.

## Not verified here

Running helpmate's own `make gate` end-to-end through nexus. That needs
helpmate's live Postgres and its golden set, and it belongs to gate G3 in
Phase 3 — where "the tenant's own gates must not regress after integration"
is the actual claim being tested. What this note establishes is narrower and
prior: **every network call helpmate makes can be served by nexus without
touching helpmate.**

## Environment note

This machine routes through a SOCKS proxy (`http_proxy`/`ALL_PROXY` set).
Two consequences, both of which cost time before being spotted:

- `httpx` needs `socksio` or every provider call fails at connect time with
  an error naming the proxy, which reads like the provider being down.
- `curl` to `localhost` goes through the proxy and returns `503` for a
  perfectly healthy local server. Use `--noproxy '*'`.
