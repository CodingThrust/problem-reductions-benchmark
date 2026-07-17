---
name: run-cli-benchmark
description: Configure and run this problem-reductions benchmark through an installed autonomous coding-agent CLI harness. Use when the caller requests Codex, Claude Code, Kimi Code, OpenCode, or another CLI agent. Lists the harnesses actually supported by benchmark.run_submission.BACKENDS, routes unsupported harnesses through add-agent-harness before returning to model selection, verifies CLI authentication and pinned pred, runs one whole-repository session, validates submission.json, and uploads only when explicitly requested.
---

# Run the CLI benchmark

Run one whole-repository coding-agent session with the shared benchmark prompt, verifier,
submit ledger, and schema. This skill owns only coding-agent CLI execution. Do not add a
step, turn, cost, or rule-count limit; session timeouts are hung-process backstops only.

Read [references/cli-config.md](references/cli-config.md) when resolving authentication,
paths, model names, or failures.

## Ask the CLI questions

Ask only for information the caller has not already supplied. Use the product's structured
user-input UI when available; otherwise ask the quoted question in plain text. Ask one
numbered stage at a time when its answer controls the next branch.

1. Inspect `benchmark.run_submission.BACKENDS` and the direct dispatch cases in
   `_run_backend()`. Display the currently supported coding-agent harnesses, excluding
   `mini-swe`. Use human names alongside backend IDs, for example:

   ```text
   Codex       (codex)
   Claude Code (claude-code)
   ```

   Then ask:

   > Which supported coding-agent CLI should run the benchmark?

2. If the requested harness is not supported, do not substitute another agent and do not
   continue to model selection. Explain that it must first be integrated, then ask:

   > `<agent>` is not currently supported. Should I invoke `$add-agent-harness` to implement
   > and validate its adapter first?

   On approval, invoke `$add-agent-harness`. Return to this skill only after the adapter is
   present in `BACKENDS` and its `harness-evaluation.json` verdict is `reliable`. Then display
   the refreshed supported list and continue.

3. Ask:

   > Which model should the selected CLI use?

   Use the CLI's model syntax; do not silently translate to an API/LiteLLM model name.

4. Ask:

   > Is this a local/test submission, or should the completed file be uploaded to the
   > official intake?

   Do not ask for intake credentials unless upload is selected.

5. Resolve `PR_REF` (default `v0.6.0`), `SUBMIT_LIMIT` (default 100), and three separate,
   explicit paths: clone destination, authoritative submission JSON, and log directory.
   Offer concrete defaults and ask whether to accept them.

## Verify the selected harness

For Codex, require `codex` and a successful `codex login status`. For Claude Code, require
`claude` and its authenticated login/token flow. For newly integrated agents, follow their
adapter's documented auth probe.

Also require:

- `git` and benchmark Python dependencies;
- a `pred` binary matching the benchmark version;
- an absent clone destination, or an existing Git checkout whose `HEAD` exactly matches
  `PR_REF`.

Never fetch, reset, or check out an existing mismatched working tree. Choose another path or
ask the caller to update it.

## Confirm and run

Before the first real model call, show:

- CLI human name and backend ID;
- resolved CLI path/version and authentication status;
- exact model;
- target `PR_REF` and resolved commit;
- submit limit;
- clone destination, authoritative output path, and log path;
- local/test versus official-upload goal.

State that the run can consume substantial time or credits, then get explicit confirmation.

Run through `make run-local` with the selected backend, or invoke
`benchmark.run_submission` directly with the same explicit values. For example:

```bash
make run-local \
  PR_REF=v0.6.0 \
  LOCAL_BACKEND=codex \
  LOCAL_REPO_DIR=../runs/problem-reductions-v0.6.0 \
  LOCAL_OUTPUT=../runs/results/submission.json \
  LOCAL_LOG_DIR=../runs/logs
```

Every harness must begin with `submit --status`. If neither a status request nor a submit
attempt reaches the service, require `run_error` and inspect
`<log-dir>/salvaged-agent-artifacts/`; never report it as a clean zero.

## Validate and hand off

Always validate the authoritative file:

```bash
python -m benchmark.submit --predictions <submission.json> --dry-run
```

Report `bugs_found`, `total_tokens_k`, submit attempts, any `run_error`, CLI warnings, and
absolute output/log paths. Preserve partial results and logs on failure.

Upload only when the caller explicitly selected official intake and configured
`PRB_SUBMIT_URL` plus `PRB_API_KEY` locally:

```bash
python -m benchmark.submit --predictions <submission.json>
```

Never upload merely because the run completed.
