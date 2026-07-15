"""Functional C53: next_step_key build_locale when lines without finals."""
from __future__ import annotations

from types import SimpleNamespace

from manju.build.graph import ShotState
from manju.build.status import project_status
from manju.core.locale import locale_dir
from manju.core.models import Timeline, TimelineMeta
from manju.core.yamlio import write_yaml


def test_next_step_build_locale_when_lines_no_final(tmp_project, monkeypatch):
    final = tmp_project.final_dir / "final_v1.mp4"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"\x00\x00fake")
    tl = Timeline(meta=TimelineMeta(mode="compiled"), duration_ms=1000, tracks={})
    tl_path = tmp_project.root / "timeline" / "timeline.json"
    tl_path.parent.mkdir(parents=True, exist_ok=True)
    tl_path.write_text(tl.model_dump_json(), encoding="utf-8")
    d = locale_dir(tmp_project, "en")
    d.mkdir(parents=True, exist_ok=True)
    write_yaml(d / "lines.yaml", {"S001": {"text": "hello"}})

    fake = [SimpleNamespace(
        shot_id="S001",
        state=ShotState.FRESH,
        note=None,
        selected_take="take_01",
        take=None,
    )]
    monkeypatch.setattr(
        "manju.build.status.next_actions",
        lambda *a, **k: [],
    )
    st = project_status(tmp_project, statuses=fake)
    assert st.get("latest_final")
    assert st.get("timeline", {}).get("exists") is True
    assert st["next_step_key"] == "build_locale", st
    assert "en" in st["next_step"]
