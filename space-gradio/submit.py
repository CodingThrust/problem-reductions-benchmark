"""Pure submission logic for the Space — NO gradio imports (unit-testable).

Two jobs:
  * validate_submission — a fast, client-side *structural* pre-check (schema-required
    fields, ranked budget, plausible spend, distinct claimed bugs). This is UX only;
    it never scores. The authoritative count comes from the backend re-verifying every
    certificate with pred (benchmark/verify_submission.py).
  * push_submission — upload an accepted submission to the submissions dataset as PENDING
    so the backend queue can pick it up.
"""
import datetime
import json

RANKED_BUDGET = 20
COST_TOLERANCE = 1.05  # allow 5% over the cap (rounding / final-call overshoot)

REQUIRED_FIELDS = [
    "schema_version", "model", "library_commit", "budget_cap",
    "bugs_found", "total_cost_usd", "total_tokens_k", "rules_tested", "results",
]
_ROW_REQUIRED = ["rule", "result", "cost", "tokens_k"]


def _distinct_claimed_bugs(results: list[dict]) -> int:
    """Distinct rules with a certificate (the model's *claim* — backend re-verifies)."""
    return len({r.get("rule") for r in results
                if r.get("result") == "bug_found" and r.get("certificate")})


def validate_submission(data) -> tuple[bool, list[str], dict]:
    """Structural pre-check. Returns (ok, errors, summary).

    ok=False means do not submit; errors is a human-readable list; summary holds the
    provisional figures to show the user (clearly labelled as unverified).
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return False, ["Submission must be a JSON object."], {}

    for field in REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"Missing required field: {field!r}")

    results = data.get("results")
    if not isinstance(results, list) or not results:
        errors.append("'results' must be a non-empty array.")
        results = results if isinstance(results, list) else []
    else:
        for i, r in enumerate(results):
            if not isinstance(r, dict):
                errors.append(f"results[{i}] must be an object.")
                continue
            for req in _ROW_REQUIRED:
                if req not in r:
                    errors.append(f"results[{i}] missing required field: {req!r}")

    budget = data.get("budget_cap")
    if budget != RANKED_BUDGET:
        errors.append(f"budget_cap must be {RANKED_BUDGET} to be ranked (got {budget!r}).")

    cost = data.get("total_cost_usd")
    if isinstance(cost, (int, float)) and isinstance(budget, (int, float)):
        if cost > budget * COST_TOLERANCE:
            errors.append(
                f"total_cost_usd ${cost} exceeds the ${budget} budget — over-budget runs "
                "are not eligible.")

    claimed = _distinct_claimed_bugs(results)
    summary = {
        "model": data.get("model"),
        "budget_cap": budget,
        "rules_tested": data.get("rules_tested"),
        "total_cost_usd": cost,
        "claimed_distinct_bugs": claimed,
        "certificate_rows": sum(1 for r in results if r.get("certificate")),
    }
    return (len(errors) == 0), errors, summary


def _safe(name: str) -> str:
    return str(name).replace("/", "_").replace(":", "_")


def submission_path(data: dict, submitted_by: str | None) -> str:
    """Deterministic repo path for a submission file in the submissions dataset."""
    user = _safe(submitted_by or "anonymous")
    model = _safe(data.get("model", "unknown"))
    stamp = data.get("created_at") or datetime.datetime.now(datetime.timezone.utc).isoformat()
    stamp = _safe(stamp).replace(".", "_")
    return f"submissions/{user}/{model}-{stamp}.json"


def push_submission(data: dict, repo: str, token: str | None,
                    submitted_by: str | None = None) -> str:
    """Upload an accepted submission to the submissions dataset as PENDING.

    Returns the repo path written. Raises if huggingface_hub is missing or no token is
    configured (the UI then shows the PR / hf-upload fallback).
    """
    if not token:
        raise RuntimeError(
            "No write token configured for the submissions dataset. Use the manual "
            "`hf upload` / PR fallback shown below.")
    try:
        from huggingface_hub import HfApi
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("huggingface_hub is required to push submissions.") from e

    data = dict(data)
    data["submitted_by"] = submitted_by
    path_in_repo = submission_path(data, submitted_by)
    api = HfApi(token=token)
    api.upload_file(
        path_or_fileobj=json.dumps(data, indent=2).encode("utf-8"),
        path_in_repo=path_in_repo,
        repo_id=repo,
        repo_type="dataset",
        commit_message=f"submission: {data.get('model')} by {submitted_by or 'anonymous'}",
    )
    return path_in_repo
