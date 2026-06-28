"""Gradio leaderboard — fixed-$20 bug-finding race. Thin view over leaderboard.py."""
import datetime
import os

import json

import gradio as gr
import pandas as pd
from gradio_leaderboard import Leaderboard, SelectColumns

import leaderboard as lb
import submit as sub

_RESULTS_PATH = os.path.join(os.path.dirname(__file__), "data", "results.json")
_PINNED_TAG = "v0.6.0"

# Submissions dataset + write token come from Space secrets. Without a token the Submit
# tab degrades to the manual hf-upload / PR fallback.
_SUBMISSIONS_DATASET = os.environ.get("SUBMISSIONS_DATASET", "isPANN/problem-reductions-submissions")
_HF_WRITE_TOKEN = os.environ.get("HF_TOKEN")
_DATA_UPDATED = datetime.date.fromtimestamp(os.path.getmtime(_RESULTS_PATH)).isoformat()

# TODO: replace placeholder citation once the benchmark has a canonical reference.
_CITATION = """@misc{TODO_citation_key,
  title  = {TODO},
  author = {TODO},
  year   = {TODO},
  url    = {TODO}
}"""

# End-to-end pipeline as a hand-built SVG flowchart. SVG renders identically in every theme
# (each box carries its own light fill + dark text, so it reads on light or dark pages) and
# needs no external JS/CDN. Generated in Python so layout/positions stay maintainable.
def _build_pipeline_svg() -> str:
    W, BX, BW = 760, 70, 620
    ARROW = 30

    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def wrap(s, n=72):
        out, cur = [], ""
        for w in s.split():
            if len(cur) + len(w) + 1 <= n:
                cur = (cur + " " + w).strip()
            else:
                out.append(cur); cur = w
        if cur:
            out.append(cur)
        return out

    svg, y = [], [0]  # y in a list so closures can mutate it

    def lane(title, grad):
        h = 38
        svg.append(f'<rect x="{BX}" y="{y[0]}" width="{BW}" height="{h}" rx="10" fill="url(#{grad})"/>')
        svg.append(f'<text x="{W/2:.0f}" y="{y[0]+h/2+5:.0f}" text-anchor="middle" '
                   f'font-size="14" font-weight="700" fill="#fff" letter-spacing="0.4">{esc(title)}</text>')
        y[0] += h

    def arrow():
        x = W / 2
        svg.append(f'<line x1="{x:.0f}" y1="{y[0]+4}" x2="{x:.0f}" y2="{y[0]+ARROW-5}" '
                   f'stroke="#94a3b8" stroke-width="2" marker-end="url(#ah)"/>')
        y[0] += ARROW

    def step(num, title, sub, chip, fill, accent):
        subl = wrap(sub, 70)
        top, title_h, sub_h, chip_h, bot = 14, 22, 18, (26 if chip else 0), 12
        h = top + title_h + sub_h * len(subl) + chip_h + bot
        svg.append(f'<rect x="{BX}" y="{y[0]}" width="{BW}" height="{h}" rx="13" '
                   f'fill="{fill}" stroke="{accent}" stroke-width="1.3"/>')
        cx, cy = BX + 28, y[0] + top + 12
        svg.append(f'<circle cx="{cx}" cy="{cy:.0f}" r="14" fill="{accent}"/>')
        svg.append(f'<text x="{cx}" y="{cy+5:.0f}" text-anchor="middle" font-size="13" '
                   f'font-weight="700" fill="#fff">{num}</text>')
        tx = BX + 54
        svg.append(f'<text x="{tx}" y="{y[0]+top+16}" font-size="15" font-weight="700" '
                   f'fill="#0f172a">{esc(title)}</text>')
        sy = y[0] + top + title_h + 13
        for ln in subl:
            svg.append(f'<text x="{tx}" y="{sy}" font-size="12.5" fill="#475569">{esc(ln)}</text>')
            sy += sub_h
        if chip:
            cy2 = y[0] + top + title_h + sub_h * len(subl) + 2
            svg.append(f'<rect x="{tx}" y="{cy2}" width="{BW-(tx-BX)-20}" height="20" rx="10" '
                       f'fill="{accent}" opacity="0.14"/>')
            svg.append(f'<text x="{tx+11}" y="{cy2+14}" font-size="12" font-weight="600" '
                       f'fill="{accent}">{esc(chip)}</text>')
        y[0] += h

    def boundary():
        y[0] += 8
        svg.append(f'<line x1="{BX}" y1="{y[0]+15}" x2="{BX+BW}" y2="{y[0]+15}" '
                   f'stroke="#ef4444" stroke-width="2" stroke-dasharray="7 5"/>')
        pw = 430; px = (W - pw) / 2
        svg.append(f'<rect x="{px:.0f}" y="{y[0]+3}" width="{pw}" height="26" rx="13" '
                   f'fill="#fff" stroke="#ef4444" stroke-width="1.5"/>')
        svg.append(f'<text x="{W/2:.0f}" y="{y[0]+20}" text-anchor="middle" font-size="12" '
                   f'font-weight="700" fill="#ef4444">🔒 TRUST BOUNDARY · self-reported counts ignored</text>')
        y[0] += 40

    I, V, S, G = "#eef2ff", "#f5f3ff", "#ecfeff", "#ecfdf5"
    IA, VA, SA, GA = "#6366f1", "#8b5cf6", "#0ea5e9", "#10b981"

    lane("YOU RUN IT — your model · your key · $20 budget", "gi")
    arrow()
    step("1", "🐳  Get the runner",
         "One Docker image with the solver and the rule library, pinned to the benchmark version. "
         "No setup beyond Docker.",
         "", I, IA)
    arrow()
    step("2", "🔌  Plug in any model",
         "OpenAI, Anthropic, DeepSeek, a self-hosted endpoint — you bring the API key and tell it "
         "your price per token.",
         "", V, VA)
    arrow()
    step("3", "🐛  Hunt for bugs",
         "Your model searches each of the ~262 reduction rules for a counterexample — an input the "
         "rule mishandles — until the $20 runs out. Each find is checked by the solver on the spot.",
         "a bug = solve(input) ≠ solve(reduce(input))", I, IA)
    arrow()
    step("4", "📤  Submit your results",
         "Upload the results file on the Submit tab — that's it.",
         "", V, VA)
    boundary()
    lane("WE SCORE IT — independent, zero-trust", "ge")
    arrow()
    step("5", "🔁  Re-verified from scratch",
         "We re-derive every claimed bug with our own solver and keep only the ones that truly "
         "reproduce. Your self-reported numbers never count.",
         "only reproducible bugs are scored", G, GA)
    arrow()
    h = 46
    svg.append(f'<rect x="{BX+70}" y="{y[0]}" width="{BW-140}" height="{h}" rx="23" fill="url(#gold)"/>')
    svg.append(f'<text x="{W/2:.0f}" y="{y[0]+h/2+5:.0f}" text-anchor="middle" font-size="14.5" '
               f'font-weight="700" fill="#fff">🏆 Leaderboard · most distinct rules bugged · efficiency = bugs / 1K tokens</text>')
    y[0] += h

    defs = (
        '<defs>'
        '<linearGradient id="gi" x1="0" x2="1"><stop offset="0" stop-color="#6366f1"/>'
        '<stop offset="1" stop-color="#8b5cf6"/></linearGradient>'
        '<linearGradient id="ge" x1="0" x2="1"><stop offset="0" stop-color="#10b981"/>'
        '<stop offset="1" stop-color="#0ea5e9"/></linearGradient>'
        '<linearGradient id="gold" x1="0" x2="1"><stop offset="0" stop-color="#f59e0b"/>'
        '<stop offset="1" stop-color="#f97316"/></linearGradient>'
        '<marker id="ah" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">'
        '<path d="M0,0 L7,3 L0,6 Z" fill="#94a3b8"/></marker>'
        '</defs>')
    return (f'<div style="max-width:780px;margin:0 auto;">'
            f'<svg viewBox="0 0 {W} {y[0]+4}" width="100%" '
            f'font-family="system-ui,-apple-system,Segoe UI,Roboto,sans-serif" '
            f'role="img" aria-label="Benchmark pipeline">{defs}{"".join(svg)}</svg></div>')


