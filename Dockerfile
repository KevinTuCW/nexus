# Two stages, for a reason worth stating.
#
# The suite shells out to real tools: `assurance/isolation` runs `git status`
# against a tenant checkout, and `assurance/conformance` runs whatever
# command a tenant's policy declares — `make gate`, `make eval`, `make test`.
# Those tests are worth having precisely because they do not mock the
# outside world, and the price is that they need `git` and `make` present.
#
# The gateway itself needs neither at runtime. So the tests run in a stage
# that has them, and the image that ships does not.

FROM python:3.12-slim AS test

RUN apt-get update \
    && apt-get install --no-install-recommends -y git make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
COPY tenants ./tenants
COPY policies ./policies
COPY baselines ./baselines
COPY db ./db
COPY tests ./tests
COPY Makefile ./

RUN pip install --no-cache-dir -e '.[dev,llm,pg]'

# Offline by construction: no credentials reach the build context, and the
# tests that need them skip themselves. An image built from a commit that
# cannot pass its own gates is worse than a failed build, because it looks
# deployable.
RUN PYTHONPATH=src:. python -m pytest -q && touch /build/.tests-passed


FROM python:3.12-slim AS runtime

# Non-root. A gateway holding every tenant's provider credentials is the
# last process that should run as root.
RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY tenants ./tenants
COPY policies ./policies
COPY baselines ./baselines
COPY db ./db

RUN pip install --no-cache-dir -e '.[llm,pg]'

# This is what makes the test stage a gate rather than a parallel branch.
# Docker only builds a stage another stage depends on; without this COPY the
# tests could be skipped entirely and the image would still tag. The marker
# exists only if pytest exited 0.
COPY --from=test /build/.tests-passed /app/.tests-passed

USER app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "nexus.app:app", "--host", "0.0.0.0", "--port", "8000"]
