.PHONY: install dev test lint format run

install:
	python -m pip install -e .

dev:
	python -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

run:
	uvicorn cherryfin.api.main:app --host 0.0.0.0 --port 8080 --reload
