.PHONY: install dev test lint build

install:
	cd api && uv sync --extra dev
	cd web && npm install

dev:
	docker compose up --build

test:
	cd api && uv run pytest
	cd web && npm test -- --run

lint:
	cd api && uv run ruff check . && uv run mypy app
	cd web && npm run lint

build:
	cd web && npm run build
