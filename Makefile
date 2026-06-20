.PHONY: install dev dev-api dev-web dev-local test test-e2e lint build

install:
	cd api && uv sync --extra dev
	cd web && npm ci
	python3 deep-sky-processor/scripts/download_starnet.py --platform local

dev:
	python3 deep-sky-processor/scripts/download_starnet.py --platform linux
	docker compose up --build

dev-api:
	cd api && uv run alembic upgrade head && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

dev-web:
	cd web && STARUN_API_PROXY_TARGET=http://localhost:8000 npm run dev

dev-local:
	$(MAKE) -j 2 dev-api dev-web


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
