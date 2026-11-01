export PYTHONPATH := src

.PHONY: sync check test test-par lint fmt types show run

sync:   ## install exactly the lockfile into .venv
	uv sync --frozen

# The same gates as the check job in .github/workflows/ci.yml. Where a gate is more than one command or
# carries flags, it is a target here that CI calls, so the two cannot drift apart.
check:  ## everything CI gates on, except the frontend
	ruff check src tests alembic
	ruff format --check src tests alembic
	pyright
	lint-imports
	python -m housing_watch schema --check schema.graphql
	$(MAKE) test-par

test:
	python -m pytest -q

test-par:  ## the suite across cores, one database per worker (set HW_TEST_DATABASE_URL)
	python -m pytest -q -n auto --durations=25 -p no:randomly

lint:
	ruff check src tests alembic $(ARGS)

fmt:
	ruff format src tests alembic

types:  ## type-check src and tests (pyrightconfig.json)
	pyright

show:   ## make show S=reside  (what one adapter returns right now; nothing stored)
	python -m housing_watch show $(S)
