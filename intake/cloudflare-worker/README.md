# prb submission intake (Cloudflare Worker)

The write-only, confidential intake for `prb submit`. Submitters POST their `submission.json`
over HTTPS; this Worker checks a bearer key and deposits the raw body (certificate +
trajectory = the answer key) into a **private R2 bucket**. Nobody reads the bucket back; the
public leaderboard only ever gets the aggregate that `score-from-r2.yml` derives.

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

# 3. event-driven trigger: a fine-grained PAT (Contents: write on the repo) so the Worker
#    can fire repository_dispatch → score-from-r2 (which still waits for your approval).
#    Create at github.com/settings/tokens?type=beta, scoped to the one repo.
wrangler secret put GH_DISPATCH_TOKEN  # paste the PAT
#    (GH_DISPATCH_REPO is a plain var in wrangler.toml — edit it if you fork.)

# 4. deploy
cd intake/cloudflare-worker
wrangler deploy
# → registers your workers.dev subdomain (e.g. prb-bench) and prints the endpoint,
#   e.g. https://intake.prb-bench.workers.dev
```

On each submission the Worker deposits the body in R2 **and** fires `repository_dispatch`,
which starts the scoring workflow — it then pauses for maintainer approval (the `scoring`
environment) before it scores and publishes the aggregate. If `GH_DISPATCH_TOKEN` is unset,
the Worker just stores the submission and you trigger scoring manually.

Give submitters the endpoint URL + a key:

```bash
export PRB_SUBMIT_URL=https://intake.prb-bench.workers.dev/submit
export PRB_API_KEY=<token>
python -m benchmark.submit --predictions out/submission.json
```

## R2 credentials for the scoring worker

The GitHub Actions scorer reads the bucket via the S3 API. In the Cloudflare dashboard →
R2 → *Manage API Tokens*, create an **Object Read & Write** token, then add these repo
secrets (Settings → Secrets → Actions): `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`, `R2_BUCKET=prb-submissions`. See `.github/workflows/score-from-r2.yml`.

## Notes
- Bearer auth is a single shared secret for now; per-submitter keys / quotas can move to a
  Worker KV lookup later without changing `prb submit`.
- Max body 25 MB (embedded trajectories). For larger, hand out an R2 presigned PUT URL
  instead of POSTing the body — not needed at current scale.
