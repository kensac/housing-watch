export PYTHONPATH := src

.PHONY: sync check test lint fmt types show run

sync:   ## install exactly the lockfile into .venv
	uv sync --frozen

check:  ## everything CI gates on, except the frontend
	ruff check src tests alembic
	ruff format --check src tests alembic
	pyright
	python -m housing_watch schema --check schema.graphql
	python -m pytest -q -n auto

test:
	python -m pytest -q

lint:
	ruff check src tests alembic $(ARGS)

fmt:
	ruff format src tests alembic

types:  ## type-check src and tests (pyrightconfig.json)
	pyright

show:   ## make show S=reside  (what one adapter returns right now; nothing stored)
	python -m housing_watch show $(S)
