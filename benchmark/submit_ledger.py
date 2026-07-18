"""Shared validation and lookup helpers for the bounded submit ledger."""
from __future__ import annotations

import json

def has_submit_ledger(submission: dict) -> bool:
    return "submit_limit" in submission or "submit_log" in submission


def submit_ledger_error(submission: dict) -> str | None:
    """Return one structural error, or ``None`` for a valid/legacy submission."""
    if not has_submit_ledger(submission):
        return None

    limit, log = submission.get("submit_limit"), submission.get("submit_log")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        return "submit_limit must be a non-negative integer"
    if not isinstance(log, list):
        return "submit_log must be a list"
    if len(log) > limit:
        return "submit_log exceeds submit_limit"

    for expected, attempt in enumerate(log, 1):
        if not isinstance(attempt, dict) or attempt.get("attempt") != expected:
            return "submit_log attempt numbers must be contiguous from 1"
        if not isinstance(attempt.get("accepted"), bool):
            return f"submit_log attempt {expected} has no boolean accepted field"
        if not isinstance(attempt.get("reason"), str):
            return f"submit_log attempt {expected} has no reason"
        if attempt["accepted"]:
            cert, rule = attempt.get("certificate"), attempt.get("rule")
            if (not isinstance(cert, dict) or not isinstance(rule, str)
                    or cert.get("rule") != rule):
                return f"accepted submit_log attempt {expected} has inconsistent rule/certificate"
    return None


def accepted_certificate_index(submission: dict) -> set[tuple[str, str]]:
    """Canonical (rule, certificate) keys for accepted attempts in a valid ledger."""
    return {
        (attempt["rule"], _canonical(attempt["certificate"]))
        for attempt in submission.get("submit_log", [])
        if attempt.get("accepted")
    }


def certificate_key(rule: str, certificate: dict) -> tuple[str, str]:
    return rule, _canonical(certificate)


def _canonical(certificate: dict) -> str:
    return json.dumps(certificate, sort_keys=True, separators=(",", ":"))
