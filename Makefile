# trispoke-outbound — convenience targets (Mac/Linux).
.PHONY: install test smoke run lint help

help:
	@echo "Targets:"
	@echo "  install   uv sync && pull the local model"
	@echo "  test      run the pytest suite (verbose)"
	@echo "  smoke     run the user-facing health checks"
	@echo "  run       launch all components (auto-detects OS via uname)"
	@echo "  lint      ruff check + ruff format --check"

install:
	uv sync && ollama pull qwen3:8b

test:
	uv run pytest -v

smoke:
	uv run python scripts/smoke_test.py

run:
	./scripts/run_$$(uname).sh

lint:
	uv run ruff check . && uv run ruff format --check .
