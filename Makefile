# Makefile for problem-reductions-benchmark
# Run from the repo root (next to benchmark/).
#
# Key targets:
#   test                 Run full pytest suite (unit + integration)
#   test-unit            Run only unit tests (no real repo/pred needed)
#   verify-calibration   Test the verifier against known fixtures (no AI needed)
#   preflight            Validate submission.env with one tiny real call before a full run
#   run                  Run the benchmark via Docker → out/submission.json (does NOT upload)
#
# Model + key + price for the real run live in submission.env (any provider — see
# submission.env.example); preflight/submission read it via --env-file. REPO_DIR is only
# for the local-clone targets (audit).

REPO_DIR ?= ../problem-reductions
# PR_REF = the problem-reductions version this benchmark round targets (tag or commit).
# It drives BOTH the build arg and the image tag, so bumping the round is one place:
#   make runner-build PR_REF=v0.7.0   →   builds + tags problem-reductions-runner:v0.7.0
PR_REF   ?= v0.6.0
IMAGE    ?= problem-reductions-runner:$(PR_REF)
SUBS_DIR ?= submissions
SCORED   ?= results/scored
ENV_FILE ?= submission.env

.PHONY: test test-unit verify-calibration verify-judgment audit install-deps help runner-build preflight run score-local publish-local serve

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

## Build the dockerized submission runner image (compiles pred at PR_REF + bundles the agent).
runner-build:
	docker build -f docker/Dockerfile --target runner \
	  --build-arg PR_REF=$(PR_REF) -t $(IMAGE) .

## Preflight: validate submission.env with one tiny real API call + pred/rules checks,
## BEFORE committing to a full $20 run. Spends a fraction of a cent. (The no-API wiring of
## the runner itself is covered by the pytest suite, not a make target.)
preflight:
	@if [ ! -f "$(ENV_FILE)" ]; then \
	  echo "No $(ENV_FILE) — copy submission.env.example and fill it in first"; exit 1; fi
	docker run --rm --env-file "$(ENV_FILE)" $(IMAGE) --preflight

## Run the budgeted bug-finding agent via Docker → writes ./out/submission.json.
## This RUNS the benchmark locally; it does NOT submit — submitting is a separate step
## (open a GitHub PR adding the file, see CONTRIBUTING.md). Config lives in submission.env
## (copy submission.env.example); run `make preflight` first to validate it.
run:
	@if [ ! -f "$(ENV_FILE)" ]; then \
	  echo "No $(ENV_FILE) — copy submission.env.example and fill it in (then: make preflight)"; exit 1; fi
	mkdir -p out
	docker run --rm --env-file "$(ENV_FILE)" -v "$(PWD)/out:/out" $(IMAGE)
	@echo "Wrote ./out/submission.json — now submit it via a GitHub PR (see CONTRIBUTING.md)."

## Score all submissions in SUBS_DIR with the zero-trust backend (needs pred).
## Writes scored results + leaderboard.json into SCORED.
score-local:
	python -m benchmark.backend_score --local $(SUBS_DIR) $(SCORED)

## Refresh the public site's aggregate from local scored submissions. SUBS_DIR holds the
## answer key (cert + trajectory) and is gitignored — it NEVER leaves your machine. This
## scores it, copies ONLY the aggregate leaderboard into site/results.json, and guards that
## no certificate / rule identity leaked. Commit site/results.json; SUBS_DIR stays local.
publish-local: score-local
	cp $(SCORED)/leaderboard.json site/results.json
	python .github/scripts/check_aggregate.py site/results.json
	@echo "Updated site/results.json (aggregate only). Commit it — SUBS_DIR stays local."

## Preview the leaderboard site locally (it's published to GitHub Pages on merge).
serve:
	@echo "Serving site/ at http://localhost:8000  (Ctrl-C to stop)"
	cd site && python3 -m http.server 8000

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
	@echo "  run                 Run the benchmark via Docker → out/submission.json (not upload)"
	@echo "  score-local         Score SUBS_DIR submissions with the backend"
	@echo "  publish-local       Score + refresh site/results.json (aggregate only; SUBS_DIR stays local)"
	@echo "  serve               Preview the leaderboard site locally (published via Pages on push to site/)"
	@echo "  audit               Audit pred CLI capabilities"
	@echo "  install-deps        Install Python requirements"
	@echo ""
	@echo "Variables:"
	@echo "  REPO_DIR=$(REPO_DIR)"
	@echo "  ENV_FILE=$(ENV_FILE)  (model/key/price for preflight + submission)"
