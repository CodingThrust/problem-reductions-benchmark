"""Self-selected Top50 workflow with frozen triage and isolated rule episodes."""
from __future__ import annotations

import copy
import json
import os
import shlex
import shutil
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol

from benchmark.agent_environment import make_agent_environment, run_as_agent, sanitized_agent_env
from benchmark.evidence_budget import EvidenceBudget, EvidenceBudgetSession, EvidenceBudgetState
from benchmark.run_mini import (
    DEFAULT_MAX_TOKENS,
    _build_model,
    _load_agent_config,
    _message_text,
    _session_usage,
)
from benchmark.top50_budget import FROZEN_CONTRACT
from benchmark.usage import Usage, usage_as_dict
from benchmark.verify import Verdict, verify

TOP50_SIZE = 50
DEFAULT_HYPOTHESIS_CHARS = 500
MAX_SHORTLIST_BYTES = 128 * 1024
_RANKABLE_TOKEN = object()


@dataclass(frozen=True)
class TriageBudget:
    model_generations: int
    shell_actions: int
    max_output_chars: int = 10_000
    command_timeout_seconds: int = 300

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class Top50Contract:
    triage: TriageBudget
    episode: EvidenceBudget
    shortlist_size: int = TOP50_SIZE
    hypothesis_chars: int = DEFAULT_HYPOTHESIS_CHARS

    def __post_init__(self) -> None:
        if self.shortlist_size != TOP50_SIZE:
            raise ValueError("the rankable contract requires exactly 50 rules")
        if self.hypothesis_chars <= 0:
            raise ValueError("hypothesis_chars must be positive")


@dataclass(frozen=True)
class ShortlistEntry:
    rule: str
    hypothesis: str = ""


@dataclass
class PhaseResult:
    messages: list[dict]
    tokens_k: float = 0.0
    usage: object | None = None
    error: str | None = None


class PhaseExecutor(Protocol):
    def run_triage(self, session: "TriageSession", *, repo_path: Path,
                   inventory: tuple[str, ...], model: str) -> PhaseResult: ...

    def run_episode(self, session: EvidenceBudgetSession, *, repo_path: Path,
                    entry: ShortlistEntry, index: int, total: int,
                    model: str) -> PhaseResult: ...


