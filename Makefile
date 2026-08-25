PY = PYTHONPATH=src:. .venv/bin/python

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

wuwork-eval:
	$(PY) -m tenants.wuwork.eval

run:
	$(PY) -m uvicorn nexus.app:app --reload
