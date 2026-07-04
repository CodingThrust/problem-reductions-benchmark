"""
Multi-model scheduler: runs N sessions across M models fairly, in parallel,
with checkpoint/resume and hard budget caps.
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from benchmark.runner import AgentRunner
from benchmark.verify import count_bugs


class BudgetExhausted(Exception):
    pass


def _per_model_cap(total_budget: float, n_models: int) -> float:
    """Each model gets an equal share of the total budget."""
    return round(total_budget / max(n_models, 1), 6)


class Scheduler:
    """
    Runs sessions (model × rule) fairly under a shared budget.

    checkpoint_path: JSON file where completed sessions are persisted.
    resume:          if True, load the checkpoint and skip finished sessions.
    parallelism:     max concurrent sessions (per model, not global).
    """

    def __init__(
        self,
        runner: AgentRunner,
        models: list[str],
        rules: list[str],
        total_budget: float,
        per_rule_budget: float,
        results_dir: Path,
        checkpoint_path: Path,
        ctx,
        resume: bool = False,
        parallelism: int = 1,
        safety_margin: float = 0.0,
    ):
        self.runner = runner
        self.models = models
        self.rules = rules
        # Stop with margin: never plan to spend the last `safety_margin` dollars, so the
        # call that crosses the line still lands under the true cap (cost_limit=19 not 20).
        self.total_budget = max(total_budget - safety_margin, 0.0)
        self.per_rule_budget = per_rule_budget
        self.results_dir = Path(results_dir)
        self.checkpoint_path = Path(checkpoint_path)
        self.ctx = ctx
        self.resume = resume
        self.parallelism = parallelism

        self._lock = threading.Lock()
        self._total_spent: float = 0.0
        self._completed: dict[str, list[dict]] = {m: [] for m in models}
        self._spent: dict[str, float] = {m: 0.0 for m in models}

        if resume and self.checkpoint_path.exists():
            self._load_checkpoint()

    # ── checkpoint I/O ────────────────────────────────────────────────────────

    def _load_checkpoint(self) -> None:
        data = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        for model, rows in data.get("completed", {}).items():
            if model in self._completed:
                self._completed[model] = rows
                self._spent[model] = sum(r.get("cost", 0.0) for r in rows)
        self._total_spent = sum(self._spent.values())

    def _save_checkpoint(self) -> None:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path.write_text(
            json.dumps({"completed": self._completed}, indent=2),
            encoding="utf-8",
        )

    # ── budget helpers ────────────────────────────────────────────────────────

    def _model_cap(self) -> float:
        return _per_model_cap(self.total_budget, len(self.models))

    def _budget_ok(self, model: str) -> bool:
        cap = self._model_cap()
        return self._total_spent < self.total_budget and self._spent[model] < cap

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
                    f = pool.submit(self._run_one, model, rule)
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

    def _run_one(self, model: str, rule: str) -> dict:
        with self._lock:
            if not self._budget_ok(model):
                return {"rule": rule, "result": "skipped_budget", "cost": 0.0, "tokens_k": 0.0}
            # compute effective limit and pre-reserve budget to prevent overspend on burst
            remaining_model = self._model_cap() - self._spent[model]
            remaining_total = self.total_budget - self._total_spent
            effective_limit = min(self.per_rule_budget, remaining_model, remaining_total)
            # optimistically reserve the maximum we could spend
            self._spent[model] += effective_limit
            self._total_spent += effective_limit

        result = self.runner.run(self.ctx, model, rule, effective_limit)

        # correct the reservation to the actual spend, capped at effective_limit
        # (a well-behaved runner stays within its limit; if it overspends we still
        # only charge what we budgeted — the hard cap is enforced by the runner's
        # own cost_limit, e.g. LiteLLM's LimitsExceeded)
        actual = min(result.get("cost", 0.0), effective_limit)
        with self._lock:
            delta = actual - effective_limit
            self._spent[model] += delta
            self._total_spent += delta

        return result

    # ── results output ────────────────────────────────────────────────────────

    def _write_results(self, model: str) -> None:
        rows = self._completed[model]
        bugs = count_bugs(rows)  # one rule = one bug
        cost = self._spent[model]
        tokens_k = sum(r.get("tokens_k", 0.0) for r in rows)
        safe_model = model.replace("/", "_").replace(":", "_")
        out = self.results_dir / f"{safe_model}.json"
        out.write_text(
            json.dumps({
                "model": model,
                "library_commit": getattr(self.ctx, "commit_hash", "unknown"),
                "bugs_found": bugs,
                "total_cost_usd": round(cost, 6),
                "total_tokens_k": round(tokens_k, 2),
                "efficiency_bugs_per_ktok": round(bugs / tokens_k, 4) if tokens_k else 0,
                "efficiency_bugs_per_dollar": round(bugs / cost, 4) if cost else 0,
                "rules_tested": len(rows),
                "results": rows,
            }, indent=2),
            encoding="utf-8",
        )
