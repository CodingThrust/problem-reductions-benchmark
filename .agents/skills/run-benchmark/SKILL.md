---
name: run-benchmark
description: Route a request to run, reproduce, smoke-test, or generate a submission for the standardized problem-reductions Model API Top50 benchmark.
---

# Route a benchmark run

If the caller already has a `submission.json`, invoke `$submit-benchmark-result`.

For a benchmark run, invoke `$run-api-benchmark`. Do not offer a backend or protocol choice:
the benchmark has one built-in Model API contract.

The child skill owns preflight, execution, validation, and optional upload.
