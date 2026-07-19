"""Acceptance tests for the evaluation-owned per-rule evidence budget."""
from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from benchmark.agent_pred import _request as pred_request
from benchmark.agent_submit import _request as submit_request
from benchmark.agent_environment import run_as_agent
from benchmark.evidence_budget import EvidenceBudget, EvidenceBudgetSession
from benchmark.verify import Verdict


def _fake_pred(tmp_path: Path) -> Path:
    script = tmp_path / "real-pred"
    script.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  --version) echo 'pred 0.0.0'; exit 0 ;;\n"
        "  --help) echo 'help'; exit 0 ;;\n"
        "  list) echo '[\"r1\",\"r2\"]'; exit 0 ;;\n"
        "  fail) echo 'bad args' >&2; exit 7 ;;\n"
        "  sleep) sleep 2; exit 0 ;;\n"
        "  spam) python3 -c 'print(\"x\" * 5000)'; exit 0 ;;\n"
        "  *) echo \"ran:$*\"; exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    return script


def _budget(*, pred=4, solve=1, timeout=1) -> EvidenceBudget:
    return EvidenceBudget(
        model_generations=10,
        shell_actions=10,
        pred_calls=pred,
        solve_calls=solve,
        submit_attempts=2,
        max_output_chars=1024,
        pred_timeout_seconds=timeout,
    )


def _cert(rule: str) -> dict:
    return {"rule": rule, "source": {"type": "Example"},
            "bundle": {"target": {"type": "Target"}}}


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)


def _raw_spool(channel: Path, request_id: str, raw: bytes) -> dict:
    request = channel / "inbox" / f"{request_id}.json"
    response = channel / "outbox" / f"{request_id}.json"
    request.write_bytes(raw)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            payload = json.loads(response.read_text())
        except FileNotFoundError:
            time.sleep(0.01)
            continue
        response.unlink()
        return payload
    raise AssertionError("spool service did not respond")


def test_pred_and_solve_caps_apply_to_real_invocations(tmp_path):
    with EvidenceBudgetSession(
        rule="r1", budget=_budget(), pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(False, "no bug"),
    ) as session:
        assert _run("pred", "solve", "input.json", cwd=session.workdir).returncode == 0
        assert _run("pred", "solve", "input.json", cwd=session.workdir).returncode == 75
        results = [_run("pred", "create", "X", cwd=session.workdir) for _ in range(5)]

        assert sum(result.returncode == 0 for result in results) == 3
        assert sum(result.returncode == 75 for result in results) == 2
        status = session.status()
        assert status["pred_calls"]["used"] == 4
        assert status["solve_calls"]["used"] == 1
        assert sum(record["charged"] for record in session.pred.ledger) == 4


def test_global_output_option_cannot_bypass_solve_budget(tmp_path):
    with EvidenceBudgetSession(
        rule="r1", budget=_budget(pred=2, solve=1), pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(False, "no bug"),
    ) as session:
        first = _run("pred", "-o", "out.json", "solve", "input.json", cwd=session.workdir)
        second = _run("pred", "--output=other.json", "solve", "input.json",
                      cwd=session.workdir)

        assert first.returncode == 0
        assert second.returncode == 75
        assert session.status()["pred_calls"]["used"] == 1
        assert session.status()["solve_calls"]["used"] == 1


def test_concurrent_pred_clients_cannot_overspend(tmp_path):
    with EvidenceBudgetSession(
        rule="r1", budget=_budget(pred=4, solve=0), pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(False, "no bug"),
    ) as session:
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(
                lambda _: _run("pred", "create", "X", cwd=session.workdir), range(20)))

        assert sum(result.returncode == 0 for result in results) == 4
        assert sum(result.returncode == 75 for result in results) == 16
        assert session.status()["pred_calls"] == {"used": 4, "limit": 4, "remaining": 0}


def test_free_commands_are_cached_and_do_not_charge(tmp_path):
    with EvidenceBudgetSession(
        rule="r1", budget=_budget(pred=1, solve=0), pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(False, "no bug"),
    ) as session:
        assert _run("pred", "--budget-status", cwd=session.workdir).returncode == 0
        assert _run("pred", "--version", cwd=session.workdir).returncode == 0
        assert _run("pred", "--version", cwd=session.workdir).returncode == 0
        assert _run("pred", "list", "--rules", "--json", cwd=session.workdir).returncode == 0
        assert _run("pred", "list", "--rules", "--json", cwd=session.workdir).returncode == 0

        assert session.status()["pred_calls"]["used"] == 0
        # Cache hits return without another real process or ledger entry.
        assert len(session.pred.ledger) == 2