_PIPELINE_HTML = _build_pipeline_svg()

THEME = gr.themes.Soft(primary_hue="indigo", secondary_hue="purple")

# gradio_leaderboard renders its search + column controls as big bordered boxes that
# only fill the left half and shove the table below the fold. Flatten them into a thin,
# full-width strip so the table + chart sit near the top.
_CSS = """
#lb-board { gap: 6px !important; }
#lb-board .block, #lb-board .form {
  border: none !important; box-shadow: none !important;
  background: transparent !important; padding: 2px 0 !important;
  max-width: 100% !important;
}
#lb-board .wrap { max-width: 100% !important; }
#lb-board textarea, #lb-board input[type="text"] { min-height: 36px !important; }
#lb-board label span { font-size: 12px !important; margin-bottom: 2px !important; }
"""


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


_MANUAL_FALLBACK = (
    "**Manual submission (no auto-queue configured).** Upload your `submission.json` to "
    f"the submissions dataset and the backend will pick it up:\n\n"
    "```bash\n"
    f"hf upload {_SUBMISSIONS_DATASET} submission.json \\\n"
    "  submissions/<your-handle>/<model>.json --repo-type dataset\n"
    "```\n"
    "…or open a PR adding it to the GitHub repo. Every certificate is re-verified by "
    "`pred` before it counts."
)


