"""Evaluation-owned logical budgets for one rule investigation episode.

The model sees thin ``pred`` and ``submit`` commands in its scratch environment.  The
authoritative counters, the real pred binary, and certificate verification remain in the
runner process.  This module intentionally uses the same atomic file-spool shape as
``SubmissionSession`` because localhost sockets are unavailable in several supported
agent sandboxes.
"""
from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
import shlex
import stat
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from benchmark.submit_session import SubmissionSession
from benchmark.observation_policy import (ObservationConfig, ObservationStore,
                                          metadata_dict)
from benchmark.process_control import ProcessLimits, run_capped_process, terminate_process_group
from benchmark.verify import (CPU_LIMIT_SECONDS, FSIZE_LIMIT_BYTES, MEM_LIMIT_BYTES,
                              Verdict, verify)

MAX_REQUEST_BYTES = 2 * 1024 * 1024
DEFAULT_PRED_TIMEOUT_SECONDS = 300
DEFAULT_MAX_OUTPUT_CHARS = 10_000
_DYNAMIC_COMMANDS = {"create", "reduce", "solve", "evaluate", "extract"}


@dataclass(frozen=True)
class EvidenceBudget:
    """The non-transferable logical allowance for one selected rule."""

    model_generations: int
    shell_actions: int
    pred_calls: int
    solve_calls: int
    submit_attempts: int = 2
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS
    pred_timeout_seconds: int = DEFAULT_PRED_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.solve_calls > self.pred_calls:
            raise ValueError("solve_calls must be <= pred_calls")
        if self.submit_attempts != 2:
            raise ValueError("submit_attempts must be exactly 2 for the Top50 benchmark")
        if self.max_output_chars == 0:
            raise ValueError("max_output_chars must be > 0")
        if self.pred_timeout_seconds == 0:
            raise ValueError("pred_timeout_seconds must be > 0")


class EvidenceBudgetState:
    """Atomically reserve, release, and report the counters for one episode."""

    def __init__(self, budget: EvidenceBudget):
        self.budget = budget
        self._limits = {
            "model_generations": budget.model_generations,
            "shell_actions": budget.shell_actions,
            "pred_calls": budget.pred_calls,
            "solve_calls": budget.solve_calls,
        }
        self._used = {name: 0 for name in self._limits}
        self._lock = threading.RLock()

    def reserve(self, *names: str) -> dict | None:
        """Reserve all named counters or none of them."""
        with self._lock:
            unknown = [name for name in names if name not in self._limits]
            if unknown:
                raise KeyError(f"unknown evidence counters: {unknown}")
            if any(self._used[name] >= self._limits[name] for name in names):
                return None
            before = dict(self._used)
            for name in names:
                self._used[name] += 1
            return {"before": before, "after": dict(self._used)}

    def release(self, *names: str) -> None:
        """Release an infrastructure-failed reservation."""
        with self._lock:
            for name in names:
                if self._used[name] <= 0:
                    raise RuntimeError(f"cannot release unreserved counter {name}")
            for name in names:
                self._used[name] -= 1

    def status(self) -> dict:
        with self._lock:
            return {
                name: {
                    "used": self._used[name],
                    "limit": limit,
                    "remaining": max(0, limit - self._used[name]),
                }
                for name, limit in self._limits.items()
            }


