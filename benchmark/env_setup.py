"""Setup pred environment at pinned commit."""
import os
import re
import shutil
import subprocess
from pathlib import Path
from benchmark.env_context import EnvContext

PINNED_COMMIT = "aa2d1a10cffa434871d12a4d6f411147fb7e08a8"
# The pred binary must match the pinned library tag — bugs are version-specific, so verifying
# with a different pred (e.g. an older one on PATH) gives non-reproducible results. Override
# the expected value with EXPECTED_PRED_VERSION; set it empty to skip the check.
PINNED_PRED_VERSION = "0.6.0"


def find_pred_binary() -> Path:
    """Find pred binary in PATH."""
    pred_path = shutil.which("pred")
    if not pred_path:
        raise RuntimeError(
            "pred binary not found in PATH. "
            "Install with: cargo install --git https://github.com/CodingThrust/problem-reductions problemreductions-cli"
        )
    return Path(pred_path)


def pred_version(binary: str | Path = "pred") -> str:
    """Return the pred binary's version string (e.g. '0.6.0'), parsed from `pred --version`."""
    result = subprocess.run([str(binary), "--version"],
                            capture_output=True, text=True, check=True)
    m = re.search(r"(\d+\.\d+\.\d+)", result.stdout)
    if not m:
        raise ValueError(f"could not parse pred version from {result.stdout.strip()!r}")
    return m.group(1)


def verify_pred_version(binary: str | Path = "pred", expected: str | None = None) -> str:
    """Return the binary's version; raise if it doesn't match the expected (pinned) one.

    `expected` defaults to env EXPECTED_PRED_VERSION or PINNED_PRED_VERSION. An empty
    expected (EXPECTED_PRED_VERSION="") skips the check but still returns the actual version.
    """
    if expected is None:
        expected = os.environ.get("EXPECTED_PRED_VERSION", PINNED_PRED_VERSION)
    actual = pred_version(binary)
    if expected and actual != expected:
        raise ValueError(
            f"pred binary is version {actual}, expected {expected}. Bugs are version-specific; "
            f"build pred from the pinned tag (v{expected}) or set EXPECTED_PRED_VERSION to override."
        )
    return actual


def verify_commit(repo_path: Path, expected_commit: str) -> str:
    """Verify repo is at expected commit, return actual commit hash."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    actual = result.stdout.strip()
    if actual != expected_commit:
        raise ValueError(
            f"Repo at {repo_path} is at commit {actual[:7]}, expected {expected_commit[:7]}. "
            f"Run: cd {repo_path} && git checkout {expected_commit}"
        )
    return actual


def setup_env(repo_path: str | Path) -> EnvContext:
    """
    Setup pred environment at pinned commit.

    Args:
        repo_path: Path to existing problem-reductions clone

    Returns:
        EnvContext with validated repo and pred binary

    Raises:
        FileNotFoundError: If repo_path doesn't exist
        RuntimeError: If pred binary not found
        ValueError: If repo is not at pinned commit
    """
    repo_path = Path(repo_path)
    if not repo_path.exists():
        raise FileNotFoundError(f"Repo path does not exist: {repo_path}")

    pred_binary = find_pred_binary()
    commit_hash = verify_commit(repo_path, PINNED_COMMIT)
    pred_ver = verify_pred_version(pred_binary)

    return EnvContext(
        repo_path=repo_path,
        pred_binary=pred_binary,
        commit_hash=commit_hash,
        pred_version=pred_ver,
    )