class TriageSession:
    """Evaluation-owned source-only workspace and one-shot shortlist controller."""

    def __init__(self, *, inventory: tuple[str, ...], budget: TriageBudget,
                 shortlist_size: int = TOP50_SIZE,
                 hypothesis_chars: int = DEFAULT_HYPOTHESIS_CHARS,
                 agent_uid: int | None = None, agent_gid: int | None = None):
        if len(set(inventory)) != len(inventory):
            raise ValueError("canonical inventory contains duplicates")
        if (agent_uid is None) != (agent_gid is None):
            raise ValueError("agent_uid and agent_gid must be provided together")
        self.inventory = inventory
        self.budget = budget
        self.shortlist_size = shortlist_size
        self.hypothesis_chars = hypothesis_chars
        self.agent_uid = agent_uid
        self.agent_gid = agent_gid
        evidence = EvidenceBudget(
            model_generations=budget.model_generations,
            shell_actions=budget.shell_actions,
            pred_calls=0,
            solve_calls=0,
            submit_attempts=2,
            max_output_chars=budget.max_output_chars,
            pred_timeout_seconds=budget.command_timeout_seconds,
        )
        self.state = EvidenceBudgetState(evidence)
        self._tmpdir: Path | None = None
        self._workdir: Path | None = None
        self._shortlist: tuple[ShortlistEntry, ...] | None = None
        self._events: list[dict] = []
        self._lock = threading.RLock()

    @property
    def workdir(self) -> Path:
        if self._workdir is None:
            raise RuntimeError("triage session is not active")
        return self._workdir

    @property
    def shortlist(self) -> tuple[ShortlistEntry, ...] | None:
        with self._lock:
            return copy.deepcopy(self._shortlist)

    def __enter__(self) -> "TriageSession":
        self._tmpdir = Path(tempfile.mkdtemp(prefix="prb-triage-", dir="/tmp")).resolve()
        self._workdir = self._tmpdir / "work"
        self._workdir.mkdir(mode=0o700)
        if self.agent_uid is not None and self.agent_gid is not None:
            self._tmpdir.chmod(0o711)
            os.chown(self._workdir, self.agent_uid, self.agent_gid)
            self._workdir.chmod(0o700)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._tmpdir is not None:
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def record_model_generation(self, *, outcome: str = "completed",
                                infrastructure_error: bool = False) -> bool:
        reservation = None if infrastructure_error else self.state.reserve("model_generations")
        admitted = infrastructure_error or reservation is not None
        self._append_event("model_generation", reservation is not None,
                           outcome if admitted else "budget_exhausted")
        return admitted

    def admit_shell_action(self, command: str) -> bool:
        reservation = self.state.reserve("shell_actions")
        self._append_event("shell_action", reservation is not None,
                           "admitted" if reservation is not None else "budget_exhausted",
                           command=command)
        return reservation is not None

    def commit_file(self, path: str) -> tuple[bool, str]:
        """Validate and atomically freeze a model-authored shortlist JSON file."""
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.workdir / candidate
        candidate = candidate.resolve()
        if not candidate.is_relative_to(self.workdir):
            return False, "shortlist file must be inside the triage workspace"
        try:
            raw = candidate.read_bytes()
        except OSError as error:
            return False, f"cannot read shortlist: {error}"
        if len(raw) > MAX_SHORTLIST_BYTES:
            return False, f"shortlist exceeds {MAX_SHORTLIST_BYTES} bytes"
        try:
            payload = json.loads(raw)
            entries = self._validate(payload)
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            return False, str(error)
        with self._lock:
            if self._shortlist is not None:
                return False, "shortlist is already frozen"
            self._shortlist = tuple(entries)
            self._events.append({"type": "shortlist_commit", "accepted": True,
                                 "rules": [entry.rule for entry in entries]})
        return True, f"frozen {len(entries)} rules"

    def status(self) -> dict:
        return self.state.status()

    def ledger(self) -> dict:
        with self._lock:
            return {"budget": asdict(self.budget), "status": self.status(),
                    "events": copy.deepcopy(self._events),
                    "shortlist": ([asdict(entry) for entry in self._shortlist]
                                  if self._shortlist is not None else None)}

    def _validate(self, payload: object) -> list[ShortlistEntry]:
        if not isinstance(payload, list) or len(payload) != self.shortlist_size:
            raise ValueError(f"shortlist must contain exactly {self.shortlist_size} entries")
        entries: list[ShortlistEntry] = []
        for item in payload:
            if isinstance(item, str):
                rule, hypothesis = item, ""
            elif isinstance(item, dict):
                rule, hypothesis = item.get("rule"), item.get("hypothesis", "")
            else:
                raise ValueError("each shortlist entry must be a rule string or object")
            if not isinstance(rule, str) or rule not in self.inventory:
                raise ValueError(f"unknown rule in shortlist: {rule!r}")
            if not isinstance(hypothesis, str) or len(hypothesis) > self.hypothesis_chars:
                raise ValueError(
                    f"hypothesis for {rule!r} must be at most {self.hypothesis_chars} characters")
            entries.append(ShortlistEntry(rule, hypothesis))
        rules = [entry.rule for entry in entries]
        if len(set(rules)) != len(rules):
            raise ValueError("shortlist rules must be unique")
        return entries

    def _append_event(self, event_type: str, charged: bool, outcome: str, **extra) -> None:
        with self._lock:
            self._events.append({"sequence": len(self._events) + 1, "type": event_type,
                                 "charged": charged, "outcome": outcome,
                                 "budget": self.status(), **extra})


