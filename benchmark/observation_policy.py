"""Deterministic, bounded packaging for model-facing process observations."""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

POLICY_ID = "terminal-diagnostics/v1"
DEFAULT_PREVIEW_CHARS = 10_000
DEFAULT_ARCHIVE_CHARS = 1_048_576
_CONTEXT_LINES = 2
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DIAGNOSTIC = re.compile(
    r"(?i)(error|fail(?:ed|ure)?|panic|assert(?:ion)?|traceback|exception|timeout|timed out|"
    r"sentinel|left\s*[:=]|right\s*[:=]|\b\d+\s+failed\b|test result:)")


@dataclass(frozen=True)
class ObservationConfig:
    policy_id: str = POLICY_ID
    preview_chars: int = DEFAULT_PREVIEW_CHARS
    archive_chars: int = DEFAULT_ARCHIVE_CHARS

    def __post_init__(self) -> None:
        if self.policy_id != POLICY_ID:
            raise ValueError(f"unsupported observation policy: {self.policy_id}")
        if self.preview_chars < 256 or self.archive_chars < self.preview_chars:
            raise ValueError("archive_chars must be >= preview_chars >= 256")


@dataclass(frozen=True)
class PackagedObservation:
    preview: str
    metadata: dict


@dataclass(frozen=True)
class _Line:
    number: int
    end_number: int
    text: str
    repeated: int = 1


class ObservationStore:
    """Write immutable per-session logs and produce deterministic compact previews."""

    def __init__(self, directory: str | Path, *, config: ObservationConfig,
                 relative_from: str | Path, readable_gid: int | None = None):
        self.directory = Path(directory).resolve()
        self.relative_from = Path(relative_from).resolve()
        self.config = config
        self.readable_gid = readable_gid
        self._sequence = 0
        self._lock = threading.RLock()
        self.directory.mkdir(parents=True, exist_ok=False)
        if readable_gid is not None:
            os.chown(self.directory, os.geteuid(), readable_gid)
            self.directory.chmod(0o750)
        else:
            self.directory.chmod(0o700)

    def package(self, *, kind: str, command: str, returncode: int, timed_out: bool,
                stdout: str, stderr: str, original_chars: int, original_lines: int,
                archive_truncated: bool,
                observation_id: str | None = None) -> PackagedObservation:
        if observation_id is None:
            with self._lock:
                self._sequence += 1
                observation_id = f"{kind}-{self._sequence:04d}"
        if not re.fullmatch(r"(?:shell-[0-9]{4}|pred-[0-9a-f]{32})", observation_id):
            raise ValueError(f"invalid observation id: {observation_id!r}")
        path = self.directory / f"{observation_id}.log"

        raw = _raw_log(stdout, stderr)
        raw, store_truncated = _bounded_text(raw, self.config.archive_chars)
        archive_truncated = archive_truncated or store_truncated
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o640 if self.readable_gid is not None else 0o600)
        try:
            payload = raw.encode("utf-8")
            written = 0
            while written < len(payload):
                written += os.write(fd, payload[written:])
        finally:
            os.close(fd)
        if self.readable_gid is not None:
            os.chown(path, os.geteuid(), self.readable_gid)

        relative = os.path.relpath(path, self.relative_from)
        header = (
            f"[observation {observation_id}] policy={self.config.policy_id} "
            f"returncode={returncode} timed_out={str(timed_out).lower()}\n"
            f"[raw log: {relative}; original={original_chars} chars/{original_lines} lines; "
            f"archive={'truncated' if archive_truncated else 'complete'}]\n"
        )
        body_limit = max(0, self.config.preview_chars - len(header))
        body, compacted = _compact_output(stdout, stderr, body_limit)
        preview = header + body
        metadata = {
            "observation_id": observation_id,
            "kind": kind,
            "command": command,
            "policy_id": self.config.policy_id,
            "raw_log": relative,
            "returncode": returncode,
            "timed_out": timed_out,
            "original_chars": original_chars,
            "original_lines": original_lines,
            "preview_chars": len(preview),
            "archive_chars": len(raw),
            "preview_compacted": compacted,
            "archive_truncated": archive_truncated,
        }
        return PackagedObservation(preview=preview, metadata=metadata)


def _raw_log(stdout: str, stderr: str) -> str:
    if stderr:
        return f"[stdout]\n{stdout}\n[stderr]\n{stderr}"
    return stdout


