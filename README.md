# Problem-Reductions Bug-Finding Benchmark

This benchmark measures how well a model can prioritize likely-buggy reduction rules and turn a fixed amount of evidence gathering into independently verified counterexamples.

## Current ranking contract

The primary track is **Standardized Model API / Self-selected Top50**, contract [`top50-evidence/v2`](benchmark/top50_budget.json). The target is `problem-reductions` [`v0.6.0`](https://github.com/CodingThrust/problem-reductions/commit/aa2d1a10cffa434871d12a4d6f411147fb7e08a8), and the bundled `pred` version is `0.6.0`.

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

Every terminal result uses the frozen `terminal-diagnostics/v1` policy: deterministic noise and repetition removal, diagnostic-aware head/tail selection, and a 10,000-character model preview. A separately bounded 1 MiB raw log remains read-only inside the current episode and can be inspected using a normal charged shell action.

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

It does not claim to measure host speed, network quality, willingness to run longer, coding-agent tooling, repeated-seed stability, or performance on a fixed organizer-selected Top50. Fixed Top50 diagnostics, multiple seeds, and a System Track are intentionally deferred or dropped.

A certificate is never trusted directly. The private scorer re-derives and replays it with pinned `pred`; only reproducible round-trip failures count.

## Run the rankable track

Install Docker, then:

```bash
cp submission.env.example submission.env  # set MODEL_NAME and API credentials
make runner-pull                           # or: make runner-build
make preflight                             # validates frozen path + one tiny API call
make run                                   # writes out/<timestamp>/submission.json
python -m benchmark.submit --predictions out/<timestamp>/submission.json
```

The rankable path is deliberately narrow. It rejects custom logical limits, unlimited counters, `S != 2`, custom prompts or strategies, custom model kwargs, and coding-agent backends before a scored generation. Model and `pred` watchdogs are safety controls and are not user-selectable score dimensions.

Useful local checks:

```bash
make test-unit
make verify-budget
make verify-calibration
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for artifact fields, submission, and historical-result handling.

## Historical results

Whole-repository results produced by the former contract remain visible under `legacy-whole-repo`. They use a different execution protocol and efficiency tie-break, so the site keeps them in a separate selectable table. They cannot be deduplicated, sorted, or compared into `top50-evidence/v2`.

The old host coding-agent and whole-repository runners remain in the repository only to reproduce those historical artifacts. They are not a public System Track and cannot produce rankable Top50 submissions.
