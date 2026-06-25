# Contributing to Problem-Reductions Benchmark

## Certificate JSON schema

A counterexample certificate is a JSON object submitted by an AI model to claim a bug in a reduction rule. The verifier (`benchmark/verify.py`) re-derives everything from `pred` — it never trusts the AI's claim directly.

Required fields:

```json
{
  "rule": "MaximumIndependentSetToMaximumClique",
  "violation": "unsound_extraction",
  "source": {
    "type": "MaximumIndependentSet",
    "data": { ... },
    "variant": { ... }
  },
  "bundle": {
    "source": { ... },
    "target": { "type": "MaximumClique", "data": { ... }, "variant": { ... } },
    "path": [ ... ]
  }
}
```

Additional fields by violation type:

| Violation | Extra required fields |
|-----------|----------------------|
| `unsound_extraction` | `target_config` (string), `claimed_source_solution` (list) |
| `incomplete_reduction` | none |
| `suboptimal_extraction` | `target_config` (string), `brute_force_solution` (list) |

## Violation types

**unsound_extraction**: A valid solution to the target problem, when extracted back to the source, yields an invalid source solution. The `target_config` string encodes the target solution; `claimed_source_solution` is what `pred extract` returned.

**incomplete_reduction**: The source problem has a valid solution, but the reduced target has none — the reduction lost the solution. No extra fields needed; the verifier checks both sides via `pred solve`.

**suboptimal_extraction**: The extracted source solution is not optimal. `target_config` is the target solution used for extraction; `brute_force_solution` is a strictly better source solution.

## Verification workflow

```bash
# Test the verifier against 3 canonical fixture certificates (no AI needed)
make verify-calibration

# Run robust equality and accept/reject judgment tests
make verify-judgment

# Full unit test suite
make test-unit
```

The three canonical fixtures are in `benchmark/tests/fixtures/`:
- `valid_bug.json` — must be **accepted**
- `wrong_target.json` — must be **rejected** (tampered bundle)
- `valid_solution_claimed_invalid.json` — must be **rejected** (false alarm)

`make verify-calibration` exits non-zero if any fixture gets the wrong verdict. Run it before submitting changes to `verify.py`.

## Adding a new model

1. Implement `AgentRunner` (see `benchmark/runner.py` and README).
2. Run a session and save results to `results/{safe_model}.json`.
3. Run `make validate-results` to confirm the file matches the schema.
4. Run `make build-index` to rebuild the index.
5. Open a PR — CI will re-validate and redeploy automatically.

## Running CI locally

```bash
python -m benchmark.validate_results --results-dir results
python -m benchmark.build_index --results-dir results
```

These are the exact commands the CI workflow runs before deploying.