class PredGatewaySession:
    """Own the only agent-facing route to a real pred process."""

    def __init__(
        self,
        *,
        pred_binary: str | Path,
        workdir: str | Path,
        budget_state: EvidenceBudgetState,
        oracle_uid: int | None = None,
        oracle_gid: int | None = None,
        oracle_extra_groups: tuple[int, ...] = (),
        observation_store: ObservationStore | None = None,
    ):
        if (oracle_uid is None) != (oracle_gid is None):
            raise ValueError("oracle_uid and oracle_gid must be provided together")
        self.pred_binary = Path(pred_binary).resolve()
        self.workdir = Path(workdir).resolve()
        self.budget_state = budget_state
        self.oracle_uid = oracle_uid
        self.oracle_gid = oracle_gid
        self.oracle_extra_groups = oracle_extra_groups
        self.observation_store = observation_store
        self._ledger: list[dict] = []
        self._responses: dict[str, tuple[str, dict]] = {}
        self._cache: dict[tuple[str, ...], dict] = {}
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._futures: set[Future] = set()
        self._active_processes: set = set()
        self._channel: Path | None = None
        self._inbox_fd: int | None = None
        self._processing_fd: int | None = None
        self._outbox_fd: int | None = None
        self._old_channel_env: str | None = None
        self._stopping = threading.Event()

    @property
    def ledger(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._ledger)

    @property
    def channel(self) -> Path:
        if self._channel is None:
            raise RuntimeError("pred gateway is not active")
        return self._channel

    def __enter__(self) -> "PredGatewaySession":
        if not self.pred_binary.is_file() or not os.access(self.pred_binary, os.X_OK):
            raise RuntimeError(f"pred gateway binary is unavailable: {self.pred_binary}")

        bin_dir = self.workdir.parent / ".prb-bin"
        self._channel = self.workdir.parent / ".prb-pred"
        inbox = self._channel / "inbox"
        processing = self._channel / "processing"
        outbox = self._channel / "outbox"
        for directory in (bin_dir, inbox, processing, outbox):
            directory.mkdir(parents=True, exist_ok=True)

        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        self._inbox_fd = os.open(inbox, directory_flags)
        self._processing_fd = os.open(processing, directory_flags)
        self._outbox_fd = os.open(outbox, directory_flags)

        shim = bin_dir / "pred"
        package_root = Path(__file__).resolve().parent.parent
        shim.write_text(
            f"#!/bin/sh\nPYTHONPATH={shlex.quote(str(package_root))}${{PYTHONPATH:+:$PYTHONPATH}} "
            f"exec {shlex.quote(sys.executable)} -m benchmark.agent_pred \"$@\"\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)

        self._thread = threading.Thread(target=self._serve, name="prb-pred", daemon=True)
        worker_count = max(8, min(64, self.budget_state.budget.pred_calls + 16))
        self._executor = ThreadPoolExecutor(max_workers=worker_count,
                                            thread_name_prefix="prb-pred-call")
        self._thread.start()
        self._old_channel_env = os.environ.get("PRB_PRED_DIR")
        os.environ["PRB_PRED_DIR"] = str(self._channel)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._old_channel_env is None:
            os.environ.pop("PRB_PRED_DIR", None)
        else:
            os.environ["PRB_PRED_DIR"] = self._old_channel_env
        self._stopping.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            active = list(self._active_processes)
        for process in active:
            terminate_process_group(process)
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
        for fd in (self._inbox_fd, self._processing_fd, self._outbox_fd):
            if fd is not None:
                os.close(fd)

    def prepare_agent_access(self, uid: int, gid: int) -> None:
        """Give the unprivileged agent access only to request/response directories."""
        for name in ("inbox", "outbox"):
            directory = self.channel / name
            os.chown(directory, uid, gid)
            directory.chmod(0o700)

    def _serve(self) -> None:
        assert self._inbox_fd is not None
        assert self._processing_fd is not None
        idle_wait = 0.02
        while not self._stopping.is_set():
            handled = False
            for name in os.listdir(self._inbox_fd):
                if not _valid_request_filename(name):
                    continue
                handled = True
                try:
                    os.replace(name, name, src_dir_fd=self._inbox_fd,
                               dst_dir_fd=self._processing_fd)
                except FileNotFoundError:
                    continue
                request_id = name[:-5]
                assert self._executor is not None
                future = self._executor.submit(self._process_request_file, name, request_id)
                with self._lock:
                    self._futures.add(future)
                future.add_done_callback(self._forget_future)
            if handled:
                idle_wait = 0.02
            else:
                self._stopping.wait(idle_wait)
                idle_wait = min(idle_wait * 2, 0.5)

    def _forget_future(self, future: Future) -> None:
        with self._lock:
            self._futures.discard(future)

    def _process_request_file(self, name: str, request_id: str) -> None:
        assert self._processing_fd is not None
        raw = b""
        try:
            raw = _read_request_bytes(self._processing_fd, name)
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict) or request.get("request_id") != request_id:
                raise ValueError("request id does not match filename")
            response = self._handle_idempotent(request_id, request)
        except Exception as error:
            response = self._handle_invalid_envelope(request_id, raw, error)
        self._write_response(request_id, response)
        try:
            os.unlink(name, dir_fd=self._processing_fd)
        except FileNotFoundError:
            pass

    def _handle_idempotent(self, request_id: str, request: dict) -> dict:
        fingerprint = json.dumps(request, sort_keys=True, separators=(",", ":"))
        with self._lock:
            old = self._responses.get(request_id)
            if old is not None:
                old_fingerprint, old_response = old
                if old_fingerprint != fingerprint:
                    return {
                        "infrastructure_error": True,
                        "reason": "request id was reused with a different payload",
                        "returncode": 2,
                        "stdout": "",
                        "stderr": "pred gateway request-id collision\n",
                        "budget": self.budget_state.status(),
                    }
                return copy.deepcopy(old_response)

        response = self._handle(request_id, request)
        with self._lock:
            self._responses[request_id] = (fingerprint, copy.deepcopy(response))
        return response

    def _handle_invalid_envelope(self, request_id: str, raw: bytes, error: Exception) -> dict:
        fingerprint = f"invalid:{hashlib.sha256(raw).hexdigest()}"
        with self._lock:
            old = self._responses.get(request_id)
            if old is not None:
                old_fingerprint, old_response = old
                if old_fingerprint != fingerprint:
                    return {
                        "infrastructure_error": True,
                        "reason": "request id was reused with a different payload",
                        "returncode": 2,
                        "stdout": "",
                        "stderr": "pred gateway request-id collision\n",
                        "budget": self.budget_state.status(),
                    }
                return copy.deepcopy(old_response)
        response = self._handle_invalid_args(request_id)
        response["reason"] = f"invalid pred gateway request: {type(error).__name__}: {error}"
        with self._lock:
            self._responses[request_id] = (fingerprint, copy.deepcopy(response))
        return response

    def _handle(self, request_id: str, request: dict) -> dict:
        op = request.get("op")
        if op == "status":
            return {"status": "ok", "returncode": 0, "stdout": "", "stderr": "",
                    "budget": self.budget_state.status()}
        if op != "pred":
            return {"returncode": 2, "stdout": "", "stderr": "unknown pred gateway operation\n",
                    "reason": "unknown operation", "budget": self.budget_state.status()}

        args = request.get("args")
        if (not isinstance(args, list) or not all(isinstance(arg, str) for arg in args)
                or sum(len(arg) for arg in args) > MAX_REQUEST_BYTES):
            return self._handle_invalid_args(request_id)
        cwd = self._validated_cwd(request.get("cwd"))
        command = _command_name(args)
        free = _is_free_command(args, command)
        cache_key = tuple(args) if free else None
        with self._lock:
            if cache_key in self._cache:
                return copy.deepcopy(self._cache[cache_key])

        counters = () if free else (("pred_calls", "solve_calls")
                                    if command == "solve" else ("pred_calls",))
        reservation = self.budget_state.reserve(*counters)
        if counters and reservation is None:
            response = {
                "returncode": 75,
                "stdout": "",
                "stderr": "pred evidence budget exhausted\n",
                "exhausted": True,
                "budget": self.budget_state.status(),
            }
            self._append_pred_record(request_id, args, command, False, "budget_exhausted",
                                     response["budget"])
            return response

        record_index = self._append_pred_record(
            request_id, args, command, bool(counters), "running", self.budget_state.status())
        try:
            result = self._run_pred(args, cwd, request_id=request_id)
        except OSError as error:
            model_caused = error.errno == errno.E2BIG
            if counters and not model_caused:
                self.budget_state.release(*counters)
            response = {
                "returncode": 2,
                "stdout": "",
                "stderr": (f"pred invocation rejected: {error}\n" if model_caused else
                           f"pred gateway infrastructure error: {error}\n"),
                "infrastructure_error": not model_caused,
                "reason": f"{type(error).__name__}: {error}",
                "budget": self.budget_state.status(),
            }
            self._finish_pred_record(
                record_index, "model_error" if model_caused else "infrastructure_error", response)
            return response

        response = {**result, "budget": self.budget_state.status()}
        outcome = "timeout" if result.get("timed_out") else (
            "completed" if result["returncode"] == 0 else "nonzero_exit")
        self._finish_pred_record(record_index, outcome, response)
        if cache_key is not None and result["returncode"] == 0:
            with self._lock:
                self._cache[cache_key] = copy.deepcopy(response)
        return response

    def _handle_invalid_args(self, request_id: str) -> dict:
        reservation = self.budget_state.reserve("pred_calls")
        if reservation is None:
            return {"returncode": 75, "stdout": "",
                    "stderr": "pred evidence budget exhausted\n", "exhausted": True,
                    "budget": self.budget_state.status()}
        response = {
            "returncode": 2,
            "stdout": "",
            "stderr": "pred request arguments must be a bounded list of strings\n",
            "model_error": True,
            "budget": self.budget_state.status(),
        }
        index = self._append_pred_record(
            request_id, [], None, True, "model_error", response["budget"])
        self._finish_pred_record(index, "model_error", response)
        return response

    def _validated_cwd(self, raw: object) -> Path:
        cwd = Path(raw) if isinstance(raw, str) and raw else self.workdir
        cwd = cwd.resolve()
        if not cwd.is_relative_to(self.workdir):
            # The action is model-authored, so keep it inside the episode scratch area.
            return self.workdir
        return cwd

    def _run_pred(self, args: list[str], cwd: Path, *, request_id: str) -> dict:
        result = run_capped_process(
            [str(self.pred_binary), *args],
            shell=False,
            cwd=cwd,
            env=None,
            timeout=self.budget_state.budget.pred_timeout_seconds,
            max_output_chars=(self.observation_store.config.archive_chars
                              if self.observation_store is not None
                              else self.budget_state.budget.max_output_chars),
            uid=self.oracle_uid,
            gid=self.oracle_gid,
            extra_groups=self.oracle_extra_groups,
            limits=ProcessLimits(
                cpu_seconds=CPU_LIMIT_SECONDS,
                memory_bytes=MEM_LIMIT_BYTES,
                file_bytes=FSIZE_LIMIT_BYTES,
            ),
            on_start=self._track_process,
            on_finish=self._untrack_process,
        )
        stderr = result.stderr
        if result.timed_out:
            stderr += ("\n" if stderr and not stderr.endswith("\n") else "")
            stderr += "pred process timed out\n"
        observation = None
        stdout = result.stdout
        if self.observation_store is not None:
            packaged = self.observation_store.package(
                kind="pred", command="pred " + shlex.join(args),
                returncode=result.returncode, timed_out=result.timed_out,
                stdout=result.stdout, stderr=stderr,
                original_chars=result.original_chars, original_lines=result.original_lines,
                archive_truncated=result.capture_truncated,
                observation_id=f"pred-{request_id}")
            observation = metadata_dict(packaged)
            if result.returncode == 0:
                stdout, stderr = packaged.preview, ""
            else:
                stdout, stderr = "", packaged.preview
        return {
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": result.timed_out,
            "observation": observation,
        }

    def _track_process(self, process) -> None:
        with self._lock:
            self._active_processes.add(process)

    def _untrack_process(self, process) -> None:
        with self._lock:
            self._active_processes.discard(process)

    def _append_pred_record(self, request_id: str, args: list[str], command: str | None,
                            charged: bool, outcome: str, budget: dict) -> int:
        with self._lock:
            self._ledger.append({
                "sequence": len(self._ledger) + 1,
                "request_id": request_id,
                "args": copy.deepcopy(args),
                "command": command,
                "charged": charged,
                "outcome": outcome,
                "budget": copy.deepcopy(budget),
            })
            return len(self._ledger) - 1

    def _finish_pred_record(self, index: int, outcome: str, response: dict) -> None:
        with self._lock:
            self._ledger[index]["outcome"] = outcome
            self._ledger[index]["returncode"] = response["returncode"]
            self._ledger[index]["budget"] = copy.deepcopy(response["budget"])
            if response.get("observation") is not None:
                self._ledger[index]["observation"] = copy.deepcopy(response["observation"])

    def _write_response(self, request_id: str, response: dict) -> None:
        assert self._outbox_fd is not None
        destination = f"{request_id}.json"
        temporary = f".{request_id}.{threading.get_ident()}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        response_fd = os.open(temporary, flags, 0o644, dir_fd=self._outbox_fd)
        try:
            with os.fdopen(response_fd, "w", encoding="utf-8", closefd=False) as handle:
                json.dump(response, handle)
                handle.flush()
        finally:
            os.close(response_fd)
        os.replace(temporary, destination, src_dir_fd=self._outbox_fd,
                   dst_dir_fd=self._outbox_fd)