class Top50Runner:
    """Freeze one self-selected Top50, then execute 50 fresh sequential episodes."""

    def __init__(self, *, executor: PhaseExecutor, contract: Top50Contract,
                 pred_binary: str | Path, verifier: Callable[[dict], Verdict] = verify,
                 agent_uid: int | None = None, agent_gid: int | None = None,
                 oracle_uid: int | None = None, oracle_gid: int | None = None,
                 evidence_gid: int | None = None, _rankable_token=None):
        self.executor = executor
        self.contract = contract
        self.pred_binary = Path(pred_binary)
        self.verifier = verifier
        self.identities = {"agent_uid": agent_uid, "agent_gid": agent_gid,
                           "oracle_uid": oracle_uid, "oracle_gid": oracle_gid,
                           "evidence_gid": evidence_gid}
        self._rankable_contract = _rankable_token is _RANKABLE_TOKEN

    def run(self, *, model: str, repo_path: str | Path,
            inventory: list[str] | tuple[str, ...], output: str | Path | None = None,
            metadata: dict | None = None) -> dict:
        self._result_metadata = copy.deepcopy(metadata or {})
        repo_path = Path(repo_path).resolve()
        canonical = tuple(inventory)
        with TriageSession(
            inventory=canonical,
            budget=self.contract.triage,
            shortlist_size=self.contract.shortlist_size,
            hypothesis_chars=self.contract.hypothesis_chars,
            agent_uid=self.identities["agent_uid"],
            agent_gid=self.identities["agent_gid"],
        ) as triage:
            try:
                triage_result = self.executor.run_triage(
                    triage, repo_path=repo_path, inventory=canonical, model=model)
            except Exception as error:
                triage_result = PhaseResult(
                    messages=[], error=f"{type(error).__name__}: {error}")
            shortlist = triage.shortlist
            triage_ledger = triage.ledger()
        if triage_result.error:
            result = self._result(model, triage_ledger, shortlist, [], triage_result,
                                  f"triage infrastructure error: {triage_result.error}")
            return _persist(result, output)
        if shortlist is None:
            result = self._result(model, triage_ledger, None, [], triage_result,
                                  "triage ended without a valid frozen Top50")
            return _persist(result, output)

        episodes: list[dict] = []
        run_error = None
        for index, entry in enumerate(shortlist, 1):
            episode = None
            try:
                with EvidenceBudgetSession(
                    rule=entry.rule,
                    budget=self.contract.episode,
                    pred_binary=self.pred_binary,
                    verifier=self.verifier,
                    **self.identities,
                ) as episode:
                    phase = self.executor.run_episode(
                        episode, repo_path=repo_path, entry=entry,
                        index=index, total=len(shortlist), model=model)
                    ledger = episode.ledger()
            except Exception as error:
                phase = PhaseResult(messages=[], error=f"{type(error).__name__}: {error}")
                ledger = (episode.ledger() if episode is not None
                          and episode.submit is not None and episode.pred is not None else {})
            accepted = next((attempt for attempt in ledger.get("submit", [])
                             if attempt.get("accepted")), None)
            record = {
                "index": index,
                "rule": entry.rule,
                "hypothesis": entry.hypothesis,
                "status": "run_error" if phase.error else (
                    "bug_found" if accepted else "completed"),
                "accepted_submit_attempt": accepted.get("attempt") if accepted else None,
                "ledger": ledger,
                "messages": copy.deepcopy(phase.messages),
                "tokens_k": phase.tokens_k,
                "usage": _usage_dict(phase.usage),
            }
            episodes.append(record)
            if phase.error:
                run_error = f"episode {index} ({entry.rule}) infrastructure error: {phase.error}"
                break
            checkpoint = self._result(
                model, triage_ledger, shortlist, episodes, triage_result,
                f"run incomplete after episode {index}/{len(shortlist)}")
            _persist(checkpoint, output)
        result = self._result(
            model, triage_ledger, shortlist, episodes, triage_result, run_error)
        return _persist(result, output)

    def _result(self, model: str, triage: dict,
                shortlist: tuple[ShortlistEntry, ...] | None, episodes: list[dict],
                triage_result: PhaseResult, run_error: str | None) -> dict:
        result = {
            "model": model,
            "status": "run_error" if run_error else "completed",
            "rankable": (self._rankable_contract and run_error is None
                         and len(episodes) == self.contract.shortlist_size),
            "contract": {"triage": asdict(self.contract.triage),
                         "episode": asdict(self.contract.episode),
                         "shortlist_size": self.contract.shortlist_size,
                         "hypothesis_chars": self.contract.hypothesis_chars},
            "shortlist": ([asdict(entry) for entry in shortlist] if shortlist else None),
            "triage": {"ledger": triage, "messages": copy.deepcopy(triage_result.messages),
                       "tokens_k": triage_result.tokens_k,
                       "usage": _usage_dict(triage_result.usage)},
            "episodes": episodes,
        }
        if run_error:
            result["run_error"] = run_error
        result.update(copy.deepcopy(getattr(self, "_result_metadata", {})))
        return result


def build_rankable_runner(
    *, contract: Top50Contract, pred_binary: str | Path,
    agent_uid: int, agent_gid: int, oracle_uid: int, oracle_gid: int, evidence_gid: int,
    api_base: str | None = None, api_key: str | None = None,
    verifier: Callable[[dict], Verdict] = verify,
) -> Top50Runner:
    """Construct the sole rankable harness after proving the required OS boundary."""
    if os.geteuid() != 0:
        raise RuntimeError("rankable Top50 runs require the root runner privilege boundary")
    if len({agent_uid, oracle_uid}) != 2:
        raise ValueError("agent and oracle must use distinct identities")
    inference = FROZEN_CONTRACT["inference_parameters"]
    safety = FROZEN_CONTRACT["safety_controls"]
    executor = MiniSwePhaseExecutor(
        api_base=api_base, api_key=api_key, max_tokens=inference["max_tokens"],
        model_timeout_seconds=safety["model_timeout_seconds"],
        model_retries=safety["model_retries"], model_kwargs=None,
        agent_uid=agent_uid, agent_gid=agent_gid, evidence_gid=evidence_gid)
    return Top50Runner(
        executor=executor, contract=contract, pred_binary=pred_binary, verifier=verifier,
        agent_uid=agent_uid, agent_gid=agent_gid, oracle_uid=oracle_uid,
        oracle_gid=oracle_gid, evidence_gid=evidence_gid,
        _rankable_token=_RANKABLE_TOKEN)


