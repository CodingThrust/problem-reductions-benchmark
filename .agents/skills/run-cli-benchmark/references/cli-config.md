# Coding-agent CLI configuration

Derive support from `benchmark.run_submission.BACKENDS` and `_run_backend()` at runtime.
Documentation may lag code.

## Built-in adapters

| Backend ID | Harness | Authentication | Model syntax |
|---|---|---|---|
| `codex` | Codex CLI | `codex login` or `OPENAI_API_KEY` | bare GPT name or `openai/...` |
| `claude-code` | Claude Code | Claude login, `CLAUDE_CODE_OAUTH_TOKEN`, or `ANTHROPIC_API_KEY` | bare Claude name or `anthropic/...` |

An agent named by the caller is not supported merely because its executable exists. It must
have a direct adapter, dispatch case, tests, and reliable harness-evaluation evidence.

## Required paths

| Path | Requirement |
|---|---|
| clone destination | absent, or exact checkout matching `PR_REF` |
| submission output | explicit authoritative JSON path |
| log directory | explicit and separate from the output file |

## Failure decoding

| Symptom | Action |
|---|---|
| CLI missing or unauthenticated | install/authenticate only with caller approval |
| checkout mismatch | use another path or ask the caller to update it; never mutate it |
| `pred` mismatch | install/build the matching benchmark version |
| submit channel not probed | infrastructure failure; preserve logs and salvaged artifacts |
| CLI exit, timeout, or structured failure | record `run_error` and keep partial ledger rows |
| missing trustworthy usage | do not claim a fully supported/reliable adapter |
