# Makefile for problem-reductions-benchmark
# Run from the repo root (next to benchmark/).
#
# Key targets:
#   test                 Run full pytest suite (unit + integration)
#   test-unit            Run only unit tests (no real repo/pred needed)
#   verify-calibration   Test the verifier against known fixtures (no AI needed)
#   preflight            Validate submission.env with one tiny real call before a full run
#   run                  Run the benchmark via Docker → out/submission.json (does NOT upload)
#   run-local            Clone/verify a repo and run a local headless CLI agent
#
# Model/auth configuration lives in submission.env (see submission.env.example). Local
# repository, submission, and log locations are intentionally explicit Make variables.
# PR_REF = the problem-reductions version this benchmark round targets (tag or commit).
# It drives BOTH the build arg and the image tag, so bumping the round is one place:
#   make runner-build PR_REF=v0.7.0   →   builds + tags problem-reductions-runner:v0.7.0
PR_REF   ?= v0.6.0
IMAGE    ?= problem-reductions-runner:$(PR_REF)
GHCR_IMAGE ?= ghcr.io/codingthrust/problem-reductions-runner
JOBS     ?= 1
SUBS_DIR ?= submissions
SCORED   ?= results/scored
ENV_FILE ?= submission.env
LOCAL_BACKEND ?= codex
LOCAL_REPO_DIR ?=
LOCAL_OUTPUT ?=
LOCAL_LOG_DIR ?=
REPO_URL ?= https://github.com/CodingThrust/problem-reductions.git
LOCAL_ARGS = $(if $(LOCAL_BACKEND),--backend "$(LOCAL_BACKEND)")

.PHONY: test test-unit verify-calibration verify-judgment audit install-deps help runner-build runner-pull preflight run run-local score-local board publish-local serve

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
## JOBS controls parallel rustc jobs in the pred build (default 1 = safe on small VMs).
runner-build:
	docker build -f docker/Dockerfile --target runner \
	  --build-arg PR_REF=$(PR_REF) --build-arg CARGO_JOBS=$(JOBS) -t $(IMAGE) .

## Pull the prebuilt runner image from GHCR (built by .github/workflows/runner-image.yml)
## and tag it locally as $(IMAGE). Fast alternative to runner-build's local Rust compile.
## The published image is linux/amd64 only; on Apple Silicon it runs under emulation,
## so pin the platform explicitly (docker run picks it up from the local tag).
runner-pull:
	docker pull --platform linux/amd64 $(GHCR_IMAGE):$(PR_REF)
	docker tag $(GHCR_IMAGE):$(PR_REF) $(IMAGE)

## Preflight: validate submission.env with one tiny real API call + pred/rules checks,
## BEFORE committing to a full run. Makes one tiny real API call. (The no-API wiring of
## the runner itself is covered by the pytest suite, not a make target.)
preflight:
	@if [ ! -f "$(ENV_FILE)" ]; then \
	  echo "No $(ENV_FILE) — copy submission.env.example and fill it in first"; exit 1; fi
	docker run --rm --env-file "$(ENV_FILE)" $(IMAGE) --preflight

## Run the bug-finding agent via Docker → writes ./out/submission.json.
## This RUNS the benchmark locally; it does NOT submit — submitting is a separate step
## (open a GitHub PR adding the file, see CONTRIBUTING.md). Config lives in submission.env
## (copy submission.env.example); run `make preflight` first to validate it.
run:
	@if [ ! -f "$(ENV_FILE)" ]; then \
	  echo "No $(ENV_FILE) — copy submission.env.example and fill it in (then: make preflight)"; exit 1; fi
	mkdir -p out
	docker run --rm --env-file "$(ENV_FILE)" -v "$(PWD)/out:/out" $(IMAGE)
	@echo "Wrote ./out/submission.json — now submit it with 'python -m benchmark.submit' (see CONTRIBUTING.md)."

