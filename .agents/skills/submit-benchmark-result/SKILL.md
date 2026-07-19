---
name: submit-benchmark-result
description: Validate and officially upload an existing problem-reductions benchmark submission. Use when the user wants to submit a submission.json. If no result exists yet, route the request to $run-benchmark.
---

# Submit a benchmark result

Follow this workflow for an existing `submission.json`. Do not run a model or create a
submission in this skill.

## 1. Find and validate the file

Ask for the path only if the user did not provide it. If no result exists, use
`$run-benchmark` instead.

Treat the file as confidential. Do not print certificates, trajectories, source instances,
submit-log contents, or credentials. Do not edit the submission.

Run:

```bash
python3 -m benchmark.submit --predictions <submission.json> --dry-run
```

Stop if validation fails. Otherwise report only:

- absolute path;
- `model` and `library_commit`;
- claimed bugs, `total_tokens_k`, and number of submit attempts;
- `run_error`, if present.

Do not submit a result containing `run_error`. Report the error and stop.

## 2. Confirm the upload

Immediately before uploading, show:

- endpoint hostname;
- absolute file path;
- model and claimed bug count;

State that this is an official submission and that the private file will leave the machine,
then obtain explicit confirmation.

## 3. Prepare authentication

Check whether the Cloudflare Access client is already available:

```bash
command -v cloudflared >/dev/null && cloudflared --version
```

If it is missing, identify the operating system and tell the user that `cloudflared` must
be installed before submission. Give the installation step from Cloudflare's official
[downloads page](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/)
for that system. Obtain confirmation before running an installer or package-manager
command. On macOS with Homebrew, the official command is:

```bash
brew install cloudflared
```

Do not improvise a download URL, use `sudo`, or change the system without confirmation.
After installation, run `cloudflared --version`. If installation cannot be completed,
report the prerequisite and stop before uploading.

## 4. Log in and upload once

Use the official intake:

```bash
export PRB_SUBMIT_URL=https://intake.prb-bench.workers.dev/submit
PRB_ACCESS_APP="${PRB_SUBMIT_URL%/submit}"
PRB_ACCESS_TOKEN="$(cloudflared access login --no-verbose --auto-close "$PRB_ACCESS_APP")"
test -n "$PRB_ACCESS_TOKEN"
PRB_ACCESS_TOKEN="$PRB_ACCESS_TOKEN" \
  python3 -m benchmark.submit --predictions <submission.json>
unset PRB_ACCESS_TOKEN
```

The login opens GitHub in the user's browser. If no browser opens, give the user the login
URL printed by `cloudflared` and wait for them to finish. Keep token acquisition inside
command substitution. Stop if login fails or returns an empty token; do not run the upload
command. Never print or ask for the token. Do not substitute a GitHub PAT, `gh auth token`,
or `GITHUB_TOKEN`.

Upload only once. Do not retry a timeout because the first request may already be queued.
For HTTP 401, re-authenticate. If the login page says access is denied or the upload returns
HTTP 403, explain that the GitHub account is not authorized. Tell the user to ensure their
GitHub primary email has been added to the intake authorization list by a maintainer, then
stop. For 413 or 429, stop and report the error.

## 5. Report the result

On success, report the `submission_id`, model, and endpoint. Say that `accepted` means
queued privately, not scored or published. Keep the local submission until scoring is
confirmed.

## 6. Trigger scoring

After reporting an accepted submission, trigger the scoring workflow once. If GitHub CLI
is installed and authenticated, run:

```bash
SUBMISSION_ID="<submission_id returned by the upload>"
RUN_URL="$(gh workflow run score-from-r2.yml \
  --repo CodingThrust/problem-reductions-benchmark \
  --ref main)"
RUN_ID="${RUN_URL##*/}"
gh run watch "$RUN_ID" \
  --repo CodingThrust/problem-reductions-benchmark \
  --exit-status --compact

SHORT_ID="${SUBMISSION_ID:0:8}"
gh pr list \
  --repo CodingThrust/problem-reductions-benchmark \
  --state all --limit 100 \
  --json headRefName,url \
  --jq ".[] | select(.headRefName | endswith(\"--${SHORT_ID}\")) | .url"
```

If `gh` is unavailable, give the user the
[workflow page](https://github.com/CodingThrust/problem-reductions-benchmark/actions/workflows/score-from-r2.yml)
and ask them to select **Run workflow** on `main`.

The intake authorization list and GitHub Actions permissions are separate. If GitHub hides
the **Run workflow** button or returns 403, explain that a repository maintainer or a
collaborator with Actions write permission must trigger it. Do not upload again: the
accepted submission remains privately queued and the daily scheduled workflow will process
it if nobody triggers a run manually.

When the triggered run succeeds, return the matching PR URL. Match it using the first eight
characters of this upload's `submission_id`, which appear at the end of the leaderboard
branch name; do not return an unrelated latest PR. If the run fails, return its URL and the
failure. If it succeeds but no matching PR exists, return the run URL and explain that no PR
was created instead of guessing a link.