def _load_upload(file_path):
    """Read an uploaded submission file. Returns (data | None, error_markdown)."""
    if not file_path:
        return None, "⚠️ Upload a `submission.json` first."
    try:
        with open(file_path, encoding="utf-8") as f:
            return json.load(f), ""
    except (OSError, json.JSONDecodeError) as e:
        return None, f"❌ Could not read the file as JSON: {e}"


def _summary_md(summary: dict) -> str:
    return (
        f"- **Model:** `{summary.get('model')}`\n"
        f"- **Budget cap:** ${summary.get('budget_cap')}\n"
        f"- **Rules attempted:** {summary.get('rules_tested')}\n"
        f"- **Spend:** ${summary.get('total_cost_usd')}\n"
        f"- **Claimed distinct-rule bugs:** {summary.get('claimed_distinct_bugs')} "
        f"(from {summary.get('certificate_rows')} certificate row(s))\n\n"
        "<sub>These are the model's *claims*. The backend re-verifies every certificate "
        "with `pred`; only confirmed, distinct-rule bugs are ranked.</sub>"
    )


def _validate_view(file_path) -> str:
    data, err = _load_upload(file_path)
    if err:
        return err
    ok, errors, summary = sub.validate_submission(data)
    if not ok:
        return "❌ **Not ready to submit:**\n" + "\n".join(f"- {e}" for e in errors)
    return "✅ **Looks valid.**\n\n" + _summary_md(summary)


