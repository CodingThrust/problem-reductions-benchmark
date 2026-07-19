# Submitting a model run

The benchmark gives every model one repository-wide, self-terminating agent session and
asks: how many distinct reduction-rule bugs can it find?

```
  make run / make run-local ─▶ submission.json ─▶ python -m benchmark.submit
                                                              │
                                                     private store (R2)
                                                              │
                              maintainer re-verifies every certificate with pred
                                                              │
                                  aggregate only ─▶ PR ─▶ GitHub Pages
```

Your submission carries counterexample certificates plus the bounded submit ledger, so it
uploads to a private store; only the aggregate is published. Self-reported counts are never
trusted — the score is recomputed by `pred`.

## 1. Produce a `submission.json`

Both backends use the same runner and `submit` budget:

| Backend | Execution | Repository skill |
|---|---|---|
| Model API | mini-swe/LiteLLM in Docker | `$run-api-benchmark` |
| Coding-agent CLI | installed agent on the host | `$run-cli-benchmark` |

Use `$run-benchmark` to choose interactively.

### Model API backend (Docker)

The runner image bundles the `pred` binary, the agent stack (mini-swe-agent + LiteLLM),
and the problem-reductions source pinned at `v0.6.0`. Any LiteLLM-routable provider key
works.

The official round is pinned to the exact tag and commit listed in the
[README](README.md#current-benchmark-round). `PR_REF` selects the corresponding
problem-reductions source when preparing an image; the runner records the resolved commit
and its matching `pred`. Pull the published image for that ref, or build it locally when
unavailable:

```bash
make runner-pull PR_REF=v0.6.0
# Fallback: make runner-build PR_REF=v0.6.0
```

### Configure and run

All run config goes in **one env-file** so you don't juggle a dozen `-e` flags. Copy the
template, fill the model and (for mini-swe) API key, and run:

```bash
cp submission.env.example submission.env   # set model + mini-swe provider key
mkdir -p out
docker run --rm --env-file submission.env -v "$PWD/out:/out" \
  problem-reductions-runner:v0.6.0
# → ./out/submission.json      (or just: make run)
```

`MODEL_NAME` and any credentials required by the selected provider are required for the
mini-swe path. Everything else has a sane default — uncomment only what you need:

| Tier | Vars | When |
|---|---|---|
| **Required** | `MODEL_NAME`; mini-swe also needs one provider key or generic `API_KEY` | always |
| **Non-standard provider** | `API_BASE`, `API_KEY`, `MODEL_KWARGS` (JSON of extra litellm kwargs: `api_version`, `custom_llm_provider`, `extra_headers`, …) | OpenRouter / gateway / local vLLM / Azure |
| **Limits** | `MAX_TOKENS=8192`, `SUBMIT_LIMIT=100` | per-call output ceiling; run-wide certificate attempts |
| **Custom prompt** | `AGENT_CONFIG`, `AGENT_STRATEGY_FILE` (mount the files too) | bring your own bug-hunting prompt |
| **Version pins** | `EXPECTED_PRED_VERSION` (empty disables), `EXPECTED_PRED_COMMIT` | debugging only — baked from the image build |

`MODEL_NAME` accepts any LiteLLM-routable name (`anthropic/…`, `openai/…`, `openrouter/…`,
`azure/…`, or `openai/<model>` against a custom `API_BASE`) — nothing is hardcoded to one
provider. (`REPO_DIR` / `OUTPUT` are container-internal and already defaulted; you don't set
them.)

> Agents choose when to stop; there is no step, turn, or dollar limit. A run-wide `submit`
> budget (100 attempts by default) bounds scored counterexample claims.
> Every accepted, rejected, or malformed counterexample submission consumes one attempt;
> raw token counts are recorded and travel in the submission (`usage_totals`); ranking is by **confirmed bugs**, with
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

### Coding-agent CLI backend (host)

Install the Python dependencies and the pinned `pred`, then authenticate one supported CLI:

```bash
codex login       # for the default codex backend
# or authenticate the Claude CLI

cp submission.env.example submission.env
# Set MODEL_NAME; no key is needed in this file when the CLI login is already usable.

make run-local \
  LOCAL_REPO_DIR=../runs/problem-reductions-v0.6.0 \
  LOCAL_OUTPUT=../runs/results/submission.json \
  LOCAL_LOG_DIR=../runs/logs
# Claude alternative: add LOCAL_BACKEND=claude-code
```

The default CLI backend is Codex. Codex uses the
non-interactive `codex exec --json --ephemeral` interface with a `workspace-write`
sandbox; Claude uses `claude -p`. Both receive the same benchmark prompts and agent-only
`submit` command, and both produce the same schema as the API backend. All backends run
exactly one whole-repository session and stop themselves; no turn count is passed to either
CLI.

The CLI backend always runs on the host through `make run-local` and is selected with
`LOCAL_BACKEND`. Do not set `AGENT_BACKEND` or use Docker for Codex, Claude Code, or another
coding-agent CLI.

The runner gives every backend a writable scratch workspace. The `submit` CLI exchanges
atomic request/response files there while the attempt budget and verified ledger remain in
runner memory, avoiding sandbox-blocked sockets or localhost networking. The prompt begins
with the free `submit --status` health probe. If no status or submit request reaches the
service, the run is saved as partial with `run_error` instead of a misleading clean zero;
small certificate artifacts are salvaged under the configured log directory.

`run-local` clones `PR_REF` into the explicitly configured `LOCAL_REPO_DIR` when absent.
An existing checkout is only accepted when `HEAD` matches the requested ref; it is never
mutated automatically. Submission JSON and live/final logs go to the separate, required
`LOCAL_OUTPUT` and `LOCAL_LOG_DIR` paths.

The CLI backend is intentionally less hermetic: it uses the host binary, authentication,
Python environment, and `pred`. The runner still verifies the pinned `pred` version and
records the target commit; use the API backend when container-level reproducibility is
required.

#### Add another coding-agent CLI

Codex (`codex`) and Claude Code (`claude-code`) are built in. For another CLI, use
`$add-agent-harness`; do not substitute a different agent. The adapter must pass the
[contract tests](.agents/skills/add-agent-harness/references/adapter-contract.md) and a real
[smoke evaluation](.agents/skills/add-agent-harness/references/reliability-evaluation.md)
must produce `verdict: reliable`.

## 2. Submit it (CLI upload)

Submission is a **CLI upload** — no web form, and the file never enters git. Choose one:

- validate and keep the result locally with `--dry-run`;
- upload an intake test with `--test` (privately scored, excluded from the leaderboard);
- upload an official submission with neither flag.

Use `$submit-benchmark-result` when the result already exists. It validates the authoritative
file, preserves test/official intent, handles the authentication mode actually deployed by
the intake, asks before the external write, and reports the opaque submission ID. Submitters
do not need repository write access, R2 access, or permission to run the scoring workflow.

For either upload, get the endpoint URL from the maintainer and authenticate through the
GitHub-backed Cloudflare Access application:

```bash
export PRB_SUBMIT_URL=<intake endpoint>   # from the maintainer
PRB_ACCESS_APP="${PRB_SUBMIT_URL%/submit}"
PRB_ACCESS_TOKEN="$(cloudflared access token -app="$PRB_ACCESS_APP")" \
  python -m benchmark.submit --predictions out/submission.json
#   --dry-run   validate locally, don't send
#   --test      scored + stored privately, but excluded from the public leaderboard
```

The token is short-lived and scoped to this Access application. Keep token acquisition
inside command substitution because a standalone `cloudflared access login` or
`cloudflared access token` may print it. Never substitute `gh auth token`, a GitHub PAT, or
`GITHUB_TOKEN`.

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

1. validates the current submission structure, pinned library commit, and clean-run status, then flips
   status `PENDING → RUNNING`,
2. re-runs `benchmark/verify_submission.py` — which calls `verify()` on **every**
   certificate and re-derives the bundle from `pred`, so a fabricated or tampered
   counterexample is rejected, and checks new runs against the bounded submit ledger
   (legacy submissions retain trajectory provenance),
3. recomputes `bugs_found` as **distinct rules with a confirmed bug** (many certificates
   for one rule collapse to one — no count padding),
4. writes the scored result + a ranked `leaderboard.json`, and sets status `FINISHED`; a
   permanent input error is isolated under R2 `failed/`, while a retryable verifier or
   infrastructure failure remains in `incoming/` for the next run.

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
- Agents stop themselves. `MAX_TOKENS` only bounds one model response; token totals travel
  as raw 4-bucket counts (`usage_totals`) the backend recomputes `total_tokens_k` from.
- Counterexamples count only when the agent sends them through the evaluation-owned
  `submit` command. Its in-memory ledger enforces the shared `SUBMIT_LIMIT` atomically;
  certificates printed only in final prose or written to other files are ignored.

## Status: validated against a live model

The runner pipeline is unit-tested end-to-end in fake mode + the certificate fixtures
**and** has been exercised against a live model API (a DeepSeek OpenAI-compatible endpoint
via `MODEL_NAME=openai/<model>` + `API_BASE`): preflight passes, and a real run drives the
agent through a whole-repository session and emits a schema-valid `submission.json`. PR scoring and GitHub Pages
publishing are live; full official runs are the remaining step.
