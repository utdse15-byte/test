"""OpenClap adapter — read / write / inspect (raw-preserving parser).

Fixtures are built in-test (gzip + ``yaml.safe_dump`` of a list) — no binary
fixture files. Covers WP test matrix items 1, 2, 3, 4, 6, 13.
"""

from __future__ import annotations

import gzip

import pytest
import yaml

from manju.exporters.openclap import (
    ClapLimits,
    inspect_clap,
    read_clap,
    write_clap,
)
from manju.exporters.openclap.model import ClapReadError


# --------------------------------------------------------------- fixtures


def _write_clap_file(path, items, *, gzip_it=True, encoding="utf-8"):
    payload = yaml.safe_dump(items, allow_unicode=True, sort_keys=False).encode(encoding)
    if gzip_it:
        payload = gzip.compress(payload, mtime=0)
    path.write_bytes(payload)
    return path


def _minimal_items():
    """header + meta + 1 entity + 1 scene + 1 segment (a minimal legal file)."""
    return [
        {"format": "clap-0", "numberOfWorkflows": 0, "numberOfEntities": 1,
         "numberOfScenes": 1, "numberOfSegments": 1},
        {"id": "m", "title": "雨夜便利店", "width": 1080, "height": 1920,
         "durationInMs": 2000, "orientation": "portrait"},
        {"id": "linxia", "category": "CHARACTER", "triggerName": "linxia", "label": "林夏"},
        {"id": "store", "scene": "便利店"},
        {"id": "shot:S001/video:main", "track": 0, "category": "VIDEO",
         "startTimeInMs": 0, "endTimeInMs": 2000, "assetDurationInMs": 2000,
         "assetUrl": "media/gen/S001/take_01.mp4"},
    ]


# ---------------------------------------------------- (1) inspect minimal


def test_inspect_minimal_legal_file(tmp_path):
    path = _write_clap_file(tmp_path / "min.clap", _minimal_items())
    doc = read_clap(path)

    assert [d for d in doc.diagnostics if d.severity == "error"] == []
    assert doc.actual_counts == {"workflows": 0, "entities": 1, "scenes": 1, "segments": 1}

    summary = inspect_clap(doc)
    assert summary["header"]["format"] == "clap-0"
    assert summary["header"]["declared"] == {
        "workflows": 0, "entities": 1, "scenes": 1, "segments": 1}
    assert summary["actual_counts"] == doc.actual_counts
    assert summary["meta"]["title"] == "雨夜便利店"
    assert summary["meta"]["width"] == 1080 and summary["meta"]["height"] == 1920
    assert summary["segment_categories"] == {"VIDEO": 1}
    assert summary["entity_categories"] == {"CHARACTER": 1}
    assert summary["locator_histogram"] == {"project_path": 1}
    assert not any(d["severity"] == "error" for d in summary["diagnostics"])


# ---------------------------------------------- (2) unknown-field fidelity


def test_unknown_fields_survive_round_trip(tmp_path):
    """Custom keys at every level (meta, entity, scene, segment, nested dicts)
    survive read -> write -> read, and top-level item order is preserved."""
    items = [
        {"format": "clap-0", "numberOfWorkflows": 0, "numberOfEntities": 1,
         "numberOfScenes": 1, "numberOfSegments": 1, "vendorHeaderKey": "H"},
        {"id": "m", "title": "T", "customMeta": {"deep": {"deeper": [1, 2, 3]}}},
        {"id": "e1", "category": "CHARACTER", "vendorEntity": "keep",
         "nested": {"a": {"b": 1}}},
        {"id": "sc1", "scene": "S", "vendorScene": [4, 5, 6]},
        {"id": "seg1", "category": "VIDEO", "startTimeInMs": 0, "endTimeInMs": 10,
         "vendorSeg": "x", "vendorDict": {"k": "v"}},
    ]
    path = _write_clap_file(tmp_path / "fidelity.clap", items)

    doc1 = read_clap(path)
    order1 = [it.get("id") or it.get("format") for it in doc1.raw_items]

    out = tmp_path / "fidelity_rt.clap"
    write_clap(doc1, out)
    doc2 = read_clap(out)

    # Every mapping survives verbatim, and item order is unchanged.
    assert doc2.raw_items == doc1.raw_items
    assert [it.get("id") or it.get("format") for it in doc2.raw_items] == order1

    assert doc2.raw_items[0]["vendorHeaderKey"] == "H"
    assert doc2.raw_items[1]["customMeta"] == {"deep": {"deeper": [1, 2, 3]}}
    assert doc2.raw_items[2]["nested"] == {"a": {"b": 1}}
    assert doc2.raw_items[3]["vendorScene"] == [4, 5, 6]
    assert doc2.raw_items[4]["vendorDict"] == {"k": "v"}


