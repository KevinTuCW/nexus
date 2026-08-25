# Integrating shopscout, and one deliberate attack on gate G1

Measured on 2026-08-25 against `shopscout` at `~/ai_projects/shopscout`.
No file in that repo was created, modified or deleted at any point.

## Why this tenant is the interesting one

shopscout runs a three-model jury and reaches three different labs on
purpose: disagreement between them is the signal it acts on. Before nexus,
that diversity was **physically enforced** — its own config held two
different `base_url` values pointing at two different vendors, so no code
path could accidentally make the jurors identical.

Routing everything through one gateway destroys that physical guarantee and
replaces it with a promise. Gate G1 is that promise, written down and
enforced.

## Integration

```
GLM_BASE_URL={NEXUS}/v1
SILICONFLOW_BASE_URL={NEXUS}/v1
```

**Lines changed in the tenant repo: 0.**

The three jurors arrive under shopscout's own names and are served as
themselves:

| shopscout sends | nexus serves | weight family |
|---|---|---|
| `glm-4.6` | `zai/glm-4.6` | `glm` |
| `Qwen/Qwen3-235B-A22B` | `siliconflow/Qwen/Qwen3-235B-A22B` | `qwen3` |
| `deepseek-ai/DeepSeek-V3` | `siliconflow/deepseek-ai/DeepSeek-V3` | `deepseek-v3` |

Three jurors, three distinct families. The jury is intact.

## The attack

`policies/shopscout.yaml` pins all three models. The attack is to unpin
them — to write the configuration a well-meaning platform engineer would
write while doing cost optimisation, having never been told what the jury is
for:

```yaml
models:
  zai/glm-4.6:
    substitutable_to: [qwen3, glm, deepseek-v3]
  siliconflow/Qwen/Qwen3-235B-A22B:
    substitutable_to: [qwen3, glm, deepseek-v3]
  siliconflow/deepseek-ai/DeepSeek-V3:
    substitutable_to: [qwen3, glm, deepseek-v3]
```

No code changed. Same binary, same request, same tenant.

**Before** — three jurors, three families:

```
zai/glm-4.6                          family=glm          $0.000033
siliconflow/Qwen/Qwen3-235B-A22B     family=qwen3        $0.000020
siliconflow/deepseek-ai/DeepSeek-V3  family=deepseek-v3  $0.000016
distinct families = 3    jury total = $0.000069
```

**After** — three jurors, one family:

```
siliconflow/Qwen/Qwen3-8B            family=qwen3        $0.000002
siliconflow/Qwen/Qwen3-8B            family=qwen3        $0.000002
siliconflow/Qwen/Qwen3-8B            family=qwen3        $0.000002
distinct families = 1    jury total = $0.000006
```

**Weight families 3 → 1. Bill down 91%. Every response HTTP 200. Nothing
raised, nothing logged a warning, no field in any response said the jury had
become an echo of one model asked three times.**

The cross-check shopscout's compliance verdict rests on was dead, and the
only visible consequence was a cost graph pointing in the direction everyone
wanted it to point.

That is the whole argument for G1, and it is why the guard re-derives its
verdict from the tenant policy instead of trusting anything the router says
about itself: the router is precisely the component that gets rewritten in
pursuit of a number like 91%.

The policy file was restored immediately; `git diff policies/` is empty.

## Not verified here

shopscout's own `make eval` run end-to-end through nexus, including its
51/51 compliance gate. That is gate G3 in Phase 3, where "a tenant's own
gates must not regress after integration" is the claim under test. This note
establishes something narrower: the jury reaches nexus intact, and the
mechanism that could silently flatten it exists and has been demonstrated.
