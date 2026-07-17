---
name: run-api-benchmark
description: Configure and run this problem-reductions benchmark through a model API using the containerized mini-swe/LiteLLM backend. Use when the caller chooses an API, provider endpoint, gateway, hosted model, or container run. Guides provider configuration without collecting secrets, detects the container engine, runs preflight, produces submission.json, validates it, and uploads only when explicitly requested.
---

# Run the API benchmark

Run one whole-repository mini-swe session. The agent chooses rules, search depth, and when to
stop. Do not add a step, turn, cost, or rule-count limit. The evaluation-owned `submit`
command is the only scored certificate channel; `SUBMIT_LIMIT` defaults to 100.

Read [references/provider-config.md](references/provider-config.md) when configuring the
provider or diagnosing API/preflight failures. Use `scripts/detect-engine.sh` before build.

## Ask the API questions

Ask only for information the caller has not already supplied. Ask in this order and wait at
each numbered stage when its answer determines the next question. Use the product's
structured user-input UI when available; otherwise ask the quoted question in plain text.
Do not dump every configuration question into one message.

1. Ask:

   > Which API model should run the benchmark? Give its provider/model identifier, for
   > example `openai/gpt-...`, `anthropic/claude-...`, or `openrouter/...`.

2. Ask:

   > Does this model use the provider's standard endpoint, or a custom OpenAI-compatible
   > endpoint/gateway?

   For a custom endpoint, ask for `API_BASE` and any non-secret `MODEL_KWARGS`. Never ask the
   caller to paste an API key into chat. Tell them exactly which key variable to set locally
   in the gitignored `submission.env`, then wait for confirmation that it is configured.

3. Ask:

   > Is this a local/test submission, or should the completed file be uploaded to the
   > official intake?

   Default to local/test only when the caller explicitly delegates the choice. Do not ask
   for intake credentials unless upload is selected.

4. Resolve `PR_REF` (default `v0.6.0`), `SUBMIT_LIMIT` (default 100), authoritative output
   path, and log path. Offer defaults and ask the caller whether to accept them rather than
   demanding paths one at a time.

## Configure safely

Create `submission.env` from `submission.env.example` when absent. It is gitignored. Set or
guide the caller to set:

- `MODEL_NAME`;
- one provider-specific key or `API_KEY`;
- `API_BASE` and `MODEL_KWARGS` only when needed;
- `SUBMIT_LIMIT` when non-default.

Do not expose secret values in command output or the final response. Do not add removed
`AGENT_MODE`, `MAX_RULES`, or max-turn settings.

## Prepare and preflight

1. Run `scripts/detect-engine.sh` and parse its `KEY=VALUE` output. Read
   [references/engines.md](references/engines.md) only if no engine is available or the RAM
   hint is low.
2. Build with one consistent ref:

   ```bash
   make runner-build PR_REF=v0.6.0
   ```

   For Podman, use the equivalent command from `references/engines.md`.
3. Before the preflight's real API call, show the resolved model, backend `mini-swe`, API
   endpoint with secrets redacted, `PR_REF`, submit limit, output path, and log path. Ask for
   explicit confirmation.
4. Run `make preflight`. It checks `pred`, rule sources, and one tiny LiteLLM call. Stop on
   any failure; never proceed to a full run after a failed preflight.

## Run and validate

After preflight passes, state that the full session can consume substantial time and API
credits and ask for explicit confirmation to start it. Then run `make run` or the equivalent
Podman command using the detector's `RUN_FLAGS`.

Confirm the authoritative `submission.json` exists. Always validate it:

```bash
python -m benchmark.submit --predictions <submission.json> --dry-run
```

Report `bugs_found`, `total_tokens_k`, submit attempts, any `run_error`, and absolute output
and log paths. A `run_error` means partial salvage, not a clean zero-bug completion.

Upload only when the caller explicitly selected official intake and provided
`PRB_SUBMIT_URL` plus `PRB_API_KEY` locally:

```bash
python -m benchmark.submit --predictions <submission.json>
```

Add `--test` only when the caller requests an end-to-end intake test excluded from the
public board. Never upload merely because the run completed.

An exit code 137 means the engine needs more memory. Preserve partial outputs and read
actual command errors before recommending changes.
