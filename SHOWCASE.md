---
title: Problem-Reductions Benchmark — Showcase & User Guide
date: 2026-06-25
tags:
  - benchmark
  - guide
  - showcase
---

# Problem-Reductions Benchmark — Showcase & User Guide

> Repo: https://github.com/Ferrari-72/problem-reductions-benchmark
> Leaderboard & submission: Hugging Face Space (Gradio) — see `SUBMISSION.md`
> Library: https://github.com/CodingThrust/problem-reductions (pinned commit `aa2d1a1`)

---

## 1. What this benchmark is

**The question:** for an equal compute budget, which LLM finds the most bugs in the
problem-reductions library?

**Workflow:**

```
LLM Agent
  │
  ├─ pred create       # build a small source instance
  ├─ pred reduce       # A → B (reduction bundle)
  ├─ pred solve        # solve B, get a target config
  ├─ pred extract      # target config → source config
  └─ pred evaluate     # check the extracted source config is valid
         │
         └─ invalid → emit a CERTIFICATE (bug claim)
                │
                └─ Verifier re-checks independently → accepted / rejected
                         │
                         └─ accepted → counts on the leaderboard
```

**Primary metric:** `bugs_found` — on a fixed commit, the number of **distinct rules with
≥1 confirmed bug** (one rule counts once, no matter how many counterexamples target it).
Fully verifiable and impossible to inflate. `bugs / Ktok` and `bugs / $` are efficiency
reference metrics (self-reported denominators) used only to break ties.

---

## 2. The single test for a bug: round-trip

A rule A→B is correct on an instance `a` iff solving it **directly** agrees with solving it
**through the reduction** — by **value** (optimization) or **feasibility** (decision):

```
solve(a)  ==  solve(reduce(a))
```

`pred solve <bundle>` already does the whole round-trip (solve the target → extract back to
the source → evaluate in source space), so you just compare it against `pred solve <source>`.
A mismatch is a real bug. The verifier re-runs `pred` itself and **never trusts the AI's
claim**. The mismatch gets a derived label:

| Label | Meaning |
|-------|---------|
| `optimum_not_preserved` | both feasible, but the round-tripped value differs |
| `feasibility_not_preserved` | source is solvable but the round-trip yields none |
| `spurious_solution` | the round-trip claims a solution the source has none of |

Optionally, a certificate carries a `target_config` (a specific target solution) to also
catch **extraction-layer** bugs — `unsound_extraction` (a valid target solution extracts to
an invalid source solution) and `suboptimal_extraction` (an optimal target solution extracts
to a suboptimal source solution) that the solver's own optimum would hide. Values, never
specific solutions, are compared, so multiple optima never cause a false mismatch.

---

## 3. How to run and submit a model

### 3.1 Environment setup

```bash
# 1. Clone the benchmark repo
git clone https://github.com/Ferrari-72/problem-reductions-benchmark
cd problem-reductions-benchmark

# 2. Clone and pin the library to the fixed commit
git clone https://github.com/CodingThrust/problem-reductions
cd problem-reductions && git checkout aa2d1a1 && cd ..

# 3. Install dependencies
pip install -e ".[dev]"

# 4. Confirm the pred CLI works
pred --version
```

### 3.2 Configure the API key

Set the environment variable matching your model:

```bash
# OpenAI
export OPENAI_API_KEY=sk-...

# Anthropic Claude
export ANTHROPIC_API_KEY=sk-ant-...

# DeepSeek
export DEEPSEEK_API_KEY=sk-...

# Windows PowerShell
$env:OPENAI_API_KEY = "sk-..."
$env:PYTHONUTF8 = "1"   # required on Windows so emoji output doesn't hit a GBK encoding error
```

### 3.3 Run a budgeted session → submission.json

The recommended path is the Docker runner, which enforces the budget and emits a
self-describing `submission.json`:

```bash
make submission          # → ./out/submission.json (real run; needs an API key + price)
```

