.PHONY: install test check test-cov clean

install:
	uv sync --all-extras

test:
	uv run pytest -x -q

check:
	uv run ruff check --fix .
	uv run ruff format .
	uv run mypy entropy_meter/

test-cov:
	uv run pytest --cov=entropy_meter --cov-report=term-missing --cov-fail-under=80

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache __pycache__ build dist *.egg-info
