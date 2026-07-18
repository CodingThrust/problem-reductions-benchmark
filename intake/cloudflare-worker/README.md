# prb submission intake (Cloudflare Worker)

The write-only, confidential intake for `prb submit`. Submitters POST their `submission.json`
over HTTPS; this Worker checks a bearer key and deposits the raw body (certificates plus the
bounded submit ledger) into a **private R2 bucket**. The public leaderboard only receives the
aggregate that `score-from-r2.yml` derives.

```
prb submit ──POST /submit──▶ Worker ──put──▶ R2 s3://prb-submissions/incoming/<ts>-<uuid>.json
                              (Bearer PRB_API_KEY)                     (private)
```

## One-time setup (maintainer)

```bash
npm i -g wrangler
wrangler login

# 1. private bucket for raw submissions
wrangler r2 bucket create prb-submissions

# 2. the bearer token submitters will use (pick a strong value)
wrangler secret put PRB_API_KEY        # paste the token

# 3. deploy
cd intake/cloudflare-worker
wrangler deploy
# → registers your workers.dev subdomain (e.g. prb-bench) and prints the endpoint,
#   e.g. https://intake.prb-bench.workers.dev
```

The Worker only writes to R2 — no GitHub token needed. Scoring is picked up by
`.github/workflows/score-from-r2.yml` on its **daily cron** (or trigger it manually via
`workflow_dispatch`). It scores privately and opens a PR; the maintainer reviewing +
merging that aggregate PR is the single human checkpoint (no pre-run approval).

The scorer snapshots the exact `incoming/` object keys at the start of each run and moves
them individually afterward. Successfully scored objects go to `processed/`; permanent
submission-format failures go to `failed/` with diagnostics under `failed-status/`;
retryable verifier/infrastructure failures stay in `incoming/`. An upload that arrives
during scoring is not in that run's snapshot and remains queued for the next run.

Give submitters the endpoint URL + a key:

```bash
export PRB_SUBMIT_URL=https://intake.prb-bench.workers.dev/submit
export PRB_API_KEY=<token>
python -m benchmark.submit --predictions out/submission.json
```

## R2 credentials for the scoring worker

The GitHub Actions scorer reads the bucket via the S3 API. In the Cloudflare dashboard →
R2 → *Manage API Tokens*, create an **Object Read & Write** token, then add these as
**repository** secrets (Settings → Secrets and variables → Actions → Repository secrets):
`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET=prb-submissions`.
See `.github/workflows/score-from-r2.yml`.

## Notes
- Bearer auth is a single shared secret for now; per-submitter keys / quotas can move to a
  Worker KV lookup later without changing `prb submit`.
- Max body 25 MB. For larger submissions, hand out an R2 presigned PUT URL
  instead of POSTing the body — not needed at current scale.
- The intake response intentionally exposes only the opaque submission ID, not the internal
  R2 object key.
