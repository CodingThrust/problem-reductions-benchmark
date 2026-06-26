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


def _leaderboard_view(total: int):
    results = lb.load_results(_RESULTS_PATH)
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
        **{"Bugs/Ktok": df["efficiency_bugs_per_ktok"]},
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


def build_ui() -> gr.Blocks:
    tasks_df, tasks_err = _tasks_state()
    total = len(tasks_df) if not tasks_err else lb.TOTAL_TASKS_DEFAULT
    table, banner = _leaderboard_view(total)

    with gr.Blocks(theme=THEME, title="Problem-Reductions Bug-Finding Benchmark") as ui:
        gr.Markdown(f"# 🐛 Problem-Reductions Bug-Finding Benchmark\n"
                    f"### Same **${lb.RANKED_BUDGET}** — who finds the most bugs? "
                    f"· pinned @ `{_PINNED_TAG}`")

        with gr.Tab("🏆 Leaderboard"):
            if banner:
                gr.Markdown(f"**{banner}**")
            gr.Dataframe(value=table, interactive=False, wrap=True)

        with gr.Tab(f"📋 Tasks ({len(tasks_df)})"):
            if tasks_err:
                gr.Markdown(f"**{tasks_err}**")
            else:
                query = gr.Textbox(label="Search tasks", placeholder="e.g. ILP, MaxCut, clique")
                tasks_view = gr.Dataframe(value=tasks_df, interactive=False, wrap=True)
                query.change(lambda q: lb.filter_tasks(tasks_df, query=q or None),
                             inputs=query, outputs=tasks_view)

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
