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

# End-to-end pipeline as styled HTML cards (Gradio Markdown can't render mermaid; gr.HTML
# renders inline CSS reliably and themes via inherited colors — no external JS/CDN).
def _build_pipeline_html() -> str:
    I, V, S, G, A = "#6366f1", "#8b5cf6", "#0ea5e9", "#10b981", "#f59e0b"

    def card(n, title, sub, color):
        return (
            '<div style="display:flex;gap:12px;align-items:flex-start;'
            'background:rgba(127,127,127,.08);border:1px solid rgba(127,127,127,.18);'
            f'border-left:4px solid {color};border-radius:10px;padding:11px 13px;">'
            f'<div style="flex:0 0 26px;height:26px;border-radius:50%;background:{color};'
            'color:#fff;font-weight:700;font-size:13px;display:flex;align-items:center;'
            f'justify-content:center;">{n}</div>'
            f'<div style="line-height:1.45;"><b>{title}</b><br>'
            f'<span style="opacity:.72;font-size:.88em;">{sub}</span></div></div>')

    arrow = ('<div style="text-align:center;opacity:.35;font-size:17px;'
             'line-height:1;margin:3px 0;">&#8595;</div>')

    def label(txt):
        return ('<div style="text-transform:uppercase;letter-spacing:.06em;font-size:.72em;'
                f'font-weight:700;opacity:.6;margin:16px 2px 8px;">{txt}</div>')

    submitter = [
        ("1", "Build the runner image",
         "make runner-build PR_REF=v0.6.0 — compiles pred &amp; bundles the library at that version", I),
        ("2", "Configure (any provider)",
         "submission.env: MODEL_NAME · API_KEY · PRICE_IN / PRICE_OUT", V),
        ("3", "Preflight",
         "make preflight — one tiny real call checks pred · rules · key / endpoint", S),
        ("4", "Run the bug hunt",
         "make run — agent (LiteLLM → your model) probes each rule: solve(source) ≠ solve(reduced) "
         "⇒ certificate, re-checked locally by pred. Spend = tokens × your price, capped at $20.", I),
        ("5", "Upload",
         'Space "Submit" tab / hf upload  →  queued as PENDING', V),
    ]

    parts = ['<div style="max-width:720px;margin:0 auto;font-family:inherit;">']
    parts.append(label("Submitter side · your machine, your key, your money"))
    for i, (n, t, s, c) in enumerate(submitter):
        parts.append(card(n, t, s, c))
        if i < len(submitter) - 1:
            parts.append(arrow)
    parts.append(
        '<div style="text-align:center;font-size:.78em;font-weight:700;color:#ef4444;'
        'border-top:2px dashed #ef4444;border-bottom:2px dashed #ef4444;'
        'padding:7px 0;margin:16px 0;letter-spacing:.03em;">'
        '⛔ TRUST BOUNDARY — self-reported counts are ignored</div>')
    parts.append(label("Backend side · zero-trust scorer with its own pred"))
    parts.append(card(
        "6", "Zero-trust re-verify",
        "backend_score re-derives each bug from {rule, source} with pred and keeps only what "
        "reproduces. Score = distinct rules with a confirmed bug (deduplicated).", G))
    parts.append(arrow)
    parts.append(
        f'<div style="text-align:center;background:rgba(245,158,11,.12);border:1px solid {A};'
        'border-radius:10px;padding:12px;font-weight:700;">'
        '🏆 Leaderboard — ranked by bugs / Ktok</div>')
    parts.append("</div>")
    return "".join(parts)


_PIPELINE_HTML = _build_pipeline_html()

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
                "You run the benchmark locally under a fixed **$20** budget; the backend "
                "re-verifies every claimed bug with `pred` before it scores. Self-reported "
                "counts are never trusted — the line below the **trust boundary** is all that "
                "decides your rank."
            )
            gr.HTML(_PIPELINE_HTML)
            gr.Markdown(
                "**Why a bug is a bug.** A reduction A→B is buggy on an instance when solving "
                "the source directly disagrees with solving it *through* the reduction "
                "(`pred solve <source>` vs `pred solve <reduced>`), compared by value / "
                "feasibility. That's deterministic, so the backend can re-check it from just "
                "`{rule, source}` — no hidden answer key needed.\n\n"
                "**Why $20 is a real cap.** Spend is recomputed as `tokens × your declared "
                "price` (not the gateway's self-reported dollars), capped per-rule and in "
                "total with a safety margin. Ranking uses **bugs / Ktok** (token counts are "
                "auditable); self-reported dollars are advisory."
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
    build_ui().launch()