`make submission` runs `benchmark.run_submission` inside the runner image. Key flags
(see `SUBMISSION.md` for the full list, including the per-token price that makes the budget
a hard cap):

| Flag / env | Meaning | Default |
|------------|---------|---------|
| `--model` / `MODEL_NAME` | LiteLLM model name | `anthropic/claude-sonnet-4-6` |
| `--budget` / `BUDGET_USD` | total budget (USD); must be 20 to be ranked | 20.0 |
| `--per-rule` / `PER_RULE_BUDGET` | per-rule cost cap | 0.5 |
| `--price-in` / `--price-out` | your model price, USD / 1M tokens | built-in for known models |
| `--safety-margin` | USD held back as overshoot headroom | 1.0 |
| `--max-rules` | cap rules attempted (smoke runs) | all |

### 3.4 Submit

Upload `out/submission.json` on the Space's Submit tab. The backend re-verifies every
certificate with `pred` (zero trust) and ranks the run automatically. See `SUBMISSION.md`.

---

## 4. Highlights worth showing

### 4.1 Single-rule pipeline demo

Run one rule end to end (agent → certificate → verify), ~$0.05:

```bash
python -m benchmark.run_mini \
  --model deepseek/deepseek-chat \
  --api-base https://api.deepseek.com/v1 \
  --repo-dir path/to/problem-reductions \
  --rules exactcoverby3sets_subsetproduct \
  --per-rule 0.5 \
  --output results/demo.json \
  --trajectory-dir results/demo_traj
```

### 4.2 Trajectory inspection

With `--trajectory-dir`, each rule saves a JSONL file recording the agent's full reasoning:

```bash
cat results/demo_traj/deepseek_deepseek-chat_exactcoverby3sets_subsetproduct.jsonl \
  | python -c "
import sys, json
for line in sys.stdin:
    m = json.loads(line)
    if m['role'] == 'assistant':
        print('=== AGENT ===')
        print(m['content'][:500])
        print()
"
```

You can see the actual `pred` commands the agent ran and its reasoning about the reduction —
evidence the model is really *working through it*, not guessing.

### 4.3 Leaderboard

The leaderboard is a static HF Space (`space/`); submission is command-line (`hf upload`):
- `bugs_found` (distinct rules) is the headline cross-model metric
- sorted by `bugs_found`, ties broken by `efficiency_bugs_per_ktok`; rows appear once the
  backend re-verification passes
- self-reported dollars are advisory only (the price is declared by the submitter); the
  efficiency headline is bugs/Ktok

### 4.4 Independent verifier (zero trust)

The design centerpiece: the verifier **trusts none** of the AI's values. It re-derives the
bundle from `source` with `pred reduce`, then round-trips with `pred solve`:

```
# direct                 → pred solve source         → compare
# via the reduction      → pred solve reduce(source) → value/feasibility mismatch = bug
# with a target_config   → extract + evaluate independently to catch extraction bugs
```

The AI cannot fabricate a certificate — whatever values it writes are ignored; the verifier
recomputes everything, and a wrong or non-minimal certificate is simply `rejected`.

### 4.5 Test suite (all mocked, no API key)

```bash
pytest benchmark/tests/ -q 2>&1 | tail -3
# 130 passed, 4 skipped
```

Unit tests monkeypatch `PredSolver`, so no real API key is needed; only the integration
tests (`-m integration`) require `pred`. Anyone can run the unit tests right after cloning.

---

## 5. Known issues and notes

### 5.1 Bug distribution on the pinned version (v0.6.0 / `aa2d1a1`)

Most reduction rules on `aa2d1a1` are correct, but **real reduction bugs do exist** — we have
confirmed several counterexamples on this fixed commit with the round-trip verifier + raw
`pred` (concentrated in less-trodden, weighted / boundary-input rules). The specific
counterexamples are the benchmark answer key and are **not** in the public repo (see the
gitignored `benchmark/tests/fixtures/private/`).

