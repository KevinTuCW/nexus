PY = PYTHONPATH=src .venv/bin/python

install:
	.venv/bin/pip install -e '.[dev]'

test:
	$(PY) -m pytest -q

run:
	$(PY) -m uvicorn nexus.app:app --reload