class EvidenceBudgetSession:
    """Compose the pred gateway and a two-attempt, rule-scoped submit judge."""

    def __init__(
        self,
        *,
        rule: str,
        budget: EvidenceBudget,
        pred_binary: str | Path,
        verifier: Callable[[dict], Verdict] = verify,
        agent_uid: int | None = None,
        agent_gid: int | None = None,
        oracle_uid: int | None = None,
        oracle_gid: int | None = None,
        evidence_gid: int | None = None,
        observation_config: ObservationConfig | None = None,
    ):
        if not rule:
            raise ValueError("rule must be non-empty")
        if (agent_uid is None) != (agent_gid is None):
            raise ValueError("agent_uid and agent_gid must be provided together")
        if (oracle_uid is None) != (oracle_gid is None):
            raise ValueError("oracle_uid and oracle_gid must be provided together")
        if evidence_gid is not None and (agent_uid is None or oracle_uid is None):
            raise ValueError("evidence_gid requires both agent and oracle identities")
        self.rule = rule
        self.budget = budget
        self.state = EvidenceBudgetState(budget)
        self.pred_binary = Path(pred_binary)
        self.verifier = verifier
        self.agent_uid = agent_uid
        self.agent_gid = agent_gid
        self.oracle_uid = oracle_uid
        self.oracle_gid = oracle_gid
        self.evidence_gid = evidence_gid
        self.observation_config = observation_config or ObservationConfig(
            preview_chars=budget.max_output_chars)
        self.submit: SubmissionSession | None = None
        self.pred: PredGatewaySession | None = None
        self._scratch: Path | None = None
        self._stack: ExitStack | None = None
        self._event_lock = threading.RLock()
        self._model_events: list[dict] = []
        self._shell_events: list[dict] = []
        self._observations: list[dict] = []
        self.observations: ObservationStore | None = None

    @property
    def workdir(self) -> Path:
        if self._scratch is None:
            raise RuntimeError("evidence budget session is not active")
        return self._scratch

    def __enter__(self) -> "EvidenceBudgetSession":
        stack = ExitStack()
        try:
            self.submit = stack.enter_context(SubmissionSession(
                limit=self.budget.submit_attempts,
                verifier=self.verifier,
                expected_rule=self.rule,
            ))
            self._scratch = self.submit.workdir / "scratch"
            self._scratch.mkdir(mode=0o700)
            self.observations = ObservationStore(
                self.submit.workdir / "observations",
                config=self.observation_config,
                relative_from=self._scratch,
                readable_gid=self.evidence_gid,
            )
            self.pred = stack.enter_context(PredGatewaySession(
                pred_binary=self.pred_binary,
                workdir=self._scratch,
                budget_state=self.state,
                oracle_uid=self.oracle_uid,
                oracle_gid=self.oracle_gid,
                oracle_extra_groups=((self.evidence_gid,) if self.evidence_gid is not None else ()),
                observation_store=self.observations,
            ))
            if self.agent_uid is not None and self.agent_gid is not None:
                self._prepare_agent_access(self.agent_uid, self.agent_gid)
            self._stack = stack
            return self
        except BaseException:
            stack.__exit__(*sys.exc_info())
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._stack is not None:
            self._stack.__exit__(exc_type, exc, tb)

    def _prepare_agent_access(self, uid: int, gid: int) -> None:
        assert self.submit is not None and self.pred is not None and self._scratch is not None
        scratch_gid = self.evidence_gid if self.evidence_gid is not None else gid
        os.chown(self._scratch, uid, scratch_gid)
        self._scratch.chmod(0o2770 if self.evidence_gid is not None else 0o700)
        self.submit.prepare_agent_access(uid, gid)
        self.pred.prepare_agent_access(uid, gid)

    def admit_shell_action(self, command: str) -> bool:
        """Atomically debit one model-authored shell action and append its audit event."""
        reservation = self.state.reserve("shell_actions")
        with self._event_lock:
            self._shell_events.append({
                "sequence": len(self._shell_events) + 1,
                "command": command,
                "charged": reservation is not None,
                "outcome": "admitted" if reservation is not None else "budget_exhausted",
                "budget": self.state.status(),
            })
        return reservation is not None

    def record_model_generation(self, *, outcome: str = "completed",
                                infrastructure_error: bool = False) -> bool:
        """Record a provider generation; infrastructure failures do not debit the model."""
        reservation = None if infrastructure_error else self.state.reserve("model_generations")
        admitted = infrastructure_error or reservation is not None
        with self._event_lock:
            self._model_events.append({
                "sequence": len(self._model_events) + 1,
                "charged": reservation is not None,
                "outcome": outcome if admitted else "budget_exhausted",
                "budget": self.state.status(),
            })
        return admitted

    def record_shell_observation(self, command: str, metadata: dict | None) -> None:
        if metadata is None:
            return
        with self._event_lock:
            self._observations.append(copy.deepcopy(metadata))
            for event in reversed(self._shell_events):
                if event.get("command") == command and "observation_id" not in event:
                    event["observation_id"] = metadata["observation_id"]
                    if event.get("charged") is True:
                        event["outcome"] = ("timeout" if metadata["timed_out"] else
                                            "completed" if metadata["returncode"] == 0 else
                                            "nonzero_exit")
                    break

    def status(self) -> dict:
        if self.submit is None:
            raise RuntimeError("evidence budget session is not active")
        return {
            **self.state.status(),
            "submit_attempts": {
                "used": self.submit.used,
                "limit": self.submit.limit,
                "remaining": self.submit.remaining,
            },
        }

    def ledger(self) -> dict:
        if self.submit is None or self.pred is None:
            raise RuntimeError("evidence budget session is not active")
        with self._event_lock:
            model_events = copy.deepcopy(self._model_events)
            shell_events = copy.deepcopy(self._shell_events)
        pred_ledger = self.pred.ledger
        observations = copy.deepcopy(self._observations)
        observations.extend(copy.deepcopy(record["observation"])
                            for record in pred_ledger if "observation" in record)
        observations.sort(key=lambda item: item["observation_id"])
        return {
            "rule": self.rule,
            "budget": asdict(self.budget),
            "status": self.status(),
            "pred": pred_ledger,
            "submit": self.submit.attempts,
            "model_generations": model_events,
            "shell_actions": shell_events,
            "observations": observations,
        }


