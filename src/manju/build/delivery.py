"""AI_IDE_13C — ``manju.delivery-manifest/v1``: one derived, deletable delivery
manifest over the artifacts Manju ALREADY produces.

The batch's single new public schema. It is a PURE DERIVATION — it owns no
truth and drives no build:

    delivery status ....... :func:`exportstatus.deliverables`   (the ONE status owner)
    release readiness ..... :func:`baseline.release_assessment` (07C, consumed verbatim)
    compiled timeline ..... :func:`exportstatus._gather`        (the ONE staleness recompile)
    locale ................ :func:`core.locale.locale_status`   (base-hash / lines owner)
    secret scan ........... ``core.check.SECRET_PATTERNS``       (the ONE token list)
    project revision ...... :func:`shotpackage.project_revision`
    path escape guard ..... :meth:`Project.resolve`
    atomic write .......... :func:`media.ffmpeg.atomic_output`

Hard boundaries it never crosses (contract §1.3-1.5, §15):

- It is NEVER an export INPUT: build/export/cache/resume never read it; deleting
  or hand-editing it changes nothing downstream (tests §13.1.6).
- It creates NO editorial decision: shot selection / order / duration / caption
  text / voice content come only from the existing source/timeline/roundtrip.
- It has NO second export engine, NO second timeline/take resolver, NO auto
  semantic cutdown, NO core vision model, NO auto translation/TTS, NO stored
  credential, NO real upload, and NO parallel NLEHandoff/VariantPlan/
  SegmentDecisionList/LocalizationPackage/PublishReceipt schema.

The manifest states, honestly and separately: what bytes exist, whether they are
technically fresh, whether a human verified them, which NLE media they bind to
(by exact sha256 + frame mapping), and what a platform hand-off would need —
never conflating file-exists with ready, technical-ready with human-approved, or
hand-off-ready with published.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any

from ..core.container import Project, ProjectError
from ..core.hashing import HASH_PREFIX, hash_file, hash_value

SCHEMA = "manju.delivery-manifest/v1"

# ---------------------------------------------------------------- vocabularies

# variant kinds (contract §4)
MASTER = "MASTER"
FORMAT_ONLY = "FORMAT_ONLY"
EDITORIAL_CUTDOWN = "EDITORIAL_CUTDOWN"
LOCALIZED = "LOCALIZED"
PLATFORM_PACKAGE = "PLATFORM_PACKAGE"
_VARIANT_KINDS = {MASTER, FORMAT_ONLY, EDITORIAL_CUTDOWN, LOCALIZED, PLATFORM_PACKAGE}

# artifact states (contract §6.4)
GENERATED = "GENERATED"
TECHNICALLY_VERIFIED = "TECHNICALLY_VERIFIED"
HUMAN_VERIFIED = "HUMAN_VERIFIED"
STALE = "STALE"
MISSING = "MISSING"
INVALID = "INVALID"
BLOCKED = "BLOCKED"

# Only the deliverable kinds Manju REALLY produces map to a role. VTT captions,
# textless / M&E masters and dialogue/music/sfx/full-mix stems are NOT produced
# by the current pipeline, so their roles are declared-but-unpopulated
# (contract §6.3: "只列真实支持/生成的角色"; do not pre-generate empty files).
_ROLE_BY_KIND = {
    "final": "MASTER_VIDEO",
    "proxy": "PROXY_VIDEO",
    "srt": "CAPTIONS_SRT",
    "ass": "CAPTIONS_ASS",
    "otio": "NLE_OTIO",
    "jianying": "NLE_JIANYING",
    "capcut": "NLE_CAPCUT",
    "cover": "POSTER",
    "teaser": "TEASER",
}
_ARTIFACT_ID_BY_KIND = {
    "final": "master:main",
    "proxy": "proxy:main",
    "srt": "captions:srt",
    "ass": "captions:ass",
    "otio": "nle:otio",
    "jianying": "nle:jianying",
    "capcut": "nle:capcut",
    "cover": "poster:main",
    "teaser": "teaser:main",
}
_MIME_BY_KIND = {
    "final": "video/mp4",
    "proxy": "video/mp4",
    "srt": "application/x-subrip",
    "ass": "text/x-ssa",
    "otio": "application/vnd.opentimelineio+json",
    "jianying": "application/json",
    "capcut": "application/json",
    "cover": "image/png",
    "teaser": "video/mp4",
}
# exportstatus Freshness value → manifest artifact state.
_STATE_BY_FRESHNESS = {
    "up_to_date": TECHNICALLY_VERIFIED,
    "verified": HUMAN_VERIFIED,
    "stale": STALE,
    "missing": MISSING,
    "problematic": INVALID,
    "needs_manual": GENERATED,
}
# All roles the schema RECOGNISES (allow-set is additive; §6.3).
KNOWN_ROLES = frozenset({
    "MASTER_VIDEO", "PROXY_VIDEO", "TEXTLESS_MASTER", "M_AND_E_MASTER",
    "CAPTIONS_SRT", "CAPTIONS_ASS", "CAPTIONS_VTT",
    "DIALOGUE_STEM", "MUSIC_STEM", "SFX_STEM", "FULL_MIX",
    "POSTER", "THUMBNAIL", "TEASER",
    "NLE_OTIO", "NLE_JIANYING", "NLE_CAPCUT",
    "LOCALIZATION_SOURCE", "PLATFORM_METADATA", "OTHER_DECLARED",
})
_NLE_ROLES = {"NLE_OTIO", "NLE_JIANYING", "NLE_CAPCUT"}

# deterministic framing strategies the manifest may RECORD from a profile. It
# records them as a variant fact; it never executes pixels and never drives the
# renderer (contract §8.3 — framing is editorial-engine work outside this batch).
_FRAMING_STRATEGIES = {"center_crop", "pad", "blanking_fill", "fit"}


class DeliveryManifestError(RuntimeError):
    """A manifest/bundle could not be produced. ``str()`` carries no secret."""


# ------------------------------------------------------------- small utilities


def _ms_to_frames(ms: int, fps: float) -> int:
    """Whole-frame boundary for an ms offset — integer frames, never float
    seconds as the only boundary (contract §7). Mirrors the exporters'
    ``round(ms*fps/1000)`` convention."""
    try:
        return int(round((ms or 0) * float(fps) / 1000.0))
    except (TypeError, ValueError, ZeroDivisionError):
        return 0


def _derived_id(digest: str) -> str:
    return "derived:" + digest


def _delivery_profiles(config: Any) -> dict[str, dict]:
    """The additive ``delivery_profiles`` map from project.yaml (extra=allow, so
    NO models.py change). Absent → ``{}`` → every request defaults to MASTER,
    byte-identical to a project.yaml that predates this batch (§8.1 / test §13.3.28)."""
    raw = getattr(config, "delivery_profiles", None)
    if raw is None and getattr(config, "model_extra", None):
        raw = config.model_extra.get("delivery_profiles")
    if isinstance(raw, dict):
        return {str(k): (v if isinstance(v, dict) else {}) for k, v in raw.items()}
    return {}


def _norm_kind(value: Any) -> str:
    if not value:
        return MASTER
    up = str(value).strip().upper()
    return up if up in _VARIANT_KINDS else MASTER


# --------------------------------------------------------- timeline semantic id


def timeline_semantic_digest(timeline: Any) -> str | None:
    """Digest over segment IDENTITY / ORDER / SOURCE-IN-OUT / DURATION only —
    the format-only invariant surface (contract §4.2 / §8.2).

    Deliberately EXCLUDES width/height/fps/encoding/frame-layout so a master
    16:9 and a genuine format-only 9:16 built from the same cut share it, and
    INCLUDES exactly the fields a format-only must preserve so dropping/
    reordering/retiming a segment moves it. ``source_out`` is implicit
    (``source_in_ms + duration_ms``): the compiled ``VideoClip`` carries no
    ``source_out_ms`` (audit fact). Order-sensitive (a list, not a set)."""
    if timeline is None:
        return None
    tracks = getattr(timeline, "tracks", None)
    video = list(getattr(tracks, "video", None) or []) if tracks is not None else []
    segments = [
        {
            "shot": getattr(c, "shot", None),
            "take": getattr(c, "take", None),
            "source": getattr(c, "source", None),
            "start_ms": getattr(c, "start_ms", None),
            "duration_ms": getattr(c, "duration_ms", None),
            "source_in_ms": getattr(c, "source_in_ms", 0) or 0,
        }
        for c in video
    ]
    return hash_value({"video_segments": segments})


def check_format_only_invariant(base_digest: str | None,
                                variant_digest: str | None) -> list[dict]:
    """The format-only proof (contract §8.2): the variant's SOURCE timeline
    semantic digest must equal the base master's. A mismatch is a manifest
    DIAGNOSTIC that blocks the format-only labelling — never a build failure
    (addendum ruling 4)."""
    if base_digest is None:
        return [{
            "code": "FORMAT_ONLY_UNVERIFIED",
            "severity": "warning",
            "detail": "no base master timeline digest to compare against — "
                      "cannot prove this is format-only",
        }]
    if base_digest != variant_digest:
        return [{
            "code": "VARIANT_KIND_MISMATCH",
            "severity": "blocking",
            "detail": "segment selection/order/duration differs from the base "
                      "master — this is not format-only",
        }]
    return []


# --------------------------------------------------------------- artifact rows


def _content_key(kind: str, abspath: Path) -> str | None:
    """The SAME content key exportstatus judges freshness against — never a
    second formula (final/proxy: render sidecar; cover/teaser: packaging key)."""
    try:
        if kind in ("final", "proxy"):
            from ..media.render import _read_key_sidecar

            return _read_key_sidecar(abspath)
        if kind in ("cover", "teaser"):
            from ..media.packaging import _read_key

            return _read_key(abspath)
    except Exception:
        return None
    return None


def _artifact_from_row(project: Project, row: Any) -> dict:
    kind = row.kind
    role = _ROLE_BY_KIND.get(kind, "OTHER_DECLARED")
    freshness = row.freshness.value
    state = _STATE_BY_FRESHNESS.get(freshness, GENERATED)
    art: dict[str, Any] = {
        "artifact_id": _ARTIFACT_ID_BY_KIND.get(kind, f"{kind}:main"),
        "role": role,
        "path": row.path,
        "sha256": None,
        "bytes": None,
        "mime": _MIME_BY_KIND.get(kind),
        "content_key": None,
        "source_refs": [],
        "state": state,
        "freshness": freshness,
        "basis": row.basis,
        "verification": {
            "technical": _technical_verdict(freshness),
            "human": "VERIFIED" if freshness == "verified" else "PENDING",
            "evidence_refs": [],
        },
    }
    if row.path:
        try:
            abspath = project.resolve(row.path)
        except ProjectError:
            abspath = None
        if abspath is not None and abspath.exists() and abspath.stat().st_size > 0:
            try:
                art["sha256"] = hash_file(abspath)
                art["bytes"] = abspath.stat().st_size
            except OSError:
                pass
            art["content_key"] = _content_key(kind, abspath)
    if freshness == "verified" and row.verified_by:
        art["verification"]["evidence_refs"].append({
            "kind": "human_verification",
            "actor": row.verified_by,
            "at": row.verified_at,
        })
    return art


def _technical_verdict(freshness: str) -> str:
    if freshness == "up_to_date":
        return "VERIFIED"
    if freshness == "stale":
        return "STALE"
    if freshness == "problematic":
        return "FAILED"
    if freshness == "missing":
        return "MISSING"
    return "PENDING"


# ----------------------------------------------------------------- NLE section


def _nle_section(project: Project, config: Any, timeline: Any,
                 rows_by_kind: dict[str, Any]) -> tuple[dict | None, list[dict]]:
    """Bind the NLE project's media from the COMPILED timeline — scan-free
    (``clip.source`` + :meth:`Project.resolve`, never a directory glob) — and add
    the exact-sha256 + frame-mapping binding the current OTIO/JianYing/CapCut
    adapters genuinely LACK (audit fact: no exporter hashes media). Only added
    bindings/checks; the exporters are not rewritten (contract §7, addendum 6)."""
    diagnostics: list[dict] = []
    if timeline is None:
        return None, diagnostics
    fmt = project_file_kind = None
    project_row = None
    for kind, fmt_name in (("otio", "OTIO"), ("jianying", "JIANYING"), ("capcut", "CAPCUT")):
        row = rows_by_kind.get(kind)
        if row is not None and row.path and row.freshness.value != "missing":
            fmt, project_file_kind, project_row = fmt_name, kind, row
            break
    if fmt is None:
        return None, diagnostics

    fps = float(getattr(timeline, "fps", 0) or getattr(config, "fps", 0) or 24)
    project_file: dict[str, Any] = {"path": project_row.path, "sha256": None}
    try:
        pabs = project.resolve(project_row.path)
        if pabs.exists():
            project_file["sha256"] = hash_file(pabs)
    except (ProjectError, OSError):
        pass

    tracks = getattr(timeline, "tracks", None)
    media: list[dict] = []
    missing_caps: list[str] = []
    for clip in (getattr(tracks, "video", None) or []):
        src = getattr(clip, "source", None)
        asset_sha = None
        media_state = "OK"
        rel = src
        if not src:
            media_state = "MISSING"
        else:
            try:
                cabs = project.resolve(src)
            except ProjectError:
                cabs = None
                media_state = "INVALID"
            if cabs is not None:
                rel = project.relpath(cabs)
                if cabs.exists() and cabs.stat().st_size > 0:
                    try:
                        asset_sha = hash_file(cabs)
                    except OSError:
                        media_state = "UNREADABLE"
                else:
                    media_state = "MISSING"
        if media_state != "OK":
            diagnostics.append({
                "code": "NLE_MEDIA_UNRESOLVED",
                "severity": "blocking",
                "detail": f"NLE clip {getattr(clip, 'shot', '?')}/"
                          f"{getattr(clip, 'take', '?')} media is {media_state}: {src}",
            })
        in_ms = getattr(clip, "source_in_ms", 0) or 0
        dur_ms = getattr(clip, "duration_ms", 0) or 0
        start_ms = getattr(clip, "start_ms", 0) or 0
        media.append({
            "clip_id": f"shot:{getattr(clip, 'shot', '?')}/video:main",
            "shot": getattr(clip, "shot", None),
            "take": getattr(clip, "take", None),
            "asset_sha256": asset_sha,
            "path": rel,
            "source_in_frames": _ms_to_frames(in_ms, fps),
            "source_out_frames": _ms_to_frames(in_ms + dur_ms, fps),
            "timeline_in_frames": _ms_to_frames(start_ms, fps),
            "timeline_out_frames": _ms_to_frames(start_ms + dur_ms, fps),
            "handles": {"head_frames": 0, "tail_frames": 0},
            "media_state": media_state,
        })

    # The exporter row's own freshness carries selected-take/timeline staleness
    # (mtime/lint based). A stale NLE draft means it was written from an older
    # cut than the current selected take (contract §13.2.12).
    draft_freshness = project_row.freshness.value
    if draft_freshness == "stale":
        diagnostics.append({
            "code": "NLE_STALE",
            "severity": "blocking",
            "detail": f"{project_file_kind} draft is older than the current "
                      "timeline/selected take — re-export",
        })

    # Human "opened in the target app" is a SEPARATE axis from adapter roundtrip
    # (contract §7 / §13.2.18): only a draft row marked 已人工确认 (VERIFIED) is
    # human-opened; everything else is PENDING.
    opened = "VERIFIED" if draft_freshness == "verified" else "PENDING"
    adapter_roundtrip = "VERIFIED" if draft_freshness in ("up_to_date", "verified") else (
        "STALE" if draft_freshness == "stale" else "PENDING")

    section = {
        "format": fmt,
        "project_file": project_file,
        "timebase": {"fps_num": int(round(fps)), "fps_den": 1, "drop_frame": False},
        "media": media,
        "captions": [],
        "audio": [],
        "missing_capabilities": missing_caps,
        "verification": {
            "adapter_roundtrip": adapter_roundtrip,
            "opened_in_target_app": opened,
        },
    }
    return section, diagnostics


# -------------------------------------------------------- localization section


def _localization_section(project: Project, locale: str | None,
                          timeline_changed: bool) -> tuple[dict | None, list[dict]]:
    """Consume ``core.locale.locale_status`` verbatim (base-hash / lines owner).
    A stale base hash blocks a LOCALIZED variant (contract §9, addendum 7).
    Never auto-translates, never synthesises voice."""
    diagnostics: list[dict] = []
    if not locale:
        return None, diagnostics
    try:
        from ..core.locale import load_lines, locale_status

        status = locale_status(project, locale)
        body = (status.get("locales") or {}).get(locale) or {}
        lines = load_lines(project, locale)
    except Exception as exc:
        diagnostics.append({
            "code": "LOCALE_UNAVAILABLE",
            "severity": "blocking",
            "detail": "locale status could not be derived: "
                      + " ".join(str(exc).split())[:120],
        })
        return {"locale": locale, "status": "UNAVAILABLE"}, diagnostics

    line_rows = body.get("lines") or []
    stale = [r for r in line_rows if r.get("state") == "翻译过期"]
    missing = [r for r in line_rows if r.get("state") == "missing"]
    if stale:
        loc_status = "STALE"
        diagnostics.append({
            "code": "LOCALE_BASE_HASH_STALE",
            "severity": "blocking",
            "detail": f"{len(stale)} localized line(s) reference an out-of-date "
                      "base text hash — re-translate against the current source",
        })
    elif missing:
        loc_status = "INCOMPLETE"
        diagnostics.append({
            "code": "LOCALE_LINES_MISSING",
            "severity": "blocking",
            "detail": f"{len(missing)} source line(s) have no translation",
        })
    else:
        loc_status = "CURRENT"

    # base_hash keys: stored_base_hash (at-translation) vs base_hash (current).
    stored = sorted({str(v.get("base_hash")) for v in lines.values() if v.get("base_hash")})
    caption_body = body.get("captions") or {}
    captions_ids = ["captions:srt:" + locale] if caption_body.get("path") else []
    if caption_body.get("ass"):
        captions_ids.append("captions:ass:" + locale)
    voice_body = body.get("voice") or {}
    voice_ids = [f"voice:{sid}:{locale}" for sid, st in voice_body.items()
                 if st not in ("", "not_needed")]

    section = {
        "locale": locale,
        "base_text_hash": stored[0] if len(stored) == 1 else stored,
        "status": loc_status,
        "counts": body.get("counts") or {},
        "source_ids": [r.get("shot") for r in line_rows],
        "caption_artifact_ids": captions_ids,
        "voice_artifact_ids": voice_ids,
        "picture_timeline_changed": bool(timeline_changed),
        "verification": {
            "timing": "VERIFIED" if loc_status == "CURRENT" else "PENDING",
            "language_review": "PENDING",  # never auto-approved (§9)
        },
    }
    return section, diagnostics


# ------------------------------------------------------ platform hand-off (§10)


def _scan_credentials(text: str) -> bool:
    """A REAL secret scan (addendum 8) reusing the ONE canonical token list
    (``core.check.SECRET_PATTERNS``) plus the signed-URL query detector — never
    a constant. Returns True if any credential/token/signed-URL is present."""
    if not text:
        return False
    try:
        from ..core.check import SECRET_PATTERNS

        if any(p.search(text) for p in SECRET_PATTERNS):
            return True
    except Exception:
        pass
    try:
        from ..providers.submission import _SECRET_KEY_RE, _URL_QUERY_RE

        for m in _URL_QUERY_RE.finditer(text):
            if _SECRET_KEY_RE.search(m.group(0)):
                return True
    except Exception:
        pass
    return False


def _platform_handoff(project: Project, config: Any, profile: dict, variant_kind: str,
                      artifacts: list[dict], metadata_file: str | None
                      ) -> tuple[dict | None, list[dict]]:
    diagnostics: list[dict] = []
    platform = profile.get("platform")
    if not platform and variant_kind != PLATFORM_PACKAGE:
        return None, diagnostics
    platform = platform or "unspecified"

    default_files = [a["artifact_id"] for a in artifacts
                     if a["role"] in ("MASTER_VIDEO", "POSTER", "CAPTIONS_SRT")
                     and a["state"] not in (MISSING,)]
    files = profile.get("files") or default_files

    meta_rel = None
    credentials_present = False
    mpath = metadata_file or profile.get("metadata_file")
    if mpath:
        try:
            mabs = project.resolve(mpath)
            meta_rel = project.relpath(mabs)
            if mabs.exists():
                credentials_present = _scan_credentials(
                    mabs.read_text(encoding="utf-8", errors="replace"))
        except (ProjectError, OSError):
            meta_rel = str(mpath)

    checks: list[dict] = []
    # Deterministic checks: aspect ratio (profile frame vs project) + disclosure.
    frame = profile.get("frame") or {}
    fw, fh = frame.get("width"), frame.get("height")
    if fw and fh:
        checks.append({
            "code": "ASPECT_RATIO",
            "status": "PASS" if (fw > 0 and fh > 0) else "FAIL",
            "detail": f"{fw}x{fh}",
        })
    max_ms = profile.get("max_duration_ms")
    if max_ms:
        checks.append({"code": "DURATION_LIMIT", "status": "PENDING_HUMAN",
                       "detail": f"limit {max_ms}ms — verify against the master runtime"})
    checks.append({"code": "DISCLOSURE_REVIEW", "status": "PENDING_HUMAN",
                   "detail": "AI-content / sponsorship disclosure needs a human decision"})
    if credentials_present:
        checks.append({"code": "CREDENTIALS_PRESENT", "status": "FAIL",
                       "detail": "the metadata file contains a token / signed URL — "
                                 "remove it; credentials never enter a manifest"})
        diagnostics.append({
            "code": "PLATFORM_CREDENTIAL_LEAK",
            "severity": "blocking",
            "detail": "platform metadata carries a credential/signed-URL; refused",
        })

    # Platform rules can change: a profile must be source-dated or its freshness
    # is UNKNOWN (contract §10, test §13.5.37).
    rules_dated = profile.get("rules_updated") or profile.get("rules_source_dated")
    section = {
        "platform": platform,
        "profile_id": profile.get("id") or profile.get("platform") or platform,
        "files": files,
        "metadata_file": meta_rel,
        "checks": checks,
        "credentials_present": bool(credentials_present),
        "upload_supported": False,   # never (contract §11, §15.7)
        "rules_freshness": "DATED" if rules_dated else "UNKNOWN",
        "rules_updated": rules_dated,
    }
    return section, diagnostics


# ------------------------------------------------------------- release section


def _release_section(project: Project, rows: list, artifacts: list[dict],
                     required_roles: list[str], variant_kind: str,
                     blocking_diags: bool, final_ref: str | None) -> dict:
    """Consume 07C ``release_assessment`` VERBATIM (contract §12). Never
    re-derives run/QC/submission honesty — it reads the assessment's own
    ``ready`` boolean + blocker codes and layers the four separated delivery
    states (contract §6.4)."""
    from . import baseline as BL

    assessment = BL.release_assessment(project, rows=rows)
    assessment_ready = bool(assessment.get("ready"))
    blocker_codes = sorted({b.get("code") for b in (assessment.get("blockers") or [])})
    baseline_status = (assessment.get("baseline") or {}).get("status")
    regression_status = (assessment.get("regression_review") or {}).get("status")

    present_roles = {a["role"]: a for a in artifacts}
    required_ok = True
    for role in required_roles:
        a = present_roles.get(role)
        if a is None or a["state"] not in (TECHNICALLY_VERIFIED, HUMAN_VERIFIED):
            required_ok = False
            break

    technical_ready = assessment_ready and required_ok and not blocking_diags
    editor_approved = technical_ready and any(
        a["role"] in _NLE_ROLES and a["state"] == HUMAN_VERIFIED for a in artifacts)

    return {
        "assessment_ready": assessment_ready,
        "baseline_status": baseline_status,
        "blocker_codes": blocker_codes,
        "regression_status": regression_status,
        "required_roles": list(required_roles),
        "required_roles_satisfied": required_ok,
        "delivery_state": {
            # the four separations the contract forbids collapsing (§6.4)
            "technical_ready": technical_ready,
            "editor_approved": editor_approved,
            "publish_handoff_ready": False,   # set True only by a clean handoff below
            "published": "unknown_not_owned",
        },
    }


# --------------------------------------------------------------- variant build


def _resolve_base_master_digest(project: Project, profile: dict,
                                base_master: Any) -> str | None:
    """The base master's RECORDED source timeline digest for the format-only
    invariant. Accepts a manifest dict, a raw digest string, or looks up a
    materialized master manifest on disk (deterministic reports path)."""
    if isinstance(base_master, dict):
        return (base_master.get("variant") or {}).get("source_timeline_digest")
    if isinstance(base_master, str) and base_master.startswith(HASH_PREFIX):
        return base_master
    base_profile = profile.get("base_profile") or "master"
    path = _manifest_path(project, base_profile)
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            return (doc.get("variant") or {}).get("source_timeline_digest")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _build_variant(project: Project, profile: dict, profile_id: str,
                   timeline: Any, base_master: Any) -> tuple[dict, list[dict]]:
    diagnostics: list[dict] = []
    kind = _norm_kind(profile.get("variant_kind"))
    tl_digest = timeline_semantic_digest(timeline)
    variant: dict[str, Any] = {
        "kind": kind,
        "source_timeline_digest": tl_digest,
        "base_master_manifest_digest": None,
        "locale": profile.get("locale"),
    }

    # Framing is RECORDED as a variant fact, never executed / never drives render
    # (contract §8.3; framing execution SKIPPED_WITH_EVIDENCE this batch).
    frame = profile.get("frame") or {}
    strategy = frame.get("strategy")
    if strategy is not None:
        variant["frame"] = {
            "width": frame.get("width"),
            "height": frame.get("height"),
            "strategy": strategy if strategy in _FRAMING_STRATEGIES else "unknown",
            "applied_by": "declared_only",  # manifest records; renderer is untouched
        }
        if strategy not in _FRAMING_STRATEGIES:
            diagnostics.append({
                "code": "FRAMING_STRATEGY_UNKNOWN",
                "severity": "warning",
                "detail": f"frame.strategy={strategy!r} is not a recognised "
                          "deterministic strategy; recorded but not applied",
            })

    # An external smart-framing artifact is inert until adopted into source
    # (contract §8.3): recorded with path+sha, never drives render.
    ext = profile.get("external_framing")
    if isinstance(ext, dict):
        variant["external_framing"] = {
            "path": ext.get("path"),
            "sha256": ext.get("sha256"),
            "producer": ext.get("producer"),
            "adopted": bool(ext.get("adopted")),
            "drives_render": False,
        }
        if not ext.get("adopted"):
            diagnostics.append({
                "code": "EXTERNAL_FRAMING_NOT_ADOPTED",
                "severity": "warning",
                "detail": "external smart-framing artifact is not adopted into "
                          "source — it is inert and does not drive render",
            })

    if kind == FORMAT_ONLY:
        base_digest = _resolve_base_master_digest(project, profile, base_master)
        variant["base_master_manifest_digest"] = base_digest
        diagnostics += check_format_only_invariant(base_digest, tl_digest)

    elif kind == EDITORIAL_CUTDOWN:
        # A cutdown must reference an explicit, diffable source revision — this
        # batch packages an EXISTING cut, it never invents one (contract §8.4).
        src = profile.get("cutdown_source")
        variant["cutdown_source"] = src
        if not (isinstance(src, dict) and src.get("ref")):
            diagnostics.append({
                "code": "CUTDOWN_SOURCE_REQUIRED",
                "severity": "blocking",
                "detail": "an EDITORIAL_CUTDOWN must reference an explicit source "
                          "revision (timeline revision / approved roundtrip / "
                          "approved proposal / sibling source); none given",
            })

    elif kind == LOCALIZED:
        base_digest = _resolve_base_master_digest(project, profile, base_master)
        variant["base_master_manifest_digest"] = base_digest

    return variant, diagnostics


# ----------------------------------------------------------------- the builder


def build_manifest(project: Project, profile_id: str = "master", *,
                   final_ref: str | None = None, metadata_file: str | None = None,
                   base_master: Any = None) -> dict:
    """Derive the ``manju.delivery-manifest/v1`` for ``profile_id`` — instant,
    read-only, deterministic (NO wall-clock field, every path project-relative,
    no secret). Writes nothing (materialization is a separate, optional step).

    A pure composition over existing services: it never re-derives status,
    readiness, staleness, or take selection."""
    from . import exportstatus as ES
    from .shotpackage import project_revision

    config = ES._safe(lambda: project.load_config())
    profiles = _delivery_profiles(config) if config is not None else {}
    profile = dict(profiles.get(profile_id) or {})
    profile.setdefault("id", profile_id)

    # ONE gather (the single staleness recompile) + the nine status rows from the
    # ONE status owner. No second engine (contract §14, test §13.1.10).
    ctx = ES._gather(project)
    rows = ES.deliverables(project)
    rows_by_kind = {r.kind: r for r in rows}

    variant, variant_diags = _build_variant(project, profile, profile_id,
                                             ctx.timeline, base_master)
    variant_kind = variant["kind"]

    artifacts = [_artifact_from_row(project, r) for r in rows]

    nle, nle_diags = _nle_section(project, config, ctx.timeline, rows_by_kind)

    locale = variant.get("locale") if variant_kind == LOCALIZED else None
    localization, loc_diags = _localization_section(
        project, locale, bool(variant.get("picture_timeline_changed")))

    platform_handoff, ph_diags = _platform_handoff(
        project, config, profile, variant_kind, artifacts, metadata_file)

    diagnostics = variant_diags + nle_diags + loc_diags + ph_diags
    blocking = any(d.get("severity") == "blocking" for d in diagnostics)

    required_roles = _required_roles(profile, variant_kind)
    release = _release_section(project, rows, artifacts, required_roles,
                               variant_kind, blocking, final_ref)

    # publish_handoff_ready: only a technically-ready delivery WITH a credential-
    # free hand-off whose checks carry no FAIL — and even then a PENDING_HUMAN
    # disclosure keeps it false (contract §5, §10, §12).
    if platform_handoff is not None:
        clean = (not platform_handoff["credentials_present"]
                 and all(c["status"] == "PASS" for c in platform_handoff["checks"]))
        release["delivery_state"]["publish_handoff_ready"] = bool(
            release["delivery_state"]["technical_ready"] and clean)

    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "manifest_id": None,               # filled from the digest below
        "project_revision": ES._safe(lambda: project_revision(project)),
        "profile_id": profile_id,
        "variant": variant,
        "release": release,
        "artifacts": artifacts,
        "nle": nle,
        "localization": localization,
        "platform_handoff": platform_handoff,
        "checksums": None,                 # set when a bundle is written
        "diagnostics": diagnostics,
    }
    digest = _manifest_digest(manifest)
    manifest["manifest_digest"] = digest
    manifest["manifest_id"] = _derived_id(digest)
    return manifest


def _required_roles(profile: dict, variant_kind: str) -> list[str]:
    raw = profile.get("required_roles")
    if isinstance(raw, list) and raw:
        return [str(r).upper() for r in raw]
    # sensible default: a master delivery must at least have the master video.
    return ["MASTER_VIDEO"]


# --------------------------------------------------------------- deterministic


def _manifest_digest(manifest: dict) -> str:
    """The manifest digest (contract §6.5). INCLUDES project/source/timeline/
    profile/locale digests, artifact path/role/hash/bytes/content-key, NLE media
    bindings, verification evidence refs and diagnostics semantic fields.
    EXCLUDES absolute paths, current time, zip mtime, tokens/signed URLs, UI
    order/text and operator username (all naturally absent here — the manifest
    carries no wall-clock field and no absolute path)."""
    v = manifest["variant"]
    core: dict[str, Any] = {
        "schema": manifest["schema"],
        "project_revision": manifest["project_revision"],
        "profile_id": manifest["profile_id"],
        "variant": {
            "kind": v.get("kind"),
            "source_timeline_digest": v.get("source_timeline_digest"),
            "base_master_manifest_digest": v.get("base_master_manifest_digest"),
            "locale": v.get("locale"),
            "frame": v.get("frame"),
            "cutdown_source": v.get("cutdown_source"),
            "external_framing": v.get("external_framing"),
        },
        "artifacts": [
            {
                "artifact_id": a["artifact_id"],
                "role": a["role"],
                "path": a["path"],
                "sha256": a["sha256"],
                "bytes": a["bytes"],
                "content_key": a["content_key"],
                "source_refs": a["source_refs"],
                "state": a["state"],
                "verification": {
                    "technical": a["verification"]["technical"],
                    "human": a["verification"]["human"],
                    "evidence_refs": a["verification"]["evidence_refs"],
                },
            }
            for a in manifest["artifacts"]
        ],
        "nle": _nle_digest_fields(manifest["nle"]),
        "localization": manifest["localization"],
        "platform_handoff": _handoff_digest_fields(manifest["platform_handoff"]),
        "release": manifest["release"],
        "diagnostics": sorted(
            (d.get("code"), d.get("severity")) for d in manifest["diagnostics"]),
    }
    return hash_value(core)


def _nle_digest_fields(nle: dict | None) -> dict | None:
    if nle is None:
        return None
    return {
        "format": nle["format"],
        "project_file": nle["project_file"],
        "timebase": nle["timebase"],
        "media": [
            {k: m[k] for k in ("clip_id", "asset_sha256", "path",
                               "source_in_frames", "source_out_frames",
                               "timeline_in_frames", "timeline_out_frames",
                               "handles", "media_state")}
            for m in nle["media"]
        ],
        "verification": nle["verification"],
    }


def _handoff_digest_fields(ph: dict | None) -> dict | None:
    if ph is None:
        return None
    # NEVER the metadata bytes — only its project-relative ref and the check codes.
    return {
        "platform": ph["platform"],
        "files": ph["files"],
        "metadata_file": ph["metadata_file"],
        "checks": [(c["code"], c["status"]) for c in ph["checks"]],
        "credentials_present": ph["credentials_present"],
        "upload_supported": ph["upload_supported"],
        "rules_freshness": ph["rules_freshness"],
    }


# ------------------------------------------------------------- materialization


def _manifest_path(project: Project, profile_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", profile_id) or "master"
    return project.reports_dir / "delivery" / f"{safe}.delivery-manifest.json"


def materialize_manifest(project: Project, manifest: dict, *,
                         output: Path | None = None) -> Path:
    """Atomically write the manifest to the existing reports area (optional —
    build/export never read it). Same inputs → semantically identical file
    (deterministic). Deleting or hand-editing it is provably inert (test)."""
    from ..core.yamlio import atomic_write_text

    dest = output if output is not None else _manifest_path(project, manifest["profile_id"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(dest, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return dest


# ---------------------------------------------------------------- bundle (§11)


# The audit found the project pack/unpack zip guards are INLINE in cli.py (not an
# importable module). We therefore reuse the importable PRIMITIVES that back them
# — Project.resolve (the canonical path-escape guard, container.py) and
# media.ffmpeg.atomic_output (temp+replace, failure leaves the old output intact)
# — and enforce the §11.3 guards here (stable order, no `..`/absolute/symlink,
# duplicate-entry rejection, SHA256SUMS over exact bytes, atomic write). These
# guards are MORE complete than pack's (which has no dup guard and is not atomic).
_ZIP_UNSAFE = re.compile(r"(^/)|(^[A-Za-z]:)|(\\)|(^\.\.$)")


def _bundle_members(project: Project, manifest: dict) -> list[tuple[str, Path]]:
    """Only the files the manifest REGISTERED — master/proxy/captions/audio
    stems (if any)/poster/teaser/NLE project/platform metadata + the manifest
    itself (contract §11.2). Never the whole project, keys, runtime DB, provider
    config or unrelated takes."""
    members: list[tuple[str, Path]] = []
    seen: set[str] = set()

    def _add(arcname: str, abspath: Path) -> None:
        if arcname in seen:
            raise DeliveryManifestError(f"duplicate bundle entry: {arcname}")
        if _ZIP_UNSAFE.search(arcname) or ".." in Path(arcname).parts:
            raise DeliveryManifestError(f"unsafe bundle entry name: {arcname}")
        seen.add(arcname)
        members.append((arcname, abspath))

    for a in manifest["artifacts"]:
        if not a["path"] or a["state"] == MISSING:
            continue
        try:
            abspath = project.resolve(a["path"])  # rejects escapes above root
        except ProjectError as exc:
            raise DeliveryManifestError(str(exc)) from exc
        if abspath.is_symlink():
            raise DeliveryManifestError(f"refusing symlink in bundle: {a['path']}")
        if abspath.exists():
            _add(a["path"], abspath)
    nle = manifest.get("nle")
    if nle and nle.get("project_file", {}).get("path"):
        pf = nle["project_file"]["path"]
        try:
            abspath = project.resolve(pf)
            if abspath.exists() and not abspath.is_symlink():
                _add(pf, abspath)
        except ProjectError:
            pass
    ph = manifest.get("platform_handoff")
    if ph and ph.get("metadata_file"):
        try:
            abspath = project.resolve(ph["metadata_file"])
            if abspath.exists() and not abspath.is_symlink():
                _add(ph["metadata_file"], abspath)
        except ProjectError:
            pass
    members.sort(key=lambda t: t[0])  # stable entry order
    return members


def _sha256sums(members: list[tuple[str, Path]]) -> str:
    """SHA256SUMS over the EXACT bytes of every packed file (no such writer
    existed in the repo — this is the first). Stable order, project-relative
    names, ``sha256:``-stripped hex to match the coreutils format."""
    lines = []
    for arcname, abspath in members:
        digest = hash_file(abspath)[len(HASH_PREFIX):]
        lines.append(f"{digest}  {arcname}")
    return "\n".join(lines) + ("\n" if lines else "")


def write_bundle(project: Project, manifest: dict, *, output: Path | None = None
                 ) -> tuple[Path, dict]:
    """Write a delivery bundle ZIP of exactly the manifest-registered files plus
    a SHA256SUMS and the manifest JSON. Reproducible entry set + checksums;
    atomic (a failure leaves any prior bundle intact). Returns (path, manifest')
    where manifest' carries the ``checksums`` binding.

    Distinct from ``manju pack`` (whole-project archive) — this is only the
    NLE/platform-facing delivery files (contract §11.3)."""
    from ..media.ffmpeg import atomic_output

    members = _bundle_members(project, manifest)
    sums_text = _sha256sums(members)

    # bind the checksums file into the manifest BEFORE embedding it, so the
    # embedded manifest matches what the bundle contains.
    manifest = dict(manifest)
    manifest["checksums"] = {
        "path": "SHA256SUMS",
        "sha256": hash_value({"sha256sums": sums_text}),
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    dest = output if output is not None else (
        project.exports_dir / "delivery" / f"{_safe_name(manifest['profile_id'])}.bundle.zip")
    dest.parent.mkdir(parents=True, exist_ok=True)

    # atomic: write to a sibling temp, os.replace on clean completion; on any
    # failure the temp is unlinked and an existing bundle is left untouched.
    with atomic_output(dest) as tmp:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            # normalized, fixed archive timestamp for reproducibility (not now()).
            for arcname, abspath in members:
                info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, abspath.read_bytes())
            for arcname, data in (("SHA256SUMS", sums_text.encode("utf-8")),
                                  ("delivery-manifest.json", manifest_bytes)):
                info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, data)
    return dest, manifest


def _safe_name(profile_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", profile_id) or "master"
