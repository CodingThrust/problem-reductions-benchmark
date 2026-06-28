---
title: Problem Reductions Bug-Finding Benchmark
emoji: 🐛
colorFrom: indigo
colorTo: purple
sdk: static
pinned: false
short_description: Who finds the most bugs on a fixed $20 budget?
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

Static leaderboard (instant first paint, no app server). Same **$20** budget for every
model — who finds the most bugs in the problem-reductions reduction rules? Tasks pinned at
**v0.6.0**; every bug is independently re-verified by `pred` (one rule = one bug).

`index.html` reads two data files served alongside it:

- `results.json` — scored runs (mirror of the backend's leaderboard output)
- `tasks.json` — the rule set shown on the Tasks tab

Refresh them by replacing the files (e.g. `make` target / `hf upload`); no rebuild needed.
Source of truth is the [GitHub repo](https://github.com/Ferrari-72/problem-reductions-benchmark).
