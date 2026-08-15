"""Wave 12 — behavioural performance pins for the mature product cockpit.

These tests deliberately assert *work avoided*, not wall-clock milliseconds:

* an empty export center must not compile the project timeline merely to report
  nine missing artifacts;
* once an artifact really needs current content keys, timeline compilation and
  key derivation happen once and are shared by final/proxy rows;
* one cockpit request must evaluate the creation funnel once and thread that
  read through suggestions, the hero action and the authoring journey.

The absolute 12/100/300-shot observations live in the report-only development
benchmark.  CI remains deterministic and machine-independent.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from manju.build.exportstatus import Freshness, deliverables
from manju.gui.cockpit import cockpit_data


def test_missing_deliverables_do_not_compile_timeline(monkeypatch, tmp_project):
    import manju.timeline.compiler as compiler

    calls = {"gather": 0, "compile": 0}

    def forbid_gather(*args, **kwargs):
        calls["gather"] += 1
        raise AssertionError("missing export rows must not gather a timeline")

    def forbid_compile(*args, **kwargs):
        calls["compile"] += 1
        raise AssertionError("missing export rows must not compile a timeline")

    monkeypatch.setattr(compiler, "gather_compile_input", forbid_gather)
    monkeypatch.setattr(compiler, "compile_timeline", forbid_compile)

    rows = deliverables(tmp_project)

    assert len(rows) == 9
    assert all(row.freshness is Freshness.MISSING for row in rows)
    assert calls == {"gather": 0, "compile": 0}


def test_final_and_proxy_share_one_lazy_timeline_and_key_pass(monkeypatch, tmp_project):
    import manju.media.render as render
    import manju.timeline.compiler as compiler

    final = tmp_project.final_dir / "final_v1.mp4"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"final")
    final.with_suffix(".key.json").write_text(
        json.dumps({"final_key": "key-final", "target": "final"}),
        encoding="utf-8",
    )

    proxy = tmp_project.proxy_dir / "proxy.mp4"
    proxy.parent.mkdir(parents=True, exist_ok=True)
    proxy.write_bytes(b"proxy")
    proxy.with_suffix(".key.json").write_text(
        json.dumps({"final_key": "key-proxy", "target": "proxy"}),
        encoding="utf-8",
    )

    calls = {"gather": 0, "compile": 0, "keys": []}
    timeline = SimpleNamespace(width=1080, height=1920)

    def gather(project, probe):
        calls["gather"] += 1
        return object()

    def compile_timeline(inputs):
        calls["compile"] += 1
        return timeline

    def content_key(project, current_timeline, *, ass_file, target):
        assert current_timeline is timeline
        calls["keys"].append(target)
        return f"key-{target}"

    monkeypatch.setattr(compiler, "gather_compile_input", gather)
    monkeypatch.setattr(compiler, "compile_timeline", compile_timeline)
    monkeypatch.setattr(render, "final_content_key", content_key)

    by_kind = {row.kind: row for row in deliverables(tmp_project)}

    assert by_kind["final"].freshness is Freshness.UP_TO_DATE
    assert by_kind["proxy"].freshness is Freshness.UP_TO_DATE
    assert calls == {"gather": 1, "compile": 1, "keys": ["final", "proxy"]}


def test_cockpit_threads_one_funnel_read_through_all_consumers(
    monkeypatch, tmp_project, add_shot
):
    import manju.build.funnel as funnel

    add_shot(tmp_project, "S001")
    real = funnel.funnel_status
    calls = 0

    def counted(project):
        nonlocal calls
        calls += 1
        return real(project)

    monkeypatch.setattr(funnel, "funnel_status", counted)

    payload = cockpit_data(tmp_project)

    assert calls == 1
    assert payload["journeys"]["authoring"]["label"] == "创作"
    assert payload["next_action"]["funnel"] is not None
    assert payload["focus"]["source"] in {
        "authoring", "shots", "engine", "finishing", "risk", "fallback"
    }


def test_cockpit_reuses_one_picture_staleness_pass(
    monkeypatch, tmp_project, add_shot
):
    import manju.build.stale as stale
    import manju.gui.cockpit as cockpit

    add_shot(tmp_project, "S001")
    real = stale.evaluate_all
    calls = 0

    def counted(project):
        nonlocal calls
        calls += 1
        return real(project)

    # cockpit imported the function into its own module, while suggest_next
    # imports from build.stale at module load.  Point both owners at one spy.
    monkeypatch.setattr(stale, "evaluate_all", counted)
    monkeypatch.setattr(cockpit, "evaluate_all", counted)

    payload = cockpit.cockpit_data(tmp_project)

    assert calls == 1
    assert payload["state"]["shots_total"] == 1
    assert payload["suggestions"]["items"] is not None
