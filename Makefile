.PHONY: install lint test build check clean

install:
	uv sync --extra dev || pip install -e ".[dev]"

lint:
	ruff check .
	ruff format --check .

test:
	pytest

build:
	python -m build

check: lint test

clean:
	rm -rf dist build *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