def _valid_request_filename(name: str) -> bool:
    return (len(name) == 37 and name.endswith(".json")
            and all(char in "0123456789abcdef" for char in name[:-5]))


def _read_request_bytes(directory_fd: int, name: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    request_fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        info = os.fstat(request_fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("request is not a regular file")
        if info.st_size > MAX_REQUEST_BYTES:
            raise ValueError(f"request exceeds {MAX_REQUEST_BYTES} bytes")
        with os.fdopen(request_fd, "rb", closefd=False) as request_file:
            raw = request_file.read(MAX_REQUEST_BYTES + 1)
    finally:
        os.close(request_fd)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError(f"request exceeds {MAX_REQUEST_BYTES} bytes")
    return raw


def _command_name(args: list[str]) -> str | None:
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg in {"-o", "--output"}:
            skip_value = True
            continue
        if arg.startswith("--output=") or (arg.startswith("-o") and arg != "-o"):
            continue
        if arg in {"-q", "--quiet", "--json"}:
            continue
        if arg in _DYNAMIC_COMMANDS or arg in {"list", "help"}:
            return arg
        if not arg.startswith("-"):
            return arg
    return None


def _is_free_command(args: list[str], command: str | None) -> bool:
    if not args:
        return True  # invoking pred without args only displays help
    if any(arg in {"--help", "-h", "--version", "-V"} for arg in args):
        return True
    if command == "help":
        return True
    return command == "list" and "--rules" in args


def _drain_capped(stream, limit: int, destination: dict[str, str], key: str) -> None:
    """Drain a child pipe completely while retaining at most ``limit`` characters."""
    chunks: list[str] = []
    kept = 0
    total = 0
    while True:
        chunk = stream.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if kept < limit:
            piece = chunk[:limit - kept]
            chunks.append(piece)
            kept += len(piece)
    text = "".join(chunks)
    if total > kept:
        marker = f"\n... {total - kept} characters elided ...\n"
        prefix_limit = max(0, limit - len(marker))
        text = text[:prefix_limit] + marker[:limit]
    destination[key] = text
