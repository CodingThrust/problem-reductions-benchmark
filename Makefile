# Makefile for problem-reductions-benchmark
# Run from the repo root (next to benchmark/).
#
# Key targets:
#   test                 Run full pytest suite (unit + integration)
#   test-unit            Run only unit tests (no real repo/pred needed)
#   verify-calibration   Test the verifier against known fixtures (no AI needed)
#   preflight            Validate submission.env with one tiny real call before a full run
#   submission           Run the real budgeted runner via Docker
#
# Required env vars for targets that call the AI:
#   ANTHROPIC_API_KEY   (or OPENAI_API_KEY etc., depending on model)
#   REPO_DIR            Path to a problem-reductions clone at the pinned commit
#                       (default: ../problem-reductions)

REPO_DIR ?= ../problem-reductions
MODEL    ?= anthropic/claude-sonnet-4-6
IMAGE    ?= problem-reductions-runner:v0.6.0
SUBS_DIR ?= submissions
SCORED   ?= results/scored
ENV_FILE ?= submission.env

.PHONY: test test-unit verify-calibration verify-judgment audit install-deps help runner-build preflight submission score-local

## Run the full test suite (unit + integration tests that need real repo).
test:
	pytest -v

## Run only unit tests — no real repo or pred binary required.
test-unit:
	pytest -v -m "not integration"

## Pred-free sanity tests (docs, CI workflow, trajectory).
verify-judgment:
	pytest -v -m "judgment"

## Test the verifier against the fixture certificates — no AI, no API keys needed.
## Must pass before any real session is run.
verify-calibration:
	python -m benchmark.verify --calibrate

## Build the dockerized submission runner image (compiles pred + bundles the agent).
runner-build:
	docker build -f docker/Dockerfile --target runner -t $(IMAGE) .

## Preflight: validate submission.env with one tiny real API call + pred/rules checks,
## BEFORE committing to a full $20 run. Spends a fraction of a cent. (The no-API wiring of
## the runner itself is covered by the pytest suite, not a make target.)
preflight:
	@if [ ! -f "$(ENV_FILE)" ]; then \
	  echo "No $(ENV_FILE) — copy submission.env.example and fill it in first"; exit 1; fi
	docker run --rm --env-file "$(ENV_FILE)" $(IMAGE) --preflight

## Run the real budgeted runner via Docker → ./out/submission.json.
## Preferred: copy submission.env.example → submission.env, fill it, then `make submission`
## (all config in one --env-file). Falls back to MODEL + ANTHROPIC_API_KEY env if no file.
submission:
	mkdir -p out
	@if [ -f "$(ENV_FILE)" ]; then \
	  echo "Using --env-file $(ENV_FILE)"; \
	  docker run --rm --env-file "$(ENV_FILE)" -v "$(PWD)/out:/out" $(IMAGE); \
	else \
	  echo "No $(ENV_FILE) (copy submission.env.example); using MODEL + ANTHROPIC_API_KEY env"; \
	  docker run --rm \
	    -e MODEL_NAME=$(MODEL) \
	    -e ANTHROPIC_API_KEY=$(ANTHROPIC_API_KEY) \
	    -e BUDGET_USD=20 \
	    -v "$(PWD)/out:/out" \
	    $(IMAGE); \
	fi
	@echo "Submission → ./out/submission.json"

## Score all submissions in SUBS_DIR with the zero-trust backend (needs pred).
## Writes scored results + leaderboard.json into SCORED.
score-local:
	python -m benchmark.backend_score --local $(SUBS_DIR) $(SCORED)

## Audit pred CLI capabilities against the pinned library commit.
audit:
	python -m benchmark.pred_audit $(REPO_DIR)

## Install Python dependencies.
install-deps:
	pip install -r benchmark/requirements.txt

help:
	@echo "Targets:"
	@echo "  test                Run full pytest suite"
	@echo "  test-unit           Run unit tests only (no real repo needed)"
	@echo "  verify-calibration  Test verifier against fixtures (no AI needed)"
	@echo "  runner-build        Build the dockerized submission runner image"
	@echo "  preflight           Validate submission.env (1 tiny real call) before a full run"
	@echo "  submission          Run the real runner via Docker → out/submission.json"
	@echo "  score-local         Score SUBS_DIR submissions with the backend"
	@echo "  audit               Audit pred CLI capabilities"
	@echo "  install-deps        Install Python requirements"
	@echo ""
	@echo "Variables:"
	@echo "  REPO_DIR=$(REPO_DIR)"
	@echo "  MODEL=$(MODEL)"
