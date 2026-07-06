# Per-submission leaderboard entries

Each file here is **one submission's** public leaderboard entry:
`<model-slug>--<UTC-time>--<short-id>.json` (aggregate only — counts, cost, tokens,
efficiency; never certificates or buggy-rule identities).

- Written by `score-from-r2.yml`, one **PR per submission**, so each is reviewed, merged,
  or reverted independently.
- On merge, `publish-on-merge.yml` aggregates them (best run per model) into the deployed
  `site/results.json` (generated, not committed) and publishes to GitHub Pages.
