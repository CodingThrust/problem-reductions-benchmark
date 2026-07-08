"""
Multi-model scheduler: runs N sessions across M models in parallel,
with checkpoint/resume.
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from benchmark.runner import AgentRunner
from benchmark.verify import count_bugs


class Scheduler:
    """
    Runs sessions (model × rule) with checkpoint/resume.

    checkpoint_path: JSON file where completed sessions are persisted.
    resume:          if True, load the checkpoint and skip finished sessions.
    parallelism:     max concurrent sessions (per model, not global).
    """

    def __init__(
        self,
        runner: AgentRunner,
        models: list[str],
        rules: list[str],
        results_dir: Path,
        checkpoint_path: Path,
        ctx,
        resume: bool = False,
        parallelism: int = 1,
    ):
        self.runner = runner
        self.models = models
        self.rules = rules
        self.results_dir = Path(results_dir)
        self.checkpoint_path = Path(checkpoint_path)
        self.ctx = ctx
        self.resume = resume
        self.parallelism = parallelism

        self._lock = threading.Lock()
        self._completed: dict[str, list[dict]] = {m: [] for m in models}

        if resume and self.checkpoint_path.exists():
            self._load_checkpoint()

    # ── checkpoint I/O ────────────────────────────────────────────────────────

    def _load_checkpoint(self) -> None:
        data = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        for model, rows in data.get("completed", {}).items():
            if model in self._completed:
                self._completed[model] = rows

    def _save_checkpoint(self) -> None:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path.write_text(
            json.dumps({"completed": self._completed}, indent=2),
            encoding="utf-8",
        )

    # ── completed-rule lookup ─────────────────────────────────────────────────

    def _done_rules(self, model: str) -> set[str]:
        return {r["rule"] for r in self._completed[model]}

    # ── run ───────────────────────────────────────────────────────────────────

    def run_all(self) -> dict[str, list[dict]]:
        """Run all sessions and return {model: [result, ...]}."""
        with ThreadPoolExecutor(max_workers=max(self.parallelism, 1)) as pool:
            futures = {}
            for model in self.models:
                done = self._done_rules(model)
                for rule in self.rules:
                    if rule in done:
                        continue
                    f = pool.submit(self.runner.run, self.ctx, model, rule)
                    futures[f] = (model, rule)

            for f in as_completed(futures):
                model, _ = futures[f]
                result = f.result()
                with self._lock:
                    self._completed[model].append(result)
                    self._save_checkpoint()

        self.results_dir.mkdir(parents=True, exist_ok=True)
        for model in self.models:
            self._write_results(model)

        return dict(self._completed)

    # ── results output ────────────────────────────────────────────────────────

    def _write_results(self, model: str) -> None:
        rows = self._completed[model]
        bugs = count_bugs(rows)  # one rule = one bug
        tokens_k = sum(r.get("tokens_k", 0.0) for r in rows)
        safe_model = model.replace("/", "_").replace(":", "_")
        out = self.results_dir / f"{safe_model}.json"
        out.write_text(
            json.dumps({
                "model": model,
                "library_commit": getattr(self.ctx, "commit_hash", "unknown"),
                "bugs_found": bugs,
                "total_tokens_k": round(tokens_k, 2),
                "efficiency_bugs_per_ktok": round(bugs / tokens_k, 4) if tokens_k else 0,
                "rules_tested": len(rows),
                "results": rows,
            }, indent=2),
            encoding="utf-8",
        )
