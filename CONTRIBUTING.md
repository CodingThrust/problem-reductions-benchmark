# Contributing to Problem-Reductions Benchmark

## Certificate JSON schema

A counterexample certificate is a JSON object submitted by an AI model to claim a bug in a reduction rule. The verifier (`benchmark/verify.py`) re-derives everything from `pred` — it never trusts the AI's claim directly.

Fields:

```json
{
  "rule": "MaximumIndependentSet/SimpleGraph/i32 -> IntegralFlowBundles",
  "source": {
    "type": "MaximumIndependentSet",
    "data": { ... },
    "variant": { ... }
  },
  "bundle": {
    "target": { "type": "IntegralFlowBundles" }
  },
  "target_config": "optional witness config"
}
```

Only `rule`, `source`, and the **target type** are required — the latter from `bundle.target.type` (paste the full `pred reduce` bundle) or a top-level `target_type` string. `target_config` is optional. Any `violation` / `note` you add is free-form; the backend ignores it and derives the authoritative label itself.

## How a certificate is judged (round-trip)

The verifier re-derives the bundle from `source` with `pred reduce`, then checks that solving the source directly agrees with solving it through the reduction — by **value** (optimization) or **feasibility** (decision):

```
solve(source)  ==  solve(reduce(source))
```

A mismatch is a confirmed bug, reported with a derived label:

| Label | Meaning |
|-------|---------|
| `optimum_not_preserved` | both feasible, but the round-tripped value differs |
| `feasibility_not_preserved` | source is solvable but the round-trip yields none |
| `spurious_solution` | the round-trip claims a solution the source has none of |

If `target_config` is given, the verifier additionally checks that specific target solution — catching `unsound_extraction` (valid target solution extracts to an invalid source solution) and `suboptimal_extraction` (an optimal target solution extracts to a suboptimal source solution) that the solver's own optimum would hide. Values, never specific solutions, are compared, so multiple optima never cause a false mismatch.

## Verification workflow

```bash
# Test the verifier against the fixture certificates (no AI needed)
make verify-calibration

# Docs / CI / observability sanity tests (no pred, no network)
make verify-judgment

# Full unit test suite
make test-unit
```

Calibration covers both verdicts. The **reject path** uses published, safe fixtures in `benchmark/tests/fixtures/` (none is a real bug):
- `valid_bug.json` — rejected (its `target_config` was non-optimal; the round-trip recovers the optimum)
- `wrong_target.json` — rejected (tampered bundle)
- `valid_solution_claimed_invalid.json` — rejected (false alarm)

The **accept path** uses genuine-bug fixtures that are the benchmark answer key, so they are **not committed** — they live in a gitignored `benchmark/tests/fixtures/private/` (override with `BENCHMARK_PRIVATE_FIXTURES`). Calibration runs them when present and skips them on a public clone.

`make verify-calibration` exits non-zero if any fixture gets the wrong verdict. Run it before submitting changes to `verify.py`.

## Adding a new model

1. Implement `AgentRunner` (see `benchmark/runner.py` and README) — or just use the default `MiniSweRunner` via the Docker runner.
2. Produce a `submission.json` (`make submission`, or `python -m benchmark.run_submission …`).
3. Submit it on the Space; the backend re-verifies every certificate and ranks it. See `SUBMISSION.md`.

## Running CI locally

```bash
pytest -m "not integration"          # unit + pred-free sanity tests
python -m benchmark.verify --calibrate   # verifier calibration
```

These are the exact commands `.github/workflows/ci.yml` runs.
