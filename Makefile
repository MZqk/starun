.PHONY: install dev test test-e2e lint build

install:
	cd api && uv sync --extra dev
	cd web && npm ci

dev:
	docker compose up --build

test:
	cd api && uv run pytest
	cd web && npm test -- --run

test-e2e:
	cd web && npm run test:e2e

lint:
	cd api && uv run ruff check . && uv run mypy app
	cd web && npm run lint

build:
	cd web && npm run build
