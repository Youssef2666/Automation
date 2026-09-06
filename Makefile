# Automation Lab - developer shortcuts. Run from the repo root; needs GNU make, docker compose, python 3.10+.
# Every target is a thin wrapper around scripts/ so CI and humans run the same commands.

PY ?= python
COMPOSE ?= docker compose
PROFILES ?= core
comma := ,
PROFILE_FLAGS := $(foreach p,$(subst $(comma), ,$(PROFILES)),--profile $(p))
FOLDER ?=
SERVICE ?=
IDS ?=
SINCE ?=

.DEFAULT_GOAL := help
.PHONY: help up down status logs setup import export validate matrix matrix-check preview smoke test reseed secret-scan milestone changelog scaffold

help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

up: ## start the stack (PROFILES=core,docs,ai,observability,queue)
	$(COMPOSE) $(PROFILE_FLAGS) up -d

down: ## stop the stack (volumes are kept; never uses -v)
	$(COMPOSE) $(PROFILE_FLAGS) down

status: ## container status
	$(COMPOSE) $(PROFILE_FLAGS) ps

logs: ## follow logs (SERVICE=n8n to filter)
	$(COMPOSE) $(PROFILE_FLAGS) logs -f --tail=200 $(SERVICE)

setup: ## owner + API key + credentials + import + publish (after `make up`)
	bash scripts/setup.sh

import: ## import workflows into n8n (FOLDER=workflows/T01-... to limit)
	bash scripts/import-workflows.sh $(FOLDER)

export: ## export workflows from n8n back into the repo (IDS="T01 P03" to limit)
	bash scripts/export-workflows.sh $(IDS)

validate: ## folder contract + secret scan (FOLDER=... to limit)
	$(PY) scripts/validate.py $(FOLDER)

matrix: ## regenerate the README coverage matrix
	$(PY) scripts/build-matrix.py

matrix-check: ## fail if the README matrix is out of date (CI)
	$(PY) scripts/build-matrix.py --check

preview: ## render assets/screenshot.png previews (FOLDER=... or all)
	$(PY) scripts/render-preview.py $(if $(FOLDER),$(FOLDER),--all)

smoke: ## end-to-end smoke test against the running stack
	$(PY) scripts/dev/smoke.py

test: ## unit tests for the tooling
	$(PY) -m unittest discover -s scripts/tests -v

reseed: ## drop + recreate the demo database from seed/
	bash scripts/reseed.sh

secret-scan: ## working tree + git history secret scan
	$(PY) scripts/validate.py --secrets-only
	git log --all -p | $(PY) scripts/validate.py --secrets-stdin

milestone: ## progress against PRD milestones and goals
	$(PY) scripts/milestone.py

changelog: ## conventional-commit changelog since the last tag (SINCE=v0.1.0)
	$(PY) scripts/changelog.py $(if $(SINCE),--since $(SINCE),)

scaffold: ## new folder: make scaffold ID=T01 SLUG=webhook-to-database TITLE="Webhook to Database"
	$(PY) scripts/scaffold.py $(ID) $(SLUG) --title "$(TITLE)"