## Lightweight host run through an installed headless agent CLI. The checkout is cloned at
## PR_REF when LOCAL_REPO_DIR is absent; an existing checkout must already match exactly.
## Requires explicit repo/output/log paths, local Python deps, pred, and CLI authentication.
run-local:
	@if [ ! -f "$(ENV_FILE)" ]; then \
	  echo "No $(ENV_FILE) — copy submission.env.example and set MODEL_NAME first"; exit 1; fi
	@if [ -z "$(LOCAL_REPO_DIR)" ] || [ -z "$(LOCAL_OUTPUT)" ] || [ -z "$(LOCAL_LOG_DIR)" ]; then \
	  echo "Set LOCAL_REPO_DIR, LOCAL_OUTPUT, and LOCAL_LOG_DIR explicitly"; exit 1; fi
	python3 -m benchmark.run_submission \
	  --env-file "$(ENV_FILE)" \
	  --repo-dir "$(abspath $(LOCAL_REPO_DIR))" \
	  --repo-ref "$(PR_REF)" \
	  --repo-url "$(REPO_URL)" \
	  --output "$(abspath $(LOCAL_OUTPUT))" \
	  --trajectory-dir "$(abspath $(LOCAL_LOG_DIR))" $(LOCAL_ARGS)
	@echo "Wrote $(LOCAL_OUTPUT); logs are in $(LOCAL_LOG_DIR)."

## Score all submissions in SUBS_DIR with the zero-trust backend (needs pred). Writes scored
## results + leaderboard.json into SCORED, and one public entry per submission into SCORED/board.
score-local:
	python -m benchmark.backend_score --local $(SUBS_DIR) $(SCORED)

## Build the deployed board (site/results.json) from the per-submission entries in
## site/results/*.json (best run per model), then guard it. Generated — not committed.
board:
	python -m benchmark.backend_score --build-board site/results site/results.json
	python .github/scripts/check_aggregate.py site/results.json
	@echo "Built site/results.json from site/results/*.json (aggregate only)."

## Self-run publish: score SUBS_DIR (gitignored answer key, stays local), stage each
## submission's public entry into site/results/, and rebuild the deployed board. Commit the
## new site/results/<slug>.json files; SUBS_DIR stays local.
publish-local: score-local
	mkdir -p site/results
	for f in $(SCORED)/board/*.json; do \
	  python .github/scripts/check_aggregate.py "$$f" && cp "$$f" site/results/; done
	$(MAKE) board
	@echo "Staged site/results/<slug>.json + rebuilt site/results.json. Commit the entries."

## Preview the leaderboard site locally (published to GitHub Pages on merge). Builds the
## board first so results.json (gitignored) exists for the preview.
serve: board
	@echo "Serving site/ at http://localhost:8000  (Ctrl-C to stop)"
	cd site && python3 -m http.server 8000

## Audit pred CLI capabilities against the pinned library commit.
audit:
	@if [ -z "$(LOCAL_REPO_DIR)" ]; then echo "Set LOCAL_REPO_DIR explicitly"; exit 1; fi
	python -m benchmark.pred_audit $(LOCAL_REPO_DIR)

## Install Python dependencies.
install-deps:
	pip install -r benchmark/requirements.txt

help:
	@echo "Targets:"
	@echo "  test                Run full pytest suite"
	@echo "  test-unit           Run unit tests only (no real repo needed)"
	@echo "  verify-calibration  Test verifier against fixtures (no AI needed)"
	@echo "  runner-build        Build the dockerized submission runner image"
	@echo "  runner-pull         Pull the prebuilt runner image from GHCR (fast runner-build alternative)"
	@echo "  preflight           Validate submission.env (1 tiny real call) before a full run"
	@echo "  run                 Run the benchmark via Docker → out/submission.json (not upload)"
	@echo "  run-local           Clone/verify a repo and run a local headless CLI agent"
	@echo "  score-local         Score SUBS_DIR submissions with the backend"
	@echo "  board               Build site/results.json from site/results/*.json (aggregate)"
	@echo "  publish-local       Score + stage per-submission entries + rebuild board (SUBS_DIR stays local)"
	@echo "  serve               Preview the leaderboard site locally (published via Pages on push to site/)"
	@echo "  audit               Audit pred CLI capabilities"
	@echo "  install-deps        Install Python requirements"
	@echo ""
	@echo "Variables:"
	@echo "  ENV_FILE=$(ENV_FILE)  (model/key for preflight + submission)"
	@echo "  LOCAL_BACKEND=$(LOCAL_BACKEND)  (codex default; set claude-code if desired)"
	@echo "  LOCAL_REPO_DIR=$(LOCAL_REPO_DIR)  (required for run-local/audit)"
	@echo "  LOCAL_OUTPUT=$(LOCAL_OUTPUT)  (required for run-local)"
	@echo "  LOCAL_LOG_DIR=$(LOCAL_LOG_DIR)  (required for run-local)"
