# Submitting a model run

The benchmark gives every model the **same $20 API budget** and asks: how many distinct
reduction-rule bugs (counterexamples) can it find? This document describes the end-to-end
submission pipeline.

```
  docker run runner ─▶ submission.json ─▶ Space “🚀 Submit” tab ─▶ submissions dataset (PENDING)
                                                                          │
                                              backend_score.py (zero-trust pred re-verify)
                                                                          │
                                                 results + leaderboard.json ─▶ leaderboard
```

The headline number on the leaderboard is **only** what the backend's `pred`
re-verification confirms. Self-reported counts are never trusted.

## 1. Produce a `submission.json` (dockerized runner)

The runner image bundles the `pred` binary, the agent stack (mini-swe-agent + LiteLLM),
and the problem-reductions source pinned at `v0.6.0`. LiteLLM enforces the budget across
whatever provider key you supply.

```bash
# Build once (compiles pred from source — takes a few minutes):
docker build -f docker/Dockerfile --target runner -t problem-reductions-runner:v0.6.0 .

# Run. The API key is passed at run time via -e, never baked into the image:
mkdir -p out
docker run --rm \
  -e MODEL_NAME=anthropic/claude-sonnet-4-6 \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -e BUDGET_USD=20 \
  -v "$PWD/out:/out" \
  problem-reductions-runner:v0.6.0
# → ./out/submission.json
```

| Env var | Default | Meaning |
|---|---|---|
| `MODEL_NAME` | — (required) | LiteLLM model name (`anthropic/...`, `openai/...`, …) |
| `<PROVIDER>_API_KEY` | — | The key matching the model (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …) |
| `BUDGET_USD` | `20` | Total budget. **Must be 20 to be ranked.** |
| `PER_RULE_BUDGET` | `0.5` | Per-rule cost cap |
| `MAX_RULES` | (all) | Cap rules attempted (smoke runs) |
| `OUTPUT` | `/out/submission.json` | Where the submission is written |

No image yet? Smoke-test the wiring with no API key:
`make runner-smoke` (uses `FakeRunner`, writes a dummy submission).

## 2. Submit it

**Via the Space (recommended).** On
[the Space](https://huggingface.co/spaces/isPANN/problem-reductions-benchmarks),
open the **🚀 Submit** tab, upload `submission.json`, optionally add a contact handle,
click **Validate** then **Submit**. The Space runs a structural pre-check (required
fields, ranked budget, plausible spend) and queues the file in the submissions dataset
as `PENDING`.

**Manual fallback.** If the Space's auto-queue isn't configured, upload directly:

```bash
hf upload isPANN/problem-reductions-submissions submission.json \
  submissions/<your-handle>/<model>.json --repo-type dataset
```

## 3. Backend verification (automatic, zero-trust)

`benchmark/backend_score.py` is the queue worker. It runs inside the same Docker image
(which has `pred`):

```bash
# Local directory of submissions → scored results + leaderboard.json:
python -m benchmark.backend_score --local submissions/ results/scored/

# Or against the HF datasets (needs HF_TOKEN with write access to the results repo):
HF_TOKEN=… python -m benchmark.backend_score \
  --hf-submissions isPANN/problem-reductions-submissions \
  --hf-results     isPANN/problem-reductions-results
```

For each `PENDING` submission it:

1. flips status `PENDING → RUNNING`,
2. re-runs `benchmark/verify_submission.py` — which calls `verify()` on **every**
   certificate and re-derives the bundle from `pred`, so a fabricated or tampered
   counterexample is rejected,
3. recomputes `bugs_found` as **distinct rules with a confirmed bug** (many certificates
   for one rule collapse to one — no count padding),
4. writes the scored result + a ranked `leaderboard.json`, and sets status
   `FINISHED` (or `FAILED` with a reason).

To run it as a service, wrap `process_local` / `process_hf` in a polling loop, or trigger
it from a webhook on the submissions dataset as an HF Job.

## Why this is hard to cheat

- The score is computed **server-side** from the raw certificates, never from the
  submission's `bugs_found` field.
- Counterexamples are **deterministically re-checkable** — we don't even need a hidden
  answer key; a bug either violates the rule under `pred` or it doesn't.
- Distinct-rule de-duplication caps the count at one per rule.
- The $20 budget is enforced inside the runner (LiteLLM per-call cost tracking); the
  Space cross-checks that reported spend is within the cap.

## Status: framework

This pipeline is wired and unit-tested end-to-end with `FakeRunner` and the certificate
fixtures, but has **not** yet been exercised with a live model API. The first real `$20`
runs are pending an API key.
