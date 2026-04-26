.PHONY: help install test lint typecheck format clean serve

help:
	@echo "Available targets:"
	@echo "  install   - Install package and dev dependencies"
	@echo "  test      - Run tests"
	@echo "  lint      - Run ruff linter"
	@echo "  typecheck - Run mypy type checker"
	@echo "  format    - Auto-format code with ruff"
	@echo "  serve     - Start llama.cpp inference server"
	@echo "  clean     - Remove caches and build artifacts"

install:
	uv venv
	. .venv/bin/activate && uv pip install -e ".[dev]"
	. .venv/bin/activate && pre-commit install

test:
	. .venv/bin/activate && pytest

lint:
	. .venv/bin/activate && ruff check src tests

typecheck:
	. .venv/bin/activate && mypy src

format:
	. .venv/bin/activate && ruff format src tests
	. .venv/bin/activate && ruff check --fix src tests

serve:
	./scripts/run_llama_server.sh

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
