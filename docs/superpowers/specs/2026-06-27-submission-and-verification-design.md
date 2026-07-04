# Submission runner + Space submission & backend verification — design

Date: 2026-06-27
Branch: `score-by-distinct-rule-bugs`
Status: approved-by-default (user delegated autonomous build; no model API available for live testing)

> **Superseded in two areas** (this remains the historical design of record):
> 1. **Verification** is now a single round-trip check — a reduction is buggy on `a` iff
>    `solve(a) != solve(reduce(a))` (compared by value/feasibility), with an optional
>    `target_config` witness. The earlier 5-violation taxonomy was dropped; the violation
>    is now a *derived* label. See `benchmark/verify.py` and `CONTRIBUTING.md`.
> 2. **Budget** is no longer enforced via LiteLLM's dollar figure. Spend is recomputed from
>    token usage × the submitter-supplied price (`benchmark/cost.py`), so mini-swe-agent's
>    `cost_limit` uses our number; a scheduler `safety_margin` + per-call `max_tokens` back
>    it up. See `benchmark/run_mini.py` and `SUBMISSION.md`.

## Goal

Two halves of one pipeline:

1. **Dockerized model-runner.** Given a model (LiteLLM name), a provider API key, and a
   **$20 budget**, run the existing bug-hunting agent across the reduction rules and emit a
   single `submission.json` recording the bugs (rule counterexamples) it found.
2. **HF Space submission + backend verification.** On the existing Gradio Space
   (`isPANN/problem-reductions-benchmarks`), add an entry point to submit that `submission.json`,
   plus a server-side, zero-trust verification flow that re-checks every claimed bug and feeds
   the leaderboard.

No live model API is available, so everything must be exercisable without one: the runner via
`FakeRunner`, the verifier/scorer via the existing certificate fixtures, the Space submit logic
via pure unit tests.

## Context — what already exists (reuse, don't rebuild)

- `benchmark/run_mini.py::run_one` — runs one rule with mini-swe-agent + LiteLLM; LiteLLM's
  `cost_limit` is the hard per-rule budget; emits a certificate; calls `verify()`.
- `benchmark/scheduler.py::Scheduler` — runs model×rule under a shared budget, checkpoint/resume,
  writes a per-model results JSON. Already enforces a **total budget cap**.
- `benchmark/verify.py::verify / count_bugs` — zero-trust certificate re-validation via `pred`;
  `count_bugs` = distinct rules with a confirmed bug (one rule = one bug).
- `benchmark/runner.py` — `AgentRunner` interface, `FakeRunner` (no API), `MiniSweRunner` (real).
- `docker/Dockerfile` — builds `pred` from problem-reductions `v0.6.0`; runtime stage bundles the
  stdlib-only verifier. CMD = verifier calibration.
- `space-gradio/{app.py,leaderboard.py}` — read-only Gradio leaderboard. `leaderboard.py` keys
  ranked rows on `budget_cap == 20`; rows carry `bugs_found`, `rules_tested`, costs,
  `bug_certificates`, `placeholder`.

## Architecture

Mirrors the proven HF pattern (Open LLM Leaderboard / GAIA / SWE-bench):
**runner image → submissions store (PENDING) → backend scorer re-verifies → results store → leaderboard.**
We never trust self-reported bug counts; we re-run `pred` on every certificate. We don't even need
a hidden answer key — counterexamples are deterministically re-checkable.

```
[user] docker run runner  ──>  submission.json
                                     │ upload (Gradio Submit tab / hf upload)
                                     v
                           submissions store (HF dataset, PENDING)   [local dir in --local mode]
                                     │ poll
                                     v
                    backend_score.py  ──uses──>  verify_submission.py ──uses──> verify.py (pred)
                                     │ writes scored result + status
                                     v
                              results store ──> build_index ──> Space leaderboard
```

## Components

### 1. `benchmark/submission.schema.json`
Superset of `results.schema.json`. Adds an envelope so a submission is self-describing and
rankable, while `results[]` stays byte-compatible with what the scheduler already writes.

Required: `schema_version, model, library_commit, budget_cap, bugs_found, total_cost_usd,
total_tokens_k, rules_tested, results`. Optional: `submitted_by, runner_version, created_at,
efficiency_bugs_per_ktok, efficiency_bugs_per_dollar, notes, placeholder`.
`budget_cap` must be a number (20 for ranked runs). `results[]` items reuse the existing per-rule
shape (rule/result/cost/tokens_k/certificate/...).

### 2. `benchmark/run_submission.py` (the runner entry point)
Env-driven (Docker-friendly), CLI flags override env:
- `MODEL_NAME` (required, LiteLLM format), provider key via standard env (`ANTHROPIC_API_KEY`…).
- `BUDGET_USD` (default 20), `PER_RULE_BUDGET` (default 0.5), `OUTPUT` (default `/out/submission.json`),
  `REPO_DIR` (default `/app/pr-src`), `MAX_RULES` (optional cap for smoke runs), `FAKE=1` (FakeRunner).
- Builds `EnvContext` directly (repo_path + `find_pred_binary()` + pinned commit) — no git needed
  in the image. Lists rules from `<repo>/src/rules/*.rs`.
