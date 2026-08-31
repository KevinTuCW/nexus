# Locally this repo is driven out of .venv/. CI is not: it installs into the
# runner's own interpreter and never creates one. That matters beyond taste,
# because `make wuwork-eval` is not only typed by hand -- the conformance
# tests shell out to it as wuwork's gate. A hardcoded .venv/bin/python turns
# three of them red on every CI run for a reason that has nothing to do with
# the code under test.
PYBIN := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PY = PYTHONPATH=src:. $(PYBIN)

install:
	.venv/bin/pip install -e '.[dev]'

test:
	$(PY) -m pytest -q

# Tests that need real credentials or a real database read them from the
# *shell*, never from .env -- conftest disables .env during tests so that a
# value sitting there cannot leak into a unit test. This target opts in
# explicitly, which is why it is a separate command and not a flag: reaching
# real providers should be something you typed, not something you inherited.
test-live:
	set -a; . ./.env; set +a; $(PY) -m pytest -q

# The four gates. Reads the ledger the gateway wrote (DATABASE_URL) or rows
# handed to it with --ledger-json; a gate with nothing to judge reports
# `no evidence`, which is not a pass.
eval:
	$(PY) -m nexus.eval

# What a delivery runs. The difference is the only one that matters: here a
# gate that judged nothing fails the build instead of printing a caveat
# nobody reads.
eval-delivery:
	$(PY) -m nexus.eval --require-evidence

wuwork-eval:
	$(PY) -m tenants.wuwork.eval

# Sources .env, unlike `test`. The asymmetry it removes is the confusing one:
# Settings reads .env through pydantic, so DATABASE_URL and UPSTREAM written
# there already take effect -- but `build_key_index` reads os.environ, so
# NEXUS_KEY_* written in the same file silently did not. A dev server that
# honours four of a file's six settings has no key index, refuses every
# request with 401, and the console it serves comes up blank with no clue why.
#
# This does not weaken the rule that reaching real providers must be typed:
# UPSTREAM defaults to "fake" and only .env or the shell can say otherwise,
# which was already true before this line.
# .env is gitignored, so a fresh clone has none and must still start.
run:
	set -a; [ ! -f .env ] || . ./.env; set +a; $(PY) -m uvicorn nexus.app:app --reload
