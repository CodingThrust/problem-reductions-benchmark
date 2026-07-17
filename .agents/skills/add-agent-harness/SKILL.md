---
name: add-agent-harness
description: Add, repair, or validate a headless coding-agent CLI harness backend for this problem-reductions benchmark, such as OpenCode, Kimi Code, Gemini CLI, or another agent runtime. Use when a requested agent harness is not yet listed in benchmark.run_submission.BACKENDS, when its CLI or event format changed, or when proving that a harness can use the evaluation-owned submit channel and produce a valid submission without spending model credits.
---

# Add an agent harness

Implement a first-class backend over the existing runner contract. Keep certificate
verification, the run-wide submit ledger, scoring, and submission format independent of the
harness.

Read [references/adapter-contract.md](references/adapter-contract.md) before editing code.
Read [references/reliability-evaluation.md](references/reliability-evaluation.md) before
claiming that an adapter is reliable.

## Discover the CLI contract

1. Inspect the installed CLI's `--help` and `--version` output when available.
2. For unstable or missing details, consult only the harness's official documentation or
   source. Confirm a non-interactive one-prompt command, model selection, structured event
   output, permission controls, exit behavior, and token-usage fields.
3. Distinguish a model provider from an agent harness. Use the existing mini-swe/LiteLLM
   backend when the request only changes the model API.
4. Stop and explain the gap if the CLI cannot run autonomously, execute `submit`, or expose
   reliable per-run usage. Do not invent usage. Offer an explicitly experimental local
   backend only when the user accepts the missing metric.

Do not install a CLI, authenticate it, or make a paid model call without the user's
authorization. Before a real harness run, show the resolved CLI version, model, repository
ref, submit limit, output path, and log path, then get explicit confirmation.

## Implement the adapter

Follow `benchmark/codex_cli.py` or `benchmark/claude_code.py`, and reuse
`benchmark/headless.py`.

1. Add `benchmark/<harness>_cli.py` with small, independently testable functions:
   - command construction;
   - child-environment construction;
   - JSON/JSONL event parsing into `Usage` and a terminal result event;
   - terminal-event error extraction;
   - one `run_repo_<harness>()` entry point calling `run_headless_session()`.
2. Render the shared prompt through `load_rendered_prompts()`. Do not fork the benchmark
   task text or completion protocol.
3. Run in `submit_session.workdir`, inherit the runner-injected `PATH`, and preserve stdout
   as the raw trajectory log. Do not replace the file-backed `submit` transport.
4. Disable ambient user rules, plugins, skills, MCP servers, and persistent sessions where
   the CLI supports it. Grant only the filesystem and shell capabilities needed to inspect
   the pinned repository, call `pred`, write artifacts, and call `submit`.
5. Add the backend name to `BACKENDS` and `_run_backend()` in
   `benchmark/run_submission.py`. Add explicit configuration parameters rather than a
   generic arbitrary-shell backend.
6. Update `Makefile`, `submission.env.example`, `README.md`, and `CONTRIBUTING.md` only for
   harness behavior users can actually run.

Preserve unrelated worktree changes.

## Prove the integration

Add `benchmark/tests/test_<harness>_cli.py`. Tests must not invoke a real model or require
`pred`.

Cover the complete matrix in the adapter contract, including:

- exact non-interactive command and model normalization;
- parser behavior for assistant, tool, usage, malformed, and terminal-error events;
- four-bucket `Usage` accounting without double counting;
- missing executable, non-zero exit, and structured failure propagation;
- dispatch through `run_submission.run()`;
- a stub executable running inside a real `SubmissionSession`, successfully calling
  `submit --status`, submitting a fixture certificate through the injected command, and
  leaving a ledger-derived result;
- raw stdout/stderr log persistence when a trajectory directory is configured.

Use a fake verifier for the stub certificate. This is a wiring test, not a benchmark score.
Do not weaken `SubmissionSession`, bypass the submit command, or insert result rows directly.

Run the new test file, the existing headless/backend tests, and then the full pred-free unit
suite. Validate any documented command against a stub or the installed CLI's help output.

## Evaluate reliability

After offline tests pass, run the controlled canary protocol in the reliability reference.
It uses the real harness and model but a fake certificate verifier, so it measures the
harness boundary rather than bug-finding ability or `pred`. Get confirmation before these
model calls.

Produce `harness-evaluation.json` with the CLI/model identity, offline test counts, three
canary run records, usage evidence, log paths, and a derived verdict. Apply the acceptance
table in the reference exactly; do not choose the verdict subjectively.

Never use `bugs_found` as harness-reliability evidence. A correct harness may find zero real
bugs, while a broken harness may print plausible certificates without reaching `submit`.

## Completion gate

Call the adapter complete only when:

- all required tests pass without network, credentials, model spend, or `pred`;
- the status probe reaches the authoritative submission service;
- accepted and rejected attempts consume the shared ledger correctly;
- a CLI/process failure becomes `run_error` while preserving partial ledger results;
- token usage is parsed from a documented per-run source;
- documentation states authentication, configuration isolation, supported CLI versions,
  and the authoritative output/log paths;
- `harness-evaluation.json` satisfies every `reliable` invariant in the reliability
  reference, including three consecutive successful canaries.

Report unsupported capabilities explicitly. Record the CLI version used by the local smoke
test, and do not claim compatibility with untested versions.
