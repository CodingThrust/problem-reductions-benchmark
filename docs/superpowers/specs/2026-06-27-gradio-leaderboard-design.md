# Gradio Leaderboard — Design Spec

**Date:** 2026-06-27
**Status:** approved (pending written-spec review)
**Scope:** Upgrade the static HF Space to an interactive Gradio app (display-only).

## 1. Benchmark identity (the framing this design serves)

The benchmark is a **fixed-budget bug-finding race**:

> Give every model the **same $20 API budget**. Who finds the most bugs in the
> problem-reductions reduction rules before the budget runs out?

At a fixed budget, **`bugs_found`** (distinct rules with a `pred`-confirmed bug,
one rule = one bug) is already budget-normalized and is the **primary metric** —
consistent with PR #17, no change needed. Token/cost efficiency is secondary.

A run does **not** aim to complete all 253 tasks; the budget is expected to run
out first. "How far the $20 got you" (**budget reach** = rules tested ÷ 253) is a
*context* stat that explains the bug count, not a completion target.

## 2. Goals / non-goals

**Goals (scope A — display only):**
- Interactive, good-looking leaderboard centered on the "$20 budget" frame.
- Browse the 253 benchmark tasks, loaded **live from the HF dataset** (the thing
  GitHub Pages cannot do).
- Make the trust model and metric definitions visible in-app.
- Build now against **placeholder data**; swap in real $20 runs later.

**Non-goals (deferred to phase B/C):**
- No submission upload, no server-side `pred` verification, no agent runs.
- No `pred` in the Space → stays a lightweight `gradio` SDK Space (not Docker).

## 3. Architecture

A single HF Space, `sdk: gradio`, brand colours (indigo/purple) via a custom
`gr.themes.Soft` variant.

```
space-gradio/
  app.py            # gr.Blocks UI: 3 tabs, wires data → components
  leaderboard.py    # pure data layer (no gradio imports) — testable
  data/
    results.json    # leaderboard rows (PLACEHOLDER until real $20 runs)
  requirements.txt  # gradio, huggingface_hub, pandas
  README.md         # HF Space card (sdk: gradio, datasets:, tags)
  tests/test_leaderboard.py
```

`leaderboard.py` holds all logic as **pure functions** (no gradio), so it is unit
testable and `app.py` is a thin view:

- `load_results(path) -> list[dict]` — read results.json rows.
- `load_tasks(repo_id, token) -> pandas.DataFrame` — pull the dataset jsonl via
  `huggingface_hub.hf_hub_download` (server-side, Space `HF_TOKEN` secret).
- `TOTAL_TASKS = len(tasks)` — the coverage denominator (253), from the dataset.
- `budget_reach(rules_tested, total) -> float` — coverage fraction.
- `leaderboard_frame(results, total) -> pandas.DataFrame` — assembles the ranked
  table (sort by bugs_found desc, then bugs/Ktok); adds Budget reach, Spent.

## 4. Data sources & model

| Data | Source | Notes |
|------|--------|-------|
| Tasks (253) | HF dataset `isPANN/problem-reductions-benchmarks` | live read at startup, server-side token |
| Results (scores) | bundled `data/results.json` | placeholder now; real runs re-push later |

**Results schema (per row)** adds one field to today's results format:
`budget_cap` (USD, e.g. `20`). The leaderboard **only ranks rows with
`budget_cap == 20`**; rows without it (the old demo runs) render in a separate,
clearly-labelled "demo (unranked)" section so the ranked table stays comparable.

**Placeholder data:** `data/results.json` ships with ~4 synthetic models at
`budget_cap: 20` (varied bugs/coverage/spend) so the UI can be built and styled
immediately. A banner notes "Showing placeholder data" whenever any displayed row
is synthetic (flagged by a `placeholder: true` field).

## 5. Components (3 tabs)

**Tab 1 — 🏆 Leaderboard**
- Header: hero line "Same $20 — who finds the most bugs?", plus `Budget: $20 ·
  pinned @ v0.6.0`.
- Sortable/searchable table: `# · Model · Bugs · Budget reach (bar, n/253) ·
  Spent · Tokens · Bugs/Ktok`. Primary sort `Bugs`; rank 1 gets 🥇.
- Row expand → that model's confirmed bug certificates (rule, violation, note) +
  trajectory link when present.

**Tab 2 — 📋 Tasks (253)**
- Table of benchmark tasks from the dataset: `rule · source → target · summary ·
  overhead`. Filter by source/target type, free-text search.
- This data is fetched live from the dataset — demonstrates the HF-native value.

**Tab 3 — ℹ️ About**
- Methodology: round-trip bug definition, one-rule-one-bug, zero-trust `pred`
  re-verification, the $20-budget framing, the v0.6.0 pin. Links to the dataset,
  the GitHub repo, and the reproduction Docker image.

## 6. Visual design

Custom theme (indigo/purple, generous spacing, rounded). Bug counts and budget
reach rendered as coloured bars/badges rather than bare numbers; rank medals;
monospace for `source → target`. Mobile-friendly single-column collapse.
(Detailed visual polish handled during implementation.)

## 7. Error handling

- Dataset fetch fails (network/token) → Tasks tab shows a clear error card;
  Leaderboard still works (coverage denominator falls back to a constant 253 with
  a note). App never crashes on startup.
- `data/results.json` missing/empty → Leaderboard shows an empty state.
- Local dev: the dataset is **private**, so `load_tasks` needs `HF_TOKEN` in the
  env, OR a `--tasks-file` / `TASKS_FILE` pointing at a local copy of the jsonl
  (the `dataset/` build artifact). In the Space, the `HF_TOKEN` secret covers it.

## 8. Testing

- `tests/test_leaderboard.py`: pure-function coverage of `budget_reach`,
  `leaderboard_frame` (sorting, ranked vs demo split, placeholder flag),
  `load_results`. No gradio/network in unit tests (dataset load mocked).
- Smoke: `python app.py` launches locally; manual check of all 3 tabs.

## 9. Privacy & rollout

Space stays **private** until visual/data review passes, then flipped public.
Built and previewed locally first (`python app.py`), then pushed to a new
`gradio` Space (replacing the static one, or a parallel `-gradio` Space during
testing — decided at implementation time).

## 10. Open items / assumptions

- Real $20-budget runs for sonnet/deepseek are a **separate data task**, not part
  of this build. UI ships on placeholder data.
- Whether to replace the existing static Space in place or stand up a parallel
  Space for testing is an implementation detail (lean: parallel `-gradio` test
  Space, swap names once approved).
