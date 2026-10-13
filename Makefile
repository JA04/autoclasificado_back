VENV   := venv
PYTHON := $(VENV)/bin/python
HOST   ?= 127.0.0.1
PORT   ?= 8000
ARGS   ?=

.DEFAULT_GOAL := help
.PHONY: help install run scrape test clean

help: ## Muestra esta ayuda
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  make %-9s %s\n", $$1, $$2}'

$(VENV)/bin/activate: requirements.txt
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt
	@touch $@

install: $(VENV)/bin/activate ## Crea el entorno virtual e instala las dependencias

run: install ## Levanta la API en http://localhost:8000 (con recarga automática)
	$(PYTHON) -m uvicorn app.main:app --reload --host $(HOST) --port $(PORT)

scrape: install ## Descarga publicaciones a cars.db (ej: make scrape ARGS="--pages 2")
	$(PYTHON) -m app.ingest $(ARGS)

test: install ## Corre los tests (sin red, con SQLite en memoria)
	$(PYTHON) -m pytest $(ARGS)

clean: ## Borra cachés de Python y de pytest (no toca cars.db ni el venv)
	find . -path ./$(VENV) -prune -o -name __pycache__ -type d -exec rm -rf {} +
	rm -rf .pytest_cache
