---
title: Problem Reductions Bug-Finding Benchmark
emoji: 🐛
colorFrom: indigo
colorTo: purple
sdk: static
app_file: index.html
pinned: false
short_description: Leaderboard for finding bugs in problem-reduction rules
tags:
- leaderboard
- benchmark
- bug-finding
- reductions
- reproducibility
datasets:
- isPANN/problem-reductions-benchmarks
---

# Problem-Reductions Bug-Finding Benchmark — Leaderboard

A static, display-only mirror of the
[problem-reductions bug-finding benchmark](https://github.com/Ferrari-72/problem-reductions-benchmark)
leaderboard.

Models are ranked by **`bugs_found`** — the number of *distinct reduction rules*
with at least one independently `pred`-confirmed bug, on a pinned library commit.
One rule = one bug. Token- and cost-efficiency (`bugs/Ktok`, `bugs/$`) are shown as
tiebreak / reference metrics only.

This Space renders `results/index.json`; it does not run any model or accept
submissions. The source of truth is the GitHub repository.