def test_nonzero_exit_and_timeout_consume_pred_budget(tmp_path):
    with EvidenceBudgetSession(
        rule="r1", budget=_budget(pred=2, solve=0), pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(False, "no bug"),
    ) as session:
        failed = _run("pred", "fail", cwd=session.workdir)
        timed_out = _run("pred", "sleep", cwd=session.workdir)
        exhausted = _run("pred", "create", "X", cwd=session.workdir)

        assert failed.returncode == 7
        assert timed_out.returncode == 124 and "timed out" in timed_out.stderr
        assert exhausted.returncode == 75
        assert session.status()["pred_calls"]["used"] == 2
        assert [record["outcome"] for record in session.pred.ledger[:2]] == [
            "nonzero_exit", "timeout"]


def test_gateway_infrastructure_failure_releases_reservation(tmp_path):
    real_pred = _fake_pred(tmp_path)
    with EvidenceBudgetSession(
        rule="r1", budget=_budget(pred=1, solve=0), pred_binary=real_pred,
        verifier=lambda cert: Verdict(False, "no bug"),
    ) as session:
        real_pred.unlink()  # force spawn failure after the gateway health check
        result = _run("pred", "create", "X", cwd=session.workdir)
        assert result.returncode == 2
        assert "infrastructure error" in result.stderr
        assert session.status()["pred_calls"]["used"] == 0
        assert session.pred.ledger[0]["outcome"] == "infrastructure_error"


def test_pred_output_is_drained_but_observation_is_capped(tmp_path):
    with EvidenceBudgetSession(
        rule="r1", budget=_budget(pred=1, solve=0), pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(False, "no bug"),
    ) as session:
        result = _run("pred", "spam", cwd=session.workdir)
        assert result.returncode == 0
        assert len(result.stdout) <= 1024
        assert "characters elided" in result.stdout


def test_submit_budget_is_two_per_rule_and_acceptance_closes_episode(tmp_path):
    pred = _fake_pred(tmp_path)
    rejected = tmp_path / "rejected.json"
    accepted = tmp_path / "accepted.json"
    rejected.write_text(json.dumps(_cert("r1")))
    accepted.write_text(json.dumps(_cert("r2")))

    with EvidenceBudgetSession(
        rule="r1", budget=_budget(), pred_binary=pred,
        verifier=lambda cert: Verdict(False, "no bug"),
    ) as first:
        assert _run("submit", str(rejected), cwd=first.workdir).returncode == 1
        assert _run("submit", str(rejected), cwd=first.workdir).returncode == 1
        assert _run("submit", str(rejected), cwd=first.workdir).returncode == 2
        assert first.status()["submit_attempts"] == {"used": 2, "limit": 2, "remaining": 0}

    with EvidenceBudgetSession(
        rule="r2", budget=_budget(), pred_binary=pred,
        verifier=lambda cert: Verdict(True, "confirmed"),
    ) as second:
        assert second.status()["submit_attempts"]["remaining"] == 2
        assert _run("submit", str(accepted), cwd=second.workdir).returncode == 0
        closed = _run("submit", str(accepted), cwd=second.workdir)
        assert closed.returncode == 2 and "BUDGET_EXHAUSTED" in closed.stderr
        assert second.submit.used == 1
        assert second.submit.remaining == 0


def test_wrong_rule_and_malformed_submissions_consume(tmp_path):
    malformed = tmp_path / "bad.json"
    wrong = tmp_path / "wrong.json"
    malformed.write_text("{bad")
    wrong.write_text(json.dumps(_cert("r2")))
    with EvidenceBudgetSession(
        rule="r1", budget=_budget(), pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(True, "confirmed"),
    ) as session:
        assert _run("submit", str(malformed), cwd=session.workdir).returncode == 1
        result = _run("submit", str(wrong), cwd=session.workdir)
        assert result.returncode == 1 and "does not match" in result.stderr
        assert session.submit.used == 2


