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

   > What should happen after the run?
   >
   > 1. Keep and validate the result locally without uploading.
   > 2. Upload an official submission.

   The `$submit-benchmark-result` skill owns submission validation, authentication, and
   upload.

5. Read this checkout's benchmark version with `make -s print-benchmark-version`. Read the
   latest version from the official repository's
   [`main/VERSION`](https://github.com/CodingThrust/problem-reductions-benchmark/blob/main/VERSION);
   do not use the `problem-reductions` version or guess. Show this in the caller's language
   and wait for confirmation:

   > Benchmark version: `<checkout version>` (latest version: `<main/VERSION>`)

   If the versions differ, explain that the checkout is outdated and ask the caller to
   update it before an official run. If the latest-version lookup fails, show `unknown`
   rather than substituting the pinned `problem-reductions` version.

6. Resolve the internal problem-reductions pin with `make -s print-pr-ref`, plus
   `SUBMIT_LIMIT` (default 100) and three separate, explicit paths: clone destination,
   authoritative submission JSON, and log directory. Offer concrete defaults and ask
   whether to accept them.

## Verify the selected harness

For Codex, require `codex` plus either a successful `codex login status` or a configured
`OPENAI_API_KEY`. For Claude Code, require `claude` and its authenticated login/token flow.
For newly integrated agents, follow their adapter's documented auth probe.

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
- confirmed benchmark version and resolved commit;
- submit limit;
- clone destination, authoritative output path, and log path;
- local versus official-upload goal.

State that the run can consume substantial time or credits, then get explicit confirmation.

This route runs the selected CLI directly on the host. Select it only with
`LOCAL_BACKEND=<backend-id>`. Do not set `AGENT_BACKEND`, invoke `make run`/`make preflight`,
or start Docker/Podman for a CLI run.

Run through `make run-local` with the selected backend. If invoking
`benchmark.run_submission` directly, pass `--host-cli` with the same explicit values. For
example:

```bash
PR_REF="$(make -s print-pr-ref)"
make run-local \
  LOCAL_BACKEND=codex \
  LOCAL_REPO_DIR="../runs/problem-reductions-${PR_REF}" \
  LOCAL_OUTPUT=../runs/results/submission.json \
  LOCAL_LOG_DIR=../runs/logs
```

Every harness must begin with `submit --status`. If neither a status request nor a submit
attempt reaches the service, require `run_error` and inspect
`<log-dir>/salvaged-agent-artifacts/`; never report it as a clean zero.

## Validate and hand off

For option 1, validate the authoritative file locally:

```bash
python -m benchmark.submit --predictions <submission.json> --dry-run
```

Report `bugs_found`, `total_tokens_k`, submit attempts, any `run_error`, CLI warnings, and
absolute output/log paths. Preserve partial results and logs on failure.

For option 2, invoke `$submit-benchmark-result` with the authoritative path. Do not validate
it first: that skill owns validation, authentication, final confirmation, upload, scoring,
and PR reporting. Never upload merely because the run completed.
