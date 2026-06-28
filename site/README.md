# Leaderboard site

Static leaderboard (one `index.html`, no app server, instant first paint), published to
**GitHub Pages** by `.github/workflows/publish-on-merge.yml`. Same **$20** budget for every
model — who finds the most bugs in the problem-reductions reduction rules? Every bug is
independently re-verified by `pred` (one rule = one bug).

`index.html` reads two data files served alongside it:

- `results.json` — the scored leaderboard (rebuilt from `submissions/` on each merge)
- `tasks.json` — the rule set shown on the Tasks tab

Preview locally with `make serve` (the data files are loaded via `fetch`, so open it over
HTTP, not `file://`). The published `results.json` is regenerated on every merge; the copy
checked into the repo is a seed for local preview.
