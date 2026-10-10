# Nimbrix task runner. Run `make help` for the list of targets.
SHELL := /bin/bash
.DEFAULT_GOAL := help

API_DIR := apps/api
COMPOSE := docker compose
# Admin URL for integration tests: they create and drop a throwaway database.
TEST_DATABASE_URL ?= postgresql+psycopg://$${POSTGRES_USER:-nimbrix}:$${POSTGRES_PASSWORD}@127.0.0.1:$${NIMBRIX_POSTGRES_PORT:-5440}/postgres
LOAD_ENV := set -a; [ -f .env ] && . ./.env; set +a;

.PHONY: help
help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.env:
	cp .env.example .env
	@echo "Created .env from .env.example; review it before continuing."

.PHONY: install
install: ## Install Python (uv) and Node (npm workspaces) dependencies
	cd $(API_DIR) && uv sync --frozen
	npm ci

# --- Docker stack -------------------------------------------------------------
.PHONY: up
up: .env ## Build and start postgres, migrations, API, and web
	$(COMPOSE) up -d --build --wait

.PHONY: demo
demo: .env ## Start the stack with demo tenants, users, and synthetic data (opt-in seed)
	NIMBRIX_DEMO_SEED=true $(COMPOSE) up -d --build --wait

.PHONY: down
down: ## Stop the stack (data volume is kept)
	$(COMPOSE) down

.PHONY: reset-db
reset-db: ## Stop the stack and DELETE the local Postgres volume
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Follow service logs
	$(COMPOSE) logs -f

.PHONY: ps
ps: ## Show service status
	$(COMPOSE) ps

# --- Local development (host processes) --------------------------------------
.PHONY: db
db: .env ## Start only Postgres (for host-run API/tests)
	$(COMPOSE) up -d --wait postgres

.PHONY: migrate
migrate: ## Apply migrations using NIMBRIX_DATABASE_URL from .env
	@$(LOAD_ENV) cd $(API_DIR) && uv run alembic upgrade head

.PHONY: api-dev
api-dev: ## Run the API with reload on 127.0.0.1:8200
	@$(LOAD_ENV) cd $(API_DIR) && uv run uvicorn nimbrix_api.asgi:app --reload --host 127.0.0.1 --port 8200

.PHONY: web-dev
web-dev: ## Run the Next.js dev server on 127.0.0.1:3200
	@$(LOAD_ENV) npm run dev -w @nimbrix/web

# --- Quality gates ---------------------------------------------------------------
.PHONY: lint
lint: ## Ruff (lint + format check), ESLint, Prettier check
	cd $(API_DIR) && uv run ruff check . && uv run ruff format --check .
	npm run lint
	npm run format:check -w @nimbrix/web

.PHONY: format
format: ## Auto-format Python and TypeScript
	cd $(API_DIR) && uv run ruff check --fix . && uv run ruff format .
	npm run format -w @nimbrix/web

.PHONY: typecheck
typecheck: ## mypy (strict) and tsc
	cd $(API_DIR) && uv run mypy src tests
	npm run typecheck
	npx tsc -p tsconfig.json

.PHONY: test-api
test-api: ## API unit tests (no database needed)
	cd $(API_DIR) && uv run pytest tests/unit

.PHONY: test-integration
test-integration: ## API integration tests against the compose Postgres (make db first)
	@$(LOAD_ENV) cd $(API_DIR) && NIMBRIX_TEST_DATABASE_URL="$(TEST_DATABASE_URL)" uv run pytest tests/integration

.PHONY: test-web
test-web: ## Frontend unit/component tests (Vitest)
	npm test

.PHONY: test
test: test-api test-integration test-web ## All unit and integration tests

.PHONY: e2e
e2e: ## Playwright E2E tests against the running stack (make up first)
	npx playwright test

.PHONY: contracts
contracts: ## Export OpenAPI from the API and regenerate TypeScript types
	cd $(API_DIR) && uv run nimbrix-export-openapi ../../packages/contracts/openapi.json
	npm run contracts:generate

.PHONY: contracts-check
contracts-check: contracts ## Fail if committed contracts are stale
	git diff --exit-code -- packages/contracts

.PHONY: build
build: ## Production build of the web app
	npm run build

.PHONY: check
check: lint typecheck test build ## Everything CI runs except Docker and E2E
