PYTHON ?= python3.12

.PHONY: install dev test lint build

install:
	cd api && $(PYTHON) -m pip install -e ".[dev]"
	cd web && npm install

dev:
	docker compose up --build

test:
	cd api && $(PYTHON) -m pytest
	cd web && npm test -- --run

lint:
	cd api && ruff check . && mypy app
	cd web && npm run lint

build:
	cd web && npm run build
