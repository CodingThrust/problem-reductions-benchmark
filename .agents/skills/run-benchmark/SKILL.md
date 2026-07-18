---
name: run-benchmark
description: Route a request to run, reproduce, smoke-test, or generate a submission for this problem-reductions benchmark. Use when the caller has not yet chosen between a model API run and an installed coding-agent CLI run. Ask the execution-route question, then hand off to run-api-benchmark or run-cli-benchmark; do not implement either backend flow here.
---

# Route a benchmark run

Choose exactly one execution route before asking about models, credentials, paths, or
submission settings.

If the caller already explicitly requested an API, container, mini-swe, Codex, Claude Code,
or another coding-agent CLI, do not ask the route question again. Invoke the matching child
skill immediately.

Otherwise ask this question and wait:

> How should the benchmark call the model?
>
> 1. **Model API** — use the containerized mini-swe/LiteLLM runner with an API key or custom
>    endpoint.
> 2. **Coding-agent CLI** — use an installed autonomous coding agent such as Codex or Claude
>    Code.

Use the product's structured user-input UI when available; otherwise ask the same question
in plain text. Do not combine both routes in one run.

- For **Model API**, invoke `$run-api-benchmark` and follow it completely.
- For **Coding-agent CLI**, invoke `$run-cli-benchmark` and follow it completely.

The route also selects the runtime. Model API means mini-swe/LiteLLM in a container.
Coding-agent CLI means an installed host process through `make run-local`; never place
Codex, Claude Code, or another CLI harness inside the API container path.

The child skill owns all later questions, preflight, execution, validation, and optional
upload. Do not duplicate those workflows here.
