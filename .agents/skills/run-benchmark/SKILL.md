---
name: run-benchmark
description: >-
  Run this repository's problem-reductions bug-finding benchmark end to end and produce a
  submission.json. Supports reproducible Docker/Podman runs and lightweight local headless
  runs with codex exec or claude -p, including pinned repository preparation, explicit output
  and log paths, preflight where applicable, and optional upload. Use when asked to run,
  reproduce, smoke-test, or generate a model submission. Not for pytest/unit-test requests.
---

# Run the benchmark

Run one whole-repository agent session. The agent chooses rules, search depth, and when to
stop. Do not add a step, turn, cost, or rule-count limit. The evaluation-owned `submit`
command is the only scored certificate channel; its run-wide attempt limit defaults to 100.

First determine two choices if the user has not already supplied them:

1. Frontend: reproducible container, or lightweight local headless CLI.
2. Goal: produce/test a local submission, or upload it to the official intake.

Do not ask for intake secrets unless upload is requested. Before any real model run, show the
resolved model, backend, target `PR_REF`, submit limit, output path, and log path; get explicit
confirmation because the run can consume time and API credits.

## Common configuration

Create `submission.env` from the template when absent. It is gitignored.

```bash
cp submission.env.example submission.env
```

Set `MODEL_NAME` and the authentication required by the selected backend. Read
`references/env-and-troubleshoot.md` when configuring a provider or diagnosing a failure.
Do not add the removed `AGENT_MODE`, `MAX_RULES`, or max-turn settings.

## Container path

Use this for the reproducible benchmark. The image pins the problem-reductions source,
compiled `pred`, Python dependencies, and mini-swe runtime.

1. Run `<skill-dir>/scripts/detect-engine.sh` and parse its `KEY=VALUE` output. Read
   `references/engines.md` only if no engine is available or the RAM hint is low.
2. Build with one consistent ref:

   ```bash
   make runner-build PR_REF=v0.6.0
   ```

   For Podman, use the equivalent raw build shown in `references/engines.md`.
3. For the default mini-swe backend, run `make preflight`. It checks `pred`, rule sources,
   and makes one tiny LiteLLM call. Do not describe this as a Codex/Claude CLI preflight.
4. After user confirmation, run `make run` (or the equivalent Podman command using the
   detector's `RUN_FLAGS`).
5. Confirm `out/submission.json` exists. Report `bugs_found`, `total_tokens_k`, submit attempts,
   and any `run_error`. Logs are also under `out/`; the configured submission path is the
   single authoritative JSON output.

An exit code 137 during build means the engine needs more memory. Never proceed after a
failed preflight.

## Lightweight local headless path

Use this for an installed `codex exec` or `claude -p`. It reuses the same benchmark CLI,
prompt, schema, verifier, and submit ledger as the container path.

Confirm these host prerequisites:

- `git` and the benchmark's Python dependencies;
- a `pred` binary matching the target benchmark version;
- `codex` authenticated (`codex login status`) or an authenticated Claude CLI.

Require all three paths explicitly and keep them separate:

- `LOCAL_REPO_DIR`: clone destination;
- `LOCAL_OUTPUT`: authoritative submission JSON path;
- `LOCAL_LOG_DIR`: agent logs (raw CLI stream for headless backends, normalized mini-swe log).

Run, for example:

```bash
make run-local \
  PR_REF=v0.6.0 \
  LOCAL_BACKEND=codex \
  LOCAL_REPO_DIR=../runs/problem-reductions-v0.6.0 \
  LOCAL_OUTPUT=../runs/results/submission.json \
  LOCAL_LOG_DIR=../runs/logs
```

Use `LOCAL_BACKEND=claude-code` for Claude. The default is Codex.

The runner clones `PR_REF` when `LOCAL_REPO_DIR` is absent. If it already exists, it must be
a Git checkout whose `HEAD` exactly matches the requested tag, branch, or commit. The runner
must not fetch, reset, or check out an existing working tree; on mismatch, use another path or
ask the user to update it.

Every backend starts by probing the evaluation-owned file-backed submission channel with
`submit --status`. A run with neither a successful status probe nor a ledger attempt is an
infrastructure failure and must carry `run_error`; do not report it as a clean zero. On such
a failure, check `LOCAL_LOG_DIR/salvaged-agent-artifacts/` for preserved certificates.

There is no local turn cap. `CODEX_SESSION_TIMEOUT` and `CLAUDE_CODE_SESSION_TIMEOUT` are only
hung-process backstops. Codex token totals come from its JSON `turn.completed` event.

## No-spend wiring smoke test

Only use this when the user wants to test runner wiring without invoking a model or `pred`:

```bash
python -m benchmark.run_submission \
  --fake --model fake/smoke \
  --repo-dir /tmp/prb-fake-repo \
  --output /tmp/prb-smoke/submission.json \
  --trajectory-dir /tmp/prb-smoke/logs
python -m benchmark.submit --predictions /tmp/prb-smoke/submission.json --dry-run
```

Clearly label this as a wiring check, not a benchmark result.

## Validate and optionally upload

Always validate the produced file before handoff or upload:

```bash
python -m benchmark.submit --predictions <submission.json> --dry-run
```

If the goal is local/test, stop after reporting the artifact paths. If the user explicitly
requested official submission and supplied `PRB_SUBMIT_URL` plus `PRB_API_KEY`, upload with:

```bash
python -m benchmark.submit --predictions <submission.json>
# add --test to keep an end-to-end intake test off the public leaderboard
```

Never upload merely because a run completed. The backend re-verifies accepted ledger
certificates with `pred`; only aggregate results become public.

## Failure handling

- Read actual command output and branch on it; never assume build, auth, or run success.
- Preserve a partial submission and logs when `run_error` is present; report it as partial,
  not as a clean zero-bug completion.
- Treat `submit channel was not successfully probed` as runner infrastructure failure, not
  agent performance. Preserve the logs and salvaged certificate artifacts.
- Do not silently install software, change engine settings, mutate an existing checkout, or
  upload data.
- For provider fields and common failures, read `references/env-and-troubleshoot.md`.
- For Docker/Podman setup, ownership, SELinux, or OOM issues, read `references/engines.md`.
