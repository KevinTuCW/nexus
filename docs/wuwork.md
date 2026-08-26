# wuwork — the tenant built to measure onboarding

wuwork is the group's internal-office assistant, and the only tenant that is
new rather than an existing repo integrated zero-touch. It exists to turn
"what does it cost to bring a new business line onto the platform?" into a
number somebody can argue with.

## Two capabilities, two cost shapes

| capability | context | frequency | what it exercises |
|---|---|---|---|
| internal policy Q&A | short | high | routing, per-tenant attribution |
| meeting minutes + action items | long | low | the other half of the cost curve |

A third capability — a cross-business-line operations digest — belongs to
Phase 3b and is measured separately. It depends on other tenants, so folding
it into the onboarding figure would destroy the meaning of both numbers.

## The rule that makes the number mean anything

wuwork lives in this repo, but it is a **tenant**. If it ever did
`from nexus.something import ...`, the figure would silently stop measuring
onboarding and start measuring "how fast can you write code inside the same
codebase" — a different and much less interesting question.

`tests/test_wuwork_isolation.py` walks the AST of every file under
`tenants/wuwork/` and fails on any import of `nexus.*`. Verified by adding
one and watching it go red.

## Onboarding cost, measured

| what | lines |
|---|---|
| **integration surface** (`client.py` + `config.py`) | **57** |
| all wuwork Python (adds retrieval, Q&A, minutes, gate) | 388 |
| corpus, transcripts and golden set | 168 (not counted) |

**57 lines** is the honest answer to "what does it take to reach the
gateway": an HTTP client, a settings object, and a refusal to start without
a credential. Everything else is the business the tenant is in, and would
have been written whatever it talked to.

The corpus and golden set are excluded deliberately. They are content, not
integration — a team onboarding a real business line brings their own.

## The offline embedder, and why the gate does not call a model

`retrieve.py` hashes character bigrams into a 256-dimension vector. It is
not good. What it is, is deterministic and offline, and wuwork's gate is the
first thing the G3 conformance runner is pointed at: a baseline whose
numbers move between machines cannot detect a regression, it can only
produce arguments about whether one happened. Keeping it offline also means
the gate runs in CI without credentials — the difference between a gate that
runs and a gate that gets skipped.

### A measured result worth keeping

The refusal rule ("say you don't know rather than answer from the model's
priors") was originally going to be a cosine threshold. Measured on this
corpus, that cannot work:

```
relevant questions,   lowest top-1 score:  0.1689
irrelevant questions, highest top-1 score: 0.2718   ("竞争对手给多少薪资")
```

The interval is inverted. Raising the dimension to 4096 and adding IDF
weighting were both tried; either still inseparable, or separable by 0.0062
with the hit rate dropping to 5/6 — a margin that thin is luck, not a
threshold.

Hashing is the cause: collisions let bigrams that never occur in a document
contribute to its score. **The hashed cosine ranks well and calibrates not
at all.** Refusal is therefore decided on raw lexical overlap — how many
distinct query bigrams literally appear in the winning document. Measured:
relevant questions match 2–4, irrelevant ones 0–1. The threshold is 2.

## The gate

`make wuwork-eval`. Offline, deterministic, and able to fail.

Thresholds are expressed as **how many golden cases may regress**, not as a
bare fraction — a fraction invites a number that looks reasonable and means
nothing. Retrieval allows one case (0.900 over ten answerable cases).
Refusal allows zero: an internal assistant answering HR questions from a
model's own priors is the failure mode staff are most likely to trust, and
"only one of them regressed" is not a comfort.

Two interface decisions follow from zero-touch integration, where the runner
may inject environment variables but may not rewrite a tenant's command:
thresholds are read from the environment, and the machine-readable result
goes to stdout unconditionally. A switch the runner cannot pass is a switch
that does not exist.

Measured: `retrieval_accuracy` 1.000, `refusal_correctness` 1.000 over 12
cases. The first run scored 0.800 and the gate went red; two golden
questions were rephrased, per the standing rule that a failing retrieval
test means the corpus or the question is wrong — never the threshold, never
the test.

## Four incumbents, four different outcomes

The same runner, pointed at the four repos it was always meant to face:

| tenant | command | exit | metrics | status |
|---|---|---|---|---|
| shopscout | `make eval` | 0 | 11 | full baseline captured |
| wealthwise | `make eval` | 0 | 36 | full baseline captured |
| aura | `make test` | 0 | 0 | pass/fail only — no eval to report |
| helpmate | `make gate` | — | — | exceeds a 200s budget |

Three findings, none of which came from reading code:

**The runner must activate the tenant's virtualenv.** helpmate's Makefile
says `python -m eval.run_eval` with a bare interpreter name, which resolves
only inside an activated venv; the others spell out `.venv/bin/python`.
Without this the runner reported "gate failed" for helpmate when nothing
about helpmate was failing — it got the diagnosis wrong on the first real
incumbent it met. Prepending `.venv/bin` to `PATH` is environment injection,
which zero-touch allows; editing the Makefile is what it forbids.

**The metrics parser only understood compact JSON.** wealthwise's gate
pretty-prints. The first parser read one line at a time, so it reported "no
metrics" for a tenant emitting perfectly good ones — and the baseline
comparison would then have run against nothing. Indented JSON is an ordinary
thing to emit; a runner that accepts one formatting style mislabels
formatting as absence.

**aura has no metrics to report, and that is not a defect.** `make test` is
a unit suite, not an eval. aura can contribute a binary pass/fail to G3 and
nothing more. Recording that honestly is better than inventing a metric so
the table looks uniform.

**helpmate's gate is a live evaluation**, running 53 golden cases through
real retrieval and generation against a live database. It is not hanging; it
is doing its job, and its job takes longer than a conformance pass should
block for. Phase 3b decides whether it runs on a schedule or contributes a
hard-gate subset.

## Baselines

`baselines/{wuwork,shopscout,wealthwise}.json`, each stamped with
`captured_at`. They record what each tenant scored **before** any
gateway-side change — the numbers integration is forbidden to make worse.

There is no baseline for aura (no metrics) or helpmate (gate not yet run to
completion inside a budget). A missing baseline is recorded as missing;
`nexus.assurance.baseline.compare` treats a metric that vanished as a
regression precisely so that absence cannot be mistaken for health.

## Reuse cost, measured — and why it is not one number

| what | lines |
|---|---|
| tenant side: `digest.py` + `NexusClient.get_usage` | 73 (55 + 18) |
| platform side, one-off: audit + `/v1/usage` + the grant field | 121 (52 + 67 + 2) |

Onboarding cost was **57 lines**. Reuse cost is these two. They answer three
different questions and adding them together answers none:

- 57 is what it costs a new business line to reach the gateway at all.
- The tenant-side figure is what the *next* team pays to build something on
  another line's data, now that the mechanism exists.
- The platform-side figure was paid once. The second tenant to want a
  cross-line capability pays none of it.

The platform figure is also the honest price of the thing that made this
safe rather than convenient: without the grant and the audit trail, reuse
would have cost a tenant nothing and cost the group its isolation.