- Runs `Scheduler` with a **single model** and `total_budget = BUDGET_USD`. Reuses the scheduler's
  cap + per-rule reservation. Then assembles `submission.json` (envelope + scheduler's per-model
  result), stamping `budget_cap`, `schema_version`, `runner_version`, `created_at`.
- Writes to `OUTPUT`; prints a one-line summary. `--fake` path lets tests run with no API/pred.

### 3. Dockerfile — `runner` stage
New stage on top of `runtime`: copy problem-reductions **source** (`/src/src`, `Cargo.toml`) from
the build stage to `/app/pr-src`, `pip install -r benchmark/requirements.txt` (mini-swe-agent +
litellm), `ENV REPO_DIR=/app/pr-src BUDGET_USD=20 OUTPUT=/out/submission.json`,
`ENTRYPOINT ["python","-m","benchmark.run_submission"]`. Key passed at run time via `-e`,
never baked in. `runtime` stays the default target (verifier); `runner` is `--target runner`.

### 4. `benchmark/verify_submission.py` (authoritative scorer)
Pure server-side re-scoring of one `submission.json`:
- For each `results[]` entry that carries a `certificate`, re-run `verify(cert)`; mark
  `result = bug_found` only if accepted, else `rejected` with the verifier reason.
- `bugs_found` = `count_bugs(rescored)` (distinct rules) — ignores self-reported count.
- Recompute efficiencies from the submission's reported cost/tokens (cost is from the runner;
  we cross-check plausibility but can't recompute spend server-side).
- Returns `(scored_result_dict, report)` where `scored_result_dict` is leaderboard-shaped
  (model, budget_cap, bugs_found, rules_tested, costs, efficiencies, bug_certificates, results)
  and `report` lists per-certificate verdicts. CLI: `python -m benchmark.verify_submission sub.json`.

### 5. `benchmark/backend_score.py` (queue worker)
Implements the requests→results loop, HF or local:
- `--local <subs_dir> <results_dir>`: scan `subs_dir/*.json` lacking a sibling status, score each
  via `verify_submission`, write `<results_dir>/<safe_model>.json` + a `<name>.status.json`
  (`PENDING→RUNNING→FINISHED/FAILED`). Deterministic, no network → unit-testable.
- HF mode (`--hf-submissions <repo> --hf-results <repo>`): same loop over a HF dataset using
  `huggingface_hub` (snapshot_download submissions, upload scored results + status). Guarded import;
  only exercised when a token is present (not in CI).
- Runs inside the `runner`/`runtime` image (has `pred`). Can be a polling Space or an HF Job.

### 6. `space-gradio/submit.py` (pure submit logic, no gradio)
- `validate_submission(data) -> (ok: bool, errors: list[str], summary: dict)`:
  schema-required fields present; `budget_cap == 20`; `total_cost_usd <= budget_cap * 1.05`
  (plausibility); `results` non-empty; distinct-rule certificate count; flags an implausibly
  perfect/over-budget run. **Structural only — not authoritative scoring** (no `pred` in the Space).
- `push_submission(data, repo, token, submitted_by) -> str`: write the JSON to the submissions
  dataset under `submissions/<user>/<safe_model>-<created_at>.json` with status PENDING via
  `huggingface_hub`. Guarded import; returns the repo path. When no token → raise a clear error so
  the UI shows the PR fallback.

### 7. `space-gradio/app.py` — “🚀 Submit” tab
`gr.File` upload + model-name + optional contact. **Validate** button → runs `validate_submission`
and shows the structural report + recomputed distinct-rule count + a clear note that the backend
re-verifies every bug with `pred` (the displayed count is provisional). **Submit** button → if a
write token is configured, `push_submission` (PENDING) and confirm; else show copy-paste
`hf upload` / PR-fallback instructions. Update the About tab to describe the new flow.

## Data flow & trust

- Budget is enforced **inside the runner** (LiteLLM `cost_limit` per rule + scheduler total cap).
  The submission reports `total_cost_usd`; the backend cross-checks plausibility but the cap is the
  runner's job.
- The leaderboard count is **only** what the backend's `pred` re-verification confirms; the Space's
  client-side validation is a UX pre-check, never the score.
- De-dup to distinct rules (`count_bugs`) prevents padding the count with many variants of one rule —
  matches the branch's “score by distinct rule bugs”.

## Testing (no model API, no reliance on live `pred` in CI)

- `test_run_submission.py` — `FAKE=1` runner: produces schema-valid `submission.json`,
  `budget_cap==20`, total spend ≤ budget, `bugs_found` consistent with results. (unit)
- `test_verify_submission.py` — wrap the 3 fixtures into submissions; assert valid_bug → 1 bug,
  wrong_target/false-alarm → 0; report verdicts present. (`integration`, needs `pred`)
- `test_backend_score.py` — `--local` loop with `verify_submission` monkeypatched (or a
  zero-certificate submission) → result file + FINISHED status, idempotent on re-run. (unit)
- `space-gradio/tests/test_submit.py` — `validate_submission` accepts a good submission and rejects
  missing-field / wrong-budget / over-budget / empty-results cases. (unit)
- `submission.schema.json` validated by the existing minimal validator style.

## Out of scope (now)

- Live model runs (no API key available).
- HF OAuth identity / per-user rate limiting (documented as the next hardening step; structural
  gates + PR fallback ship first).
- Webhook-triggered HF Jobs (the polling `backend_score.py` is the v1; webhook is a later swap).
