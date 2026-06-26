"""Gradio leaderboard — fixed-$20 bug-finding race. Thin view over leaderboard.py."""
import datetime
import os

import gradio as gr
import pandas as pd
from gradio_leaderboard import ColumnFilter, Leaderboard, SelectColumns

import leaderboard as lb

_RESULTS_PATH = os.path.join(os.path.dirname(__file__), "data", "results.json")
_PINNED_TAG = "v0.6.0"
_DATA_UPDATED = datetime.date.fromtimestamp(os.path.getmtime(_RESULTS_PATH)).isoformat()

_CITATION = """@misc{problem_reductions_benchmark,
  title  = {Problem-Reductions Bug-Finding Benchmark},
  author = {Pan, Xiwei},
  year   = {2026},
  url    = {https://huggingface.co/spaces/isPANN/problem-reductions-benchmarks}
}"""

THEME = gr.themes.Soft(primary_hue="indigo", secondary_hue="purple")


def _short(model: str) -> str:
    return model.split("/")[-1]


def _reach_bar(frac: float, total: int, tested: int) -> str:
    filled = round(frac * 10)
    return f"{'▓' * filled}{'░' * (10 - filled)} {tested}/{total}"


def _leaderboard_view(results: list[dict], total: int):
    df = lb.leaderboard_frame(results, total)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    display = pd.DataFrame({
        "Rank": df["rank"].map(lambda r: medals.get(r, str(r))),
        "Model": df["model"].map(_short),
        "Bugs": df["bugs_found"],
        "Budget reach": df.apply(
            lambda r: _reach_bar(r["budget_reach"], total, int(r["rules_tested"])), axis=1),
        "Spent ($)": df["total_cost_usd"].map(lambda c: f"${c:.2f}"),
        "Tokens (K)": df["total_tokens_k"].round(0).astype(int),
        "Bugs/Ktok": df["efficiency_bugs_per_ktok"].map(lambda x: f"{x:.4f}"),
    })
    banner = ("⚠️ Showing placeholder data — real $20 runs pending."
              if lb.has_placeholder(results) else "")
    return display, banner, df


def _scatter_df(frame: pd.DataFrame) -> pd.DataFrame:
    """Points for the bugs-vs-tokens efficiency scatter."""
    return pd.DataFrame({
        "Tokens (K)": frame["total_tokens_k"],
        "Bugs": frame["bugs_found"],
        "Model": frame["model"].map(_short),
    })


def _reshape_tasks(df: pd.DataFrame) -> pd.DataFrame:
    """Reshape raw task df into the tidied display columns for the Tasks tab."""
    if df.empty:
        return pd.DataFrame(columns=["Rule", "Source → Target", "Overhead (vars)"])
    return pd.DataFrame({
        "Rule": df["rule"].values,
        "Source → Target": (df["source"] + " → " + df["target"]).values,
        "Overhead (vars)": df["overhead_num_vars"].values,
    })


def _cert_markdown(model_name: str, certs: list[dict]) -> str:
    """Render bug certificates as markdown for the detail panel."""
    if not certs:
        return f"No confirmed bugs for **{model_name}** within the budget."
    lines = [f"### Confirmed bugs for **{model_name}**\n"]
    for c in certs:
        rule = c.get("rule", "?")
        violation = c.get("violation", "?")
        note = c.get("note", "")
        source_type = c.get("source_type", "?")
        target_type = c.get("target_type", "?")
        traj = c.get("trajectory_file")
        entry = (f"- **Rule:** `{rule}` | **Violation:** `{violation}` | "
                 f"`{source_type} → {target_type}`")
        if note:
            entry += f"  \n  Note: {note}"
        if traj:
            entry += f"  \n  [Trajectory]({traj})"
        lines.append(entry)
    return "\n".join(lines)


def _tasks_state():
    try:
        return lb.load_tasks(), ""
    except Exception as e:  # network/token failure — Tasks tab degrades gracefully
        return pd.DataFrame(columns=["rule", "source", "target", "summary"]), \
            f"⚠️ Could not load the task dataset: {e}"