So:
- **0 bugs doesn't mean the library is clean** — it means the agent missed them; that's where
  models pull apart
- the more obscure / less-trodden rules (and weighted / degenerate / empty-or-zero inputs)
  are the most worth probing
- scoring is based on `pred`-recheckable counterexamples on `aa2d1a1`; novelty is not scored

### 5.2 Windows-specific issues

| Issue | Cause | Fix |
|-------|-------|-----|
| `UnicodeEncodeError 'gbk'` | mini-swe-agent prints emoji; the Windows console defaults to GBK | set `$env:PYTHONUTF8="1"` first |
| `pred` can't read stdin | Windows pipe limitation | the agent prompt already says: write to a file first, then pass the path |
| `git push` fails | needs a proxy | `git config --global http.proxy http://127.0.0.1:7890` |

### 5.3 Tuning step_limit

Default `step_limit=35`. Suggested ranges:

| Scenario | Suggested |
|----------|-----------|
| Quick pipeline check | 20 (enough for one round-trip) |
| Real evaluation, ample budget | 35–50 |
| Complex rules (e.g. graph problems) | 50+ |

If `step_limit` is too low, the agent spends all its steps reading code and has no time to
run `pred reduce/solve/extract`, returning `no_certificate`.

### 5.4 Cost estimates

| Model | Avg cost / rule | 15 rules total |
|-------|-----------------|----------------|
| DeepSeek Chat | ~$0.04 | ~$0.63 |
| Claude Sonnet 4.x | ~$0.15 | ~$2.25 |
| GPT-4o | ~$0.10 | ~$1.50 |

`--per-rule 0.5` is a safe ceiling; most models spend well under it per rule.

### 5.5 Solve-timeout limitation

The round-trip check needs `pred solve` on both sides (ILP first, brute-force fallback). For
large instances solving can time out; the verifier then returns `rejected` (it does not
crash — a timeout is not a proof), and a bug on that instance cannot be confirmed.

**Tip:** use **minimal** counterexamples (a few nodes / clauses) — they are both faster and
a stronger witness; the verifier also rejects oversized sources (> 256KB).

---

## 6. Directory layout at a glance

```
problem-reductions-benchmark/
├── benchmark/
│   ├── run_submission.py     # main entry: budgeted session → submission.json
│   ├── run_mini.py           # one agent session for a single rule
│   ├── scheduler.py          # multi-model/rule scheduling + budget cap
│   ├── cost.py               # token×price cost accounting (hard cap)
│   ├── verify.py             # independent verifier (zero trust, round-trip)
│   ├── verify_submission.py  # backend scoring (re-verifies every cert)
│   ├── backend_score.py      # submission-queue scoring + webhook entry
│   ├── config.yaml           # agent prompt + step_limit
│   └── tests/                # unit tests
├── space/                    # HF static Space (instant leaderboard; CLI submit)
├── docker/Dockerfile         # runner image (pred + agent)
└── SHOWCASE.md               # this file
```

---

## 7. Quick start (3 commands)

```bash
# 1. Set up
git clone https://github.com/Ferrari-72/problem-reductions-benchmark && cd problem-reductions-benchmark
pip install -e ".[dev]"

# 2. Set the API key (DeepSeek shown)
export DEEPSEEK_API_KEY=your_key_here  # or $env:DEEPSEEK_API_KEY="..." on Windows
export PYTHONUTF8=1  # required on Windows

# 3. Run one rule to validate the pipeline
python -m benchmark.run_mini \
  --model deepseek/deepseek-chat \
  --api-base https://api.deepseek.com/v1 \
  --repo-dir path/to/problem-reductions \
  --rules knapsack_subsetsum \
  --output results/my_test.json
```

---

*Generated: 2026-06-25 | Repo: https://github.com/Ferrari-72/problem-reductions-benchmark*
