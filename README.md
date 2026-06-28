# Problem-Reductions Bug-Finding Benchmark

A benchmark that measures how efficiently AI models find bugs in reduction rules from the [problem-reductions](https://github.com/CodingThrust/problem-reductions) library (290+ rules).

The leaderboard is a static Hugging Face Space (`space/`, deploy with `make deploy-space`). Submitting is command-line only — there's no web upload form. See `SUBMISSION.md` to run and submit.

## What this measures

A reduction rule maps problem A → problem B. A **bug** is a round-trip failure:

```
A  →(reduce)→  B  →(solve)→  s  →(extract)→  A'
```

The rule is correct on an instance `a` only if solving it directly agrees with solving it through the reduction, compared by **value** (optimization) or **feasibility** (decision):

```
solve(a)  ==  solve(reduce(a))
```

A mismatch is a bug. The AI finds these by constructing **counterexample certificates** — a JSON object naming the source instance `a` and the rule; the backend re-derives the bundle and round-trips it with `pred`, so the AI's claim is never trusted directly. The mismatch is reported with a derived label (`optimum_not_preserved`, `feasibility_not_preserved`, or `spurious_solution`); an optional `target_config` witness can additionally expose extraction bugs on a specific target solution (`unsound_extraction` / `suboptimal_extraction`).

**Primary metric: bugs found** — the number of *distinct rules* with at least one confirmed bug, on a pinned library commit. One rule = one bug, no matter how many counterexamples (or violation types) target it. This count is fully verifiable and cannot be inflated by resubmitting certificates.
**Secondary metrics: bugs/Ktok and bugs/$** — token- and cost-efficiency. These have a self-reported denominator (tokens/cost), so they rank ties and serve as reference, not as the headline.

Provenance is intentionally *not* scored: on a fixed commit, a `pred`-confirmed certificate is a bug regardless of who or what produced it.

## How to add a model

Implement the `AgentRunner` interface in `benchmark/runner.py`:

```python
from benchmark.runner import AgentRunner

class MyRunner(AgentRunner):
    def run(self, ctx, model: str, rule_name: str, per_rule_budget: float) -> dict:
        # Run the model, return a certificate if a bug is found
        return {
            "rule": rule_name,
            "result": "bug_found",   # or "no_certificate" | "rejected" | "error:..."
            "cost": 0.05,            # USD spent
            "tokens_k": 12.3,        # tokens used (thousands)
            "certificate": {...},    # required when result == "bug_found"
        }
```

Then pass it to `Scheduler` in `benchmark/scheduler.py`. See `MiniSweRunner` for a full example.

A run is packaged as a `submission.json` (envelope around the per-rule rows, see `benchmark/submission.schema.json`) and submitted to the Space, where the backend re-verifies every certificate. See `SUBMISSION.md`.

## How to run locally

Requirements:
- `pred` binary in PATH (pinned commit `aa2d1a1` of problem-reductions)
- Python 3.11+ with dependencies: `pip install -r benchmark/requirements.txt`
- An API key for your model

```bash
# Run all unit tests (no API key needed) — this is what exercises the runner wiring
make test-unit

# Test the verifier against the fixtures (no API key)
make verify-calibration

# Configure your run, then validate it with one tiny real call before the full batch
cp submission.env.example submission.env   # fill in MODEL_NAME, key, PRICE_IN/OUT
make preflight

# Run the benchmark via Docker → ./out/submission.json (this does NOT upload it)
make run
```

Key `make` targets:

| Target | Description |
|--------|-------------|
| `make test-unit` | All unit tests, no API key needed |
| `make verify-calibration` | Test verifier against the fixtures (accept + reject paths) |
| `make verify-judgment` | Pred-free sanity tests (docs, CI, trajectory) |
| `make preflight` | Validate `submission.env` with one tiny real call before a full run |
| `make run` | Run the benchmark via Docker → `out/submission.json` (does not upload) |
| `make score-local` | Score submissions with the zero-trust backend |

## How to read the metrics

| Metric | Formula | When to use |
|--------|---------|-------------|
| `bugs_found` | distinct rules with a confirmed bug | **Primary ranking** — fully verifiable, cannot be inflated |
| `bugs/Ktok` | bugs ÷ tokens(K) | Tiebreak / efficiency reference — self-reported denominator |
| `bugs/$` | bugs ÷ USD spent | Tiebreak / cost-efficiency reference — self-reported denominator |

Rank by `bugs_found`. Among models that find the same number of bugs, `bugs/Ktok` breaks the tie (use `bugs/$` when optimizing for budget). The efficiency metrics divide by tokens/cost, which the submitter self-reports — treat them as informative, not authoritative.

Models that don't publish pricing can still compete on `bugs/Ktok`.
