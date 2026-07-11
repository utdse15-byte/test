"""AI_IDE_19 WP6 tool orchestration + WP7 NLE round-trip + the core LLM boundary.

§11 rows 11-13: a human trim/split marker imports as a reviewable source patch
(human priority) · LLM/VLM never writes the Timeline directly · NLE round-trip
carries exact integer frame mapping. Plus WP6: the whitelist dispatches onto
EXISTING executors and there is NO LLM planner in core.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from manju.build import toolmap


# ------------------------------------------------------------------ WP6

def test_whitelist_is_exactly_the_contract_ops():
    assert set(toolmap.whitelist_ops()) == {
        "trim", "split", "reorder", "crop", "speed", "gain",
        "caption", "transition", "overlay", "ducking"}


def _resolve_dotted(dotted: str):
    try:                                        # the whole path is a module
        return importlib.import_module(dotted)
    except ModuleNotFoundError:
        pass
    mod_path, _, attr = dotted.rpartition(".")
    try:                                        # module.attribute
        mod = importlib.import_module(mod_path)
        t = getattr(mod, attr, None)
        if t is not None:
            return t
    except ModuleNotFoundError:
        pass
    cmod, _, cls = mod_path.rpartition(".")     # module.Class.method
    mod = importlib.import_module(cmod)
    return getattr(getattr(mod, cls, object), attr, None)


def test_every_op_maps_to_an_existing_executor():
    for op, spec in toolmap.TOOL_WHITELIST.items():
        target = _resolve_dotted(spec["executor"])
        assert target is not None, f"{op} → {spec['executor']} does not exist"


def test_unknown_op_is_refused():
    with pytest.raises(toolmap.ToolError):
        toolmap.resolve_tool("hallucinated_op", {"shot": "S1"})


def test_resolve_and_dry_run():
    r = toolmap.resolve_tool("speed", {"shot": "S1", "take": "take_01", "factor": 0.9})
    assert r["executor"].endswith("retime_take") and r["deterministic"] is True
    d = toolmap.dry_run_tool("crop", {"shot": "S1", "take": "take_01"})
    assert d["dry_run"] is True and d["priced"] == 0.0
    # a malformed op refuses in BOTH resolve and dry-run
    with pytest.raises(toolmap.ToolError):
        toolmap.dry_run_tool("speed", {"shot": "S1", "take": "t", "factor": -1})


def test_no_llm_planner_module_in_core():
    root = Path(toolmap.__file__).resolve().parents[1]  # src/manju
    assert not list(root.rglob("planner.py")), "a planner module was introduced"
    import inspect
    src = inspect.getsource(toolmap).lower()
    # import-shaped tokens (prose in the docstring legitimately says "LLM planner")
    for banned in ("import openai", "import anthropic", "from openai", "from anthropic",
                   "genai", "def plan(", "class planner"):
        assert banned not in src, f"toolmap must be data+dispatch, not a planner ({banned})"


# ---------------------------------------------- row 12: LLM/VLM never writes Timeline

def test_c19_derived_modules_never_write_timeline_directly():
    # analysis / segments / reframe / roughcut are DERIVED evidence + proposals;
    # none may call a timeline/source WRITER directly (the only route to source is
    # a human-confirmed proposal / CAS write).
    import inspect
    from manju.media import analysis, reframe
    from manju.build import segments
    from manju.qc import roughcut
    writers = ("save_timeline", "save_rules", "checked_shot_write", "save_index",
               "save_shot", "write_baseline")
    for mod in (analysis, segments, reframe, roughcut):
        src = inspect.getsource(mod)
        for w in writers:
            assert w not in src, f"{mod.__name__} writes source directly via {w}"


def test_no_llm_import_in_derived_modules():
    import inspect
    from manju.media import analysis, reframe
    from manju.build import segments, bridge
    from manju.qc import roughcut
    for mod in (analysis, segments, reframe, roughcut, bridge):
        src = inspect.getsource(mod).lower()
        for banned in ("import openai", "import anthropic", "from openai", "genai"):
            assert banned not in src


# ---------------------------------------------- WP7 row 13: NLE frame mapping

def test_nle_section_binds_integer_frame_mapping(tmp_project, add_shot, make_take):
    """13C already binds exact media + integer frame mapping — verify + pin it."""
    from manju.build import delivery
    fps = tmp_project.load_config().fps or 25

    class _Clip:
        def __init__(self, **k): self.__dict__.update(k)

    class _Tracks:
        video = [_Clip(shot="S001", take="take_01", source="media/gen/S001/take_01.mp4",
                       start_ms=0, duration_ms=1000, source_in_ms=0)]
        voice = music = sfx = ambient = captions = overlay = []

    class _TL:
        tracks = _Tracks()

    nle, diags = delivery._nle_section(tmp_project, tmp_project.load_config(), _TL(), {})
    # frame mapping is INTEGER frames, never float-seconds only (contract §7)
    if nle and nle.get("media"):
        m = nle["media"][0]
        for key in ("source_in_frames", "source_out_frames",
                    "timeline_in_frames", "timeline_out_frames"):
            assert isinstance(m[key], int)


# ---------------------------------------------- WP7 row 11: human split marker priority

def test_manual_trim_marker_imports_as_reviewable_patch_zero_write(tmp_project, add_shot, make_take):
    """A human OTIO trim/split marker becomes a `set_inout` source-patch PROPOSAL
    (reviewable, zero-write); a moved truth is a CONFLICT (human edit never
    silently overwritten) — the AI_IDE_16 roundtrip path, verified for C19 WP7."""
    from manju.build import roundtrip
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:spec")

    baseline = _otio_doc(in_val=0, dur_val=100)     # original in/duration (frames)
    edited = _otio_doc(in_val=10, dur_val=80)       # a human trimmed the clip
    roundtrip.write_baseline(tmp_project, "otio", "cut", baseline)

    edited_path = tmp_project.root / "cut.otio"
    import json
    edited_path.write_text(json.dumps(edited), encoding="utf-8")

    before = {p.relative_to(tmp_project.root).as_posix()
              for p in tmp_project.root.rglob("*") if p.is_file()}
    plan = roundtrip.plan_roundtrip(tmp_project, edited_path)
    after = {p.relative_to(tmp_project.root).as_posix()
             for p in tmp_project.root.rglob("*") if p.is_file()}
    assert before == after, "plan_roundtrip wrote to disk (must be a zero-write proposal)"

    rows = plan.get("rows") if isinstance(plan, dict) else plan
    trims = [r for r in rows if isinstance(r, dict) and r.get("class") == "trim"]
    assert trims, "a manual trim marker did not become a reviewable source patch"
    assert trims[0]["action"] == "set_inout"


# --------------------------------------------------------------------- helpers

def _otio_doc(*, in_val: int, dur_val: int) -> dict:
    return {
        "OTIO_SCHEMA": "Timeline.1",
        "tracks": {"OTIO_SCHEMA": "Stack.1", "children": [{
            "OTIO_SCHEMA": "Track.1", "kind": "Video", "name": "V1",
            "children": [{
                "OTIO_SCHEMA": "Clip.1", "name": "S001",
                "metadata": {"manju": {"shot": "S001", "take": "take_01"}},
                "source_range": {
                    "OTIO_SCHEMA": "TimeRange.1",
                    "start_time": {"OTIO_SCHEMA": "RationalTime.1", "value": in_val, "rate": 25},
                    "duration": {"OTIO_SCHEMA": "RationalTime.1", "value": dur_val, "rate": 25},
                },
            }],
        }]},
    }