def _usage_dict(usage: object | None) -> dict | None:
    return usage_as_dict(usage) if isinstance(usage, Usage) else None


def _persist(result: dict, output: str | Path | None) -> dict:
    if output is None:
        return result
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    os.replace(temporary, destination)
    return result


class TriageEnvironment:
    """mini-swe environment that exposes source shell actions and intercepted commit-top50."""

    def __init__(self, session: TriageSession, *, uid: int, gid: int,
                 extra_groups: tuple[int, ...] = ()):
        self.session = session
        self.uid = uid
        self.gid = gid
        self.extra_groups = extra_groups

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict:
        command = action.get("command", "")
        if not self.session.admit_shell_action(command):
            return {"output": "shell action budget exhausted\n", "returncode": 75,
                    "exception_info": ""}
        try:
            words = shlex.split(command)
        except ValueError as error:
            return {"output": str(error), "returncode": 2, "exception_info": ""}
        if words and words[0] == "commit-top50":
            if len(words) != 2:
                return {"output": "usage: commit-top50 SHORTLIST.json\n", "returncode": 2,
                        "exception_info": ""}
            accepted, message = self.session.commit_file(words[1])
            return {"output": message + "\n", "returncode": 0 if accepted else 2,
                    "exception_info": ""}
        try:
            result = run_as_agent(
                command, cwd=cwd or str(self.session.workdir), env=sanitized_agent_env(),
                timeout=timeout or self.session.budget.command_timeout_seconds,
                uid=self.uid, gid=self.gid, extra_groups=self.extra_groups,
                max_output_chars=self.session.budget.max_output_chars)
            return {"output": result.stdout, "returncode": result.returncode,
                    "exception_info": ""}
        except Exception as error:
            return {"output": getattr(error, "output", "") or "", "returncode": -1,
                    "exception_info": f"{type(error).__name__}: {error}"}

    def get_template_vars(self, **kwargs) -> dict:
        return {"cwd": str(self.session.workdir), **kwargs}

    def serialize(self) -> dict:
        return {"info": {"environment_type": type(self).__name__}}


def format_status(session, *, index: int | None = None, total: int = TOP50_SIZE) -> str:
    status = session.status()
    lines = []
    if index is not None:
        lines.append(f"rule {index}/{total}: {session.rule}")
    for key, label in (("model_generations", "model generations"),
                       ("shell_actions", "shell actions"),
                       ("pred_calls", "pred calls"), ("solve_calls", "solve calls")):
        if key in status:
            counter = status[key]
            lines.append(f"{label}: {counter['used']}/{counter['limit']}")
    if "submit_attempts" in status:
        counter = status["submit_attempts"]
        lines.append(f"submit attempts: {counter['used']}/{counter['limit']}")
    return "\n".join(lines)


