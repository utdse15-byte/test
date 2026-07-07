"""Reference-image budget allocation (goal item 9) + cleanliness (goal item 10).

BUDGET: deterministic priority allocation, no-limit byte-identity (allocator AND
the generic_cloud delivery path), and delivery integration with a mock manifest.
CLEANLINESS: local ffmpeg heuristics on synthetic refs (solid = clean;
noise-border = busy background; different-brightness pair = lighting conflict;
tiny = unclear scale) and honest needs_vision advisories when no vision vendor.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from manju.providers.base import GenerationRequest
from manju.providers.generic_cloud import GenericCloudProvider, HttpResponse
from manju.providers.manifest import ProviderManifest
from manju.providers.refbudget import (
    ROLE_CHARACTER_PRIMARY,
    ROLE_SCENE,
    ROLE_SHOT_PARAM,
    allocate,
    classify_role,
)
from manju.providers.refs import TIER_BIBLE, TIER_PARAMS, resolve_refs
from manju.qc.ref_checks import RefFinding, check_refs

FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")


# --------------------------------------------------------------- fixtures


def _touch(project, rel, data=b"img"):
    p = project.resolve(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _ffmpeg(*args) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                   check=True, capture_output=True)


def _solid(project, rel, color="blue", size="640x640"):
    p = project.resolve(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s={size}", "-frames:v", "1", str(p))
    return p


def _noise_border(project, rel, size="640x640"):
    """A solid subject box surrounded by camera-noise borders — a busy
    background around a clean centre."""
    p = project.resolve(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg("-f", "lavfi", "-i", f"color=c=gray:s={size}",
            "-vf", "noise=alls=90:allf=t+u,"
                   "drawbox=x=140:y=140:w=360:h=360:color=blue@1.0:t=fill,format=rgb24",
            "-frames:v", "1", str(p))
    return p


class ScriptedTransport:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        return self.script.pop(0)


def _resp(status, payload):
    return HttpResponse(status, {}, json.dumps(payload).encode())


def _url_manifest(**limits) -> ProviderManifest:
    base = {
        "id": "video_x", "type": "video", "adapter": "generic_cloud",
        "capabilities": ["image_to_video"], "auth": {"key_env": "VIDEO_X_KEY"},
        "submit": {"url": "https://api.example.com/v1/videos",
                   "body_template": {"prompt": "{prompt}", "image_url": ""},
                   "job_id_path": "$.data.task_id"},
        "poll": {"url": "https://api.example.com/v1/videos/{job_id}",
                 "status_path": "$.data.status",
                 "status_map": {"SUCCEEDED": "succeeded", "FAILED": "failed"},
                 "result_url_path": "$.data.video_url"},
        "cost": {"per_second": 0.0, "currency": "CNY"},
        "refs": {"image_mode": "url_field", "field": "$.image_url", "max_images": 3},
    }
    if limits:
        base["limits"] = limits
    return ProviderManifest.model_validate(base)


def _req(project, shot, **params):
    return GenerationRequest(project=project, shot=shot, bible=project.load_bible(),
                             spec_hash="sha256:x", duration_ms=3000, candidates=1,
                             params=dict(params))


# ======================================================= budget: allocate


def test_priority_order_keeps_high_tier_drops_scene(tmp_project, add_shot):
    """explicit shot-param > character primary > scene primary (contract order)."""
    _touch(tmp_project, "media/refs/param.png")
    bible = tmp_project.load_bible()
    bible["linxia"]["ref_image"] = "media/refs/char.png"
    bible["convenience_store"]["ref_image"] = "media/refs/scene.png"
    _touch(tmp_project, "media/refs/char.png")
    _touch(tmp_project, "media/refs/scene.png")
    shot = add_shot(tmp_project, "S001",
                    generation={"params": {"image": "media/refs/param.png"}})
    rs = resolve_refs(tmp_project, shot, bible)

    class L:  # only max_ref_images matters (duck-typed)
        max_ref_images = 2
        max_ref_videos = None

    rep = allocate(rs, L(), shot, bible=bible)
    assert [it.ref for it in rep.selected_images] == \
        ["media/refs/param.png", "media/refs/char.png"]
    assert len(rep.omitted) == 1
    om = rep.omitted[0]
    assert om.role == ROLE_SCENE
    # exact contract wording
    assert om.reason == "省略了场景参考图:场景一致性将只依赖提示词描述"


def test_extra_angle_is_demoted_below_scene(tmp_project, add_shot):
    """A 2nd ref of the same role is an 'additional angle' — dropped before a
    scene primary even though its role rank is higher."""
    bible = tmp_project.load_bible()
    bible["linxia"]["ref_images"] = ["media/refs/c1.png", "media/refs/c2.png"]
    bible["convenience_store"]["ref_image"] = "media/refs/scene.png"
    for r in ("c1", "c2", "scene"):
        _touch(tmp_project, f"media/refs/{r}.png")
    shot = add_shot(tmp_project, "S001")
    rs = resolve_refs(tmp_project, shot, bible)

    class L:
        max_ref_images = 2
        max_ref_videos = None

    rep = allocate(rs, L(), shot, bible=bible)
    assert [it.ref for it in rep.selected_images] == \
        ["media/refs/c1.png", "media/refs/scene.png"]
    assert [o.item.ref for o in rep.omitted] == ["media/refs/c2.png"]
    assert "额外角度" in rep.omitted[0].reason


def test_allocation_is_deterministic(tmp_project, add_shot):
    bible = tmp_project.load_bible()
    bible["linxia"]["ref_images"] = ["media/refs/c1.png", "media/refs/c2.png"]
    bible["convenience_store"]["ref_image"] = "media/refs/scene.png"
    for r in ("c1", "c2", "scene"):
        _touch(tmp_project, f"media/refs/{r}.png")
    shot = add_shot(tmp_project, "S001")
    rs = resolve_refs(tmp_project, shot, bible)

    class L:
        max_ref_images = 2
        max_ref_videos = None

    a = allocate(rs, L(), shot, bible=bible).to_lineage()
    b = allocate(rs, L(), shot, bible=bible).to_lineage()
    assert a == b


def test_no_limit_is_byte_identical_allocation(tmp_project, add_shot):
    """No budget → refs in resolved order, zero omissions, inactive."""
    bible = tmp_project.load_bible()
    bible["linxia"]["ref_images"] = ["media/refs/c1.png", "media/refs/c2.png"]
    for r in ("c1", "c2"):
        _touch(tmp_project, f"media/refs/{r}.png")
    shot = add_shot(tmp_project, "S001")
    rs = resolve_refs(tmp_project, shot, bible)

    rep = allocate(rs, None, shot, bible=bible)  # limits=None
    assert not rep.active
    assert rep.omitted == []
    assert [it.ref for it in rep.selected_images] == [it.ref for it in rs.image_items()]


def test_classify_role_from_tier_and_bible(tmp_project, add_shot):
    _touch(tmp_project, "media/refs/param.png")
    bible = tmp_project.load_bible()
    bible["linxia"]["ref_image"] = "media/refs/char.png"
    bible["convenience_store"]["ref_image"] = "media/refs/scene.png"
    _touch(tmp_project, "media/refs/char.png")
    _touch(tmp_project, "media/refs/scene.png")
    shot = add_shot(tmp_project, "S001",
                    generation={"params": {"image": "media/refs/param.png"}})
    rs = resolve_refs(tmp_project, shot, bible)
    by_tier = {it.tier: classify_role(it, shot, bible) for it in rs.image_items()}
    assert by_tier[TIER_PARAMS] == ROLE_SHOT_PARAM
    # bible refs split into character-primary and scene by the Bible mapping
    roles = {it.ref: classify_role(it, shot, bible)
             for it in rs.image_items() if it.tier == TIER_BIBLE}
    assert roles["media/refs/char.png"] == ROLE_CHARACTER_PRIMARY
    assert roles["media/refs/scene.png"] == ROLE_SCENE


# ==================================================== budget: delivery wire


def test_delivery_trims_and_records_budget(tmp_project, add_shot, monkeypatch):
    """A configured max_ref_images trims what is sent AND records selected +
    omitted (with 中文 reason) on ref_delivery.budget."""
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    bible = tmp_project.load_bible()
    bible["linxia"]["ref_images"] = ["https://cdn/char1.png", "https://cdn/char2.png"]
    bible["convenience_store"]["ref_image"] = "https://cdn/scene.png"
    shot = add_shot(tmp_project, "S001")

    manifest = _url_manifest(max_ref_images=2)
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    req = _req(tmp_project, shot)
    req.bible = bible  # the URLs live on this bible

    provider.submit(req)
    sent = json.loads(transport.requests[0][3])
    # highest-priority survivors delivered: char1 (primary) + scene, NOT char2
    assert sent["image_url"] == ["https://cdn/char1.png", "https://cdn/scene.png"]

    budget = req.params["ref_delivery"]["budget"]
    assert budget["max_images"] == 2
    assert [s["ref"] for s in budget["selected"]] == \
        ["https://cdn/char1.png", "https://cdn/scene.png"]
    assert [o["ref"] for o in budget["omitted"]] == ["https://cdn/char2.png"]
    assert "额外角度" in budget["omitted"][0]["reason"]
    # only 2 images actually delivered
    assert len(req.params["ref_delivery"]["images"]) == 2


def test_delivery_no_budget_is_byte_identical(tmp_project, add_shot, monkeypatch):
    """No manifest budget → all refs sent in resolved order and NO budget block
    on ref_delivery (delivery byte-identical to before item 9)."""
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    bible = tmp_project.load_bible()
    bible["linxia"]["ref_images"] = ["https://cdn/char1.png", "https://cdn/char2.png"]
    bible["convenience_store"]["ref_image"] = "https://cdn/scene.png"
    shot = add_shot(tmp_project, "S001")

    manifest = _url_manifest()  # no limits section at all
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    req = _req(tmp_project, shot)
    req.bible = bible

    provider.submit(req)
    sent = json.loads(transport.requests[0][3])
    assert sent["image_url"] == [
        "https://cdn/char1.png", "https://cdn/char2.png", "https://cdn/scene.png",
    ]
    assert "budget" not in req.params["ref_delivery"]


def test_delivery_single_image_no_budget_unchanged(tmp_project, add_shot, monkeypatch):
    """The common single-ref path stays exactly as pinned by test_refs."""
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    shot = add_shot(tmp_project, "S001",
                    generation={"params": {"image": "https://cdn/hero.png"}})
    manifest = _url_manifest()
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    req = _req(tmp_project, shot, image="https://cdn/hero.png")
    provider.submit(req)
    sent = json.loads(transport.requests[0][3])
    # list shape here is the field cap (max_images=3), not the budget — the
    # point is the value + NO budget block (delivery unchanged by item 9)
    assert sent["image_url"] == ["https://cdn/hero.png"]
    rd = req.params["ref_delivery"]
    assert "budget" not in rd
    assert rd["images"][0]["tier"] == TIER_PARAMS


# ========================================================== cleanliness


@needs_ffmpeg
def test_busy_background_flagged_clean_is_not(tmp_project, add_shot):
    bible = tmp_project.load_bible()
    bible["linxia"]["ref_image"] = "media/refs/busy.png"
    _noise_border(tmp_project, "media/refs/busy.png")
    shot = add_shot(tmp_project, "S001")
    rs = resolve_refs(tmp_project, shot, bible)
    findings = check_refs(tmp_project, shot, rs, bible=bible)
    assert any(f.code == "busy_background" for f in findings)

    # a solid-background character ref is clean
    bible2 = tmp_project.load_bible()
    bible2["linxia"]["ref_image"] = "media/refs/clean.png"
    _solid(tmp_project, "media/refs/clean.png")
    shot2 = add_shot(tmp_project, "S002")
    rs2 = resolve_refs(tmp_project, shot2, bible2)
    findings2 = check_refs(tmp_project, shot2, rs2, bible=bible2)
    assert not any(f.code == "busy_background" for f in findings2)


@needs_ffmpeg
def test_lighting_conflict_across_refs(tmp_project, add_shot):
    _solid(tmp_project, "media/refs/dark.png", color="black", size="512x512")
    _solid(tmp_project, "media/refs/bright.png", color="white", size="512x512")
    shot = add_shot(tmp_project, "S001", generation={"params": {
        "images": ["media/refs/dark.png", "media/refs/bright.png"]}})
    rs = resolve_refs(tmp_project, shot, tmp_project.load_bible())
    findings = check_refs(tmp_project, shot, rs, bible=tmp_project.load_bible())
    conflict = [f for f in findings if f.code == "lighting_conflict"]
    assert conflict and conflict[0].level == "warn"
    assert conflict[0].evidence["brightness_delta"] > 60


@needs_ffmpeg
def test_unclear_scale_low_resolution(tmp_project, add_shot):
    _solid(tmp_project, "media/refs/tiny.png", color="green", size="100x100")
    shot = add_shot(tmp_project, "S001",
                    generation={"params": {"image": "media/refs/tiny.png"}})
    rs = resolve_refs(tmp_project, shot, tmp_project.load_bible())
    findings = check_refs(tmp_project, shot, rs, bible=tmp_project.load_bible())
    scale = [f for f in findings if f.code == "unclear_scale"]
    assert scale and scale[0].evidence["short_side"] == 100


@needs_ffmpeg
def test_extreme_aspect_ratio_flagged(tmp_project, add_shot):
    _solid(tmp_project, "media/refs/wide.png", color="red", size="1600x200")
    shot = add_shot(tmp_project, "S001",
                    generation={"params": {"image": "media/refs/wide.png"}})
    rs = resolve_refs(tmp_project, shot, tmp_project.load_bible())
    findings = check_refs(tmp_project, shot, rs, bible=tmp_project.load_bible())
    assert any(f.code == "unclear_scale" and f.evidence.get("aspect_ratio", 0) > 3
               for f in findings)


# ------------------------------------------------------- vision-required


def _vision_setup(tmp_project, add_shot, monkeypatch):
    """A shot with a scene ref + two character refs; no vision vendor
    configured (empty providers dir)."""
    empty = tmp_project.root / "_empty_providers"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(empty))
    bible = tmp_project.load_bible()
    bible["convenience_store"]["ref_image"] = "media/refs/scene.png"
    bible["linxia"]["ref_images"] = ["media/refs/c1.png", "media/refs/c2.png"]
    for r in ("scene", "c1", "c2"):
        _touch(tmp_project, f"media/refs/{r}.png")
    shot = add_shot(tmp_project, "S001")
    return shot, bible


def test_needs_vision_advisories_fire_without_vendor(tmp_project, add_shot, monkeypatch):
    shot, bible = _vision_setup(tmp_project, add_shot, monkeypatch)
    rs = resolve_refs(tmp_project, shot, bible)
    findings = check_refs(tmp_project, shot, rs, bible=bible)
    codes = {f.code for f in findings if f.level == "needs_vision"}
    assert "scene_has_people" in codes    # scene ref → must check for people
    assert "outfit_conflict" in codes     # 2 character refs → outfit clash check
    for f in findings:
        if f.level == "needs_vision":
            # Round V (§6): the structured needs_vision slot stays, but the prose
            # points at the driving agent's own eyes + the visual-qc-review skill,
            # not a vision vendor — no fake verdict, no vendor-slot messaging.
            assert "需要图像判读" in f.message
            assert "visual-qc-review" in f.message
            assert "需要视觉模型" not in f.message


def test_vision_vendor_runs_when_injected(tmp_project, add_shot, monkeypatch):
    shot, bible = _vision_setup(tmp_project, add_shot, monkeypatch)
    rs = resolve_refs(tmp_project, shot, bible)

    calls = []

    def screener(code, subject, refs):
        calls.append(code)
        if code == "outfit_conflict":
            return RefFinding("warn", code, "服装冲突:两张参考图衣着不同",
                              evidence={"n": len(refs)}, subject=subject)
        return None

    findings = check_refs(tmp_project, shot, rs, bible=bible, vision=screener)
    # the injected vendor RAN (never a heuristic faking it)
    assert "outfit_conflict" in calls and "scene_has_people" in calls
    outfit = [f for f in findings if f.code == "outfit_conflict"]
    assert outfit and outfit[0].level == "warn"  # real vendor verdict, not needs_vision
    # and no needs_vision advisory for a check the vendor handled
    assert not any(f.code == "outfit_conflict" and f.level == "needs_vision"
                   for f in findings)