def _submit_view(file_path, contact) -> str:
    data, err = _load_upload(file_path)
    if err:
        return err
    ok, errors, summary = sub.validate_submission(data)
    if not ok:
        return "❌ **Cannot submit — fix these first:**\n" + "\n".join(f"- {e}" for e in errors)
    try:
        path = sub.push_submission(data, _SUBMISSIONS_DATASET, _HF_WRITE_TOKEN,
                                   submitted_by=(contact or None))
    except RuntimeError as e:
        return f"ℹ️ {e}\n\n{_MANUAL_FALLBACK}"
    except Exception as e:  # network/permission — show the fallback so the user isn't stuck
        return f"❌ Upload failed: {e}\n\n{_MANUAL_FALLBACK}"
    return (f"✅ **Submitted as PENDING** → `{_SUBMISSIONS_DATASET}/{path}`.\n\n"
            "The backend will re-verify every certificate with `pred` and post the "
            "confirmed score to the leaderboard.")


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

    with gr.Blocks(theme=THEME, css=_CSS, title="Problem-Reductions Bug-Finding Benchmark") as ui:
        gr.Markdown(
            "# 🐛 Problem-Reductions Bug-Finding Benchmark\n"
            f"### Same **${lb.RANKED_BUDGET}** for every model — who finds the most bugs?\n"
            f"<sub>tasks pinned @ `{_PINNED_TAG}` · one rule = one bug, every bug re-verified by "
            f"`pred` · data updated {_DATA_UPDATED}</sub>"
        )

        with gr.Tab("🏆 Leaderboard"):
            if banner:
                gr.Markdown(f"**{banner}**")
            Leaderboard(
                value=table,
                elem_id="lb-board",
                search_columns=["Model"],
                select_columns=SelectColumns(
                    default_selection=list(table.columns),
                    cant_deselect=["Rank", "Model", "Bugs"],
                    label="Show columns",
                ),
            )
            gr.Markdown(
                "<sub>**Budget reach** = how far the $20 got (rules reached / total). "
                "**Bugs/Ktok** = bugs per 1000 tokens.</sub>"
            )
            gr.ScatterPlot(
                value=scatter, x="Tokens (K)", y="Bugs", color="Model",
                title="Bugs vs. tokens (up-and-left is better)",
                tooltip=["Model", "Bugs", "Tokens (K)"], height=340,
            )
            gr.Markdown("#### 🔎 Inspect a model's bugs")
            model_pick = gr.Dropdown(choices=list(cert_map), label="Model", value=None)
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

        with gr.Tab("🔄 How it works"):
            gr.Markdown(
                "### From your model to the leaderboard\n"
                "Give a model the same **$20** and see how many bugs it can find in the library's "
                "problem reductions. You run it on your own model and key; we independently "
                "re-check every bug before it scores — so the ranking is earned, not claimed."
            )
            gr.HTML(_PIPELINE_HTML)
            gr.Markdown(
                "**What counts as a bug.** Each reduction is supposed to turn problem *A* into an "
                "equivalent problem *B*. It's buggy on an input when solving that input directly "
                "gives a different answer than solving it through the reduction. That's a fact "
                "anyone can re-check — so there's no hidden answer key, and a wrong claim simply "
                "fails to reproduce.\n\n"
                "**Why the $20 is fair.** Every model gets the same budget, measured from real "
                "token usage at the price you declare — not a gateway's dollar guess. The "
                "leaderboard ranks by **confirmed distinct-rule bugs**, with **bugs per 1K "
                "tokens** as the efficiency tie-break."
            )

        with gr.Tab("🚀 Submit"):
            gr.Markdown(
                "### Submit a run\n"
                f"Run the dockerized runner at the fixed **${lb.RANKED_BUDGET}** budget "
                f"against problem-reductions `{_PINNED_TAG}` (any provider — config goes in "
                "one `submission.env`), then upload the `submission.json` it produces. See the "
                "**🔄 How it works** tab for the full pipeline.\n\n"
                "```bash\n"
                "cp submission.env.example submission.env   # set MODEL_NAME, API_KEY, PRICE_IN/OUT\n"
                "make preflight                             # validate config (1 tiny real call)\n"
                "make run                                   # → ./out/submission.json\n"
                "```\n"
                "Self-reported counts are advisory — **every certificate is re-verified "
                "by `pred`** on the backend before it counts."
            )
            up = gr.File(label="submission.json", file_types=[".json"], type="filepath")
            contact = gr.Textbox(label="Contact / handle (optional)",
                                 placeholder="HF username or email — kept for attribution")
            with gr.Row():
                validate_btn = gr.Button("Validate", variant="secondary")
                submit_btn = gr.Button("Submit", variant="primary")
            submit_out = gr.Markdown()
            validate_btn.click(_validate_view, inputs=up, outputs=submit_out)
            submit_btn.click(_submit_view, inputs=[up, contact], outputs=submit_out)

        with gr.Tab("ℹ️ About"):
            gr.Markdown(
                f"**Metric.** Every model gets the same **${lb.RANKED_BUDGET}** API budget; "
                "the score is the number of distinct reduction rules with a confirmed bug "
                "(**one rule = one bug**). *Budget reach* shows how far the $20 got — "
                "how many of the tasks the run reached before the money ran out.\n\n"
                "**Zero trust.** Every claimed bug is independently re-derived with `pred` "
                f"(problem-reductions `{_PINNED_TAG}`); the model's claim is never trusted.\n\n"
                "**Submit a model.** Run the dockerized runner at the fixed **$20** budget "
                f"against problem-reductions `{_PINNED_TAG}`, then upload the `submission.json` "
                "on the **🚀 Submit** tab. The backend re-verifies every certificate with "
                "`pred` and posts the confirmed, distinct-rule score to the leaderboard — "
                "your self-reported count is never trusted.\n\n"
                "**Links.** "
                "[Task dataset](https://huggingface.co/datasets/isPANN/problem-reductions-benchmarks) · "
                "[GitHub](https://github.com/Ferrari-72/problem-reductions-benchmark)"
            )
            gr.Markdown("**How to cite**")
            gr.Code(value=_CITATION, language=None, label="BibTeX")

    return ui


if __name__ == "__main__":
    # NB: HF Spaces already enables SSR by default, so it isn't the lever for the first-paint
    # flash — that's a Gradio frontend CSS/hydration timing issue (see gradio #5825), not
    # fixable from here. A static leaderboard site is the robust fix if the FOUC matters.
    build_ui().launch()
