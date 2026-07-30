"""TRISURFACE round 3 — the deferred findings that turned out tractable
(F-09/10/13/15/18/19/22, R2-4), red-first against the recorded behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manju.core.models import TakeSidecar
from manju.core.spec import SPEC_VERSION, compute_spec_hash


# --------------------------------------------- F-13 history repr wall


def test_history_summarizes_dict_details_instead_of_repring_them(tmp_project):
    """`manju history` printed relink_apply's detail as a 700-column Python
    repr wall (`summary={'restored': 1, …}, rows=[{'id': …}]`) — the exact
    noise class `manju events` already solved. One summarizer, both feeds."""
    from manju.core.events import append_event
    from manju.core.history import history

    append_event(tmp_project.root, "human", "relink_apply", {
        "summary": {"restored": 1, "restored_unverified": 0, "refused": 1,
                    "skipped": 0},
        "rows": [{"id": "take:S003/take_01", "target": "media/gen/S003/x.mp4",
                  "status": "restored", "reason": None}],
        "root": "/backup", "plan": "plan.json", "extra": 1,
    })
    row = next(r for r in history(tmp_project, n=5) if "relink_apply" in r["text"])
    assert "{'" not in row["text"], row["text"]
    assert "None" not in row["text"]
    # elision is said, not silent — and the full record stays in detail/--json.
    assert "→ --json" in row["text"]
    assert row["detail"]["summary"]["restored"] == 1


def test_events_and_history_share_the_one_brief(tmp_project):
    from manju.core.events import append_event, event_detail_brief
    from manju.core.history import history

    detail = {"a": {"x": 1}, "b": "short"}
    append_event(tmp_project.root, "ai", "probe_action", detail)
    row = next(r for r in history(tmp_project, n=5) if "probe_action" in r["text"])
    assert event_detail_brief(detail) in row["text"]


# --------------------------------------- R2-4 support-bundle repr summary


def test_support_bundle_summary_is_not_a_python_dict(tmp_project, monkeypatch,
                                                     tmp_path):
    from typer.testing import CliRunner

    from manju.cli import app

    monkeypatch.chdir(tmp_project.root)
    out = tmp_path / "s.zip"
    res = CliRunner().invoke(app, ["support-bundle", "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert "{'" not in res.output
    assert "events_included=" in res.output
    assert "self-scan" in res.output and "True" not in res.output


# ------------------------------------- F-15 ingest names its consequences


def _fresh_take(project, shot_id):
    current = compute_spec_hash(project.load_shot(shot_id), project.load_bible(),
                                version=SPEC_VERSION, project_root=project.root)
    src = project.root / "_t.mp4"
    src.write_bytes(b"take-bytes-" + shot_id.encode())
    take = project.register_take(
        shot_id, src, TakeSidecar(provider="test", spec_hash=current,
                                  spec_version=SPEC_VERSION))
    src.unlink()
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name))
    return take


def test_bible_ref_ingest_reports_the_shots_it_staled(tmp_project, add_shot,
                                                      tmp_path):
    """Registering one ref image edits the bible and silently staled every
    shot referencing that asset — ingest printed ✓ and nothing else; the
    owner discovered the re-generation bill later in status."""
    from manju.build.ingest import apply_ingest, plan_ingest

    add_shot(tmp_project, "S001")  # references linxia (conftest default)
    _fresh_take(tmp_project, "S001")

    ref = tmp_path / "linxia_ref.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\nfakepng")
    plan = plan_ingest(tmp_project, [ref])
    result = apply_ingest(tmp_project, plan, actor="human")

    staled = result.to_dict().get("staled_shots")
    assert staled == {"linxia": ["S001"]}, result.to_dict()


def test_plain_import_ingest_stales_nothing(tmp_project, add_shot, tmp_path):
    from manju.build.ingest import apply_ingest, plan_ingest

    add_shot(tmp_project, "S001")
    src = tmp_path / "随手素材.mp4"
    src.write_bytes(b"not-a-ref")
    plan = plan_ingest(tmp_project, [src])
    result = apply_ingest(tmp_project, plan, actor="human")
    assert not result.to_dict().get("staled_shots")


# ----------------------------------------------- F-09 Edge TTS scaffold


def test_providers_add_edge_adapter_scaffolds_a_keyless_manifest(tmp_path,
                                                                 monkeypatch):
    """The one recommended free provider had NO scaffold path: the generic
    template's three next-steps were all wrong for it (fill submit.url,
    export EDGE_API_KEY, …). `--adapter edge` now writes the keyless manifest
    that passes the offline check as-is."""
    from typer.testing import CliRunner

    from manju.cli import app

    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "providers"))
    res = CliRunner().invoke(
        app, ["providers", "add", "edge", "--type", "tts", "--adapter", "edge"])
    assert res.exit_code == 0, res.output
    manifest = tmp_path / "providers" / "edge" / "provider.yaml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert "manju.providers.edge_tts:EdgeTtsProvider" in text
    assert "EDGE_API_KEY" not in res.output  # keyless: no export-the-key step
    assert "★" not in res.output  # …and no "fill the ★ fields" for a complete file
    check = CliRunner().invoke(app, ["providers", "check", "edge"])
    assert check.exit_code == 0, check.output


# ------------------------------------------------ F-22 walkable refusals


def test_segments_missing_file_message_has_no_errno(tmp_project, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    monkeypatch.chdir(tmp_project.root)
    res = CliRunner().invoke(app, ["segments", "reports/analysis/nope.json"])
    assert res.exit_code == 1
    assert "[Errno" not in res.output
    assert "不存在" in res.output or "not found" in res.output
    assert "manju analyze" in res.output  # the producing command


def test_analyze_refusal_names_the_two_real_paths(tmp_project, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    monkeypatch.chdir(tmp_project.root)
    (tmp_project.root / "clip.mp4").write_bytes(b"x")
    res = CliRunner().invoke(app, ["analyze", "clip.mp4"])
    assert res.exit_code == 1
    assert "contract §2" not in res.output
    assert "--fixture" in res.output and "--provider" in res.output


# --------------------------------------- F-10 readonly gates the buttons


def test_exports_page_renders_buttons_disabled_in_readonly(tmp_project):
    """--readonly correctly 403s every write, but the page still rendered 15
    inviting 生成/更新 buttons; the cockpit disables its controls, this page
    must too."""
    from manju.gui import exports_page

    rw = exports_page.render(tmp_project, "tok")
    ro = exports_page.render(tmp_project, "tok", readonly=True)
    assert 'data-act="gen"' in rw and "disabled" not in rw.split('data-act="gen"')[1][:80]
    for chunk in ro.split("<button")[1:]:
        head = chunk.split(">", 1)[0]
        if 'data-act="' in head:
            assert "disabled" in head, head
    assert "只读" in ro


# ----------------------------------------------- F-18 branchable MCP codes


def test_mcp_lock_violation_and_cas_have_their_own_codes(tmp_project, add_shot):
    from manju.core.locks import seal_lock
    from manju.mcp.tools import ToolError, call_tool

    add_shot(tmp_project, "S001")
    raw = tmp_project.load_shot_raw("S001")
    digest = seal_lock(raw, "dialogue.text")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("locked", {}).__setitem__("dialogue.text", digest))

    yaml_text = tmp_project.shot_path("S001").read_text(encoding="utf-8")
    evil = yaml_text.replace("这不可能。", "改掉了。")
    with pytest.raises(ToolError) as exc:
        call_tool(tmp_project, "update_shot",
                  {"shot_id": "S001", "yaml_content": evil})
    assert exc.value.code == "locked_field"

    with pytest.raises(ToolError) as exc:
        call_tool(tmp_project, "update_shot",
                  {"shot_id": "S001", "yaml_content": yaml_text,
                   "expected_rev": "sha256:deadbeef"})
    assert exc.value.code == "rev_conflict"


def test_mcp_unknown_tool_and_proposal_codes(tmp_project):
    from manju.mcp.tools import ToolError, call_tool

    with pytest.raises(ToolError) as exc:
        call_tool(tmp_project, "unlock", {})
    assert exc.value.code == "unknown_tool"

    with pytest.raises(ToolError) as exc:
        call_tool(tmp_project, "director_execute", {"id": "prop_9999"})
    assert exc.value.code == "unknown_proposal"


# ----------------------------------------------- F-19 MCP export parity


def test_mcp_export_accepts_every_cli_format(tmp_project):
    """The CLI exports nine-plus formats; the MCP tool accepted three and told
    agents "use srt|otio|jianying". Same engine, same surface now — the gate
    (F-01) still runs first, so clear it via config for this probe."""
    from manju.mcp.tools import ToolError, call_tool, list_tools

    config = tmp_project.load_config()
    config.ask_before = ["expensive_generation"]
    tmp_project.save_config(config)

    export = next(t for t in list_tools() if t["name"] == "export")
    enum = set(export["inputSchema"]["properties"]["formats"]["items"]["enum"])
    assert {"srt", "vtt", "ttml", "otio", "edl", "fcpxml", "xmeml",
            "jianying", "capcut"} <= enum

    # every advertised format must reach the shared next precondition (the
    # missing timeline), never an "unknown format" refusal.
    for fmt in sorted(enum):
        with pytest.raises(ToolError) as exc:
            call_tool(tmp_project, "export", {"formats": [fmt]})
        assert "timeline" in str(exc.value), (fmt, str(exc.value))
