# La gazette — raccourcis. Sous Windows : mingw32-make, ou make via Git Bash / WSL.

# Utilise le virtualenv local s'il existe.
ifneq ($(wildcard .venv/Scripts/python.exe),)
PYTHON ?= .venv/Scripts/python.exe
else ifneq ($(wildcard .venv/bin/python),)
PYTHON ?= .venv/bin/python
else
PYTHON ?= python3
endif

COMPOSE ?= docker compose
# Date de collecte facultative : make collect DATE=2026-10-21
DATE ?=

.PHONY: help install up down logs collect test lint format

help:
	@echo "install  virtualenv local + dépendances de dev"
	@echo "up       construit l'image et démarre postgres + airflow"
	@echo "down     arrête la pile (les volumes sont conservés)"
	@echo "logs     suit les journaux des conteneurs"
	@echo "collect  collecte les flux hors Airflow (DATE=AAAA-MM-JJ facultatif)"
	@echo "test     lance pytest"
	@echo "lint     ruff check + ruff format --check"

install:
	python3 -m venv .venv || python -m venv .venv
	$(MAKE) PYTHON=$(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,.venv/bin/python) _install

_install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements-dev.txt -e .

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

collect:
	$(PYTHON) -m gazette collect $(if $(DATE),--date $(DATE),)

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .
