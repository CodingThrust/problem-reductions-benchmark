# Makefile for problem-reductions-benchmark
# Run from the repo root (next to benchmark/ and leaderboard/).
#
# Key targets:
#   test                 Run full pytest suite (unit + integration)
#   test-unit            Run only unit tests (no real repo/pred needed)
#   verify-calibration   Test the verifier against known fixtures (no AI needed)
#   validate-results     Schema-check all results/*.json files
#   demo                 Run a tiny real session and rebuild the leaderboard index
#
# Required env vars for targets that call the AI:
#   ANTHROPIC_API_KEY   (or OPENAI_API_KEY etc., depending on model)
#   REPO_DIR            Path to a problem-reductions clone at the pinned commit
#                       (default: ../problem-reductions)

REPO_DIR ?= ../problem-reductions
MODEL    ?= anthropic/claude-sonnet-4-6
BUDGET   ?= 2.0
PER_RULE ?= 0.5
RESULTS  ?= results/results_mini.json

.PHONY: test test-unit verify-calibration verify-judgment validate-results build-index space space-serve demo audit install-deps help

## Run the full test suite (unit + integration tests that need real repo).
test:
	pytest -v

## Run only unit tests — no real repo or pred binary required.
test-unit:
	pytest -v -m "not integration"

## Test verifier robust equality and accept/reject judgment.
verify-judgment:
	pytest -v -m "judgment"

## Test the verifier against the fixture certificates — no AI, no API keys needed.
## Must pass before any real session is run.
verify-calibration:
	python -m benchmark.verify --calibrate

## Schema-check all results/*.json files.
## Fails and names the missing field if any file is malformed.
validate-results:
	python -m benchmark.validate_results --results-dir results

## Rebuild results/index.json from whatever is in results/*.json.
build-index:
	python -m benchmark.build_index --results-dir results

## Rebuild the index, then assemble the static HF Space bundle into space/site/.
space: build-index
	python -m benchmark.build_space

## Serve the built Space bundle locally for preview (Ctrl-C to stop).
space-serve:
	python -m http.server --directory space/site 8000

## Run a tiny real bug-hunting session (2 rules, small budget) then rebuild the index.
## Requires ANTHROPIC_API_KEY and REPO_DIR to be set.
demo:
	python -m benchmark.run_mini \
	  --model $(MODEL) \
	  --budget $(BUDGET) \
	  --per-rule $(PER_RULE) \
	  --repo-dir $(REPO_DIR) \
	  --output $(RESULTS)
	$(MAKE) build-index
	@echo ""
	@echo "Demo complete. Open leaderboard/index.html (served from repo root) to view results."

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
	@echo "  validate-results    Schema-check results/*.json"
	@echo "  build-index         Rebuild results/index.json"
	@echo "  space               Build the static HF Space bundle (space/site/)"
	@echo "  space-serve         Preview the Space bundle at localhost:8000"
	@echo "  demo                Run a tiny real session + rebuild index"
	@echo "  audit               Audit pred CLI capabilities"
	@echo "  install-deps        Install Python requirements"
	@echo ""
	@echo "Variables:"
	@echo "  REPO_DIR=$(REPO_DIR)"
	@echo "  MODEL=$(MODEL)"
	@echo "  BUDGET=$(BUDGET)"
