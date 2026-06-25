# Problem-Reductions Bug-Finding Benchmark

A benchmark that measures how efficiently AI models find bugs in reduction rules from the [problem-reductions](https://github.com/CodingThrust/problem-reductions) library (290+ rules).

**Live leaderboard:** https://ferrari-72.github.io/problem-reductions-benchmark/

## What this measures

A reduction rule maps problem A → problem B. A **bug** is a round-trip failure:

```
A  →(reduce)→  B  →(solve)→  s  →(extract)→  A'
```

If `A'` is an invalid solution to `A`, the rule has a bug. The AI finds these by constructing **counterexample certificates** — JSON objects that describe the violation and the evidence.

Three violation types:

| Type | Meaning |
|------|---------|
| `unsound_extraction` | A valid target solution extracts back to an invalid source solution |
| `incomplete_reduction` | Source has a solution but the reduced target has none |
| `suboptimal_extraction` | Extracted source solution is not optimal; a better one exists |

Every certificate is independently re-verified by `pred` — the AI's claim is never trusted directly.

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

Results must be written to `results/{safe_model}.json` following `benchmark/results.schema.json`.

## How to run locally

Requirements:
- `pred` binary in PATH (pinned commit `aa2d1a1` of problem-reductions)
- Python 3.11+ with dependencies: `pip install -r benchmark/requirements.txt`
- An API key for your model

```bash
# Set env vars
export ANTHROPIC_API_KEY=sk-...
export REPO_DIR=/path/to/problem-reductions   # must be at pinned commit

# Run a small session (2 rules, $2 budget)
make demo

# Rebuild the leaderboard index
make build-index

# Run all unit tests (no API key needed)
make test-unit

# Validate results files against schema
make validate-results
```

Key `make` targets:

| Target | Description |
|--------|-------------|
| `make test-unit` | All unit tests, no API key needed |
| `make verify-calibration` | Test verifier against 3 known fixtures |
| `make verify-judgment` | Robust equality + accept/reject judgment tests |
| `make validate-results` | Schema-check all `results/*.json` |
| `make build-index` | Rebuild `results/index.json` |
| `make demo` | Run a tiny real session + rebuild index |

## How to read the metrics

| Metric | Formula | When to use |
|--------|---------|-------------|
| `bugs_found` | distinct rules with a confirmed bug | **Primary ranking** — fully verifiable, cannot be inflated |
| `bugs/Ktok` | bugs ÷ tokens(K) | Tiebreak / efficiency reference — self-reported denominator |
| `bugs/$` | bugs ÷ USD spent | Tiebreak / cost-efficiency reference — self-reported denominator |

Rank by `bugs_found`. Among models that find the same number of bugs, `bugs/Ktok` breaks the tie (use `bugs/$` when optimizing for budget). The efficiency metrics divide by tokens/cost, which the submitter self-reports — treat them as informative, not authoritative.

Models that don't publish pricing can still compete on `bugs/Ktok`.
