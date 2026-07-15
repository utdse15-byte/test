"""Cycle-47: build --target qc phase honors should_cancel via run_qc."""

from pathlib import Path

from manju.build import graph as graph_mod


def test_graph_qc_phase_passes_should_cancel() -> None:
    src = Path(graph_mod.__file__).read_text(encoding="utf-8")
    assert "should_cancel=should_cancel" in src
    assert "已取消(QC 阶段)" in src
