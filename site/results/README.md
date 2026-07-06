# Per-submission leaderboard entries

Each file here is **one submission's** public leaderboard entry:
`<model-slug>--<UTC-time>--<short-id>.json`. Aggregate only; no certificates or rule identities.

- Written by `score-from-r2.yml`, one **PR per submission**.
- On merge, `publish-on-merge.yml` aggregates them (best run per model) into the deployed
  `site/results.json` and publishes to GitHub Pages.
