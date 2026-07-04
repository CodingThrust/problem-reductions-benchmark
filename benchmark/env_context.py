"""Environment context for pred CLI."""
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EnvContext:
    """Encapsulates the pred environment: repo clone + pred binary."""

    repo_path: Path
    """Path to problem-reductions clone at pinned commit."""

    pred_binary: Path
    """Path to pred executable."""

    commit_hash: str
    """Git commit hash of the repo."""

    pred_version: str = ""
    """Version of the pred binary (e.g. '0.6.0'); must match the pinned tag."""

    def __post_init__(self):
        self.repo_path = Path(self.repo_path).resolve()
        self.pred_binary = Path(self.pred_binary).resolve()
        if not self.repo_path.exists():
            raise FileNotFoundError(f"Repo path does not exist: {self.repo_path}")
        if not self.pred_binary.exists():
            raise FileNotFoundError(f"pred binary not found: {self.pred_binary}")