def _bounded_text(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    marker = f"\n... raw archive truncated; {len(text) - limit} or more characters omitted ...\n"
    available = max(0, limit - len(marker))
    head = available // 2
    tail = available - head
    return text[:head] + marker + (text[-tail:] if tail else ""), True


def _compact_output(stdout: str, stderr: str, limit: int) -> tuple[str, bool]:
    streams = [("stdout", stdout)]
    if stderr:
        streams.append(("stderr", stderr))
    rendered: list[str] = []
    compacted = False
    stream_limit = max(0, (limit - len(streams)) // len(streams))
    for name, text in streams:
        normalized_json = _compact_json(text)
        if normalized_json is not None:
            text = normalized_json
            compacted = True
        lines, changed = _normalize_lines(text)
        compacted = compacted or changed
        rendered.append(f"[{name}]")
        selected, selected_compacted = _select_lines(
            lines, max(0, stream_limit - len(name) - 3))
        compacted = compacted or selected_compacted
        rendered.extend(selected)
    result = "\n".join(rendered).rstrip() + "\n"
    if len(result) > limit:
        raise AssertionError("observation renderer exceeded its character budget")
    return result, compacted


def _compact_json(text: str) -> str | None:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        value = json.loads(stripped)
    except (json.JSONDecodeError, UnicodeError):
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"


def _normalize_lines(text: str) -> tuple[list[_Line], bool]:
    changed = False
    physical = text.split("\n")
    lines: list[_Line] = []
    for number, raw in enumerate(physical, 1):
        if number == len(physical) and raw == "":
            continue
        if "\r" in raw:
            raw = raw.rsplit("\r", 1)[-1]
            changed = True
        clean = _CONTROL.sub("", _ANSI.sub("", raw))
        changed = changed or clean != raw
        if lines and lines[-1].text == clean:
            previous = lines[-1]
            lines[-1] = _Line(previous.number, number, clean, previous.repeated + 1)
            changed = True
        else:
            lines.append(_Line(number, number, clean))
    return lines, changed


def _render_line(line: _Line) -> str:
    text = line.text
    if len(text) > 600:
        text = text[:300] + f" ... {len(text) - 600} chars omitted ... " + text[-300:]
    suffix = (f" (repeated {line.repeated} times; original lines "
              f"{line.number}-{line.end_number})" if line.repeated > 1 else "")
    return f"[L{line.number:05d}] {text}{suffix}"


def _select_lines(lines: list[_Line], limit: int) -> tuple[list[str], bool]:
    full = [_render_line(line) for line in lines]
    if len("\n".join(full)) <= limit:
        return full, False
    diagnostic = {index for index, line in enumerate(lines) if _DIAGNOSTIC.search(line.text)}
    selected: set[int] = set()

    # Diagnostics own the budget. Prefer the latest diagnostics because compilers and test
    # runners normally put the decisive failure and final summary last.
    for index in sorted(diagnostic, reverse=True):
        _try_add(lines, selected, index, limit)
    for distance in range(1, _CONTEXT_LINES + 1):
        for index in sorted(diagnostic, reverse=True):
            _try_add(lines, selected, index - distance, limit)
            _try_add(lines, selected, index + distance, limit)

    # Spend only the remaining space on a balanced command head/tail.
    for offset in range(len(lines)):
        added = _try_add(lines, selected, offset, limit)
        added = _try_add(lines, selected, len(lines) - 1 - offset, limit) or added
        if not added and len(_render_selected(lines, selected)) >= limit:
            break
    output = _render_selected(lines, selected)
    if len("\n".join(output)) > limit:
        raise AssertionError("observation selector exceeded its character budget")
    return output, True


def _try_add(lines: list[_Line], selected: set[int], index: int, limit: int) -> bool:
    if not 0 <= index < len(lines) or index in selected:
        return False
    candidate = selected | {index}
    if len("\n".join(_render_selected(lines, candidate))) > limit:
        return False
    selected.add(index)
    return True


def _render_selected(lines: list[_Line], selected: set[int]) -> list[str]:
    ordered = sorted(selected)
    if not ordered:
        return ([f"... {sum(line.repeated for line in lines)} original lines omitted ..."]
                if lines else [])
    output: list[str] = []
    previous = -1
    for index in ordered:
        if previous == -1 and lines[index].number > 1:
            output.append(f"... {lines[index].number - 1} original lines omitted ...")
        elif previous >= 0 and index > previous + 1:
            omitted = lines[index].number - lines[previous].end_number - 1
            output.append(f"... {omitted} original lines omitted ...")
        output.append(_render_line(lines[index]))
        previous = index
    if lines[ordered[-1]].end_number < lines[-1].end_number:
        omitted = lines[-1].end_number - lines[ordered[-1]].end_number
        output.append(f"... {omitted} original lines omitted ...")
    return output


def metadata_dict(packaged: PackagedObservation) -> dict:
    """Return a defensive JSON-compatible copy for ledgers."""
    return dict(packaged.metadata)
