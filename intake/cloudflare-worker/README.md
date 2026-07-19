# prb submission intake (Cloudflare Worker)

The write-only, confidential intake for `prb submit`. Submitters POST their `submission.json`
over HTTPS; Cloudflare Access authenticates the submitter with GitHub, this Worker verifies
the application JWT, and then deposits the raw body (certificates plus the bounded submit
ledger) into a **private R2 bucket**. The public leaderboard only receives the aggregate that
`score-from-r2.yml` derives.

```
prb submit ──GitHub Access──▶ Worker ──put──▶ R2 s3://prb-submissions/incoming/<ts>-<uuid>.json
             (short JWT)      (verify JWT)                              (private)
```

## One-time setup (maintainer)

```bash
cd intake/cloudflare-worker
npm install
npx wrangler login

# 1. private bucket for raw submissions
npx wrangler r2 bucket create prb-submissions

# 2. deploy the Access-only Worker
npm run deploy
# → registers your workers.dev subdomain (e.g. prb-bench) and prints the endpoint,
#   e.g. https://intake.prb-bench.workers.dev
```

### GitHub-backed Cloudflare Access

Create a GitHub identity provider under **Zero Trust → Integrations → Identity providers**.
Then create a self-hosted Access application for the `intake` Worker. Its Allow policy
should select the intended GitHub organization/team, or exact GitHub-verified emails
together with `Login Methods: GitHub`; never use `Everyone`.

The checked-in `TEAM_DOMAIN` and `POLICY_AUD` in `wrangler.toml` identify the configured
Access organization and application. They are public identifiers, not credentials. The
GitHub OAuth client secret stays in Cloudflare and must not be added to this repository.

Upload an authenticated preview without promoting it to production:

```bash
npm run preview
# → https://access-auth-intake.<workers-subdomain>.workers.dev
```

The Worker validates the `Cf-Access-Jwt-Assertion` signature, issuer, audience, and expiry
against Cloudflare's rotating JWKS. It records the verified Access subject/email separately
from the submitter-claimed `submitted_by` field. A missing or invalid assertion is rejected;
there is no shared intake credential.

The Worker only writes to R2 — no GitHub token needed. Scoring is picked up by
`.github/workflows/score-from-r2.yml` on its **daily cron** (or trigger it manually via
`workflow_dispatch`). It scores privately and opens a PR; the maintainer reviewing +
merging that aggregate PR is the single human checkpoint (no pre-run approval).

The scorer snapshots the exact `incoming/` object keys at the start of each run and moves
them individually afterward. Successfully scored objects go to `processed/`; permanent
submission-format failures go to `failed/` with diagnostics under `failed-status/`;
retryable verifier/infrastructure failures stay in `incoming/`. An upload that arrives
during scoring is not in that run's snapshot and remains queued for the next run.

Submitters authenticate in their own browser and obtain a short-lived, application-scoped
token. No maintainer-issued secret or GitHub personal access token is involved:

```bash
export PRB_SUBMIT_URL=https://intake.prb-bench.workers.dev/submit
PRB_ACCESS_APP="${PRB_SUBMIT_URL%/submit}"
PRB_ACCESS_TOKEN="$(cloudflared access login --no-verbose --auto-close "$PRB_ACCESS_APP")" \
  python3 -m benchmark.submit --predictions out/submission.json --test
```

Remove `--test` only when the run is ready to become an official leaderboard submission.
Keep token acquisition inside command substitution so the JWT goes directly into the upload
process instead of the terminal. While the local Access session is valid, a later upload may
use `cloudflared access token -app="$PRB_ACCESS_APP"` in the same position.

## R2 credentials for the scoring worker

The GitHub Actions scorer reads the bucket via the S3 API. In the Cloudflare dashboard →
R2 → *Manage API Tokens*, create an **Object Read & Write** token, then add these as
**repository** secrets (Settings → Secrets and variables → Actions → Repository secrets):
`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET=prb-submissions`.
See `.github/workflows/score-from-r2.yml`.

## Notes
- `gh auth token`, GitHub PATs, and `GITHUB_TOKEN` are never intake credentials.
- Max body 25 MB. For larger submissions, hand out an R2 presigned PUT URL
  instead of POSTing the body — not needed at current scale.
- The intake response intentionally exposes only the opaque submission ID, not the internal
  R2 object key.
