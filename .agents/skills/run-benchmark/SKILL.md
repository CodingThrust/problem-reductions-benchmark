---
name: run-benchmark
description: Route a request to run, reproduce, smoke-test, or generate a submission for the problem-reductions benchmark. The current rankable route is the standardized Model API Top50 runner; coding-agent CLI execution is historical and non-ranking.
---

# Route a benchmark run

If the caller already has a `submission.json`, invoke `$submit-benchmark-result`.

For a current/rankable run, invoke `$run-api-benchmark`. Do not offer a backend choice: the frozen public contract is Model API only.

If the caller explicitly asks to reproduce a legacy Codex, Claude Code, or other coding-agent artifact, explain that it belongs to `legacy-whole-repo`, cannot enter the Top50 table, and invoke `$run-cli-benchmark` only after they confirm they want a non-ranking historical run.

The child skill owns preflight, execution, validation, and optional upload.
