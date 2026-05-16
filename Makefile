SHELL := /bin/bash
PY := python
UVICORN := uvicorn
PNPM := pnpm

.PHONY: help up down api worker curator web seed wait test test-unit test-integration lint format typecheck demo curator-run curator-dry-run curator-status janitor

help:
	@echo "Common targets:"
	@echo "  make up                 # docker compose up -d (Cosmos + Azurite + Redis)"
	@echo "  make down               # docker compose down"
	@echo "  make wait               # block until emulators are reachable"
	@echo "  make api                # run FastAPI dev server"
	@echo "  make worker             # run classifier worker"
	@echo "  make web                # run Next.js dev server"
	@echo "  make seed               # seed sample skills"
	@echo "  make test-unit          # pytest unit tests (no docker required)"
	@echo "  make test-integration   # pytest integration tests (requires docker stack)"
	@echo "  make test               # both"
	@echo "  make lint               # ruff check"
	@echo "  make format             # ruff format"
	@echo "  make typecheck          # tsc --noEmit (frontend)"
	@echo "  make demo               # full e2e happy path test against live stack"

up:
	docker compose up -d

down:
	docker compose down

wait:
	$(PY) scripts/wait_for_emulators.py

api:
	$(UVICORN) backend.app:create_app --factory --reload --host 0.0.0.0 --port 8000

worker:
	$(PY) -m backend.workers.classifier

curator:
	$(PY) -m backend.workers.curator_scheduler

curator-run:
	curl -fsS -X POST -H "X-User-Email: admin@example.com" http://localhost:8000/v1/admin/curator/run | jq

curator-dry-run:
	curl -fsS -X POST -H "X-User-Email: admin@example.com" "http://localhost:8000/v1/admin/curator/run?dry_run=true" | jq

curator-status:
	curl -fsS -H "X-User-Email: admin@example.com" http://localhost:8000/v1/admin/curator/status | jq

janitor:
	curl -fsS -X POST -H "X-User-Email: admin@example.com" http://localhost:8000/v1/admin/curator/janitor | jq

web:
	$(PNPM) --filter frontend dev

seed:
	$(PY) scripts/seed_skills.py

test-unit:
	$(PY) -m pytest backend/tests/unit -v

test-integration:
	$(PY) -m pytest backend/tests/integration -v -m integration

test: test-unit test-integration

lint:
	ruff check .

format:
	ruff format .

typecheck:
	$(PNPM) --filter frontend typecheck

demo:
	$(PY) -m pytest backend/tests/integration/test_e2e_happy_path.py -v -s
