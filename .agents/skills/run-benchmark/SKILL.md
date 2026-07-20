---
name: run-benchmark
description: Configure, run, reproduce, smoke-test, or generate a submission for the standardized problem-reductions Self-selected Top50 benchmark, and route an existing submission to the upload workflow.
---

# Run the benchmark

If the caller already has a `submission.json`, invoke `$submit-benchmark-result`.

The benchmark uses source-only triage to freeze 50 rules, followed by 50 fresh sequential
episodes. Each rule receives M=10 model generations, E=12 shell actions, P=24 `pred` calls,
P_solve=10 solve calls, O=10000 observed characters, and exactly S=2 submit attempts. These
limits, prompts, inference settings, and the harness are part of the benchmark code.

Read [references/provider-config.md](references/provider-config.md) for endpoint setup and
`scripts/detect-engine.sh` before preparing the image.

## Collect only required choices

1. Ask for the Model API identifier.
2. Ask whether it uses the standard provider endpoint or a custom OpenAI-compatible `API_BASE`.
   Never ask the caller to paste a secret; direct them to the gitignored `submission.env`.
3. Ask whether to keep and validate locally or upload officially. `$submit-benchmark-result`
   owns upload.
4. Read `make -s print-benchmark-version`, compare it with the official
   [`main/VERSION`](https://github.com/CodingThrust/problem-reductions-benchmark/blob/main/VERSION),
   and show `Benchmark version: <checkout> (latest version: <main>)`. Wait for confirmation. If
   they differ, say the checkout is outdated and stop the official run.
5. Resolve `PR_REF` with `make -s print-pr-ref`, plus a fixed `STAMP` and
   `out/<stamp>/submission.json`. Do not ask for budget values.

## Configure and preflight

Create `submission.env` from the example when absent. Configure `MODEL_NAME`, a provider key or
`API_KEY`, and `API_BASE` only when needed. Do not add other execution settings.

Detect the container engine. Prefer `make runner-pull`; use `make runner-build` only when
necessary. Before the first real API call, show the redacted model/endpoint, built-in counters,
target ref, output path, and upload goal, then ask for confirmation.

Run `make preflight`. It verifies the frozen source and `pred`, then checks the endpoint and
credentials with a tiny model call. Never continue after a failed preflight.

## Run and validate

Explain that 50 isolated episodes can use substantial API credits, then get explicit
confirmation and run `make run STAMP=<stamp>`.

For local-only validation:

```bash
python -m benchmark.submit --predictions <submission.json> --dry-run
```

Report the contract, completed episode count, rankability, claimed/verified bug fields available
locally, cap-hit diagnostics, and absolute artifact path. Time, tokens, and cost are diagnostic
only.

For official upload, invoke `$submit-benchmark-result`. Never upload merely because the run
completed. Preserve partial checkpoints on failure; a `run_error` is not a clean zero.