def test_verifier_infrastructure_error_does_not_consume(tmp_path):
    certificate = tmp_path / "certificate.json"
    certificate.write_text(json.dumps(_cert("r1")))

    def broken_verifier(cert):
        raise RuntimeError("oracle unavailable")

    with EvidenceBudgetSession(
        rule="r1", budget=_budget(), pred_binary=_fake_pred(tmp_path),
        verifier=broken_verifier,
    ) as session:
        result = _run("submit", str(certificate), cwd=session.workdir)
        assert result.returncode == 2
        assert "INFRASTRUCTURE_ERROR" in result.stderr
        assert session.submit.used == 0
        assert session.submit.remaining == 2


def test_pred_request_replay_is_idempotent(tmp_path, monkeypatch):
    request_id = "a" * 32
    with EvidenceBudgetSession(
        rule="r1", budget=_budget(pred=1, solve=0), pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(False, "no bug"),
    ) as session:
        monkeypatch.chdir(session.workdir)
        first = pred_request({"op": "pred", "args": ["create", "X"],
                              "cwd": str(session.workdir)}, request_id)
        second = pred_request({"op": "pred", "args": ["create", "X"],
                               "cwd": str(session.workdir)}, request_id)
        assert first == second
        assert session.status()["pred_calls"]["used"] == 1
        assert len(session.pred.ledger) == 1


def test_invalid_pred_envelope_is_idempotent_and_charged(tmp_path):
    request_id = "c" * 32
    with EvidenceBudgetSession(
        rule="r1", budget=_budget(pred=1, solve=0), pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(False, "no bug"),
    ) as session:
        first = _raw_spool(session.pred.channel, request_id, b"{bad")
        second = _raw_spool(session.pred.channel, request_id, b"{bad")

        assert first == second
        assert first["model_error"] is True
        assert session.status()["pred_calls"]["used"] == 1
        assert len(session.pred.ledger) == 1


def test_submit_request_replay_is_idempotent(tmp_path):
    request_id = "b" * 32
    payload = {"op": "submit", "certificate_text": json.dumps(_cert("r1"))}
    with EvidenceBudgetSession(
        rule="r1", budget=_budget(), pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(False, "no bug"),
    ) as session:
        first = submit_request(payload, request_id)
        second = submit_request(payload, request_id)
        assert first == second
        assert session.submit.used == 1
        assert len(session.submit.attempts) == 1


def test_malformed_submit_envelope_is_idempotent(tmp_path):
    request_id = "d" * 32
    with EvidenceBudgetSession(
        rule="r1", budget=_budget(), pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(False, "no bug"),
    ) as session:
        channel = Path(os.environ["PRB_SUBMIT_DIR"])
        first = _raw_spool(channel, request_id, b"{bad")
        second = _raw_spool(channel, request_id, b"{bad")

        assert first == second
        assert session.submit.used == 1
        assert session.submit.attempts[0]["request_id"] == request_id


def test_model_and_shell_budgets_have_auditable_events(tmp_path):
    budget = EvidenceBudget(1, 1, 0, 0, submit_attempts=2)
    with EvidenceBudgetSession(
        rule="r1", budget=budget, pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(False, "no bug"),
    ) as session:
        assert session.record_model_generation(outcome="completed") is True
        assert session.record_model_generation(outcome="completed") is False
        assert session.record_model_generation(
            outcome="provider_error", infrastructure_error=True) is True
        assert session.admit_shell_action("pwd") is True
        assert session.admit_shell_action("pwd") is False

        ledger = session.ledger()
        assert [event["charged"] for event in ledger["model_generations"]] == [True, False, False]
        assert [event["charged"] for event in ledger["shell_actions"]] == [True, False]
        assert session.status()["model_generations"]["used"] == 1
        assert session.status()["shell_actions"]["used"] == 1


def test_agent_shell_output_has_one_combined_cap(tmp_path):
    result = run_as_agent(
        "python3 -c 'import sys; print(\"x\" * 5000); print(\"y\" * 5000, file=sys.stderr)'",
        cwd=str(tmp_path),
        env=dict(os.environ),
        timeout=5,
        uid=os.getuid(),
        gid=os.getgid(),
        max_output_chars=512,
    )

    assert result.returncode == 0
    assert len(result.stdout) <= 512
    assert "characters elided" in result.stdout


def test_budget_contract_rejects_invalid_values():
    with pytest.raises(ValueError, match="exactly 2"):
        EvidenceBudget(1, 1, 1, 1, submit_attempts=100)
    with pytest.raises(ValueError, match="solve_calls"):
        EvidenceBudget(1, 1, 1, 2)
