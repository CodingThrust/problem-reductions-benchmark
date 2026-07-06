# Leaderboard site

Static leaderboard (`index.html`, no app server) published to **GitHub Pages** by
`.github/workflows/publish-on-merge.yml`. Every model gets the same **$20** budget; every
bug is re-verified by `pred`.

`index.html` reads two data files served alongside it:

- `results.json` — the aggregate leaderboard, refreshed by `score-from-r2.yml`
- `tasks.json` — the rule set shown on the Tasks tab

Preview locally with `make serve` (data files load via `fetch`, so serve over HTTP, not
`file://`). `results.json` holds only aggregate counts.
