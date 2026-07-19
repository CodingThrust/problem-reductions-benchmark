#!/usr/bin/env bash
set -u

REPO="CodingThrust/problem-reductions-benchmark"
WORKFLOW="score-from-r2.yml"
RUN_PREFIX="https://github.com/${REPO}/actions/runs/"

if [ "$#" -ne 1 ] || [ "${#1}" -lt 8 ]; then
  echo "usage: trigger-scoring.sh <submission_id>" >&2
  exit 2
fi
submission_id="$1"

if ! command -v gh >/dev/null; then
  echo "GitHub CLI is required to trigger and follow scoring." >&2
  exit 2
fi
if ! gh auth status --hostname github.com >/dev/null 2>&1; then
  echo "GitHub CLI is not authenticated. Run: gh auth login" >&2
  exit 2
fi

actor="$(gh api user --jq .login)" || exit 1
triggered_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if ! run_output="$(gh workflow run "$WORKFLOW" --repo "$REPO" --ref main)"; then
  echo "Failed to trigger the scoring workflow." >&2
  exit 1
fi

run_url="$(printf '%s\n' "$run_output" \
  | sed -nE "s#.*(${RUN_PREFIX}[0-9]+).*#\1#p" \
  | tail -n 1)"

# Older gh versions may dispatch successfully without printing the run URL. Recover the run
# created by this user after the dispatch began, allowing a short delay for API visibility.
attempt=0
while [ -z "$run_url" ] && [ "$attempt" -lt 10 ]; do
  run_url="$(gh run list --repo "$REPO" --workflow "$WORKFLOW" \
    --event workflow_dispatch --branch main --user "$actor" \
    --created ">=${triggered_at}" --limit 1 --json url --jq '.[0].url // empty')"
  [ -n "$run_url" ] || sleep 2
  attempt=$((attempt + 1))
done

run_id="${run_url##*/}"
case "$run_url" in
  "${RUN_PREFIX}"*) ;;
  *)
    echo "Scoring was triggered, but its run URL could not be found." >&2
    exit 1
    ;;
esac
case "$run_id" in
  ""|*[!0-9]*)
    echo "Scoring was triggered, but its run URL could not be found." >&2
    exit 1
    ;;
esac

if ! gh run watch "$run_id" --repo "$REPO" --exit-status --compact; then
  echo "Scoring failed: $run_url" >&2
  exit 1
fi

short_id="$(printf '%.8s' "$submission_id")"
pr_url="$(gh pr list --repo "$REPO" --state all --limit 100 \
  --json headRefName,url \
  --jq "[.[] | select(.headRefName | endswith(\"--${short_id}\"))][0].url // empty")"

if [ -z "$pr_url" ]; then
  echo "Scoring succeeded, but no matching PR was created. Run: $run_url" >&2
  exit 2
fi

printf '%s\n' "$pr_url"
