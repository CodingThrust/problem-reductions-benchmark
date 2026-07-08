# Submitting a model run

The benchmark gives every model the **same step-limited agent session** and asks: how many
distinct reduction-rule bugs can it find?

```
  make run ─▶ submission.json ─▶ python -m benchmark.submit  ──▶  private store (R2)
                                          │
                    maintainer's scorer re-verifies every certificate with pred
                                          │
              only the aggregate is published ─▶ PR ─▶ GitHub Pages
```

Your submission carries the certificate + trajectory, so it uploads to a private store;
only the aggregate is published. Self-reported counts are never trusted — the score is
recomputed by `pred`.

## 1. Produce a `submission.json` (dockerized runner)

The runner image bundles the `pred` binary, the agent stack (mini-swe-agent + LiteLLM),
and the problem-reductions source pinned at `v0.6.0`. Any LiteLLM-routable provider key
works.

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
cp submission.env.example submission.env   # then edit the REQUIRED lines (model, key)
mkdir -p out
docker run --rm --env-file submission.env -v "$PWD/out:/out" \
  problem-reductions-runner:v0.6.0
# → ./out/submission.json      (or just: make run)
```

The **required lines are model + API key** (`MODEL_NAME`, a key). Everything else in the
template has a sane default — uncomment only what you need. The knobs, by tier:

| Tier | Vars | When |
|---|---|---|
| **Required** | `MODEL_NAME`, one API key (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / generic `API_KEY`) | always |
| **Non-standard provider** | `API_BASE`, `API_KEY`, `MODEL_KWARGS` (JSON of extra litellm kwargs: `api_version`, `custom_llm_provider`, `extra_headers`, …) | OpenRouter / gateway / local vLLM / Azure |
| **Limits (defaults = ranked config)** | `MAX_TOKENS=8192`, `MAX_RULES` | quick test runs / tuning only |
| **Custom prompt** | `AGENT_CONFIG`, `AGENT_STRATEGY_FILE` (mount the files too) | bring your own bug-hunting prompt |
| **Version pins** | `EXPECTED_PRED_VERSION` (empty disables), `EXPECTED_PRED_COMMIT` | debugging only — baked from the image build |

`MODEL_NAME` accepts any LiteLLM-routable name (`anthropic/…`, `openai/…`, `openrouter/…`,
`azure/…`, or `openai/<model>` against a custom `API_BASE`) — nothing is hardcoded to one
provider. (`REPO_DIR` / `OUTPUT` are container-internal and already defaulted; you don't set
them.)

> Runs are bounded by the agent step limit (35 steps per rule; 300 steps for a whole-repo
> session), not by a dollar budget — you pay your own bill. Raw token counts are recorded
> and travel in the submission (`usage_totals`); ranking is by **confirmed bugs**, with
> **bugs/Ktok** as the efficiency tie-break.

For example, a non-standard endpoint in `submission.env`:

```ini
MODEL_NAME=openai/my-model
API_BASE=https://my-gateway.example/v1
API_KEY=...
MODEL_KWARGS={"custom_llm_provider":"openai"}
```

Equivalently with raw `-e` flags (the env-file just bundles these):

```bash
docker run --rm \
  -e MODEL_NAME=openai/my-model \
  -e API_BASE=https://my-gateway.example/v1 \
  -e API_KEY=$MY_KEY \
  -e MODEL_KWARGS='{"custom_llm_provider":"openai","extra_headers":{"X-Org":"acme"}}' \
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

**Before the full run, validate your config** with one tiny real call so a bad key / wrong
endpoint surfaces now, not 20 rules in:

```bash
make preflight        # docker run --env-file submission.env <image> --preflight
```

It checks the `pred` binary + version, that the library rules are present, and makes one
minimal model call through the exact batch code path. It exits non-zero on any failure.

## 2. Submit it (CLI upload)

Submission is a **CLI upload** — no web form, and the file never enters git. Get the
endpoint URL + a token from the maintainer, then:

```bash
export PRB_SUBMIT_URL=<intake endpoint>   # from the maintainer
export PRB_API_KEY=<token>                 # from the maintainer

python -m benchmark.submit --predictions out/submission.json
#   --dry-run   validate locally, don't send
#   --test      scored + stored privately, but excluded from the public leaderboard
```

The CLI validates the file against `submission.schema.json`, then uploads it to a private
store (Cloudflare R2). The maintainer's scorer re-verifies it with `pred` (see §3) and opens
a PR that updates the aggregate `site/results.json`; merging deploys to **GitHub Pages**.
See `intake/cloudflare-worker/README.md` for the intake setup.

## 3. Backend verification (automatic, zero-trust)

`benchmark/backend_score.py` is the queue worker. It runs inside the same Docker image
(which has `pred`). You can reproduce the scoring locally on a directory of submissions:

```bash
# Local directory of submissions → scored results + leaderboard.json:
python -m benchmark.backend_score --local submissions/ results/scored/
```

For each `PENDING` submission it:

1. flips status `PENDING → RUNNING`,
2. re-runs `benchmark/verify_submission.py` — which calls `verify()` on **every**
   certificate and re-derives the bundle from `pred`, so a fabricated or tampered
   counterexample is rejected, and checks the certificate is reproduced in the model's own
   trajectory (provenance),
3. recomputes `bugs_found` as **distinct rules with a confirmed bug** (many certificates
   for one rule collapse to one — no count padding),
4. writes the scored result + a ranked `leaderboard.json`, and sets status
   `FINISHED` (or `FAILED` with a reason).

In production this runs unattended inside GitHub Actions: `score-from-r2.yml` pulls pending
submissions from private R2, re-verifies them, and opens a PR with the refreshed aggregate;
`publish-on-merge.yml` deploys the site when that PR merges — no external service to host.

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
- Sessions are bounded by the agent step limit and per call by `MAX_TOKENS`; token totals
  travel as raw 4-bucket counts (`usage_totals`) the backend recomputes `total_tokens_k` from.

## Status: validated against a live model

The runner pipeline is unit-tested end-to-end with `FakeRunner` + the certificate fixtures
**and** has been exercised against a live model API (a DeepSeek OpenAI-compatible endpoint
via `MODEL_NAME=openai/<model>` + `API_BASE`): preflight passes, and a real run drives the
agent across a rule and emits a schema-valid `submission.json`. PR scoring and GitHub Pages
publishing are live; full official runs are the remaining step.
