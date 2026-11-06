VENV   ?= .venv
PY     ?= $(VENV)/bin/python
PIP    ?= $(VENV)/bin/pip
PORT   ?= 8080
COMPOSE?= docker compose

.PHONY: help setup test run serve up down logs clean

help:
	@echo "make setup  - buat .venv + install dependensi"
	@echo "make test   - jalankan pytest"
	@echo "make run    - server dev dengan reload (port $(PORT))"
	@echo "make serve  - server tanpa reload, listen 0.0.0.0"
	@echo "make up     - Docker Compose (build + jalan)"
	@echo "make down   - hentikan Compose"
	@echo "make clean  - hapus venv dan cache lokal"

setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt -r requirements-dev.txt

test:
	@test -n "$$TEST_DATABASE_URL" || { echo "Set TEST_DATABASE_URL to a PostgreSQL test database"; exit 1; }
	$(PY) -m pytest -q

run:
	$(PY) -m app.migrate
	$(PY) -m uvicorn app.main:app --reload --port $(PORT)

serve:
	$(PY) -m app.migrate
	$(PY) -m uvicorn app.main:app --host 0.0.0.0 --port $(PORT)

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=80

clean:
	rm -rf $(VENV) .pytest_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
