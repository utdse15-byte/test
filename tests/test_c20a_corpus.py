"""AI_IDE_20A — golden corpus, fake qc_vision reviewer + twin, deterministic
bad-media generator: the self-tests (contract §9: 必须测试 corpus 自身 —— hash、
许可、路径、schema、重复 ID、损坏 fixture；§8 harness: 同一 fixture 重复结果稳定).

Everything under test is TEST DATA in tests/fixtures/golden/. These tests prove:
  * the manifest is schema-valid, hash-pinned, path-clean, dup-free, licensed;
  * the bad-media generator produces PROBE-CONFORMANT media and the corrupt
    fixture actually fails ffprobe;
  * the fake reviewer is deterministic, its twin disagrees where declared, and
    it drives the REAL qc_brief -> record_verdicts -> assurance plumbing.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from manju.qc.agent_review import (
    LEVELS,
    OBSERVED_STATES,
    VERDICT_SCHEMA,
    qc_brief,
    read_v2_records,
    record_verdicts,
)
from manju.qc.assurance import compute_assurance, diff
from manju.qc.checks import QCReport
from manju.qc.content import black_detected, freeze_detected, vision_provider_id
from manju.qc.expectations import compile_expectations
from manju.core.models import TakeSidecar

from tests.fixtures.golden import GOLDEN_DIR, make_bad_media, make_visual
from tests.fixtures.golden.fake_reviewer import (
    MANIFEST_IDS,
    PROFILES,
    FakeVisionReviewer,
    install_manifests,
    load_manifest,
)

MANIFEST = load_manifest()
CASES = {c["id"]: c for c in MANIFEST["cases"]}
VISUAL = [c for c in MANIFEST["cases"] if c["partition"] == "visual_continuity"]
TECH = [c for c in MANIFEST["cases"] if c["partition"] == "technical_media"]


# =============================================================== manifest schema


def test_manifest_top_level_shape():
    assert MANIFEST["schema"] == "manju.golden_corpus/20A"
    # 20B extends the SAME manifest (version bumped, partitions appended); the
    # 20A discipline pinned HERE is that the original two partitions, rubric and
    # reviewer defaults survive unchanged. The 20B partitions get their own
    # discipline tests in test_c20b_corpus.py.
    assert MANIFEST["version"].startswith("20")
    assert MANIFEST["rubric_version"] == "20A.rubric.v1"
    assert MANIFEST["annotator"] == "orchestrated-synthetic-v1"
    assert {"visual_continuity", "technical_media"} <= set(MANIFEST["partitions"])
    assert set(MANIFEST["fake_reviewer_defaults"]) == set(PROFILES)
    assert MANIFEST["design_notes"] and isinstance(MANIFEST["design_notes"], list)


def test_partition_counts_match_cases():
    assert MANIFEST["partitions"]["visual_continuity"]["count"] == len(VISUAL)
    assert MANIFEST["partitions"]["technical_media"]["count"] == len(TECH)


def test_no_duplicate_case_ids():
    ids = [c["id"] for c in MANIFEST["cases"]]
    assert len(ids) == len(set(ids)), "duplicate case id in manifest"


def test_every_case_carries_the_required_annotation_fields():
    # Every case in EVERY partition carries the §4 annotation identity fields.
    # expected_observations must additionally be non-empty on the two 20A media
    # partitions (a media case without a ground-truth observation is unusable);
    # 20B evidence-index / gap / eval kinds express expectations through their
    # own fields (expected_codes / expected / status), checked in test_c20b.
    required = ("id", "partition", "dimension", "kind", "license", "rubric_version",
                "annotator", "blocking_policy", "repair_route", "rationale")
    for c in MANIFEST["cases"]:
        for field in required:
            assert field in c and c[field] not in (None, "", []), \
                f"{c['id']} missing {field}"
        assert "expected_observations" in c, f"{c['id']} missing expected_observations"
        if c["partition"] in ("visual_continuity", "technical_media"):
            assert c["expected_observations"], f"{c['id']} has no ground truth"


def test_all_licenses_are_self_made_synthetic():
    # contract §4/§9: 无私密/受版权素材 — everything synthesized.
    assert all(c["license"] == "self-made-synthetic" for c in MANIFEST["cases"])


def test_expected_observations_use_the_real_section6_enums():
    for c in MANIFEST["cases"]:
        for o in c["expected_observations"]:
            assert o["observed"] in OBSERVED_STATES, (c["id"], o)
            assert o["severity"] in (None, *LEVELS), (c["id"], o)
            assert isinstance(o["allow_unknown"], bool)
            assert "dimension" in o


def test_blocking_policy_and_repair_route_are_known_tokens():
    assert all(c["blocking_policy"] in ("blocking", "advisory", "non_blocking")
               for c in MANIFEST["cases"])
    assert all(isinstance(c["repair_route"], str) and c["repair_route"]
               for c in MANIFEST["cases"])


def test_no_absolute_paths_anywhere_in_the_manifest():
    # every committed-file reference is a RELATIVE POSIX path under the corpus.
    import re

    drive = re.compile(r"^[A-Za-z]:[\\/]")  # a real C:\ / C:/ drive path

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                yield from walk(x)
        elif isinstance(v, list):
            for x in v:
                yield from walk(x)
        elif isinstance(v, str):
            yield v

    for s in walk(MANIFEST):
        assert not s.startswith("/"), f"absolute path leaked: {s!r}"
        assert "\\" not in s, f"windows path separator: {s!r}"
        assert not drive.match(s), f"drive-letter path: {s!r}"
    for c in MANIFEST["cases"]:
        if c.get("path"):
            assert not Path(c["path"]).is_absolute()
            assert c["path"].startswith("visual/")


# ============================================================ committed images


def test_every_committed_image_sha256_matches_the_manifest():
    for c in VISUAL:
        p = GOLDEN_DIR / c["path"]
        assert p.is_file(), f"missing committed image {c['path']}"
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        assert got == c["sha256"], f"{c['id']}: sha256 drift (bytes changed?)"
        assert p.stat().st_size == c["bytes"], f"{c['id']}: byte count drift"


def test_committed_images_are_distinct_files():
    # a sha256-keyed reviewer needs every case to be its own hash.
    shas = [c["sha256"] for c in VISUAL]
    assert len(shas) == len(set(shas)), "two visual cases share committed bytes"


def test_committed_images_are_tiny_pngs_within_128px():
    from PIL import Image

    for c in VISUAL:
        with Image.open(GOLDEN_DIR / c["path"]) as im:
            assert im.format == "PNG"
            assert max(im.size) <= 128, f"{c['id']} exceeds 128px: {im.size}"


def test_total_committed_corpus_is_under_300kb():
    total = sum((GOLDEN_DIR / c["path"]).stat().st_size for c in VISUAL)
    # + committed generators/manifest/provider yamls (the whole golden tree).
    tree = sum(p.stat().st_size for p in GOLDEN_DIR.rglob("*")
               if p.is_file() and "__pycache__" not in p.parts)
    assert total < 300_000
    assert tree < 300_000, f"golden tree {tree}B exceeds 300KB budget"


def test_visual_builders_cover_exactly_the_manifest_visual_cases():
    assert set(make_visual.BUILDERS) == {c["id"] for c in VISUAL}


def test_visual_generation_is_deterministic(tmp_path):
    # §8: 同一 fixture 重复结果稳定 — regenerating yields byte-identical PNGs.
    a = make_visual.build_all(tmp_path / "a")
    b = make_visual.build_all(tmp_path / "b")
    for cid in a:
        assert hashlib.sha256(a[cid].read_bytes()).digest() == \
               hashlib.sha256(b[cid].read_bytes()).digest(), f"{cid} non-deterministic"


# ======================================================= bad-media generator
# Generated into a SESSION tmp dir (committed generator, generated media stays
# out of the repo). Probe FACTS validate it, never a byte hash.


@pytest.fixture(scope="session")
def bad_media(tmp_path_factory) -> dict[str, Path]:
    dest = tmp_path_factory.mktemp("c20a_bad_media")
    return make_bad_media.build_all(dest)


def _probe_or_none(path: Path):
    from manju.media.ffmpeg import MediaError
    from manju.media.probe import probe

    try:
        return probe(path)
    except MediaError:
        return None


@pytest.mark.parametrize("case", [c for c in TECH if c.get("validation", "").startswith(
    ("ffprobe",))], ids=lambda c: c["id"])
def test_generator_produces_probe_conformant_media(case, bad_media):
    info = _probe_or_none(bad_media[case["id"]])
    assert info is not None, f"{case['id']} did not probe"
    exp = case["probe"]
    if "has_video" in exp:
        assert (info.width is not None) == exp["has_video"]
    if "has_audio" in exp:
        assert info.has_audio == exp["has_audio"], case["id"]
    if "width" in exp:
        assert info.width == exp["width"] and info.height == exp["height"]
    if "fps_approx" in exp:
        assert info.fps is not None and abs(info.fps - exp["fps_approx"]) <= 1.0
    if "duration_ms" in exp:
        lo, hi = exp["duration_ms"]
        assert lo <= (info.duration_ms or -1) <= hi, (case["id"], info.duration_ms)


def test_corrupt_container_actually_fails_ffprobe(bad_media):
    case = CASES["technical.corrupt_container"]
    assert case["probe"].get("probe_must_fail") is True
    assert _probe_or_none(bad_media[case["id"]]) is None, "corrupt fixture must fail probe"


def test_black_and_freeze_fixtures_trip_the_production_detectors(bad_media):
    # the fault harness exercises the SAME detectors the engine gates on.
    assert black_detected(bad_media["technical.black_head_tail"]) is True
    assert freeze_detected(bad_media["technical.freeze"]) is True


def test_audio_drift_carries_a_real_stream_offset(bad_media):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=start_time", "-of", "json",
         str(bad_media["technical.audio_drift"])],
        capture_output=True, text=True).stdout
    start = float(json.loads(out)["streams"][0]["start_time"])
    floor_ms = CASES["technical.audio_drift"]["probe"]["audio_start_ms_min"]
    assert start * 1000 >= floor_ms, f"audio not drifted: start={start}s"


def test_subtitle_overlap_fixture_has_overlapping_cues(bad_media):
    text = bad_media["technical.subtitle_overlap"].read_text(encoding="utf-8")
    spans = []
    for line in text.splitlines():
        if line.startswith("Dialogue:"):
            parts = line.split(",")
            spans.append((_ass_ms(parts[1]), _ass_ms(parts[2])))
    assert len(spans) >= 2
    (s1, e1), (s2, e2) = spans[0], spans[1]
    assert s2 < e1, f"cues do not overlap: {spans}"  # second starts before first ends


def _ass_ms(t: str) -> int:
    h, m, s = t.strip().split(":")
    return int((int(h) * 3600 + int(m) * 60 + float(s)) * 1000)


def test_text_mutation_frames_differ(bad_media):
    a = bad_media["technical.text_mutation.a"].read_bytes()
    b = bad_media["technical.text_mutation.b"].read_bytes()
    assert hashlib.sha256(a).digest() != hashlib.sha256(b).digest()


def test_generation_facts_are_deterministic(tmp_path):
    from manju.media.probe import probe

    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    i1 = probe(make_bad_media.gen_good(one))
    i2 = probe(make_bad_media.gen_good(two))
    assert (i1.duration_ms, i1.width, i1.height, i1.fps, i1.has_audio) == \
           (i2.duration_ms, i2.width, i2.height, i2.fps, i2.has_audio)


# ====================================================== fake reviewer + twin


def test_provider_manifests_load_through_the_real_registry(tmp_path, monkeypatch):
    from manju.providers.manifest import load_manifests

    provdir = tmp_path / "_providers"
    ids = install_manifests(provdir)
    assert ids == MANIFEST_IDS
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(provdir))
    manifests, errors = load_manifests()
    assert errors == [], errors
    assert set(manifests) == set(MANIFEST_IDS.values())
    assert manifests["qc_vision_fake"].type == "vision"
    # qc.content's vision-slot lookup finds the primary (first sorted vision id).
    assert vision_provider_id() == "qc_vision_fake"


def test_primary_and_twin_have_distinct_profile_digests():
    p = FakeVisionReviewer("primary", MANIFEST)
    t = FakeVisionReviewer("twin", MANIFEST)
    assert p.profile_digest != t.profile_digest
    assert p.reviewer_ref()["name"] == "qc_vision_fake"
    assert t.reviewer_ref()["name"] == "qc_vision_fake_twin"


def test_reviewer_observation_is_deterministic():
    r = FakeVisionReviewer("primary", MANIFEST)
    sha = CASES["visual.identity.mismatch.charB"]["sha256"]
    assert r.observe(sha) == r.observe(sha)
    obs = r.observe(sha)[0]
    assert obs["observed"] == "absent" and obs["severity"] == "blocker"


def test_reviewer_default_fallback_is_unknown():
    # contract §5: unregistered media -> declared default, never a silent pass.
    r = FakeVisionReviewer("primary", MANIFEST)
    obs = r.observe("00" * 32)
    assert obs[0]["observed"] == "uncertain"
    assert obs[0]["allow_unknown"] is True


def test_twin_disagrees_exactly_where_declared():
    p = FakeVisionReviewer("primary", MANIFEST)
    t = FakeVisionReviewer("twin", MANIFEST)
    declared_conflicts = {
        c["id"] for c in VISUAL
        if c["reviewer"]["primary"] != c["reviewer"]["twin"]
    }
    assert declared_conflicts, "twin must declare at least one conflict"
    for c in VISUAL:
        sha = c["sha256"]
        same = p.observe(sha)[0]["observed"] == t.observe(sha)[0]["observed"]
        if c["id"] in declared_conflicts:
            assert not same, f"{c['id']} declared a twin conflict but observes agree"
        else:
            assert same, f"{c['id']} not a declared conflict but observes differ"


def test_twin_misses_a_blocker_the_primary_catches():
    # identity mismatch: primary observes absent(+blocker); twin observes present.
    sha = CASES["visual.identity.mismatch.charB"]["sha256"]
    p = FakeVisionReviewer("primary", MANIFEST).observe(sha)[0]
    t = FakeVisionReviewer("twin", MANIFEST).observe(sha)[0]
    assert p["observed"] == "absent" and p["severity"] == "blocker"
    assert t["observed"] == "present"  # the twin's missed blocker


# ---------------------------------------- reviewer drives the REAL plumbing


def _register_image(project, shot_id, case_id):
    src = GOLDEN_DIR / CASES[case_id]["path"]
    take = project.register_take(shot_id, src, TakeSidecar(provider="test", spec_hash="h"))
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name))
    return take


def _brief_row(project, shot_id="S001"):
    rows = {r["shot"]: r for r in qc_brief(project)["shots"]}
    return rows[shot_id]


def test_brief_packet_binds_the_reviewed_golden_bytes(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"must_show": ["林夏(charA)出现"]})
    _register_image(tmp_project, "S001", "visual.identity.match.charA")
    row = _brief_row(tmp_project)
    assert row["packet_id"].startswith("pkt_")
    assert row["media"]["sha256"].split(":", 1)[-1] == \
        CASES["visual.identity.match.charA"]["sha256"]


def test_reviewer_verdict_drives_assurance_accepted(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"must_show": ["林夏(charA)出现"]})
    _register_image(tmp_project, "S001", "visual.identity.match.charA")
    row = _brief_row(tmp_project)
    reviewer = FakeVisionReviewer("primary", MANIFEST)
    result = record_verdicts(tmp_project, reviewer.build_verdict(row))
    assert result["bindings"] == {"bound": 1}
    recs, malformed = read_v2_records(tmp_project)
    assert malformed == 0 and recs[0]["reviewer"]["name"] == "qc_vision_fake"
    es = compile_expectations(tmp_project, "S001")
    assert set(diff(es["expectations"], recs[0]["observations"]).values()) == {"PASS"}
    a = compute_assurance(tmp_project, "S001", qc_report=QCReport(items=[]))
    assert a["assurance_state"] == "accepted"


def test_reviewer_verdict_drives_assurance_rejected(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"must_show": ["林夏(charA)出现"]})
    _register_image(tmp_project, "S001", "visual.identity.mismatch.charB")
    row = _brief_row(tmp_project)
    reviewer = FakeVisionReviewer("primary", MANIFEST)
    record_verdicts(tmp_project, reviewer.build_verdict(row))
    recs, _ = read_v2_records(tmp_project)
    es = compile_expectations(tmp_project, "S001")
    assert set(diff(es["expectations"], recs[0]["observations"]).values()) == {"FAIL"}
    a = compute_assurance(tmp_project, "S001")  # rejected before the QC gate
    assert a["assurance_state"] == "rejected"


def test_reviewer_verdict_drives_assurance_unknown(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"must_show": ["林夏(charA)出现"]})
    _register_image(tmp_project, "S001", "visual.ambiguous.lowsignal")
    row = _brief_row(tmp_project)
    reviewer = FakeVisionReviewer("primary", MANIFEST)
    record_verdicts(tmp_project, reviewer.build_verdict(row))
    recs, _ = read_v2_records(tmp_project)
    es = compile_expectations(tmp_project, "S001")
    assert set(diff(es["expectations"], recs[0]["observations"]).values()) == {"UNKNOWN"}
    a = compute_assurance(tmp_project, "S001")
    assert a["assurance_state"] == "unknown"


def test_twin_misses_the_blocker_end_to_end(tmp_project, add_shot):
    # the SAME mismatch media the primary rejects, the twin accepts.
    add_shot(tmp_project, "S001", quality={"must_show": ["林夏(charA)出现"]})
    _register_image(tmp_project, "S001", "visual.identity.mismatch.charB")
    row = _brief_row(tmp_project)
    record_verdicts(tmp_project, FakeVisionReviewer("twin", MANIFEST).build_verdict(row))
    recs, _ = read_v2_records(tmp_project)
    es = compile_expectations(tmp_project, "S001")
    assert set(diff(es["expectations"], recs[0]["observations"]).values()) == {"PASS"}
    a = compute_assurance(tmp_project, "S001", qc_report=QCReport(items=[]))
    assert a["assurance_state"] == "accepted"  # missed blocker


def test_reviewer_maps_avoid_polarity_expectations(tmp_project, add_shot):
    # an AVOID (absent-polarity) expectation: the drift media presents the
    # avoided thing -> observed present -> FAIL (exercises the absent branch).
    add_shot(tmp_project, "S001", quality={"avoid": ["服装漂移"]})
    _register_image(tmp_project, "S001", "visual.wardrobe.drift.charA")
    row = _brief_row(tmp_project)
    record_verdicts(tmp_project, FakeVisionReviewer("primary", MANIFEST).build_verdict(row))
    recs, _ = read_v2_records(tmp_project)
    es = compile_expectations(tmp_project, "S001")
    assert es["expectations"][0]["polarity"] == "absent"
    assert set(diff(es["expectations"], recs[0]["observations"]).values()) == {"FAIL"}


def test_reviewer_default_fallback_on_generated_media(tmp_project, add_shot, bad_media):
    # unregistered media (a generated clip, unknown sha) -> default UNKNOWN.
    add_shot(tmp_project, "S001", quality={"must_show": ["林夏(charA)出现"]})
    take = tmp_project.register_take(
        "S001", bad_media["technical.good"], TakeSidecar(provider="test", spec_hash="h"))
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name))
    row = _brief_row(tmp_project)
    record_verdicts(tmp_project, FakeVisionReviewer("primary", MANIFEST).build_verdict(row))
    recs, _ = read_v2_records(tmp_project)
    es = compile_expectations(tmp_project, "S001")
    assert set(diff(es["expectations"], recs[0]["observations"]).values()) == {"UNKNOWN"}


# ================================================================= rulings


def test_runtime_code_never_reads_the_golden_corpus():
    # ruling: the corpus is TEST DATA — src/manju must not reference it.
    src = Path(__file__).resolve().parent.parent / "src" / "manju"
    needles = ("fixtures/golden", "golden_corpus", "qc_vision_fake",
               "make_bad_media", "make_visual")
    hits = []
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        for n in needles:
            if n in text:
                hits.append(f"{py}: {n}")
    assert not hits, f"runtime code references the golden corpus: {hits}"


def test_verdict_schema_is_the_real_v2_contract():
    # the reviewer emits the live intake schema (so 15 consumes it unchanged).
    r = FakeVisionReviewer("primary", MANIFEST)
    add = {"packet_id": "pkt_x", "shot": "S001", "expectations": [],
           "media": {"sha256": "sha256:" + "0" * 64}, "spec_hash": "h",
           "expectation_digest": "d"}
    v = r.build_verdict(add)
    assert v["schema"] == VERDICT_SCHEMA
