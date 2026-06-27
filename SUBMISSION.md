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

The **target library version is not hardcoded** — it tracks the benchmark. The single knob
is the `PR_REF` build arg (a tag or commit of problem-reductions); the image bakes the
commit and `pred` version it actually built into itself, and the runner records/verifies
against those. Bump `PR_REF` and rebuild for each benchmark round.

```bash
# Build once (compiles pred from source — takes a few minutes).
# --build-arg PR_REF=<tag-or-commit> selects the library version (default in the Dockerfile):
docker build -f docker/Dockerfile --target runner \
  --build-arg PR_REF=v0.6.0 -t problem-reductions-runner:v0.6.0 .

# (build args, version pins — see "Library version" below)
```

### Configure and run

All run config goes in **one env-file** so you don't juggle a dozen `-e` flags. Copy the
template, fill the two required lines, and run:

```bash
cp submission.env.example submission.env   # then edit the REQUIRED lines (model, key, price)
mkdir -p out
docker run --rm --env-file submission.env -v "$PWD/out:/out" \
  problem-reductions-runner:v0.6.0
# → ./out/submission.json      (or just: make submission)
```

The **required lines are model + API key + price** (`MODEL_NAME`, a key, `PRICE_IN`/`PRICE_OUT`).
Everything else in the template has a sane default — uncomment only what you need. The knobs,
by tier:

| Tier | Vars | When |
|---|---|---|
| **Required** | `MODEL_NAME`, one API key (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / generic `API_KEY`), `PRICE_IN`, `PRICE_OUT` | always — see the price note below |
| **Pricing (caching)** | `PRICE_CACHE_READ`, `PRICE_CACHE_WRITE` | prompt-caching models |
| **Non-standard provider** | `API_BASE`, `API_KEY`, `MODEL_KWARGS` (JSON of extra litellm kwargs: `api_version`, `custom_llm_provider`, `extra_headers`, …) | OpenRouter / gateway / local vLLM / Azure |
| **Budget (defaults = ranked config)** | `BUDGET_USD=20`, `PER_RULE_BUDGET=0.5`, `SAFETY_MARGIN=1`, `MAX_TOKENS=8192`, `MAX_RULES` | quick test runs / tuning only |
| **Custom prompt** | `AGENT_CONFIG`, `AGENT_STRATEGY_FILE` (mount the files too) | bring your own bug-hunting prompt |
| **Version pins** | `EXPECTED_PRED_VERSION` (empty disables), `EXPECTED_PRED_COMMIT` | debugging only — baked from the image build |

`MODEL_NAME` accepts any LiteLLM-routable name (`anthropic/…`, `openai/…`, `openrouter/…`,
`azure/…`, or `openai/<model>` against a custom `API_BASE`) — nothing is hardcoded to one
provider. (`REPO_DIR` / `OUTPUT` are container-internal and already defaulted; you don't set
them.)

> Why you pass the price (and why it's required): you pay your own bill at your own rate, so
> you set it. The runner computes spend from raw token counts × your price rather than
> trusting the gateway's dollar figure (which can be stale or wrong — LiteLLM $0-pricing
> incidents, Anthropic prompt-cache mis-pricing ~10×), so `$20` is a real cap. There is
> deliberately **no built-in price table**: a wrong default would silently mis-meter the
> budget, so a real run fails fast without `PRICE_IN`/`PRICE_OUT`. The backend re-verifies
> bugs regardless and ranks on **bugs/Ktok** (token counts are auditable); self-reported
> dollars are advisory only.

For example, a non-standard endpoint in `submission.env`:

```ini
MODEL_NAME=openai/my-model
API_BASE=https://my-gateway.example/v1
API_KEY=...
MODEL_KWARGS={"custom_llm_provider":"openai"}
PRICE_IN=1.5
PRICE_OUT=6.0
```

Equivalently with raw `-e` flags (the env-file just bundles these):

```bash
docker run --rm \
  -e MODEL_NAME=openai/my-model \
  -e API_BASE=https://my-gateway.example/v1 \
  -e API_KEY=$MY_KEY \
  -e MODEL_KWARGS='{"custom_llm_provider":"openai","extra_headers":{"X-Org":"acme"}}' \
  -e PRICE_IN=1.5 -e PRICE_OUT=6.0 -e BUDGET_USD=20 \
  -v "$PWD/out:/out" \
  problem-reductions-runner:v0.6.0
```

**Customize the agent's bug-hunting prompt** without rebuilding — mount your own files:

```bash
docker run --rm --env-file submission.env \
  -e AGENT_STRATEGY_FILE=/cfg/strategy.md \
  -v "$PWD/cfg:/cfg" -v "$PWD/out:/out" \
  problem-reductions-runner:v0.6.0
# strategy.md is injected into the prompt's reserved {{strategy}} slot.
# For a full prompt rewrite instead, mount a config.yaml and set AGENT_CONFIG=/cfg/config.yaml.
```

**Before the full run, validate your config** with one tiny real call (a fraction of a cent)
so a bad key / wrong endpoint / missing price surfaces now, not 20 rules in:

```bash
make preflight        # docker run --env-file submission.env <image> --preflight
```

It checks the `pred` binary + version, that the library rules are present, and makes one
minimal model call through the exact batch code path (validating credentials, endpoint,
`model_kwargs`, and that pricing computes). It exits non-zero on any failure. (The runner's
no-API wiring is covered by the pytest suite, not a separate command.)

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

### Running the scorer as a service

**Recommended: webhook → HF Job (event-driven).** Register a webhook on the submissions
dataset that fires an HF Job on every change; the Job runs `backend_score --webhook`,
reads the delivery from `WEBHOOK_PAYLOAD`, and re-runs the (idempotent) queue. No
always-on Space — free Spaces auto-pause after 48h and have ephemeral disk, so a polling
loop there stops silently.

```python
# one-off registration (needs a write token):
from benchmark.backend_score import register_webhook
register_webhook("isPANN/problem-reductions-submissions",
                 job_id="<your-hf-job-id>", secret="<shared-secret>", token="<HF_TOKEN>")
```

The Job runs this image with:

```bash
# env injected by HF: WEBHOOK_PAYLOAD, WEBHOOK_SECRET; you set SUBMISSIONS_REPO/RESULTS_REPO/HF_TOKEN
python -m benchmark.backend_score --webhook
```

Only `repo.*` content events trigger scoring; discussion/comment events are ignored, and
the shared `WEBHOOK_SECRET` is checked before any work runs.

**Simpler fallback: polling.** Wrap `process_local` / `process_hf` in a
`while True: …; sleep(N)` loop on paid always-on hardware. Fine for a quick start, but the
webhook→Job path is cheaper (pay-per-minute) and more robust.

## Why this is hard to cheat

- The score is computed **server-side** from the raw certificates, never from the
  submission's `bugs_found` field.
- Counterexamples are **deterministically re-checkable** — we don't even need a hidden
  answer key; a bug either violates the rule under `pred` or it doesn't.
- Distinct-rule de-duplication caps the count at one per rule.
- The $20 budget is enforced inside the runner by recomputing spend from raw token usage ×
  your declared price (not the gateway's self-reported dollars), held back by a safety
  margin and bounded per call by `MAX_TOKENS`; the Space cross-checks reported spend.

## Status: framework

This pipeline is wired and unit-tested end-to-end with `FakeRunner` and the certificate
fixtures, but has **not** yet been exercised with a live model API. The first real `$20`
runs are pending an API key.