# ------------------------------------------- (3) corruption fails closed


def test_counts_exceed_items_fails_closed(tmp_path):
    items = _minimal_items()
    items[0]["numberOfEntities"] = 5  # only 1 entity actually present
    path = _write_clap_file(tmp_path / "bad_counts.clap", items)
    with pytest.raises(ClapReadError) as ei:
        read_clap(path)
    assert any(d.code == "counts_exceed_items" for d in ei.value.diagnostics)


def test_number_of_segments_mismatch_fails_closed(tmp_path):
    items = _minimal_items()
    items[0]["numberOfSegments"] = 9  # only 1 segment actually present
    path = _write_clap_file(tmp_path / "bad_segcount.clap", items)
    with pytest.raises(ClapReadError) as ei:
        read_clap(path)
    assert any(d.code == "segment_count_mismatch" for d in ei.value.diagnostics)


def test_top_level_not_a_list_fails_closed(tmp_path):
    path = _write_clap_file(tmp_path / "not_list.clap", {"format": "clap-0"})
    with pytest.raises(ClapReadError) as ei:
        read_clap(path)
    assert any(d.code == "not_an_array" for d in ei.value.diagnostics)


def test_missing_header_fails_closed(tmp_path):
    path = _write_clap_file(tmp_path / "empty.clap", [])
    with pytest.raises(ClapReadError) as ei:
        read_clap(path)
    assert any(d.code == "missing_header" for d in ei.value.diagnostics)


def test_failed_read_writes_nothing(tmp_path):
    """A fail-closed parse must not create sibling/target files."""
    items = _minimal_items()
    items[0]["numberOfScenes"] = 99
    path = _write_clap_file(tmp_path / "corrupt.clap", items)
    before = {p.name for p in tmp_path.iterdir()}
    with pytest.raises(ClapReadError):
        read_clap(path)
    assert {p.name for p in tmp_path.iterdir()} == before


# ----------------------------------------- (4) resource-limit guards


def test_decompression_bomb_aborts_mid_stream(tmp_path):
    """A small .clap whose payload decompresses past a tiny cap aborts with the
    decompressed_too_large code (never decompress-then-check)."""
    big = [{"format": "clap-0", "numberOfWorkflows": 0, "numberOfEntities": 0,
            "numberOfScenes": 0, "numberOfSegments": 1},
           {"id": "m"},
           {"id": "seg", "category": "VIDEO", "prompt": "A" * 500_000}]
    path = _write_clap_file(tmp_path / "bomb.clap", big)
    assert path.stat().st_size < 50_000  # highly compressible -> small on disk
    tiny = ClapLimits(max_decompressed_bytes=4096)
    with pytest.raises(ClapReadError) as ei:
        read_clap(path, tiny)
    assert any(d.code == "decompressed_too_large" for d in ei.value.diagnostics)


def test_item_count_cap_fails_closed(tmp_path):
    items = [{"format": "clap-0", "numberOfWorkflows": 0, "numberOfEntities": 0,
              "numberOfScenes": 0, "numberOfSegments": 30}, {"id": "m"}]
    items += [{"id": f"seg{i}", "category": "VIDEO"} for i in range(30)]
    path = _write_clap_file(tmp_path / "manyitems.clap", items)
    with pytest.raises(ClapReadError) as ei:
        read_clap(path, ClapLimits(max_items=5))
    assert any(d.code == "too_many_items" for d in ei.value.diagnostics)


def test_compressed_size_cap_fails_closed(tmp_path):
    path = _write_clap_file(tmp_path / "min.clap", _minimal_items())
    with pytest.raises(ClapReadError) as ei:
        read_clap(path, ClapLimits(max_compressed_bytes=1))
    assert any(d.code == "compressed_too_large" for d in ei.value.diagnostics)


