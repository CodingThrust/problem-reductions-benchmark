# Gradio Leaderboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static HF Space with an interactive Gradio leaderboard framed as a fixed-$20-budget bug-finding race, with a live task browser backed by the HF dataset.

**Architecture:** A `gradio` SDK Space. A pure-Python data layer (`leaderboard.py`, no gradio imports) loads results from a bundled JSON and the 253-task set from the HF dataset, and assembles ranked tables. A thin `app.py` wires that data into three `gr.Blocks` tabs (Leaderboard / Tasks / About). Display-only: no submission, no `pred`.

**Tech Stack:** Python 3.11+, gradio, pandas, huggingface_hub, pytest.

## Global Constraints

- Primary ranking metric: `bugs_found` (distinct rules, one rule = one bug). Sort descending, tie-break by `efficiency_bugs_per_ktok`. (Verbatim from spec §1, consistent with PR #17.)
- Only rows with `budget_cap == 20` are ranked; all other rows render as "demo (unranked)". (Spec §4.)
- Coverage denominator (`TOTAL_TASKS`) comes from the loaded dataset; fall back to the constant `253` if the dataset can't be loaded. (Spec §3, §7.)
- `leaderboard.py` contains NO gradio imports — pure, unit-testable functions only. (Spec §3.)
- Space stays **private** until review; `sdk: gradio`. (Spec §9.)
- Dataset repo id: `isPANN/problem-reductions-benchmarks`; task file: `problem_reductions_bugs.jsonl`. (Spec §4.)
- Brand colours indigo/purple. (Spec §6.)
- All displayed synthetic rows carry `placeholder: true`; a banner shows whenever any displayed row is placeholder. (Spec §4.)

---

## File Structure

- Create: `space-gradio/leaderboard.py` — pure data layer (results, tasks, metrics, frame).
- Create: `space-gradio/app.py` — gradio UI (3 tabs), thin view over the data layer.
- Create: `space-gradio/data/results.json` — placeholder leaderboard rows (`budget_cap: 20`).
- Create: `space-gradio/requirements.txt` — gradio, pandas, huggingface_hub.
- Create: `space-gradio/README.md` — HF Space card (sdk: gradio, datasets:, tags).
- Create: `space-gradio/tests/test_leaderboard.py` — unit tests for the data layer.
- Create: `space-gradio/tests/fixtures/tasks_sample.jsonl` — 3-row local task fixture (no network in tests).

---

### Task 1: Data layer — results loading, budget reach, ranked frame

**Files:**
- Create: `space-gradio/leaderboard.py`
- Create: `space-gradio/data/results.json`
- Test: `space-gradio/tests/test_leaderboard.py`

**Interfaces:**
- Produces:
  - `RANKED_BUDGET: int = 20`
  - `TOTAL_TASKS_DEFAULT: int = 253`
  - `load_results(path: str) -> list[dict]`
  - `budget_reach(rules_tested: int, total: int) -> float` (fraction 0.0–1.0)
  - `split_results(results: list[dict]) -> tuple[list[dict], list[dict]]` (ranked, demo)
  - `has_placeholder(results: list[dict]) -> bool`
  - `leaderboard_frame(results: list[dict], total: int) -> pandas.DataFrame` with columns
    `["rank","model","bugs_found","budget_reach","rules_tested","total_cost_usd","total_tokens_k","efficiency_bugs_per_ktok"]`

- [ ] **Step 1: Write the placeholder data file**

Create `space-gradio/data/results.json`:

```json
[
  {"model": "anthropic/claude-sonnet-4-6", "budget_cap": 20, "bugs_found": 3, "rules_tested": 30, "total_cost_usd": 19.8, "total_tokens_k": 420.0, "efficiency_bugs_per_ktok": 0.0071, "placeholder": true, "bug_certificates": [{"rule": "bmf_ilp", "violation": "unsound_extraction", "note": "demo", "source_type": "BMF", "target_type": "ILP", "trajectory_file": null}]},
  {"model": "openai/gpt-5", "budget_cap": 20, "bugs_found": 2, "rules_tested": 45, "total_cost_usd": 20.0, "total_tokens_k": 510.0, "efficiency_bugs_per_ktok": 0.0039, "placeholder": true, "bug_certificates": []},
  {"model": "deepseek/deepseek-chat", "budget_cap": 20, "bugs_found": 2, "rules_tested": 180, "total_cost_usd": 20.0, "total_tokens_k": 1200.0, "efficiency_bugs_per_ktok": 0.0017, "placeholder": true, "bug_certificates": []},
  {"model": "google/gemini-2.5-pro", "budget_cap": 20, "bugs_found": 0, "rules_tested": 60, "total_cost_usd": 18.5, "total_tokens_k": 300.0, "efficiency_bugs_per_ktok": 0.0, "placeholder": true, "bug_certificates": []}
]
```

- [ ] **Step 2: Write the failing tests**

Create `space-gradio/tests/test_leaderboard.py`:

```python
import json
import math
from pathlib import Path

import leaderboard as lb


def _results():
    return [
        {"model": "m-rank-a", "budget_cap": 20, "bugs_found": 3, "rules_tested": 30,
         "total_cost_usd": 19.8, "total_tokens_k": 420.0, "efficiency_bugs_per_ktok": 0.0071},
        {"model": "m-rank-b", "budget_cap": 20, "bugs_found": 2, "rules_tested": 45,
         "total_cost_usd": 20.0, "total_tokens_k": 510.0, "efficiency_bugs_per_ktok": 0.0039},
        {"model": "m-demo", "bugs_found": 9, "rules_tested": 5,
         "total_cost_usd": 0.4, "total_tokens_k": 50.0, "efficiency_bugs_per_ktok": 0.18},
    ]


def test_budget_reach_fraction():
    assert lb.budget_reach(30, 253) == 30 / 253
    assert lb.budget_reach(0, 253) == 0.0

def test_budget_reach_clamped_and_safe():
    assert lb.budget_reach(300, 253) == 1.0      # never exceeds 1
    assert lb.budget_reach(5, 0) == 0.0          # no divide-by-zero

def test_split_ranked_vs_demo():
    ranked, demo = lb.split_results(_results())
    assert [r["model"] for r in ranked] == ["m-rank-a", "m-rank-b"]
    assert [r["model"] for r in demo] == ["m-demo"]   # missing budget_cap → demo

def test_leaderboard_frame_ranks_by_bugs():
    df = lb.leaderboard_frame(_results(), total=253)
    assert list(df["model"]) == ["m-rank-a", "m-rank-b"]  # demo excluded, bugs desc
    assert list(df["rank"]) == [1, 2]
    assert math.isclose(df.iloc[0]["budget_reach"], 30 / 253)

def test_leaderboard_frame_tiebreak_by_ktok():
    rows = [
        {"model": "lo", "budget_cap": 20, "bugs_found": 2, "rules_tested": 10,
         "total_cost_usd": 20, "total_tokens_k": 100, "efficiency_bugs_per_ktok": 0.01},
        {"model": "hi", "budget_cap": 20, "bugs_found": 2, "rules_tested": 10,
         "total_cost_usd": 20, "total_tokens_k": 100, "efficiency_bugs_per_ktok": 0.05},
    ]
    df = lb.leaderboard_frame(rows, total=253)
    assert list(df["model"]) == ["hi", "lo"]   # equal bugs → higher ktok first

def test_has_placeholder():
    assert lb.has_placeholder([{"placeholder": True}]) is True
    assert lb.has_placeholder([{"model": "x"}]) is False

def test_load_results_reads_bundled_file():
    path = Path(__file__).parent.parent / "data" / "results.json"
    rows = lb.load_results(str(path))
    assert len(rows) >= 1
    assert all("model" in r for r in rows)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd space-gradio && python -m pytest tests/test_leaderboard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leaderboard'`

- [ ] **Step 4: Implement the data layer (results + metrics + frame)**

Create `space-gradio/leaderboard.py`:

```python
"""Pure data layer for the leaderboard — NO gradio imports (unit-testable)."""
import json

import pandas as pd

RANKED_BUDGET = 20            # USD: only runs at this cap are ranked
TOTAL_TASKS_DEFAULT = 253     # coverage denominator fallback if dataset unavailable

_FRAME_COLUMNS = [
    "rank", "model", "bugs_found", "budget_reach",
    "rules_tested", "total_cost_usd", "total_tokens_k", "efficiency_bugs_per_ktok",
]


def load_results(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def budget_reach(rules_tested: int, total: int) -> float:
    """Fraction of the task set a run reached before the budget ran out (0.0–1.0)."""
    if not total:
        return 0.0
    return min(rules_tested / total, 1.0)


def has_placeholder(results: list[dict]) -> bool:
    return any(r.get("placeholder") for r in results)


def split_results(results: list[dict]) -> tuple[list[dict], list[dict]]:
    """(ranked, demo): ranked = rows run at the fixed budget cap; demo = everything else."""
    ranked = [r for r in results if r.get("budget_cap") == RANKED_BUDGET]
    demo = [r for r in results if r.get("budget_cap") != RANKED_BUDGET]
    return ranked, demo


def leaderboard_frame(results: list[dict], total: int) -> pd.DataFrame:
    """Ranked table: bugs_found desc, tie-break efficiency_bugs_per_ktok desc."""
    ranked, _ = split_results(results)
    ranked = sorted(
        ranked,
        key=lambda r: (r.get("bugs_found", 0), r.get("efficiency_bugs_per_ktok", 0.0)),
        reverse=True,
    )
    rows = []
    for i, r in enumerate(ranked, start=1):
        rows.append({
            "rank": i,
            "model": r.get("model", "?"),
            "bugs_found": r.get("bugs_found", 0),
            "budget_reach": budget_reach(r.get("rules_tested", 0), total),
            "rules_tested": r.get("rules_tested", 0),
            "total_cost_usd": r.get("total_cost_usd", 0.0),
            "total_tokens_k": r.get("total_tokens_k", 0.0),
            "efficiency_bugs_per_ktok": r.get("efficiency_bugs_per_ktok", 0.0),
        })
    return pd.DataFrame(rows, columns=_FRAME_COLUMNS)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd space-gradio && python -m pytest tests/test_leaderboard.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Commit**

```bash
git add space-gradio/leaderboard.py space-gradio/data/results.json space-gradio/tests/test_leaderboard.py
git commit -m "feat(gradio): data layer — results, budget reach, ranked frame"
```

---

### Task 2: Task-set loader + filtering (live from the HF dataset)

**Files:**
- Modify: `space-gradio/leaderboard.py` (add task functions)
- Create: `space-gradio/tests/fixtures/tasks_sample.jsonl`
- Test: `space-gradio/tests/test_leaderboard.py` (add task tests)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `DATASET_REPO: str = "isPANN/problem-reductions-benchmarks"`
  - `TASKS_FILE: str = "problem_reductions_bugs.jsonl"`
  - `load_tasks(repo_id: str = DATASET_REPO, token: str | None = None, local_file: str | None = None) -> pandas.DataFrame`
    with columns `["rule","source","target","summary","overhead_num_vars","overhead_num_constraints"]`
  - `filter_tasks(df, source: str | None = None, target: str | None = None, query: str | None = None) -> pandas.DataFrame`

- [ ] **Step 1: Create the local task fixture**

Create `space-gradio/tests/fixtures/tasks_sample.jsonl` (3 lines):

```jsonl
{"rule": "bmf_ilp", "source": "BMF", "target": "ILP<bool>", "summary": "Reduction from BMF to ILP.", "overhead_num_vars": "rows*rank", "overhead_num_constraints": "3*rows", "rule_source": "//! ...", "library_tag": "v0.6.0"}
{"rule": "sat_ksat", "source": "Satisfiability", "target": "KSatisfiability<K>", "summary": "SAT to k-SAT.", "overhead_num_vars": "n", "overhead_num_constraints": "m", "rule_source": "//! ...", "library_tag": "v0.6.0"}
{"rule": "spinglass_maxcut", "source": "SpinGlass", "target": "MaxCut", "summary": "SpinGlass to MaxCut.", "overhead_num_vars": "v", "overhead_num_constraints": "e", "rule_source": "//! ...", "library_tag": "v0.6.0"}
```

- [ ] **Step 2: Write the failing tests**

Append to `space-gradio/tests/test_leaderboard.py`:

```python
def _tasks_fixture():
    return str(Path(__file__).parent / "fixtures" / "tasks_sample.jsonl")

def test_load_tasks_from_local_file():
    df = lb.load_tasks(local_file=_tasks_fixture())
    assert len(df) == 3
    assert set(["rule", "source", "target", "summary"]).issubset(df.columns)
    assert "bmf_ilp" in list(df["rule"])

def test_filter_tasks_by_source():
    df = lb.load_tasks(local_file=_tasks_fixture())
    out = lb.filter_tasks(df, source="BMF")
    assert list(out["rule"]) == ["bmf_ilp"]

def test_filter_tasks_by_query_matches_any_text():
    df = lb.load_tasks(local_file=_tasks_fixture())
    out = lb.filter_tasks(df, query="maxcut")   # case-insensitive, matches target/summary
    assert list(out["rule"]) == ["spinglass_maxcut"]

def test_filter_tasks_empty_filters_returns_all():
    df = lb.load_tasks(local_file=_tasks_fixture())
    assert len(lb.filter_tasks(df)) == 3
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd space-gradio && python -m pytest tests/test_leaderboard.py -k tasks -v`
Expected: FAIL — `AttributeError: module 'leaderboard' has no attribute 'load_tasks'`

- [ ] **Step 4: Implement the task loader + filter**

Append to `space-gradio/leaderboard.py`:

```python
DATASET_REPO = "isPANN/problem-reductions-benchmarks"
TASKS_FILE = "problem_reductions_bugs.jsonl"

_TASK_COLUMNS = ["rule", "source", "target", "summary",
                 "overhead_num_vars", "overhead_num_constraints"]


def _read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_tasks(repo_id: str = DATASET_REPO, token: str | None = None,
               local_file: str | None = None) -> pd.DataFrame:
    """Load the 253-task set. Prefer a local file (dev/tests); else pull from the HF dataset."""
    import os
    path = local_file or os.environ.get("TASKS_FILE")
    if not path:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(repo_id=repo_id, filename=TASKS_FILE,
                               repo_type="dataset", token=token)
    rows = _read_jsonl(path)
    df = pd.DataFrame(rows)
    for col in _TASK_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[_TASK_COLUMNS]


def filter_tasks(df: pd.DataFrame, source: str | None = None,
                 target: str | None = None, query: str | None = None) -> pd.DataFrame:
    out = df
    if source:
        out = out[out["source"].str.contains(source, case=False, na=False)]
    if target:
        out = out[out["target"].str.contains(target, case=False, na=False)]
    if query:
        mask = out.apply(
            lambda row: query.lower() in " ".join(str(v) for v in row.values).lower(),
            axis=1,
        )
        out = out[mask]
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd space-gradio && python -m pytest tests/test_leaderboard.py -v`
Expected: PASS (12 tests total)

- [ ] **Step 6: Commit**

```bash
git add space-gradio/leaderboard.py space-gradio/tests/
git commit -m "feat(gradio): task-set loader (local/dataset) + filtering"
```

---

### Task 3: Gradio app — 3 tabs, theme, Space card, smoke test

**Files:**
- Create: `space-gradio/app.py`
- Create: `space-gradio/requirements.txt`
- Create: `space-gradio/README.md`
- Test: smoke (manual launch + an import test)

**Interfaces:**
- Consumes from `leaderboard.py`: `load_results`, `leaderboard_frame`, `has_placeholder`,
  `split_results`, `load_tasks`, `filter_tasks`, `TOTAL_TASKS_DEFAULT`, `RANKED_BUDGET`.
- Produces: `build_ui() -> gradio.Blocks` (importable; launched by `if __name__ == "__main__"`).

- [ ] **Step 1: Write requirements.txt**

Create `space-gradio/requirements.txt`:

```
gradio>=4.44
pandas>=2.0
huggingface_hub>=0.25
```

- [ ] **Step 2: Write the Space card**

Create `space-gradio/README.md`:

```markdown
---
title: Problem Reductions Bug-Finding Benchmark
emoji: 🐛
colorFrom: indigo
colorTo: purple
sdk: gradio
app_file: app.py
pinned: false
short_description: Fixed-$20 budget bug-finding race over problem-reduction rules
tags:
- leaderboard
- benchmark
- bug-finding
- reductions
- reproducibility
datasets:
- isPANN/problem-reductions-benchmarks
---

# Problem-Reductions Bug-Finding Benchmark

Same **$20** budget for every model — who finds the most bugs in the
problem-reductions reduction rules? Tasks are pinned at **v0.6.0** and every bug
is independently re-verified by `pred` (one rule = one bug). Display-only mirror;
source of truth is the [GitHub repo](https://github.com/Ferrari-72/problem-reductions-benchmark).
```

- [ ] **Step 3: Write a failing smoke test**

Create `space-gradio/tests/test_app.py`:

```python
import os
import gradio as gr

os.environ["TASKS_FILE"] = os.path.join(os.path.dirname(__file__), "fixtures", "tasks_sample.jsonl")

import app


def test_build_ui_returns_blocks():
    ui = app.build_ui()
    assert isinstance(ui, gr.Blocks)
```

- [ ] **Step 4: Run the smoke test to verify it fails**

Run: `cd space-gradio && python -m pytest tests/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 5: Implement app.py**

Create `space-gradio/app.py`:

```python
"""Gradio leaderboard — fixed-$20 bug-finding race. Thin view over leaderboard.py."""
import os

import gradio as gr

import leaderboard as lb

_RESULTS_PATH = os.path.join(os.path.dirname(__file__), "data", "results.json")
_PINNED_TAG = "v0.6.0"

THEME = gr.themes.Soft(primary_hue="indigo", secondary_hue="purple")


def _reach_bar(frac: float, total: int, tested: int) -> str:
    filled = round(frac * 10)
    return f"{'▓' * filled}{'░' * (10 - filled)} {tested}/{total}"


def _leaderboard_view():
    results = lb.load_results(_RESULTS_PATH)
    total = _task_total()
    df = lb.leaderboard_frame(results, total)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    display = df.assign(
        Rank=df["rank"].map(lambda r: medals.get(r, str(r))),
        Model=df["model"].str.replace(r"^.*/", "", regex=True),
        Bugs=df["bugs_found"],
        **{"Budget reach": df.apply(
            lambda r: _reach_bar(r["budget_reach"], total, int(r["rules_tested"])), axis=1)},
        **{"Spent ($)": df["total_cost_usd"].map(lambda c: f"${c:.2f}")},
        **{"Tokens (K)": df["total_tokens_k"].round(0).astype(int)},
        **{"Bugs/Ktok": df["efficiency_bugs_per_ktok"]},
    )[["Rank", "Model", "Bugs", "Budget reach", "Spent ($)", "Tokens (K)", "Bugs/Ktok"]]
    banner = ("⚠️ Showing placeholder data — real $20 runs pending."
              if lb.has_placeholder(results) else "")
    return display, banner


def _task_total() -> int:
    try:
        return len(lb.load_tasks())
    except Exception:
        return lb.TOTAL_TASKS_DEFAULT


def _tasks_state():
    try:
        return lb.load_tasks(), ""
    except Exception as e:  # network/token failure — Tasks tab degrades gracefully
        import pandas as pd
        return pd.DataFrame(columns=["rule", "source", "target", "summary"]), \
            f"⚠️ Could not load the task dataset: {e}"


def build_ui() -> gr.Blocks:
    table, banner = _leaderboard_view()
    tasks_df, tasks_err = _tasks_state()

    with gr.Blocks(theme=THEME, title="Problem-Reductions Bug-Finding Benchmark") as ui:
        gr.Markdown(f"# 🐛 Problem-Reductions Bug-Finding Benchmark\n"
                    f"### Same **${lb.RANKED_BUDGET}** — who finds the most bugs? "
                    f"· pinned @ `{_PINNED_TAG}`")

        with gr.Tab("🏆 Leaderboard"):
            if banner:
                gr.Markdown(f"**{banner}**")
            gr.Dataframe(value=table, interactive=False, wrap=True)

        with gr.Tab(f"📋 Tasks ({len(tasks_df)})"):
            if tasks_err:
                gr.Markdown(f"**{tasks_err}**")
            else:
                query = gr.Textbox(label="Search tasks", placeholder="e.g. ILP, MaxCut, clique")
                tasks_view = gr.Dataframe(value=tasks_df, interactive=False, wrap=True)
                query.change(lambda q: lb.filter_tasks(tasks_df, query=q or None),
                             inputs=query, outputs=tasks_view)

        with gr.Tab("ℹ️ About"):
            gr.Markdown(
                f"**Metric.** Every model gets the same **${lb.RANKED_BUDGET}** API budget; "
                "the score is the number of distinct reduction rules with a confirmed bug "
                "(**one rule = one bug**). *Budget reach* shows how far the $20 got — "
                "how many of the tasks the run reached before the money ran out.\n\n"
                "**Zero trust.** Every claimed bug is independently re-derived with `pred` "
                f"(problem-reductions `{_PINNED_TAG}`); the model's claim is never trusted.\n\n"
                "**Links.** "
                "[Task dataset](https://huggingface.co/datasets/isPANN/problem-reductions-benchmarks) · "
                "[GitHub](https://github.com/Ferrari-72/problem-reductions-benchmark)"
            )
    return ui


if __name__ == "__main__":
    build_ui().launch()
```

- [ ] **Step 6: Run the smoke test to verify it passes**

Run: `cd space-gradio && python -m pytest tests/test_app.py -v`
Expected: PASS

- [ ] **Step 7: Manual launch check**

Run: `cd space-gradio && TASKS_FILE=tests/fixtures/tasks_sample.jsonl python app.py`
Expected: Gradio serves at `http://localhost:7860`; all three tabs render; leaderboard shows the placeholder banner + 4 ranked models; Tasks tab shows the 3 fixture rows and search filters them.

- [ ] **Step 8: Commit**

```bash
git add space-gradio/app.py space-gradio/requirements.txt space-gradio/README.md space-gradio/tests/test_app.py
git commit -m "feat(gradio): 3-tab leaderboard app + theme + Space card"
```

---

## Self-Review

**Spec coverage:**
- §1 budget framing → Task 3 hero text + About tab; `RANKED_BUDGET` in Task 1. ✓
- §3 pure data layer / thin view → Task 1+2 (no gradio) / Task 3 (`build_ui`). ✓
- §4 data sources + `budget_cap==20` ranked / placeholder banner → Task 1 (`split_results`, `has_placeholder`), Task 3 banner. ✓
- §5 three tabs incl. live task browse + filter → Task 2 (`load_tasks`/`filter_tasks`), Task 3 tabs. ✓
- §6 theme/medals/bars → Task 3 (`THEME`, medals, `_reach_bar`). ✓
- §7 error handling (dataset fail → Tasks degrades, leaderboard fallback 253) → Task 3 `_tasks_state`/`_task_total`. ✓
- §8 testing pure funcs + smoke → Task 1/2 unit tests, Task 3 smoke. ✓
- §9 private/gradio card → Task 3 README. ✓

**Placeholder scan:** No TBD/TODO; all steps carry real code/commands. ✓
**Type consistency:** `load_tasks(local_file=...)` used identically in Task 2 tests and Task 3; frame columns from Task 1 consumed by Task 3 `_leaderboard_view`; `filter_tasks(df, query=...)` signature matches Task 3 `query.change`. ✓

**Note (real-data follow-up, out of scope here):** add `budget_cap` to the real
results pipeline (`run_mini`/`scheduler`/`build_index`) and run the field models at
$20 — a separate plan; this build ships on placeholder data.
```
