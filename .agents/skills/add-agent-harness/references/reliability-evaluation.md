# Harness reliability evaluation

## Contents

- [Purpose](#purpose)
- [Phase A: offline conformance](#phase-a-offline-conformance)
- [Phase B: real-harness controlled canary](#phase-b-real-harness-controlled-canary)
- [Phase C: failure semantics](#phase-c-failure-semantics)
- [Evidence file](#evidence-file)
- [Verdict rules](#verdict-rules)
- [Handoff](#handoff)

## Purpose

Evaluate whether a newly added agent harness reliably crosses the benchmark process
boundary. Do not evaluate how good its model is at finding reduction bugs.

The controlled evaluation must answer:

1. Can the runner start the requested CLI non-interactively with the intended prompt and
   model?
2. Can the harness execute shell commands in the supplied workspace and reach the real
   evaluation-owned `submit` command?
3. Does the runner preserve the authoritative attempt budget and derive rows only from the
   ledger?
4. Does the adapter parse completion, failure, and per-run token usage correctly?
5. Are raw logs and partial results preserved when something fails?

`bugs_found` is not an integration metric. Do not require a genuine reduction bug or run
`pred` during harness conformance.

## Phase A: offline conformance

Run without credentials, network access, model calls, or `pred`:

```bash
pytest -v benchmark/tests/test_<harness>_cli.py
pytest -v \
  benchmark/tests/test_<harness>_cli.py \
  benchmark/tests/test_codex_cli.py \
  benchmark/tests/test_claude_code.py \
  benchmark/tests/test_run_submission.py \
  benchmark/tests/test_submit_session.py
pytest -v -m "not integration"
```

Record the command, exit code, passed/failed/skipped counts, and log path. All commands must
exit zero. A skipped test that covers a required adapter invariant counts as a failure;
unrelated skips may remain but must be listed.

The harness-specific suite must prove:

- exact argv and configuration isolation;
- JSON/JSONL parsing and four-bucket usage accounting;
- missing executable, non-zero exit, timeout, and terminal event failures;
- stdout/stderr persistence;
- dispatch through the benchmark entry point;
- real file-backed `submit --status` and submit calls through `SubmissionSession` using a
  stub CLI and fake verifier;
- accepted and rejected attempts, budget exhaustion, ledger-derived rows, partial-result
  salvage, and schema-valid output.

## Phase B: real-harness controlled canary

This phase invokes the installed real harness and configured model. Show the resolved CLI
path/version, model, repository ref, submit limit, output path, and log directory, then get
explicit confirmation because it consumes model credits.

Use a dedicated conformance prompt, not the bug-finding prompt. Run inside a real
`SubmissionSession` with a controlled verifier:

- accept only the certificate whose rule is `HarnessProbe/Accepted`;
- reject the certificate whose rule is `HarnessProbe/Rejected`;
- never invoke `pred`.

Ask the harness to perform exactly this sequence:

1. Run `submit --status` and preserve its output.
2. Write an accepted fixture certificate under `$PRB_ARTIFACT_DIR` and submit it.
3. Write a rejected fixture certificate under `$PRB_ARTIFACT_DIR` and submit it.
4. Run `submit --status` again.
5. Emit the required completion marker and exit.

Use valid certificate shapes with distinct rules and a small synthetic `source`. The fake
verifier decides only from the rule name; the certificates are transport fixtures, not bug
claims.

Run the canary three times as three fresh sessions. Do not resume session state. Keep the
model, CLI version, prompt, adapter code, and verifier fixed across the three runs.

For every run record and verify:

| Invariant | Required result |
|---|---|
| Process | exit code `0`; no timeout |
| Runner | `run_error` absent |
| Status | both status commands visibly succeed |
| Attempts | exactly `2` attempts |
| Acceptance | exactly `1` accepted and `1` rejected |
| Order | accepted fixture first, rejected fixture second |
| Budget | remaining count decreases by exactly `2`; status calls are free |
| Rows | exactly two ledger-derived rows with matching rules and outcomes |
| Completion | required completion marker is present |
| Usage | documented per-run usage is present and total tokens are greater than zero |
| Logs | non-empty raw stdout log and captured stderr log exist |
| Isolation | no prior session is resumed and no result comes from files outside the run |

If the harness does not expose trustworthy per-run usage, classify it as `experimental`,
not `reliable`. Never estimate tokens from text length or cost.

## Phase C: failure semantics

Use deterministic stub processes, not the real model, to exercise:

1. accepted submit followed by CLI non-zero exit;
2. accepted submit followed by a structured terminal error with exit code zero;
3. accepted submit followed by timeout;
4. submit channel never probed;
5. malformed JSONL mixed with valid events.

Every case must set the appropriate `run_error`, preserve the accepted ledger row and raw
logs when present, and never report an infrastructure failure as a clean zero-result run.

## Evidence file

Write a machine-readable `harness-evaluation.json` outside the authoritative submission
path. Use this minimum shape:

```json
{
  "schema_version": "1.0",
  "harness": "kimi-code",
  "cli_path": "/absolute/path/to/kimi",
  "cli_version": "recorded exact output",
  "model": "provider/model",
  "adapter_commit": "git commit or worktree marker",
  "offline": {
    "commands": [],
    "passed": 0,
    "failed": 0,
    "required_skipped": 0
  },
  "canary": {
    "required_runs": 3,
    "successful_runs": 0,
    "runs": []
  },
  "failure_semantics": {
    "passed": 0,
    "failed": 0
  },
  "verdict": "reliable | experimental | unreliable",
  "reasons": []
}
```

Each canary run must include the invariant values from Phase B plus absolute stdout/stderr
log paths and the four usage buckets. Do not store credentials in the report or logs.

## Verdict rules

Apply the first matching rule:

| Verdict | Exact condition |
|---|---|
| `unreliable` | Any required offline test fails; any ledger/budget invariant fails; accepted results bypass `submit`; an infrastructure failure is reported as a clean run; or audit logs are missing |
| `experimental` | Offline conformance passes, but fewer than 3/3 canaries pass, usage is absent or untrustworthy, only stub tests were run, or the inspected CLI version differs from the smoke-tested version |
| `reliable` | All offline commands pass with zero required skips; all failure-semantics cases pass; and 3/3 fresh real-harness canaries satisfy every Phase B invariant with trustworthy usage and complete logs |

One successful live run is insufficient for `reliable`. A 2/3 result is `experimental`, not
“mostly reliable.” Re-run only after diagnosing and changing the adapter, configuration, or
harness version; do not discard a failed run and resample until three successes appear.

## Handoff

Report:

- the verdict and exact failed invariant, if any;
- CLI path/version and model;
- offline test totals;
- canary success count out of three;
- attempt, acceptance, budget, usage, and completion evidence per run;
- absolute paths to `harness-evaluation.json` and raw logs;
- whether any real model calls were made.

Do not describe an adapter as supported without attaching this evidence summary.
