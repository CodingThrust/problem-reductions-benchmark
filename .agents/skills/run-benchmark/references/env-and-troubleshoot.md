# Configuration and failure decoding

Read this when configuring a run or diagnosing a failure. `submission.env.example` remains
the source of truth.

## Backend and authentication

| Backend | Where | Authentication | Notes |
|---|---|---|---|
| `mini-swe` | Docker or host | `API_KEY` or provider-specific key | LiteLLM backend; Docker default |
| `codex` | host | existing `codex login` or `OPENAI_API_KEY`/generic `API_KEY` | `make run-local` default |
| `claude-code` | Docker or host | Claude login, `CLAUDE_CODE_OAUTH_TOKEN`, or `ANTHROPIC_API_KEY` | invokes `claude -p` |

`MODEL_NAME` is always required. Mini-swe accepts LiteLLM-routable names such as
`anthropic/...`, `openai/...`, `openrouter/...`, and `gemini/...`. Codex may use a bare GPT
model or `openai/...`; Claude may use a bare model or `anthropic/...`.

## submission.env

| Variable | Default | Purpose |
|---|---|---|
| `MODEL_NAME` | required | model identity and backend model selection |
| `AGENT_BACKEND` | `mini-swe` | `mini-swe`, `codex`, or `claude-code`; `make run-local` explicitly defaults to Codex |
| `API_BASE` | — | non-standard LiteLLM/OpenAI-compatible endpoint |
| `API_KEY` | — | generic key forwarded to the selected backend |
| `MODEL_KWARGS` | — | JSON object of extra LiteLLM kwargs |
| `MAX_TOKENS` | 8192 | mini-swe per-response output ceiling; not a run/turn limit |
| `SUBMIT_LIMIT` | 100 | run-wide certificate attempts; accepted, rejected, and malformed calls consume one |
| `AGENT_CONFIG` | bundled | complete prompt configuration override |
| `AGENT_STRATEGY_FILE` | — | extra strategy text injected into the prompt |
| `SUBMITTED_BY` | — | submitter metadata |
| `EXPECTED_PRED_VERSION` / `EXPECTED_PRED_COMMIT` | baked/default pins | debugging overrides |

Do not use the removed `AGENT_MODE`, `MAX_RULES`, or max-turn variables. Every backend runs
one whole-repository session and the agent stops itself.

For Docker, `OUTPUT=/out/submission.json` and `TRAJECTORY_DIR=/out` are the defaults. The
configured output is authoritative. Headless backends persist the raw stream and stderr;
mini-swe persists one normalized log. The submission itself uses the bounded submit ledger
for certificate provenance.

## Local Make variables

Local repository and artifact locations are not implicit environment defaults. Supply:

| Make variable | Required | Purpose |
|---|---|---|
| `PR_REF` | default `v0.6.0` | target tag, branch, or commit |
| `LOCAL_BACKEND` | default `codex` | `codex` or `claude-code` |
| `LOCAL_REPO_DIR` | yes | absent clone destination or exact existing checkout |
| `LOCAL_OUTPUT` | yes | stable submission JSON path |
| `LOCAL_LOG_DIR` | yes | live/raw/final logs, separate from submission output |
| `REPO_URL` | official upstream | clone source |

An existing `LOCAL_REPO_DIR` is never mutated. A mismatched `HEAD` is an error.

## Non-standard endpoint example

```ini
MODEL_NAME=openai/my-model
API_BASE=https://my-gateway.example/v1
API_KEY=...
MODEL_KWARGS={"custom_llm_provider":"openai"}
```

## Failure decoding

| Symptom | Meaning and action |
|---|---|
| Preflight `pred binary` fails | wrong/broken image or version override; rebuild at the intended `PR_REF` |
| Preflight `library rules` fails | container source tree missing/overridden; rebuild and do not override `REPO_DIR` |
| Preflight `model call` fails | use the printed auth, endpoint, routing, or model error to correct `submission.env` |
| Local checkout mismatch | choose a new `LOCAL_REPO_DIR` or update the existing checkout yourself |
| Local `pred` version mismatch | install/build `pred` for the benchmark version |
| CLI not found/not authenticated | install the selected CLI and complete its login flow |
| `submit channel was not successfully probed` | the agent sandbox could not reach the file-backed evaluation service, or skipped the required `submit --status`; preserve logs and `salvaged-agent-artifacts/` |
| Build exits 137 | engine VM/host needs more RAM; see `engines.md` |
| `run_error` in submission | partial salvage, not a clean zero-bug completion; preserve output and logs |
| Docker output owned by root | `chown` it or use rootless Podman with `--userns=keep-id` |

Preflight is the mini-swe/LiteLLM check. A logged-in Codex or Claude local run validates its
CLI and authentication when that CLI starts; do not claim Docker preflight exercises those
headless backends.
