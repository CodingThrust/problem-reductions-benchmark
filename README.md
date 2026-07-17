# Problem-Reductions Bug-Finding Benchmark

A benchmark that measures how efficiently AI models find bugs in reduction rules from the [problem-reductions](https://github.com/CodingThrust/problem-reductions) library (290+ rules).

The leaderboard is a static site (`site/`) published to **GitHub Pages**. Submitting uses a CLI (`python -m benchmark.submit`) that uploads your run to a private store; the maintainer re-verifies it with `pred` and publishes only the aggregate. See [CONTRIBUTING.md](CONTRIBUTING.md) to run and submit.

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
**Secondary metric: bugs/Ktok** — token efficiency. It has a self-reported denominator (tokens), so it ranks ties and serves as reference, not as the headline.

Provenance is intentionally *not* scored: on a fixed commit, a `pred`-confirmed certificate is a bug regardless of who or what produced it.

## How to add a model

Implement one repository-session function, following `run_repo_codex` or
`run_repo_claude`:

```python
def run_repo_my_agent(model, ctx, *, trajectory_dir=None, submit_session=None, **kwargs):
    # Run one repository-wide session. Scored rows come from submit_session, not this return.
    return {"tokens_k": 12.3, "usage": usage, "error": None}
```

Add its direct dispatch case to `_run_backend()` in `benchmark/run_submission.py`.

A run is packaged as a `submission.json` (see `benchmark/submission.schema.json`) and uploaded with `python -m benchmark.submit`. See [CONTRIBUTING.md](CONTRIBUTING.md).

During evaluation, counterexamples use a different, agent-only command:

```bash
submit certificate.json   # accepted or rejected: consumes one attempt
submit --status           # inspect the remaining budget: free
```

The runner owns one shared counter for the complete run (default `SUBMIT_LIMIT=100`),
verifies every call immediately, and derives scored result rows only from its accepted
ledger. The CLI crosses Codex, Claude, mini-swe, and container sandboxes through an atomic
file queue inside a disposable agent workspace; the authoritative budget and ledger stay
in runner memory. Every session must successfully run the free `submit --status` probe, or
the output is marked with `run_error` rather than reported as a clean zero. Certificates
printed only in the agent's final response do not count.

## How to run locally

Requirements:
- `pred` binary in PATH (pinned commit `aa2d1a1` of problem-reductions)
- Python 3.12 with dependencies: `pip install -r benchmark/requirements.txt`
- Either a provider API key, or a logged-in Claude/Codex CLI for headless mode

```bash
# Run all unit tests (no API key needed) — this exercises the backend wiring
make test-unit

# Test the verifier against the fixtures (no API key)
make verify-calibration

# Configure your run. Add an API key for mini-swe; a logged-in headless CLI needs only
# MODEL_NAME in this file.
cp submission.env.example submission.env

# Reproducible Docker batch (mini-swe by default): validate, then run
make preflight
make run

# Or run directly on the host through a lightweight frontend agent:
make run-local \
  LOCAL_REPO_DIR=../runs/problem-reductions-v0.6.0 \
  LOCAL_OUTPUT=../runs/results/submission.json \
  LOCAL_LOG_DIR=../runs/logs                                  # codex exec

# Claude alternative: add LOCAL_BACKEND=claude-code
```

`run-local` clones `PR_REF` into `LOCAL_REPO_DIR` when the path is absent. If the path
already exists, its `HEAD` must match that ref; the runner never resets or checks out an
existing working tree. `LOCAL_OUTPUT` and `LOCAL_LOG_DIR` are deliberately separate and
required. Local mode runs one self-terminating whole-repository session with the same
run-wide `submit` budget as Docker. There is no agent step or turn limit; the six-hour CLI
timeout and per-command timeout only guard against hung processes.

Key `make` targets:

| Target | Description |
|--------|-------------|
| `make test-unit` | All unit tests, no API key needed |
| `make verify-calibration` | Test verifier against the fixtures (accept + reject paths) |
| `make verify-judgment` | Pred-free sanity tests (docs, CI, trajectory) |
| `make preflight` | Validate `submission.env` with one tiny real call before a full run |
| `make run` | Run the benchmark via Docker → `out/submission.json` (does not upload) |
| `make run-local` | Run locally via `codex exec` or `claude -p` → the same output schema |
| `make score-local` | Score submissions with the zero-trust backend |

## How to read the metrics

| Metric | Formula | When to use |
|--------|---------|-------------|
| `bugs_found` | distinct rules with a confirmed bug | **Primary ranking** — fully verifiable, cannot be inflated |
| `bugs/Ktok` | bugs ÷ tokens(K) | Tiebreak / efficiency reference — self-reported denominator |

Rank by `bugs_found`. Among models that find the same number of bugs, `bugs/Ktok` breaks the tie. The efficiency metric divides by tokens, which the submitter self-reports — treat it as informative, not authoritative.