class MiniSwePhaseExecutor:
    """The sole rankable mini-swe/LiteLLM implementation of the phase protocol."""

    def __init__(self, *, api_base: str | None = None, api_key: str | None = None,
                 max_tokens: int = DEFAULT_MAX_TOKENS, model_kwargs: dict | None = None,
                 model_timeout_seconds: int = 300, model_retries: int = 2,
                 agent_uid: int | None = None, agent_gid: int | None = None,
                 evidence_gid: int | None = None):
        self.api_base = api_base
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.model_kwargs = model_kwargs
        self.model_timeout_seconds = model_timeout_seconds
        self.model_retries = model_retries
        self.agent_uid = os.getuid() if agent_uid is None else agent_uid
        self.agent_gid = os.getgid() if agent_gid is None else agent_gid
        self.evidence_gid = evidence_gid
        config_path = Path(__file__).with_name("top50_config.yaml")
        self.agent_config, self.model_config, _ = _load_agent_config(
            config_path, config_path, "", force_unlimited=False)
        self._models: dict[str, object] = {}

    def run_triage(self, session: TriageSession, *, repo_path: Path,
                   inventory: tuple[str, ...], model: str) -> PhaseResult:
        environment = TriageEnvironment(
            session, uid=self.agent_uid, gid=self.agent_gid,
            extra_groups=((self.evidence_gid,) if self.evidence_gid is not None else ()))
        return self._run_agent(
            model, environment, session,
            task="Select and commit exactly 50 high-risk reduction rules.",
            template_vars={"repo_dir": str(repo_path), "inventory": json.dumps(inventory),
                           "phase": "triage"}, status=lambda: format_status(session))

    def run_episode(self, session: EvidenceBudgetSession, *, repo_path: Path,
                    entry: ShortlistEntry, index: int, total: int,
                    model: str) -> PhaseResult:
        environment = make_agent_environment(
            session, uid=self.agent_uid, gid=self.agent_gid,
            extra_groups=((self.evidence_gid,) if self.evidence_gid is not None else ()))
        return self._run_agent(
            model, environment, session,
            task=f"Investigate only reduction rule {entry.rule}.",
            template_vars={"repo_dir": str(repo_path), "rule": entry.rule,
                           "hypothesis": entry.hypothesis, "phase": "episode",
                           "rule_index": index, "rule_total": total},
            status=lambda: format_status(session, index=index, total=total))

    def _run_agent(self, model_name: str, environment, session, *, task: str,
                   template_vars: dict, status: Callable[[], str]) -> PhaseResult:
        from minisweagent.agents.default import DefaultAgent
        from minisweagent.exceptions import FormatError, Submitted

        model = self._models.get(model_name)
        if model is None:
            model = _build_model(
                model_name, self.api_base, self.max_tokens,
                model_kwargs=self.model_kwargs, api_key=self.api_key,
                observation_template=self.model_config.get("observation_template"),
                format_error_template=self.model_config.get("format_error_template"),
                model_timeout_seconds=self.model_timeout_seconds,
                model_retries=self.model_retries)
            self._models[model_name] = model

        class BudgetedAgent(DefaultAgent):
            def query(self):
                if session.status()["model_generations"]["remaining"] <= 0:
                    raise Submitted({"role": "exit", "content": "generation budget exhausted",
                                     "extra": {"exit_status": "Submitted", "submission": ""}})
                try:
                    message = super().query()
                except FormatError as error:
                    session.record_model_generation(outcome="format_error")
                    budget = status()
                    for format_message in error.messages:
                        format_message["content"] = (
                            f"[authoritative budget]\n{budget}\n\n"
                            f"{format_message.get('content', '')}")
                    raise
                except Exception:
                    session.record_model_generation(
                        outcome="provider_error", infrastructure_error=True)
                    raise
                session.record_model_generation(outcome="completed")
                return message

            def execute_actions(self, message):
                actions = message.get("extra", {}).get("actions", [])
                budget_text = status()
                if len(actions) == 1:
                    outputs = [self.env.execute(actions[0])]
                elif actions:
                    outputs = [{"output": "exactly one command is required", "returncode": 2,
                                "exception_info": ""} for _ in actions]
                else:
                    observation = self.model.format_message(
                        role="user", content=(f"[authoritative budget]\n{budget_text}\n\n"
                                              "Format error: exactly one command is required"))
                    return self.add_messages(observation)
                for output in outputs:
                    output["output"] = f"[authoritative budget]\n{budget_text}\n\n{output['output']}"
                messages = self.add_messages(*self.model.format_observation_messages(
                    message, outputs, self.get_template_vars()))
                current = session.status()
                hard_exhausted = any(
                    current[name]["used"] > 0 and current[name]["remaining"] == 0
                    for name in ("model_generations", "shell_actions", "pred_calls")
                    if name in current)
                submit_closed = bool(getattr(getattr(session, "submit", None), "closed", False))
                if hard_exhausted or submit_closed or getattr(session, "shortlist", None) is not None:
                    raise Submitted({"role": "exit", "content": "phase complete",
                                     "extra": {"exit_status": "Submitted", "submission": ""}})
                return messages

        agent = BudgetedAgent(model, environment, **self.agent_config)
        agent.extra_template_vars = template_vars | {"budget_status": status()}
        error = None
        try:
            agent.run(task=task)
        except Exception as exception:
            error = f"{type(exception).__name__}: {exception}"
        tokens_k, usage = _session_usage(agent)
        messages = [{"role": message.get("role", ""), "content": _message_text(message)}
                    for message in agent.messages]
        return PhaseResult(messages=messages, tokens_k=tokens_k,
                           usage=usage, error=error)
