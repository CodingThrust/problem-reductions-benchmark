"""Setup pred environment at pinned commit."""
import shutil
import subprocess
from pathlib import Path
from benchmark.env_context import EnvContext

PINNED_COMMIT = "706856087e55f34bdc5fd3fa2a730aa74c05a675"


def find_pred_binary() -> Path:
    """Find pred binary in PATH."""
    pred_path = shutil.which("pred")
    if not pred_path:
        raise RuntimeError(
            "pred binary not found in PATH. "
            "Install with: cargo install --git https://github.com/CodingThrust/problem-reductions problemreductions-cli"
        )
    return Path(pred_path)


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

    return EnvContext(
        repo_path=repo_path,
        pred_binary=pred_binary,
        commit_hash=commit_hash,
    )