def test_nesting_depth_cap_fails_closed(tmp_path):
    node = {"x": 1}
    for _ in range(40):
        node = {"n": node}
    items = [{"format": "clap-0", "numberOfWorkflows": 0, "numberOfEntities": 0,
              "numberOfScenes": 0, "numberOfSegments": 1}, {"id": "m"},
             {"id": "seg", "category": "VIDEO", "deep": node}]
    path = _write_clap_file(tmp_path / "deep.clap", items)
    with pytest.raises(ClapReadError) as ei:
        read_clap(path, ClapLimits(max_nesting_depth=8))
    assert any(d.code == "too_deep" for d in ei.value.diagnostics)


def test_plain_uncompressed_yaml_is_accepted(tmp_path):
    """Cheap robustness: a non-gzip .clap (no 1f 8b magic) still parses."""
    path = _write_clap_file(tmp_path / "plain.clap", _minimal_items(), gzip_it=False)
    doc = read_clap(path)
    assert doc.actual_counts["segments"] == 1


def test_non_utf8_fails_closed(tmp_path):
    # latin-1 bytes that are not valid UTF-8
    path = tmp_path / "latin.clap"
    path.write_bytes("- format: clap-0\n- title: café\n".encode("latin-1"))
    with pytest.raises(ClapReadError) as ei:
        read_clap(path)
    assert any(d.code in ("not_utf8", "yaml_error") for d in ei.value.diagnostics)


# ---------------------------------------- (6) unknown enum preservation


def test_unknown_enum_values_preserved_and_normalized(tmp_path):
    """Unknown category FOO_FUTURE round-trips verbatim; provider COMFUI is kept
    raw but the typed view normalizes it to comfyui."""
    items = [
        {"format": "clap-0", "numberOfWorkflows": 1, "numberOfEntities": 0,
         "numberOfScenes": 0, "numberOfSegments": 1},
        {"id": "m"},
        {"id": "wf1", "provider": "COMFUI", "name": "gen"},
        {"id": "seg1", "category": "FOO_FUTURE", "startTimeInMs": 0, "endTimeInMs": 5},
    ]
    path = _write_clap_file(tmp_path / "enums.clap", items)
    doc = read_clap(path)

    wf = doc.workflows[0]
    assert wf.raw_provider == "COMFUI"        # raw kept verbatim
    assert wf.provider == "comfyui"           # typed view normalized

    seg = doc.segments[0]
    assert seg.raw_category == "FOO_FUTURE"
    assert seg.category is None               # not recognized
    assert seg.category_is_known is False

    # both survive a write -> read cycle byte-for-byte at the value level
    out = tmp_path / "enums_rt.clap"
    write_clap(doc, out)
    doc2 = read_clap(out)
    assert doc2.workflows[0].raw_provider == "COMFUI"
    assert doc2.segments[0].raw_category == "FOO_FUTURE"

    summary = inspect_clap(doc)
    assert "FOO_FUTURE" in summary["unknown_categories"]


# ------------------------------------ (13) endTimeInMs independence


def test_end_time_independent_of_asset_duration(tmp_path):
    """A valid endTimeInMs is kept even with NO assetDurationInMs; an invalid one
    produces a per-segment warning and is preserved raw (never zeroed)."""
    items = [
        {"format": "clap-0", "numberOfWorkflows": 0, "numberOfEntities": 0,
         "numberOfScenes": 0, "numberOfSegments": 2},
        {"id": "m"},
        # valid endTimeInMs, assetDurationInMs deliberately absent
        {"id": "good", "category": "VIDEO", "startTimeInMs": 0, "endTimeInMs": 2000},
        # invalid endTimeInMs (non-numeric)
        {"id": "bad", "category": "VIDEO", "startTimeInMs": 0, "endTimeInMs": "oops",
         "assetDurationInMs": 1000},
    ]
    path = _write_clap_file(tmp_path / "endtimes.clap", items)
    doc = read_clap(path)

    good = doc.segments[0]
    assert "assetDurationInMs" not in good.raw
    assert good.end_time_in_ms == 2000
    assert good.has_valid_end_time is True

    bad = doc.segments[1]
    assert bad.end_time_in_ms is None
    assert bad.has_valid_end_time is False
    assert bad.raw["endTimeInMs"] == "oops"  # preserved raw, not zeroed

    warnings = [d for d in doc.diagnostics if d.code == "segment_invalid_end_time"]
    assert len(warnings) == 1 and "bad" in warnings[0].message
    # the good segment must NOT be warned about
    assert all("good" not in d.message for d in warnings)
