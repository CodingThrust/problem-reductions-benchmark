"""Deterministic end-to-end acceptance tests for the self-selected Top50 workflow."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from benchmark.top50_runner import (
    PhaseResult,
    ShortlistEntry,
    Top50Contract,
    Top50Runner,
    TriageBudget,
    build_rankable_runner,
    format_status,
)
from benchmark.evidence_budget import EvidenceBudget
from benchmark.observation_policy import ObservationConfig
from benchmark.verify import Verdict


def _fake_pred(tmp_path: Path) -> Path:
    script = tmp_path / "real-pred"
    script.write_text("#!/bin/sh\necho ran:$*\n", encoding="utf-8")
    script.chmod(0o700)
    return script


def _contract() -> Top50Contract:
    return Top50Contract(
        triage=TriageBudget(model_generations=3, shell_actions=3, max_output_chars=1024),
        episode=EvidenceBudget(
            model_generations=2, shell_actions=2, pred_calls=1, solve_calls=0,
            submit_attempts=2, max_output_chars=1024, pred_timeout_seconds=2),
        observation=ObservationConfig(preview_chars=1024),
    )


class FakeExecutor:
    def __init__(self, shortlist_payload=None, *, fail_episode: int | None = None):
        self.shortlist_payload = shortlist_payload
        self.fail_episode = fail_episode
        self.workspaces: list[Path] = []
        self.initial_statuses: list[dict] = []

    def run_triage(self, session, *, repo_path, inventory, model):
        session.record_model_generation()
        session.admit_shell_action("commit-top50 shortlist.json")
        payload = self.shortlist_payload
        if payload is None:
            payload = [{"rule": rule, "hypothesis": f"risk-{index}"}
                       for index, rule in enumerate(inventory[:50])]
        path = session.workdir / "shortlist.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        accepted, _ = session.commit_file(str(path))
        return PhaseResult(messages=[{"role": "triage", "accepted": accepted}])

    def run_episode(self, session, *, repo_path, entry: ShortlistEntry, index, total, model):
        self.workspaces.append(session.workdir)
        self.initial_statuses.append(session.status())
        assert not (session.workdir / "sentinel").exists()
        if index == 1:
            (session.workdir / "sentinel").write_text("private")
            session.state.reserve("pred_calls")
            session.submit._handle(
                {"op": "submit", "certificate_text": json.dumps({
                    "rule": entry.rule, "source": {}, "bundle": {"target": {"type": "T"}}})})
        session.record_model_generation()
        session.admit_shell_action("pwd")
        error = "provider unavailable" if self.fail_episode == index else None
        return PhaseResult(messages=[{"role": "episode", "rule": entry.rule}], error=error)


def _runner(tmp_path: Path, executor) -> Top50Runner:
    return Top50Runner(
        executor=executor,
        contract=_contract(),
        pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(False, "not a bug"),
    )


def test_frozen_top50_runs_in_order_with_fresh_state(tmp_path):
    inventory = [f"rule_{index:02d}" for index in range(60)]
    executor = FakeExecutor()
    result = _runner(tmp_path, executor).run(
        model="fake/model", repo_path=tmp_path, inventory=inventory)

    # Injected executors are development-only even when they finish all 50 episodes.
    assert result["rankable"] is False
    assert [entry["rule"] for entry in result["shortlist"]] == inventory[:50]
    assert [episode["rule"] for episode in result["episodes"]] == inventory[:50]
    assert len({str(path) for path in executor.workspaces}) == 50
    assert all(status["pred_calls"]["used"] == 0 for status in executor.initial_statuses)
    assert all(status["submit_attempts"]["used"] == 0 for status in executor.initial_statuses)
    assert all(episode["messages"] == [{"role": "episode", "rule": episode["rule"]}]
               for episode in result["episodes"])


@pytest.mark.parametrize("payload", [
    [f"rule_{index:02d}" for index in range(49)],
    [f"rule_{index:02d}" for index in range(51)],
    ["rule_00"] * 50,
    [*[f"rule_{index:02d}" for index in range(49)], "unknown"],
    [*[f"rule_{index:02d}" for index in range(49)],
     {"rule": "rule_49", "hypothesis": "x" * 501}],
])
def test_invalid_shortlist_never_starts_an_episode(tmp_path, payload):
    executor = FakeExecutor(payload)
    result = _runner(tmp_path, executor).run(
        model="fake/model", repo_path=tmp_path,
        inventory=[f"rule_{index:02d}" for index in range(60)])

    assert result["rankable"] is False
    assert result["episodes"] == []
    assert "valid frozen Top50" in result["run_error"]


def test_episode_infrastructure_error_is_partial_and_unrankable(tmp_path):
    executor = FakeExecutor(fail_episode=3)
    result = _runner(tmp_path, executor).run(
        model="fake/model", repo_path=tmp_path,
        inventory=[f"rule_{index:02d}" for index in range(60)])

    assert result["rankable"] is False
    assert len(result["episodes"]) == 3
    assert result["episodes"][-1]["status"] == "run_error"
    assert "provider unavailable" in result["run_error"]


def test_raised_episode_error_is_checkpointed(tmp_path):
    class RaisingExecutor(FakeExecutor):
        def run_episode(self, session, **kwargs):
            if kwargs["index"] == 2:
                raise RuntimeError("gateway vanished")
            return super().run_episode(session, **kwargs)

    output = tmp_path / "partial.json"
    result = _runner(tmp_path, RaisingExecutor()).run(
        model="fake/model", repo_path=tmp_path,
        inventory=[f"rule_{index:02d}" for index in range(60)], output=output)

    assert result["rankable"] is False
    assert len(result["episodes"]) == 2
    assert "gateway vanished" in result["run_error"]
    assert json.loads(output.read_text())["run_error"] == result["run_error"]


def test_second_shortlist_commit_is_rejected(tmp_path):
    from benchmark.top50_runner import TriageSession

    inventory = tuple(f"rule_{index:02d}" for index in range(60))
    with TriageSession(inventory=inventory, budget=TriageBudget(2, 2)) as session:
        path = session.workdir / "shortlist.json"
        path.write_text(json.dumps(list(inventory[:50])))
        assert session.commit_file(str(path))[0] is True
        assert session.commit_file(str(path)) == (False, "shortlist is already frozen")


def test_observation_status_reports_every_authoritative_counter(tmp_path):
    executor = FakeExecutor()
    runner = _runner(tmp_path, executor)
    entry = ShortlistEntry("rule_00")
    from benchmark.evidence_budget import EvidenceBudgetSession

    with EvidenceBudgetSession(
        rule=entry.rule, budget=_contract().episode, pred_binary=runner.pred_binary,
        verifier=lambda cert: Verdict(False, "no bug"),
    ) as session:
        text = format_status(session, index=1, total=50)

    for expected in ("rule 1/50: rule_00", "model generations: 0/2",
                     "shell actions: 0/2", "pred calls: 0/1",
                     "solve calls: 0/0", "submit attempts: 0/2"):
        assert expected in text


def test_triage_builtin_and_error_results_use_observation_policy(tmp_path):
    import os
    from benchmark.top50_runner import TriageEnvironment, TriageSession

    inventory = tuple(f"rule_{index:02d}" for index in range(60))
    with TriageSession(inventory=inventory, budget=TriageBudget(2, 3)) as session:
        shortlist = session.workdir / "shortlist.json"
        shortlist.write_text(json.dumps(list(inventory[:50])))
        environment = TriageEnvironment(session, uid=os.getuid(), gid=os.getgid())
        outputs = [
            environment.execute({"command": "commit-top50 shortlist.json"}),
            environment.execute({"command": "'"}),
            environment.execute({"command": "commit-top50"}),
            environment.execute({"command": "pwd"}),
        ]

        assert [output["returncode"] for output in outputs] == [0, 2, 2, 75]
        assert all("policy=terminal-diagnostics/v1" in output["output"]
                   and "[raw log:" in output["output"] for output in outputs)
        ledger = session.ledger()
        shell_events = [event for event in ledger["events"]
                        if event.get("type") == "shell_action"]
        assert len(ledger["observations"]) == len(shell_events) == 4
        assert all(event.get("observation_id") for event in shell_events)


def test_rankable_contract_is_fixed_to_model_api_surface():
    executor = FakeExecutor()
    assert not hasattr(executor, "backend")
    with pytest.raises(ValueError, match="exactly 50"):
        Top50Contract(_contract().triage, _contract().episode, shortlist_size=49)


def test_only_standard_factory_can_mark_a_run_rankable(tmp_path, monkeypatch):
    import benchmark.top50_runner as top50

    monkeypatch.setattr(top50.os, "geteuid", lambda: 0)
    runner = build_rankable_runner(
        contract=_contract(), pred_binary=_fake_pred(tmp_path),
        agent_uid=10001, agent_gid=10001, oracle_uid=10002, oracle_gid=10002,
        evidence_gid=10003)
    assert runner._rankable_contract is True
    with pytest.raises(ValueError, match="distinct"):
        build_rankable_runner(
            contract=_contract(), pred_binary=_fake_pred(tmp_path),
            agent_uid=10001, agent_gid=10001, oracle_uid=10001, oracle_gid=10002,
            evidence_gid=10003)


def test_agent_environment_does_not_expose_provider_credentials(monkeypatch):
    from benchmark.agent_environment import sanitized_agent_env

    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("PRB_PRED_DIR", "/gateway")
    env = sanitized_agent_env()
    assert "OPENAI_API_KEY" not in env and "ANTHROPIC_API_KEY" not in env
    assert env["PRB_PRED_DIR"] == "/gateway"


def test_successful_shell_cannot_leave_background_process(tmp_path):
    from benchmark.agent_environment import run_as_agent, sanitized_agent_env
    import os

    sentinel = tmp_path / "late"
    command = f"(sleep 0.2; touch {sentinel}) >/dev/null 2>&1 &"
    result = run_as_agent(
        command, cwd=str(tmp_path), env=sanitized_agent_env(), timeout=2,
        uid=os.getuid(), gid=os.getgid())
    assert result.returncode == 0
    time.sleep(0.4)
    assert not sentinel.exists()


def test_agent_can_inspect_read_only_raw_observation_with_charged_action(tmp_path):
    import os
    import re
    from benchmark.agent_environment import run_as_agent, sanitized_agent_env
    from benchmark.evidence_budget import EvidenceBudgetSession

    with EvidenceBudgetSession(
        rule="rule_00", budget=_contract().episode, pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(False, "no bug"),
    ) as session:
        first_command = "printf 'important evidence\\n'"
        assert session.admit_shell_action(first_command)
        first = run_as_agent(
            first_command, cwd=str(session.workdir), env=sanitized_agent_env(), timeout=2,
            uid=os.getuid(), gid=os.getgid(), observation_store=session.observations)
        session.record_shell_observation(first_command, first.observation_metadata)
        reference = re.search(r"\[raw log: ([^;]+);", first.stdout).group(1)
        second_command = f"cat {reference}"
        assert session.admit_shell_action(second_command)
        second = run_as_agent(
            second_command, cwd=str(session.workdir), env=sanitized_agent_env(), timeout=2,
            uid=os.getuid(), gid=os.getgid(), observation_store=session.observations)
        session.record_shell_observation(second_command, second.observation_metadata)

        assert "important evidence" in second.stdout
        assert session.status()["shell_actions"]["used"] == 2
        assert len(session.ledger()["observations"]) == 2
        raw = (session.workdir / reference).resolve()
        assert raw.stat().st_mode & 0o060 in (0, 0o040)


@pytest.mark.integration
def test_distinct_agent_identity_cannot_mutate_or_reach_previous_raw_log(tmp_path):
    import os
    import sys
    if os.geteuid() != 0 or not sys.platform.startswith("linux"):
        pytest.skip("requires the rankable Linux root privilege boundary")
    from benchmark.agent_environment import run_as_agent, sanitized_agent_env
    from benchmark.evidence_budget import EvidenceBudgetSession

    agent_uid, agent_gid, evidence_gid = 61_001, 61_001, 61_003
    common = {
        "uid": agent_uid, "gid": agent_gid, "extra_groups": (evidence_gid,),
        "env": sanitized_agent_env(), "timeout": 2,
    }
    old_raw = None
    with EvidenceBudgetSession(
        rule="rule_00", budget=_contract().episode, pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(False, "no bug"),
        agent_uid=agent_uid, agent_gid=agent_gid,
        oracle_uid=0, oracle_gid=0, evidence_gid=evidence_gid,
        observation_config=_contract().observation,
    ) as session:
        command = "printf 'protected evidence\\n'"
        assert session.admit_shell_action(command)
        first = run_as_agent(
            command, cwd=str(session.workdir), observation_store=session.observations, **common)
        session.record_shell_observation(command, first.observation_metadata)
        old_raw = (session.workdir / first.observation_metadata["raw_log"]).resolve()
        assert run_as_agent(
            f"cat {old_raw}", cwd=str(session.workdir), **common).returncode == 0
        assert run_as_agent(
            f"printf x >> {old_raw}", cwd=str(session.workdir), **common).returncode != 0
        assert run_as_agent(
            f"rm {old_raw}", cwd=str(session.workdir), **common).returncode != 0
        assert run_as_agent(
            f"mv {old_raw} {old_raw}.moved", cwd=str(session.workdir), **common).returncode != 0
    assert old_raw is not None and not old_raw.exists()

    with EvidenceBudgetSession(
        rule="rule_01", budget=_contract().episode, pred_binary=_fake_pred(tmp_path),
        verifier=lambda cert: Verdict(False, "no bug"),
        agent_uid=agent_uid, agent_gid=agent_gid,
        oracle_uid=0, oracle_gid=0, evidence_gid=evidence_gid,
        observation_config=_contract().observation,
    ) as second:
        assert run_as_agent(
            f"cat {old_raw}", cwd=str(second.workdir), **common).returncode != 0
