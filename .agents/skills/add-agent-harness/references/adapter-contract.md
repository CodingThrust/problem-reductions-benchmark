# Agent harness adapter contract

## Runtime boundary

The harness adapter owns only the CLI process boundary:

```text
shared benchmark prompt
        |
        v
harness CLI in SubmissionSession.workdir
        |
        +-- pred from PATH
        +-- submit --status / submit certificate.json
        +-- JSON or JSONL stdout events
        v
{tokens_k, usage, error}
```

The runner continues to own the pinned repository context, `pred` validation, submission
budget, in-memory ledger, result rows, `submission.json`, backend re-verification, and score.

## Python interface

Expose one repository-session function compatible with `_run_backend()`:

```python
def run_repo_harness(
    model_name: str,
    ctx,
    *,
    trajectory_dir=None,
    config_path=None,
    strategy=None,
    api_key=None,
    session_timeout=None,
    submit_session=None,
) -> dict:
    return {"tokens_k": 0.0, "usage": Usage(), "error": None}
```

Accept harness-specific keyword arguments when needed, but keep `_run_backend()`'s common
call surface stable. A backend must launch exactly one whole-repository session.

Use these shared helpers:

- `load_rendered_prompts()` for the canonical system/task prompt;
- `child_env()` to preserve the injected `pred` and `submit` path;
- `run_headless_session()` for workspace, timeout, stdout/stderr logs, and normalized return;
- `Usage` for uncached input, output, cache-read, and cache-write tokens.

## Command requirements

The command must:

- be non-interactive and exit when the agent finishes;
- accept the requested model explicitly when supported;
- emit machine-readable events without terminal decoration;
- allow shell commands and reads of the absolute pinned repository path;
- allow writes only in the runner-provided scratch workspace where practical;
- avoid resuming a prior session;
- avoid ambient user/project instructions and external plugins where practical;
- impose no agent turn, step, rule-count, or cost cap;
- rely on the runner timeout only as a hung-process backstop.

If a CLI merges rather than replaces its system prompt, record that behavior and ensure the
benchmark prompt remains intact. Do not silently pretend prompt equivalence.

## Parser requirements

Keep parsing pure: an iterable of lines in, a dictionary out. Ignore malformed and unknown
events unless the CLI declares them terminal failures.

Return at least:

```python
{
    "trajectory": [],
    "usage": Usage(),
    "steps": 0,
    "result_event": None,
}
```

Prefer a final cumulative usage event. Otherwise deduplicate message IDs before summing
per-message usage. Map provider fields to `Usage` as follows:

| Benchmark bucket | Meaning |
|---|---|
| `input_tokens` | uncached prompt/input tokens |
| `output_tokens` | completion tokens, including reported reasoning tokens |
| `cache_read_tokens` | cached input read tokens |
| `cache_write_tokens` | cache creation/write tokens |

Never infer token counts from characters, prices, context-window size, or cumulative
machine-wide statistics.

## Required test matrix

### Pure command tests

- executable and non-interactive subcommand/flags;
- prompt placement and quoting through argv, not a shell string;
- model prefix normalization;
- structured output flag;
- permission/config-isolation flags;
- absence of turn/step/cost caps.

### Pure parser tests

- assistant text event;
- tool call and tool result events;
- cumulative usage;
- repeated message IDs or repeated cumulative events;
- cache-read/cache-write mapping;
- malformed JSON and unknown events;
- success terminal event;
- structured error terminal event.

### Stub-process tests

Create a temporary executable that emits representative events and optionally exits
non-zero. Verify:

- successful normalized return;
- missing CLI error;
- non-zero exit error with a useful tail;
- terminal error propagation even with exit code zero;
- raw stdout and sibling stderr logs;
- configured timeout behavior when practical without making the suite slow.

### Submission-channel wiring test

Use a real `SubmissionSession` with a fake verifier returning `Verdict(True, ...)`. The stub
CLI must discover `submit` through `PATH` and execute:

```sh
submit --status
submit "$PRB_ARTIFACT_DIR/stub-certificate.json"
```

Assert:

- `submit_session.reachable` is true;
- status does not consume an attempt;
- the certificate consumes exactly one attempt;
- `result_rows()` is derived from the accepted ledger;
- wrapping the backend through `run_submission.run()` produces a schema-valid envelope;
- a failing stub preserves any earlier accepted row and adds `run_error`.

Do not monkeypatch `result_rows()`, append to private ledger fields, or return certificates
from the adapter.

### Dispatch and CLI tests

- accepted backend name in argparse choices;
- direct dispatch calls the new backend exactly once;
- other backends are not invoked;
- invalid backend names remain rejected;
- explicit output and trajectory paths remain separate.

## Verification commands

Adapt filenames to the harness:

```bash
pytest -v benchmark/tests/test_<harness>_cli.py
pytest -v \
  benchmark/tests/test_<harness>_cli.py \
  benchmark/tests/test_codex_cli.py \
  benchmark/tests/test_claude_code.py \
  benchmark/tests/test_run_submission.py \
  benchmark/tests/test_submit_session.py
pytest -v -m "not integration"
python -m benchmark.submit --predictions <fake-output.json> --dry-run
```

The final dry-run is useful only when the wiring test writes a complete fake envelope. It
must remain clearly labeled as a no-spend wiring artifact, not a benchmark result.

## Validation tiers

Classify support honestly:

1. **Adapter unit-tested**: parser, dispatch, process errors, and submit wiring pass with a
   stub CLI.
2. **Installed harness supported**: the installed real CLI passes auth/preflight and one
   user-approved run while preserving logs and usage.
3. **Version compatibility recorded**: the smoke-tested CLI version and event fixtures are
   recorded, and CI continues to exercise the adapter contract without credentials or model
   spend.

Do not describe tier 1 as live-model validation, and do not claim compatibility with CLI
versions that were not inspected or smoke-tested.
