"""
Assemble the static HF Space bundle from the canonical leaderboard files.

For an HF static Space the root index.html must BE the page (no redirect) — a
meta-refresh root is fragile inside HF's embedding iframe. So the canonical
leaderboard (which lives at leaderboard/index.html and fetches ../results/...)
is flattened to the bundle root with its fetch path rewritten to same-dir:

    space/site/
      README.md              (HF Space frontmatter, from space/README.md)
      index.html             (the leaderboard itself, fetches results/index.json)
      results/index.json     (rebuilt by build_index)
      results/trajectories/  (copied if present)

Usage:
    python -m benchmark.build_space [--out space/site]

Run `python -m benchmark.build_index` first so results/index.json is current.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def build_space(out_dir: Path) -> list[str]:
    """Assemble the bundle into out_dir. Returns the list of files written."""
    readme = ROOT / "space" / "README.md"
    leaderboard = ROOT / "leaderboard" / "index.html"
    index_json = ROOT / "results" / "index.json"

    missing = [str(p.relative_to(ROOT)) for p in (readme, leaderboard, index_json) if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "cannot build Space bundle, missing source files: " + ", ".join(missing)
        )

    # index.json must be valid JSON — fail loudly rather than ship a broken Space
    try:
        json.loads(index_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"results/index.json is not valid JSON: {e}") from e

    # Flatten the leaderboard to the bundle root: it fetches "../results/index.json"
    # from leaderboard/, but at root it must fetch "results/index.json".
    html = leaderboard.read_text(encoding="utf-8")
    if "../results/" not in html:
        raise ValueError("leaderboard HTML has no '../results/' fetch path to rewrite — path changed?")
    flattened = html.replace("../results/", "results/")

    if out_dir.exists():
        shutil.rmtree(out_dir)

    written = []
    _copy(readme, out_dir / "README.md")
    written.append("README.md")
    (out_dir / "index.html").write_text(flattened, encoding="utf-8")
    written.append("index.html")
    _copy(index_json, out_dir / "results" / "index.json")
    written.append("results/index.json")

    # Trajectories are optional — only present once bugs have been found.
    traj_src = ROOT / "results" / "trajectories"
    if traj_src.is_dir():
        shutil.copytree(traj_src, out_dir / "results" / "trajectories")
        for f in sorted(traj_src.rglob("*")):
            if f.is_file():
                written.append(str(Path("results/trajectories") / f.relative_to(traj_src)))

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the static HF Space bundle")
    parser.add_argument("--out", default="space/site", help="Output bundle directory")
    args = parser.parse_args()

    out_dir = (ROOT / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    try:
        written = build_space(out_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Space bundle built: {len(written)} files → {out_dir}")
    for rel in written:
        print(f"  {rel}")
    print(f"\nPreview: python -m http.server --directory {out_dir} 8000")


if __name__ == "__main__":
    main()