def build_ui() -> gr.Blocks:
    tasks_df, tasks_err = _tasks_state()
    # tasks_err always comes with an empty df, so .empty covers both failure and zero-rows.
    total = lb.TOTAL_TASKS_DEFAULT if tasks_df.empty else len(tasks_df)

    results = lb.load_results(_RESULTS_PATH)
    table, banner, frame = _leaderboard_view(results, total)
    scatter = _scatter_df(frame)
    cert_map = {_short(r["model"]): r.get("bug_certificates", [])
                for r in lb.ranked_rows(results)}

    with gr.Blocks(theme=THEME, title="Problem-Reductions Bug-Finding Benchmark") as ui:
        gr.Markdown(f"# 🐛 Problem-Reductions Bug-Finding Benchmark\n"
                    f"### Same **${lb.RANKED_BUDGET}** — who finds the most bugs?\n"
                    f"pinned @ `{_PINNED_TAG}` · data updated {_DATA_UPDATED}")

        with gr.Tab("🏆 Leaderboard"):
            if banner:
                gr.Markdown(f"**{banner}**")
            Leaderboard(
                value=table,
                search_columns=["Model"],
                filter_columns=[ColumnFilter("Bugs", type="slider", label="Min bugs found")],
                select_columns=SelectColumns(
                    default_selection=list(table.columns),
                    cant_deselect=["Rank", "Model", "Bugs"],
                    label="Columns",
                ),
            )
            gr.ScatterPlot(
                value=scatter, x="Tokens (K)", y="Bugs", color="Model",
                title="Bugs vs. tokens — efficiency frontier (up-and-left is better)",
                tooltip=["Model", "Bugs", "Tokens (K)"], height=340,
            )
            with gr.Accordion("🔎 Inspect a model's confirmed bugs", open=False):
                model_pick = gr.Dropdown(
                    choices=list(cert_map), label="Model", value=None,
                )
                detail = gr.Markdown("*Pick a model to see its confirmed bugs.*")
                model_pick.change(
                    lambda m: _cert_markdown(m, cert_map.get(m, [])) if m else
                    "*Pick a model to see its confirmed bugs.*",
                    inputs=model_pick, outputs=detail,
                )

        with gr.Tab(f"📋 Tasks ({len(tasks_df)})"):
            if tasks_err:
                gr.Markdown(f"**{tasks_err}**")
            else:
                Leaderboard(
                    value=_reshape_tasks(tasks_df),
                    search_columns=["Rule", "Source → Target"],
                    select_columns=SelectColumns(
                        default_selection=["Rule", "Source → Target", "Overhead (vars)"],
                        cant_deselect=["Rule", "Source → Target"],
                        label="Columns",
                    ),
                )

        with gr.Tab("ℹ️ About"):
            gr.Markdown(
                f"**Metric.** Every model gets the same **${lb.RANKED_BUDGET}** API budget; "
                "the score is the number of distinct reduction rules with a confirmed bug "
                "(**one rule = one bug**). *Budget reach* shows how far the $20 got — "
                "how many of the tasks the run reached before the money ran out.\n\n"
                "**Zero trust.** Every claimed bug is independently re-derived with `pred` "
                f"(problem-reductions `{_PINNED_TAG}`); the model's claim is never trusted.\n\n"
                "**Submit a model.** Run the benchmark at the fixed **$20** budget against "
                f"problem-reductions `{_PINNED_TAG}`, then open a PR adding your `results.json` "
                "to the GitHub repo — every certificate is re-verified by `pred` before it "
                "counts.\n\n"
                "**Links.** "
                "[Task dataset](https://huggingface.co/datasets/isPANN/problem-reductions-benchmarks) · "
                "[GitHub](https://github.com/Ferrari-72/problem-reductions-benchmark)"
            )
            gr.Markdown("**How to cite**")
            gr.Code(value=_CITATION, language=None, label="BibTeX")

    return ui


if __name__ == "__main__":
    build_ui().launch()
