"""Setup pred environment at pinned commit."""
import os
import re
import shutil
import subprocess
from pathlib import Path
from benchmark.env_context import EnvContext

# The target library version is NOT a frozen constant — it tracks the benchmark as the
# upstream library evolves. The single source of truth is the Docker build arg PR_REF: at
# build time the image bakes the actually-built commit and pred version into the two files
# below (benchmark/PINNED_COMMIT, benchmark/PINNED_VERSION), and this module reads them.
# The literals here are only a fallback for local dev (no baked files, no env override).
# Precedence for each: env override > baked file (image) > module default.
_DEFAULT_PINNED_COMMIT = "aa2d1a10cffa434871d12a4d6f411147fb7e08a8"
_DEFAULT_PINNED_VERSION = "0.6.0"
DEFAULT_REPO_URL = "https://github.com/CodingThrust/problem-reductions.git"
_PIN_DIR = Path(__file__).parent


def _read_pin_file(filename: str) -> str | None:
    """Read a build-baked pin file (benchmark/<filename>), or None if absent/empty."""
    f = _PIN_DIR / filename
    if f.exists():
        text = f.read_text(encoding="utf-8").strip()
        if text:
            return text
    return None


def pinned_commit() -> str:
    """Target library commit: EXPECTED_PRED_COMMIT env > baked PINNED_COMMIT file > default."""
    return (os.environ.get("EXPECTED_PRED_COMMIT")
            or _read_pin_file("PINNED_COMMIT") or _DEFAULT_PINNED_COMMIT)


def pinned_pred_version() -> str:
    """Target pred version: baked PINNED_VERSION file > module default.

    (The EXPECTED_PRED_VERSION env is handled in verify_pred_version, where "" must mean
    "skip the check" rather than "use default" — so it is NOT folded in here.)
    """
    return _read_pin_file("PINNED_VERSION") or _DEFAULT_PINNED_VERSION


# Importable module attributes (resolved once at import; the image's baked files exist by
# then). The pred binary must match the pinned tag — bugs are version-specific, so verifying
# with a different pred (e.g. an older one on PATH) gives non-reproducible results.
PINNED_COMMIT = pinned_commit()
PINNED_PRED_VERSION = pinned_pred_version()


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
        expected = os.environ.get("EXPECTED_PRED_VERSION", pinned_pred_version())
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


def clone_or_verify_repo(repo_path: str | Path, repo_ref: str,
                         repo_url: str = DEFAULT_REPO_URL) -> str:
    """Clone ``repo_ref`` into an absent path, or verify an existing checkout.

    Existing checkouts are deliberately never fetched, reset, or checked out: a local run
    must not mutate a user's working tree.  It either already points at the requested ref or
    fails with an actionable error.  The returned full commit hash is recorded in the
    submission envelope.
    """
    repo_path = Path(repo_path).expanduser().resolve()
    if not repo_path.exists():
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        clone = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", "--branch", repo_ref,
             repo_url, str(repo_path)],
            capture_output=True,
            text=True,
        )
        if clone.returncode != 0:
            # `git clone --branch` accepts tags and branches, but not raw commit hashes.
            # For a commit pin, clone the default ref and fetch that exact object.
            if (not re.fullmatch(r"[0-9a-fA-F]{7,40}", repo_ref)
                    or repo_path.exists()):
                detail = clone.stderr.strip().splitlines()[-1] if clone.stderr.strip() else ""
                raise ValueError(
                    f"could not clone ref {repo_ref!r} from {repo_url!r} into {repo_path}"
                    + (f": {detail}" if detail else "")
                )
            try:
                subprocess.run(
                    ["git", "clone", "--depth", "1", "--no-checkout", repo_url,
                     str(repo_path)],
                    check=True,
                )
                subprocess.run(
                    ["git", "-C", str(repo_path), "fetch", "--depth", "1", "origin",
                     repo_ref],
                    check=True,
                )
                subprocess.run(
                    ["git", "-C", str(repo_path), "checkout", "--detach", "FETCH_HEAD"],
                    check=True,
                )
            except subprocess.CalledProcessError as exc:
                raise ValueError(
                    f"could not clone commit {repo_ref!r} from {repo_url!r} into {repo_path}"
                ) from exc
    elif not (repo_path / ".git").exists():
        raise ValueError(f"repo directory exists but is not a git checkout: {repo_path}")

    actual = _git_rev_parse(repo_path, "HEAD")
    try:
        expected = _git_rev_parse(repo_path, f"{repo_ref}^{{commit}}")
    except subprocess.CalledProcessError as exc:
        # A shallow checkout may know HEAD but not retain a symbolic name for a commit ref.
        if re.fullmatch(r"[0-9a-fA-F]{7,40}", repo_ref) and actual.startswith(repo_ref.lower()):
            expected = actual
        else:
            raise ValueError(
                f"ref {repo_ref!r} is not available in existing checkout {repo_path}; "
                "use an absent --repo-dir to clone it"
            ) from exc
    if actual != expected:
        raise ValueError(
            f"existing checkout {repo_path} is at {actual[:12]}, but {repo_ref!r} resolves "
            f"to {expected[:12]}; choose another --repo-dir or update it yourself"
        )
    return actual


def _git_rev_parse(repo_path: Path, revision: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", revision],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip().lower()


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
    commit_hash = verify_commit(repo_path, pinned_commit())
    pred_ver = verify_pred_version(pred_binary)

    return EnvContext(
        repo_path=repo_path,
        pred_binary=pred_binary,
        commit_hash=commit_hash,
        pred_version=pred_ver,
    )
