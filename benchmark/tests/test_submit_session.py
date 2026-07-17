"""Tests for the evaluation-owned, run-wide agent submit command."""
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from benchmark.submit_session import SubmissionSession
from benchmark.verify import Verdict


def _cert(rule="r1"):
    return {"rule": rule, "source": {"type": "Example"},
            "bundle": {"target": {"type": "Target"}}}


def _run_submit(path):
    return subprocess.run(["submit", str(path)], capture_output=True, text=True)


def test_accepted_and_rejected_calls_share_one_budget(tmp_path):
    def verifier(cert):
        return Verdict(cert["rule"] == "good", "confirmed" if cert["rule"] == "good" else "no bug",
                       {"label": "test"} if cert["rule"] == "good" else None)

    bad, good = tmp_path / "bad.json", tmp_path / "good.json"
    bad.write_text(json.dumps(_cert("bad")))
    good.write_text(json.dumps(_cert("good")))

    with SubmissionSession(limit=2, verifier=verifier) as session:
        assert os.environ.get("PRB_SUBMIT_DIR")
        assert Path(os.environ["PRB_SUBMIT_DIR"]).is_relative_to(session.workdir)
        rejected = _run_submit(bad)
        accepted = _run_submit(good)
        exhausted = _run_submit(good)

        assert rejected.returncode == 1 and "REJECTED attempt 1/2" in rejected.stderr
        assert accepted.returncode == 0 and "ACCEPTED attempt 2/2" in accepted.stdout
        assert exhausted.returncode == 2 and "BUDGET_EXHAUSTED" in exhausted.stderr
        assert session.used == 2
        assert [a["accepted"] for a in session.attempts] == [False, True]


def test_malformed_json_consumes_attempt_and_status_is_free(tmp_path):
    malformed = tmp_path / "bad.json"
    malformed.write_text("{not json")
    with SubmissionSession(limit=2, verifier=lambda c: Verdict(True, "ok")) as session:
        status = subprocess.run(["submit", "--status"], capture_output=True, text=True)
        result = _run_submit(malformed)
        assert status.returncode == 0 and "0/2 used" in status.stdout
        assert result.returncode == 1 and "invalid certificate JSON" in result.stderr
        assert session.used == 1
        assert session.status_checks == 1
        assert session.reachable


def test_oversized_file_consumes_attempt_without_loading_it_all(tmp_path):
    oversized = tmp_path / "huge.json"
    oversized.write_bytes(b"x" * (1024 * 1024 + 1))
    with SubmissionSession(limit=2, verifier=lambda c: Verdict(True, "ok")) as session:
        result = _run_submit(oversized)
        assert result.returncode == 1
        assert "certificate exceeds" in result.stderr
        assert session.used == 1


def test_result_rows_are_derived_only_from_service_ledger(tmp_path):
    paths = []
    for i, rule in enumerate(("r1", "r1", "r2")):
        path = tmp_path / f"{i}.json"
        path.write_text(json.dumps(_cert(rule)))
        paths.append(path)
    verdicts = iter((Verdict(False, "first rejected"), Verdict(True, "ok"), Verdict(True, "ok")))
    with SubmissionSession(limit=10, verifier=lambda c: next(verdicts)) as session:
        for path in paths:
            _run_submit(path)
        rows = session.result_rows()
    assert [(r["rule"], r["result"]) for r in rows] == [("r1", "bug_found"),
                                                          ("r2", "bug_found")]
    assert [r["submit_attempt"] for r in rows] == [2, 3]


def test_concurrent_clients_are_serialized_by_one_budget(tmp_path):
    paths = []
    for i in range(6):
        path = tmp_path / f"cert-{i}.json"
        path.write_text(json.dumps(_cert(f"r{i}")))
        paths.append(path)

    with SubmissionSession(limit=4, verifier=lambda c: Verdict(True, "ok")) as session:
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(_run_submit, paths))

        assert session.used == 4
        assert sum(result.returncode == 0 for result in results) == 4
        assert sum(result.returncode == 2 for result in results) == 2


def test_preserves_certificate_artifacts_before_workspace_cleanup(tmp_path):
    destination = tmp_path / "salvaged"
    with SubmissionSession() as session:
        artifact = Path(os.environ["PRB_ARTIFACT_DIR"]) / "certificate.json"
        artifact.write_text(json.dumps(_cert()))
        copied = session.preserve_artifacts(destination)

    assert len(copied) == 1
    assert copied[0].read_text() == json.dumps(_cert())


def test_runner_response_does_not_follow_agent_symlink(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("unchanged")
    request_id = "a" * 32

    with SubmissionSession() as session:
        channel = Path(os.environ["PRB_SUBMIT_DIR"])
        response = channel / "outbox" / f"{request_id}.json"
        response.symlink_to(victim)
        request = channel / "inbox" / f"{request_id}.json"
        request.write_text(json.dumps({"request_id": request_id, "op": "status"}))

        deadline = time.monotonic() + 2
        while response.is_symlink() and time.monotonic() < deadline:
            time.sleep(0.01)

        assert not response.is_symlink()
        assert json.loads(response.read_text())["status"] == "ok"
        assert victim.read_text() == "unchanged"
        assert session.status_checks == 1


def test_artifact_salvage_skips_symlinks(tmp_path):
    victim = tmp_path / "secret-certificate.json"
    victim.write_text("secret")
    destination = tmp_path / "salvaged"
    with SubmissionSession() as session:
        (Path(os.environ["PRB_ARTIFACT_DIR"]) / "certificate.json").symlink_to(victim)
        assert session.preserve_artifacts(destination) == []
