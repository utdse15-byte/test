"""Cycle-19: graph skips locale voice when budget already exceeded."""

from pathlib import Path


def test_graph_source_skips_locale_voice_on_budget() -> None:
    src = Path("src/manju/build/graph.py").read_text(encoding="utf-8")
    assert "跳过 locale 配音" in src
    assert "voice_plan = []" in src
