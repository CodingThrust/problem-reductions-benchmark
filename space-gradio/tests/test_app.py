import os
import gradio as gr

os.environ["TASKS_FILE"] = os.path.join(os.path.dirname(__file__), "fixtures", "tasks_sample.jsonl")

import app


def test_build_ui_returns_blocks():
    ui = app.build_ui()
    assert isinstance(ui, gr.Blocks)
