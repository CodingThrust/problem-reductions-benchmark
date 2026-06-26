---
title: Problem Reductions Bug-Finding Benchmark
emoji: 🐛
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: "5.50.0"
app_file: app.py
pinned: false
python_version: "3.10"
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

Same **$20** budget for every model — who finds the most bugs in the
problem-reductions reduction rules? Tasks are pinned at **v0.6.0** and every bug
is independently re-verified by `pred` (one rule = one bug). Display-only mirror;
source of truth is the [GitHub repo](https://github.com/Ferrari-72/problem-reductions-benchmark).
