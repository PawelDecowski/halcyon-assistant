# Everyday commands. `make` on its own lists them.
#
#   make setup          create .venv and install everything (needs uv)
#   make test           offline tests, no API key needed
#   make run            the service on localhost:8000 (needs ANTHROPIC_API_KEY)
#   make sim S=09       one practice scenario against the local service
#   make sim-all        all scenarios; output also saved to run.txt
#
# Keys come from a .env file if there is one (see .env.example), or from your shell.
# Override the defaults when needed, e.g. `make run PORT=9000`
# or `make sim S=09 URL=https://example.up.railway.app API_TOKEN=...`.

-include .env
export

PORT      ?= 8000
API_TOKEN ?= dev
URL       ?= http://localhost:$(PORT)

.PHONY: help setup test run sim sim-all

help:
	@sed -n 's/^#   //p' Makefile

setup:
	uv venv
	uv pip install -r requirements-dev.txt

test:
	MOCK_LATENCY_S=0 uv run pytest -q

run: require-key
	uv run uvicorn --factory app.main:create_app --port $(PORT)

sim: require-key
	@test -n "$(S)" || { echo "Choose a scenario by its number, e.g. make sim S=09"; exit 1; }
	uv run python -m scripts.simulate scenarios/$(S)_*.yaml --url $(URL) --token $(API_TOKEN)

sim-all: require-key
	uv run python -m scripts.simulate scenarios/*.yaml --url $(URL) --token $(API_TOKEN) | tee run.txt

.PHONY: require-key
require-key:
	@test -n "$$ANTHROPIC_API_KEY" || { echo "Set ANTHROPIC_API_KEY in .env or your shell first"; exit 1; }
