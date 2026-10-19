export PYTHONPATH := src

.PHONY: sync check test lint fmt show run

sync:   ## install exactly the lockfile into .venv
	uv sync --frozen

check:  ## everything CI gates on
	ruff check src tests
	ruff format --check src tests
	python -m pytest -q

test:
	python -m pytest -q

lint:
	ruff check src tests $(ARGS)

fmt:
	ruff format src tests

show:   ## make show S=reside  (what one adapter returns right now; nothing stored)
	python -m housing_watch show $(S)
