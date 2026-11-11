PYTHON_BOOTSTRAP ?= python3.12
VENV_PYTHON := .venv/bin/python

.PHONY: setup api web test-backend test-frontend test-e2e check

setup:
	$(PYTHON_BOOTSTRAP) -m venv .venv
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r services/api/requirements.txt
	cd apps/web && npm ci

api:
	cd services/api && ../../.venv/bin/alembic upgrade head
	cd services/api && ../../.venv/bin/python -m app.seed
	cd services/api && ../../.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

web:
	cd apps/web && npm run dev

test-backend:
	cd services/api && ../../.venv/bin/python -m pytest -q

test-frontend:
	cd apps/web && npm run build

test-e2e:
	cd apps/web && npm run test:e2e

check: test-backend test-frontend
