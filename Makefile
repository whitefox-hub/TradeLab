.PHONY: help install up down migrate test test-fast test-medium test-slow lint typecheck golden-update doctor pin-digests

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Create the venv and install all dependency groups
	uv venv && uv pip install -e ".[collectors,research,orchestration,api,dev]"

up:  ## Start the research stack. Does NOT start the executor (§2.2).
	TRADELAB_IMAGE_DIGEST=$$(docker images --no-trunc --format '{{.ID}}' tradelab-compute | head -1) \
		docker compose up -d --build

down:  ## Stop everything
	docker compose down

migrate:  ## Apply migrations
	docker compose run --rm migrate

test-fast:  ## Unit, schema, contracts. Budget < 15s (§16.8)
	pytest -m fast -n auto

test-medium:  ## Properties, canaries, golden run. Budget < 3min
	pytest -m "fast or medium" -n auto

test-slow:  ## Full synthetic ground truth, regression corpus, benchmarks. Nightly.
	pytest -m "not integration" -n auto

test: test-medium  ## Default: what runs on commit

lint:
	ruff check tradelab tests && ruff format --check tradelab tests

typecheck:
	mypy tradelab

doctor:  ## Check the environment is pinned for reproducibility
	python -m tradelab.cli doctor

golden-update:  ## Record a new golden digest. Requires an explicit reason.
	@test -n "$(REASON)" || (echo "Refusing: set REASON='why the golden value changed'" && exit 1)
	python scripts/update_golden.py "$(REASON)"

pin-digests:  ## Resolve base image tags to sha256 digests and rewrite the Dockerfiles
	@echo "TODO phase 0 sign-off: resolve digests with 'docker buildx imagetools inspect'"
