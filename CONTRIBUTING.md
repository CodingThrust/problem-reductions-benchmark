# Submitting a model run

The benchmark gives every model the **same $20 API budget** and asks: how many distinct
reduction-rule bugs (counterexamples) can it find? This document describes the end-to-end
submission pipeline.

```
  make run ─▶ submission.json ─▶ open a GitHub PR  (submissions/<handle>/<model>.json)
                                          │
                       PR check: schema-validate (auto, no pred)
                                          │
                 maintainer APPROVES the scoring run   ← trust boundary
                                          │
            pred re-verifies on the PR (zero trust) → verified result shown
                                          │
                 result is a REQUIRED check → maintainer MERGES
                                          │
              on merge: rebuild leaderboard.json ─▶ GitHub Pages (deploy)
```

The verified result is produced **on the PR, before merge** — you never merge a number you
haven't seen. Scoring (running `pred` on submitted input) only runs after a maintainer
approves it, and the result is a required check, so a PR can't be merged without one.
Self-reported counts are never trusted. Merge only publishes the already-verified result.

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
```

### Configure and run

All run config goes in **one env-file** so you don't juggle a dozen `-e` flags. Copy the
template, fill the two required lines, and run:

```bash
cp submission.env.example submission.env   # then edit the REQUIRED lines (model, key, price)
mkdir -p out
docker run --rm --env-file submission.env -v "$PWD/out:/out" \
  problem-reductions-runner:v0.6.0
# → ./out/submission.json      (or just: make run)
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

## 2. Submit it (GitHub pull request)

Submission is a **pull request** — there's no web upload form and no auto-running upload.
Add the file the runner produced as `submissions/<your-handle>/<model>.json` and open a PR:

```bash
# in your fork of the benchmark repo
mkdir -p submissions/<your-handle>
cp out/submission.json submissions/<your-handle>/<model>.json
git add submissions/<your-handle>/<model>.json
git commit -m "submit: <model>"
git push   # then open the PR on GitHub
```

On the PR, an automated check validates the file against `submission.schema.json`
(structure only). Then a **maintainer approves the scoring run** (running `pred` on
submitted input is the trust boundary), CI re-verifies every certificate with `pred`, and
the **verified result appears on the PR as a required check** — so you never merge a number
nobody has seen. After a maintainer merges, CI rebuilds the leaderboard and publishes the
static site to **GitHub Pages**. See `submissions/README.md`.

## 3. Backend verification (automatic, zero-trust)

`benchmark/backend_score.py` is the queue worker. It runs inside the same Docker image
(which has `pred`):

This is exactly what `.github/workflows/publish-on-merge.yml` runs after a submission PR
merges — you can reproduce the leaderboard locally:

```bash
# Local directory of submissions → scored results + leaderboard.json:
python -m benchmark.backend_score --local submissions/ results/scored/
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

In production this runs unattended inside GitHub Actions: `score-pr.yml` verifies on the PR
and `publish-on-merge.yml` rebuilds and deploys the leaderboard on merge — no external
service to host.

## Certificate format

Each row in `submission.json` carries a **counterexample certificate** — the JSON the
runner emits to claim a bug in a rule. The verifier (`benchmark/verify.py`) re-derives
everything from `pred` and never trusts the claim; the authoritative schema is
`benchmark/submission.schema.json`.

```json
{
  "rule": "MaximumIndependentSet/SimpleGraph/i32 -> IntegralFlowBundles",
  "source": {
    "type": "MaximumIndependentSet",
    "data": { ... },
    "variant": { ... }
  },
  "bundle": {
    "target": { "type": "IntegralFlowBundles" }
  },
  "target_config": "optional witness config"
}
```

Only `rule`, `source`, and the **target type** are required — the latter from
`bundle.target.type` (paste the full `pred reduce` bundle) or a top-level `target_type`
string. `target_config` is optional. Any `violation` / `note` you add is free-form; the
backend ignores it and derives the authoritative label itself:

| Label | Meaning |
|-------|---------|
| `optimum_not_preserved` | both feasible, but the round-tripped value differs |
| `feasibility_not_preserved` | source is solvable but the round-trip yields none |
| `spurious_solution` | the round-trip claims a solution the source has none of |

With `target_config` the verifier additionally checks that specific target solution,
catching `unsound_extraction` and `suboptimal_extraction` that the solver's own optimum
would hide. The round-trip judging itself is explained in the [README](README.md).

## Why this is hard to cheat

- The score is computed **server-side** from the raw certificates, never from the
  submission's `bugs_found` field.
- Counterexamples are **deterministically re-checkable** — we don't even need a hidden
  answer key; a bug either violates the rule under `pred` or it doesn't.
- Distinct-rule de-duplication caps the count at one per rule.
- The $20 budget is enforced inside the runner by recomputing spend from raw token usage ×
  your declared price (not the gateway's self-reported dollars), held back by a safety
  margin and bounded per call by `MAX_TOKENS`; the backend cross-checks reported spend.

## Status: validated against a live model

The runner pipeline is unit-tested end-to-end with `FakeRunner` + the certificate fixtures
**and** has been exercised against a live model API (a DeepSeek OpenAI-compatible endpoint
via `MODEL_NAME=openai/<model>` + `API_BASE`): preflight passes, and a real budgeted run
drives the agent across a rule and emits a schema-valid `submission.json`. PR scoring and
GitHub Pages publishing are live; full `$20` runs are the remaining step.
