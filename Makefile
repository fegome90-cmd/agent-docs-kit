.PHONY: install lint typecheck test build check clean

install:
	uv sync --extra dev

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy .
	uv run pyrefly check

test:
	uv run pytest

build:
	uv build

check: lint typecheck test

clean:
	rm -rf dist build *.egg-info .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
