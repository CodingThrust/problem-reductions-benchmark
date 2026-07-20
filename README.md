# Problem-Reductions Bug-Finding Benchmark

This benchmark measures how well a model can prioritize likely-buggy reduction rules and turn a fixed amount of evidence gathering into independently verified counterexamples.

## Current benchmark

The benchmark protocol is **Standardized Model API / Self-selected Top50**. The target is `problem-reductions` [`v0.6.0`](https://github.com/CodingThrust/problem-reductions/commit/aa2d1a10cffa434871d12a4d6f411147fb7e08a8), and the bundled `pred` version is `0.6.0`. The logical limits are part of the benchmark code, not a user-selectable configuration.

Each run has two phases:

1. Source-only triage freezes exactly 50 unique rules and a short hypothesis for each.
2. The runner opens 50 fresh, sequential, isolated episodes in shortlist order.

Every episode receives the same immutable logical budget:

| Counter | Limit per selected rule |
|---|---:|
| Model generations (`M`) | 10 |
| Shell actions (`E`) | 12 |
| Total `pred` calls (`P`) | 24 |
| `pred solve` calls (`P_solve`) | 10 |
| Submit attempts (`S`) | **2** |
| Automatic preview per action (`O`) | 10,000 characters |

Triage receives 8 model generations and 12 source-only actions. Unused budget never transfers between rules. Process timeouts remain fixed watchdogs for hung model or `pred` calls; elapsed time, network delay, tokens, and cost do not affect rank.

Every terminal result receives deterministic noise and repetition removal, diagnostic-aware head/tail selection, and a 10,000-character model preview. A separately bounded 1 MiB raw log remains read-only inside the current episode and can be inspected using a normal charged shell action.

The budget was selected once using a human-reviewed non-ranking development replay, with smaller and larger candidates around the chosen `M` and `P`. The checked-in record and rationale are in [budget-calibration.md](benchmark/docs/budget-calibration.md); validate their internal and release consistency offline with:

```bash
python -m benchmark.calibrate_budget --check benchmark/docs/budget-calibration.json
```

## Metric and fairness claim

The primary metric is **verified distinct-rule bugs among the model's frozen Top50**. One rule counts at most once. Equal bug counts are ties—there is no token-efficiency or time tie-break.

This score reflects a combined ability:

- prioritize the 50 rules most likely to contain bugs;
- form useful source-level hypotheses;
- allocate bounded model, shell, and `pred` calls within each rule;
- construct a valid counterexample within two submission opportunities.

It does not claim to measure host speed, network quality, willingness to run longer, repeated-seed stability, or performance on a fixed organizer-selected Top50.

A certificate is never trusted directly. The private scorer re-derives and replays it with pinned `pred`; only reproducible round-trip failures count.

## Run the benchmark

Install Docker, then:

```bash
cp submission.env.example submission.env  # set MODEL_NAME and API credentials
make runner-pull                           # or: make runner-build
make preflight                             # validates frozen path + one tiny API call
make run                                   # writes out/<timestamp>/submission.json
python -m benchmark.submit --predictions out/<timestamp>/submission.json
```

The runner exposes only model identity, credentials, and endpoint configuration. Logical budgets, prompts, inference settings, and the execution harness are built into the benchmark. Model and `pred` watchdogs are safety controls and are not user-selectable score dimensions.

Useful local checks:

```bash
make test-unit
make verify-budget
make verify-calibration
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for artifact fields and submission handling.
