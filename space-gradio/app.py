"""Gradio leaderboard — fixed-$20 bug-finding race. Thin view over leaderboard.py."""
import os

import gradio as gr
import pandas as pd

import leaderboard as lb

_RESULTS_PATH = os.path.join(os.path.dirname(__file__), "data", "results.json")
_PINNED_TAG = "v0.6.0"

THEME = gr.themes.Soft(primary_hue="indigo", secondary_hue="purple")


def _reach_bar(frac: float, total: int, tested: int) -> str:
    filled = round(frac * 10)
    return f"{'▓' * filled}{'░' * (10 - filled)} {tested}/{total}"


def _leaderboard_view(results: list[dict], total: int):
    df = lb.leaderboard_frame(results, total)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    display = df.assign(
        Rank=df["rank"].map(lambda r: medals.get(r, str(r))),
        Model=df["model"].str.replace(r"^.*/", "", regex=True),
        Bugs=df["bugs_found"],
        **{"Budget reach": df.apply(
            lambda r: _reach_bar(r["budget_reach"], total, int(r["rules_tested"])), axis=1)},
        **{"Spent ($)": df["total_cost_usd"].map(lambda c: f"${c:.2f}")},
        **{"Tokens (K)": df["total_tokens_k"].round(0).astype(int)},
        **{"Bugs/Ktok": df["efficiency_bugs_per_ktok"].map(lambda x: f"{x:.4f}")},
    )[["Rank", "Model", "Bugs", "Budget reach", "Spent ($)", "Tokens (K)", "Bugs/Ktok"]]
    banner = ("⚠️ Showing placeholder data — real $20 runs pending."
              if lb.has_placeholder(results) else "")
    return display, banner


def _tasks_state():
    try:
        return lb.load_tasks(), ""
    except Exception as e:  # network/token failure — Tasks tab degrades gracefully
        return pd.DataFrame(columns=["rule", "source", "target", "summary"]), \
            f"⚠️ Could not load the task dataset: {e}"


def _reshape_tasks(df: pd.DataFrame) -> pd.DataFrame:
    """Reshape raw task df into the tidied display columns for the Tasks tab."""
    if df.empty:
        return pd.DataFrame(columns=["Rule", "Source → Target", "Summary", "Overhead (vars)"])
    overhead = df["overhead_num_vars"] if "overhead_num_vars" in df.columns else pd.Series(
        [None] * len(df), index=df.index)
    return pd.DataFrame({
        "Rule": df["rule"].values,
        "Source → Target": (df["source"] + " → " + df["target"]).values,
        "Summary": df["summary"].values,
        "Overhead (vars)": overhead.values,
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


def build_ui() -> gr.Blocks:
    tasks_df, tasks_err = _tasks_state()
    total = (lb.TOTAL_TASKS_DEFAULT
             if (tasks_err or len(tasks_df) == 0)
             else len(tasks_df))

    results = lb.load_results(_RESULTS_PATH)
    _ranked = lb.ranked_rows(results)
    table, banner = _leaderboard_view(results, total)

    with gr.Blocks(theme=THEME, title="Problem-Reductions Bug-Finding Benchmark") as ui:
        gr.Markdown(f"# 🐛 Problem-Reductions Bug-Finding Benchmark\n"
                    f"### Same **${lb.RANKED_BUDGET}** — who finds the most bugs? "
                    f"· pinned @ `{_PINNED_TAG}`")

        with gr.Tab("🏆 Leaderboard"):
            if banner:
                gr.Markdown(f"**{banner}**")
            lb_table = gr.Dataframe(value=table, interactive=False, wrap=True)
            detail_panel = gr.Markdown(
                "*Select a row to see that model's confirmed bugs.*"
            )

            def _on_select(evt: gr.SelectData):
                idx = evt.index[0]
                if idx < 0 or idx >= len(_ranked):
                    return "*Row index out of range.*"
                r = _ranked[idx]
                return _cert_markdown(r.get("model", "?"), r.get("bug_certificates", []))

            lb_table.select(_on_select, inputs=None, outputs=detail_panel)

        with gr.Tab(f"📋 Tasks ({len(tasks_df)})"):
            if tasks_err:
                gr.Markdown(f"**{tasks_err}**")
            else:
                query = gr.Textbox(label="Search tasks", placeholder="e.g. ILP, MaxCut, clique")
                tasks_view = gr.Dataframe(
                    value=_reshape_tasks(tasks_df), interactive=False, wrap=True
                )
                query.change(
                    lambda q: _reshape_tasks(lb.filter_tasks(tasks_df, query=q or None)),
                    inputs=query,
                    outputs=tasks_view,
                )

        with gr.Tab("ℹ️ About"):
            gr.Markdown(
                f"**Metric.** Every model gets the same **${lb.RANKED_BUDGET}** API budget; "
                "the score is the number of distinct reduction rules with a confirmed bug "
                "(**one rule = one bug**). *Budget reach* shows how far the $20 got — "
                "how many of the tasks the run reached before the money ran out.\n\n"
                "**Zero trust.** Every claimed bug is independently re-derived with `pred` "
                f"(problem-reductions `{_PINNED_TAG}`); the model's claim is never trusted.\n\n"
                "**Links.** "
                "[Task dataset](https://huggingface.co/datasets/isPANN/problem-reductions-benchmarks) · "
                "[GitHub](https://github.com/Ferrari-72/problem-reductions-benchmark)"
            )
    return ui


if __name__ == "__main__":
    build_ui().launch()
