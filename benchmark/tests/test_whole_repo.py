"""
Tests for the whole-repo runner mode (one agent session over the whole library, emitting
a certificate per bug). Pure parsing/assembly — no pred, no API (verify monkeypatched).
"""
import json

from benchmark import run_mini, run_submission
from benchmark.verify import Verdict


def _msg(cert: dict) -> dict:
    return {"role": "assistant",
            "content": "CERTIFICATE_START\n" + json.dumps(cert) + "\nCERTIFICATE_END"}


class TestParseAllCertificates:
    def test_returns_every_block(self):
        c1 = {"rule": "r1", "source": {"n": 1}}
        c2 = {"rule": "r2", "source": {"n": 2}}
        certs = run_mini.parse_all_certificates([_msg(c1), {"role": "user", "content": "x"}, _msg(c2)])
        assert [c["rule"] for c in certs] == ["r1", "r2"]

    def test_two_blocks_in_one_message(self):
        c1 = {"rule": "r1", "source": {"n": 1}}
        c2 = {"rule": "r2", "source": {"n": 2}}
        merged = {"role": "assistant", "content": _msg(c1)["content"] + "\n" + _msg(c2)["content"]}
        assert len(run_mini.parse_all_certificates([merged])) == 2

    def test_dedups_same_rule_and_source(self):
        c = {"rule": "r1", "source": {"n": 1}}
        certs = run_mini.parse_all_certificates([_msg(c), _msg(c)])
        assert len(certs) == 1

    def test_skips_unparseable(self):
        bad = {"role": "assistant", "content": "CERTIFICATE_START\n{not json\nCERTIFICATE_END"}
        assert run_mini.parse_all_certificates([bad]) == []


class TestRowsFromCertificates:
    def test_bug_and_rejected_rows_share_trajectory(self, monkeypatch):
        # r1 confirmed, r2 rejected by pred; both rows carry the same session trajectory.
        monkeypatch.setattr(run_mini, "verify",
                            lambda c, r=None: Verdict(c.get("rule") == "r1", "ok"))
        certs = [{"rule": "r1", "source": {}}, {"rule": "r2", "source": {}}]
        rows = run_mini._rows_from_certificates(certs)
        assert [r["result"] for r in rows] == ["bug_found", "rejected"]
        # No per-row trajectory — the whole-repo trajectory lives once on the envelope.
        assert all("trajectory" not in r for r in rows)
        assert rows[0]["certificate"]["rule"] == "r1"


class TestDurableCertRecovery:
    def test_cert_only_in_disk_log_is_harvested(self, monkeypatch):
        # run_repo_session harvests from the trajectory AND the durable {{certs_file}} log
        # (deduped). A bug the agent wrote to disk but whose block never survived into the
        # parsed messages must still count — this is what makes an early-stop crash-safe.
        monkeypatch.setattr(run_mini, "verify",
                            lambda c, r=None: Verdict(c.get("rule") == "r9", "ok"))
        trajectory = [{"role": "assistant", "content": "chatter, no certificate here"}]
        disk_log = {"content": "CERTIFICATE_START\n"
                    + json.dumps({"rule": "r9", "source": {"n": 1}}) + "\nCERTIFICATE_END\n"}
        rows = run_mini._rows_from_certificates(
            run_mini.parse_all_certificates(trajectory + [disk_log]))
        assert [r["rule"] for r in rows] == ["r9"]
        assert rows[0]["result"] == "bug_found"

    def test_trajectory_and_disk_log_dedup(self, monkeypatch):
        # The same cert in both the trajectory and the disk log collapses to one row.
        monkeypatch.setattr(run_mini, "verify", lambda c, r=None: Verdict(True, "ok"))
        cert = {"rule": "r1", "source": {"n": 1}}
        block = "CERTIFICATE_START\n" + json.dumps(cert) + "\nCERTIFICATE_END\n"
        rows = run_mini._rows_from_certificates(run_mini.parse_all_certificates(
            [{"role": "assistant", "content": block}, {"content": block}]))
        assert len(rows) == 1


class TestBuildSubmissionTotals:
    def test_explicit_session_totals_override_row_sums(self):
        # whole-repo rows carry 0 cost/tokens; the session totals come in as explicit args.
        rows = [{"rule": "r1", "result": "bug_found", "cost": 0.0, "tokens_k": 0.0}]
        sub = run_submission.build_submission(
            "m", rows, budget_cap=20, library_commit="c",
            total_cost_usd=3.5, total_tokens_k=42.0)
        assert sub["total_cost_usd"] == 3.5
        assert sub["total_tokens_k"] == 42.0
        assert sub["efficiency_bugs_per_dollar"] == round(1 / 3.5, 4)


class TestRunWholeRepoWiring:
    def test_run_dispatches_to_repo_session(self, monkeypatch):
        # run(mode="whole-repo") must call run_repo_session and build an envelope from it,
        # without touching the per-rule Scheduler.
        captured = {}

        traj = [{"role": "assistant", "content": "run log"}]

        def fake_repo_session(model, ctx, cost_limit, **kw):
            captured["cost_limit"] = cost_limit
            return {"rows": [{"rule": "r1", "result": "bug_found", "cost": 0.0, "tokens_k": 0.0,
                              "certificate": {"rule": "r1", "source": {}}}],
                    "cost": 5.0, "tokens_k": 30.0, "trajectory": traj}

        monkeypatch.setattr(run_submission, "find_pred_binary", lambda: "pred")
        monkeypatch.setattr(run_submission, "verify_pred_version", lambda p: "1.2.3")
        monkeypatch.setattr(run_submission, "EnvContext",
                            lambda **kw: type("C", (), {"pred_version": "1.2.3", **kw})())
        monkeypatch.setattr(run_submission, "Scheduler",
                            lambda **kw: (_ for _ in ()).throw(AssertionError("scheduler used")))
        monkeypatch.setattr("benchmark.run_mini.run_repo_session", fake_repo_session)

        sub = run_submission.run("m", "/repo", budget=20, safety_margin=1.0,
                                 library_commit="deadbeef", mode="whole-repo")
        assert captured["cost_limit"] == 19.0            # budget - safety_margin
        assert sub["total_cost_usd"] == 5.0
        assert sub["bugs_found"] == 1
        assert sub["trajectory"] is traj                 # stored once on the envelope
        assert "trajectory" not in sub["results"][0]     # not duplicated onto rows
