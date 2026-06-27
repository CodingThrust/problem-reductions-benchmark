"""
Preflight check for a real submission run.

Uses the SAME config you'd give the full batch (model, key, api_base, model_kwargs, price,
pred version) but does the minimum real work needed to prove the run won't error out:

  1. verify the `pred` binary + version,
  2. confirm the library's reduction rules are present,
  3. make ONE tiny real model call (~$0.0001) to validate credentials / endpoint / pricing.

Exits 0 only if every check passes — so you can launch the $20 batch with confidence
instead of discovering a typo'd key or wrong base URL 20 rules in. This is a user-facing
preflight (it spends a fraction of a cent of real money); the no-API wiring of the runner
itself is covered by the pytest suite (tests/test_run_submission.py), not here.
"""
from __future__ import annotations

from benchmark.cost import Price, usage_from_response
from benchmark.env_setup import verify_pred_version
from benchmark.run_mini import DEFAULT_MAX_TOKENS, _build_model, list_rules

PROBE_PROMPT = "Reply with exactly: OK"

Check = tuple[str, bool, str]  # (name, ok, detail)


def run_checks(
    model_name: str,
    *,
    repo_dir: str,
    api_base: str | None = None,
    api_key: str | None = None,
    model_kwargs: dict | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    price: Price | None = None,
) -> list[Check]:
    """Run the three preflight checks and return their results (never raises)."""
    results: list[Check] = []

    # 1. pred binary + version (no API).
    try:
        ver = verify_pred_version("pred")
        results.append(("pred binary", True, f"version {ver}"))
    except Exception as e:
        results.append(("pred binary", False, str(e)))

    # 2. library rules present (no API).
    try:
        rules = list_rules(str(repo_dir))
        if rules:
            results.append(("library rules", True, f"{len(rules)} rules under {repo_dir}/src/rules"))
        else:
            results.append(("library rules", False, f"no .rs rules under {repo_dir}/src/rules"))
    except Exception as e:
        results.append(("library rules", False, str(e)))

    # 3. one real model call through the exact batch model config (validates key, endpoint,
    #    model name, model_kwargs, and that pricing computes).
    try:
        model = _build_model(model_name, api_base, max_tokens, price,
                             model_kwargs=model_kwargs, api_key=api_key)
        msgs = [{"role": "user", "content": PROBE_PROMPT}]
        # Call the raw completion, NOT model.query(): query() also parses the reply into an
        # agent bash-action and raises FormatError on a trivial probe. We only need to prove
        # the API round-trips and that pricing computes from the returned usage.
        prep = (model._prepare_messages_for_api(msgs)
                if hasattr(model, "_prepare_messages_for_api") else msgs)
        response = model._query(prep)
        u = usage_from_response(getattr(response, "usage", None))
        detail = f"API reachable; {u.total_tokens} tokens this call"
        if price is not None:
            detail += f", ≈ ${price.cost(u):.6f}"
        results.append(("model call", True, detail))
    except Exception as e:
        results.append(("model call", False, f"{type(e).__name__}: {e}"))

    return results


def format_report(results: list[Check]) -> bool:
    """Print a ✓/✗ report and return True iff all checks passed."""
    all_ok = True
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")
        all_ok = all_ok and ok
    print("\nPreflight " + ("PASSED — safe to launch the full run."
                            if all_ok else "FAILED — fix the above before launching."))
    return all_ok
