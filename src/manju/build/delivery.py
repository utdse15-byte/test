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

import hashlib
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
# AI_IDE_18 WP7 (addendum ruling 8): the audio-master + WebVTT kinds the export
# centre now emits are mapped onto the roles 13C ALREADY declared in KNOWN_ROLES
# (TEXTLESS/M_AND_E/CAPTIONS_VTT/*_STEM/FULL_MIX) but had honestly left
# unpopulated. Adding the mapping is what turns those declared roles real — the
# manifest picks the rows up through the SAME row→role scan (no new engine).
_ROLE_BY_KIND = {
    "final": "MASTER_VIDEO",
    "proxy": "PROXY_VIDEO",
    "srt": "CAPTIONS_SRT",
    "ass": "CAPTIONS_ASS",
    "vtt": "CAPTIONS_VTT",
    "otio": "NLE_OTIO",
    "jianying": "NLE_JIANYING",
    "capcut": "NLE_CAPCUT",
    "cover": "POSTER",
    "teaser": "TEASER",
    # CLOSEOUT C5 ruling 2 honest audio-master taxonomy (kinds map onto the RAW_*
    # roles the masters renderer now emits). ``full_mix`` is kept as a back-compat
    # alias onto RAW_STEM_SUM so an OLD on-disk index never loses its role.
    "dialogue_stem": "RAW_DIALOGUE_STEM",
    "music_stem": "RAW_MUSIC_STEM",
    "sfx_stem": "RAW_SFX_STEM",
    "ambient_stem": "RAW_AMBIENT_STEM",
    "stem_sum": "RAW_STEM_SUM",
    "full_mix": "RAW_STEM_SUM",
    "mne": "M_AND_E_BUS_EXCLUSION_MASTER",
}
_ARTIFACT_ID_BY_KIND = {
    "final": "master:main",
    "proxy": "proxy:main",
    "srt": "captions:srt",
    "ass": "captions:ass",
    "vtt": "captions:vtt",
    "otio": "nle:otio",
    "jianying": "nle:jianying",
    "capcut": "nle:capcut",
    "cover": "poster:main",
    "teaser": "teaser:main",
    "dialogue_stem": "audio:dialogue_stem",
    "music_stem": "audio:music_stem",
    "sfx_stem": "audio:sfx_stem",
    "ambient_stem": "audio:ambient_stem",
    "stem_sum": "audio:stem_sum",
    "full_mix": "audio:stem_sum",
    "mne": "audio:mne_bus_exclusion_master",
}
_MIME_BY_KIND = {
    "final": "video/mp4",
    "proxy": "video/mp4",
    "srt": "application/x-subrip",
    "ass": "text/x-ssa",
    "vtt": "text/vtt",
    "otio": "application/vnd.opentimelineio+json",
    "jianying": "application/json",
    "capcut": "application/json",
    "cover": "image/png",
    "teaser": "video/mp4",
    "dialogue_stem": "audio/wav",
    "music_stem": "audio/wav",
    "sfx_stem": "audio/wav",
    "ambient_stem": "audio/wav",
    "stem_sum": "audio/wav",
    "full_mix": "audio/wav",
    "mne": "audio/wav",
}
# roles whose extra technical facts (loudness, bus source hashes, clip
# accounting, level safety, M&E claim) live in the exports/masters index and are
# folded onto the manifest artifact row (CLOSEOUT C5 honest taxonomy; the old
# names are kept so a stale on-disk index still folds).
_MASTERS_ROLES = frozenset({
    "RAW_DIALOGUE_STEM", "RAW_MUSIC_STEM", "RAW_SFX_STEM", "RAW_AMBIENT_STEM",
    "RAW_STEM_SUM", "M_AND_E_BUS_EXCLUSION_MASTER",
    "DIALOGUE_STEM", "MUSIC_STEM", "SFX_STEM", "FULL_MIX", "M_AND_E_MASTER"})
# exportstatus Freshness value → manifest artifact state.
_STATE_BY_FRESHNESS = {
    "up_to_date": TECHNICALLY_VERIFIED,
    "verified": HUMAN_VERIFIED,
    "stale": STALE,
    "missing": MISSING,
    "problematic": INVALID,
    "needs_manual": GENERATED,
}
# All roles the schema RECOGNISES (allow-set is additive; §6.3). The CLOSEOUT C5
# honest audio-master names are added; the OLD names stay recognised so platform
# profiles / calibration corpora referencing them keep validating (no role loss).
KNOWN_ROLES = frozenset({
    "MASTER_VIDEO", "PROXY_VIDEO", "TEXTLESS_MASTER", "M_AND_E_MASTER",
    "CAPTIONS_SRT", "CAPTIONS_ASS", "CAPTIONS_VTT",
    "DIALOGUE_STEM", "MUSIC_STEM", "SFX_STEM", "FULL_MIX",
    "RAW_DIALOGUE_STEM", "RAW_MUSIC_STEM", "RAW_SFX_STEM", "RAW_AMBIENT_STEM",
    "RAW_STEM_SUM", "M_AND_E_BUS_EXCLUSION_MASTER",
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
    """ABSENT variant_kind → MASTER (old-profile compat, pinned). An EXPLICIT
    unknown kind is an error — hardening WP5 7.6 (claim 15): it used to
    silently normalize to MASTER, hiding a typo'd/unsupported variant claim."""
    if not value:
        return MASTER
    up = str(value).strip().upper()
    if up not in _VARIANT_KINDS:
        raise DeliveryManifestError(
            f"unknown variant_kind {value!r} — declared kinds are "
            f"{sorted(_VARIANT_KINDS)} (absent = MASTER)")
    return up


# --------------------------------------------------------- timeline semantic id


def timeline_semantic_digest(timeline: Any) -> str | None:
    """Digest over the compiled timeline's FULL narrative semantics — the
    format-only invariant surface (contract §4.2 / §8.2; hardening WP5 7.4).

    INCLUDES every narrative axis a format-only variant must preserve:
    video segment identity/selection/order/source-in-out/duration; audio cues
    on all four tracks (voice/music/sfx/ambient — identity/timing/source plus
    the audible shape: offset/loop/gain/fades/ducking); subtitle SOURCE
    text/owner-id/timing; overlay identity/text/timing.

    Deliberately EXCLUDES width/height/fps, encoding, pure caption LAYOUT
    (styles live outside CaptionLine), overlay burn geometry (corner/size/
    margin/opacity/template) and wall clock — so a master 16:9 and a genuine
    format-only 9:16 built from the same cut share it, while ANY narrative
    change (dropped segment, moved music cue, edited subtitle line) moves it.

    ``source_out`` is implicit (``source_in_ms + duration_ms``): the compiled
    ``VideoClip`` carries no ``source_out_ms`` (audit fact). Order-sensitive
    (lists, not sets). Hardening claim 12: the 13C digest hashed video
    segments ONLY — an audio/subtitle change on the same cut did not move it."""
    if timeline is None:
        return None
    tracks = getattr(timeline, "tracks", None)

    def _clips(track: str) -> list:
        return list(getattr(tracks, track, None) or []) if tracks is not None else []

    segments = [
        {
            "shot": getattr(c, "shot", None),
            "take": getattr(c, "take", None),
            "source": getattr(c, "source", None),
            "start_ms": getattr(c, "start_ms", None),
            "duration_ms": getattr(c, "duration_ms", None),
            "source_in_ms": getattr(c, "source_in_ms", 0) or 0,
        }
        for c in _clips("video")
    ]
    audio = {
        track: [
            {
                "source": getattr(c, "source", None),
                "start_ms": getattr(c, "start_ms", None),
                "duration_ms": getattr(c, "duration_ms", None),
                "start_offset_ms": getattr(c, "start_offset_ms", 0) or 0,
                "loop": bool(getattr(c, "loop", False)),
                "gain_db": getattr(c, "gain_db", 0.0) or 0.0,
                "ducking": bool(getattr(c, "ducking", False)),
                "fade_in_ms": getattr(c, "fade_in_ms", 0) or 0,
                "fade_out_ms": getattr(c, "fade_out_ms", 0) or 0,
            }
            for c in _clips(track)
        ]
        for track in ("voice", "music", "sfx", "ambient")
    }
    captions = [
        {
            "text": getattr(c, "text", None),
            "speaker": getattr(c, "speaker", "") or "",
            "shot": getattr(c, "shot", "") or "",
            "start_ms": getattr(c, "start_ms", None),
            "end_ms": getattr(c, "end_ms", None),
        }
        for c in _clips("captions")
    ]
    overlays = [
        {
            # identity + text + timing only — corner/size_pct/margin_pct/
            # opacity/template are burn LAYOUT, excluded per contract 7.4.
            "kind": getattr(c, "kind", None),
            "subkind": getattr(c, "subkind", "") or "",
            "text": getattr(c, "text", "") or "",
            "source": getattr(c, "source", "") or "",
            "start_ms": getattr(c, "start_ms", None),
            "duration_ms": getattr(c, "duration_ms", None),
        }
        for c in _clips("overlay")
    ]
    return hash_value({
        "video_segments": segments,
        "audio_tracks": audio,
        "captions": captions,
        "overlays": overlays,
    })


def check_format_only_invariant(base_digest: str | None,
                                variant_digest: str | None) -> list[dict]:
    """The format-only proof (contract §8.2): the variant's SOURCE timeline
    semantic digest must equal the base master's. A mismatch is a manifest
    DIAGNOSTIC that blocks the format-only labelling — never a build failure
    (addendum ruling 4).

    Hardening WP5 7.6 (claim 15/33): a FORMAT_ONLY variant whose base identity
    cannot be derived BLOCKS — it used to be a warning (FORMAT_ONLY_UNVERIFIED),
    which let an unprovable format-only claim ride as merely advisory."""
    if base_digest is None:
        return [{
            "code": "BASE_IDENTITY_MISSING",
            "severity": "blocking",
            "detail": "no base master timeline digest could be derived — a "
                      "format-only claim without a base identity is unprovable",
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


def _masters_facts(project: Project) -> dict[str, dict]:
    """AI_IDE_18 WP7: the per-role loudness / bus-source facts recorded by the
    ``exports/masters`` index, keyed by role. Empty when no masters exist — the
    manifest row then simply carries no acoustic facts (honest)."""
    try:
        from ..media.masters import load_index

        index = load_index(project)
    except Exception:
        index = None
    if not index:
        return {}
    return {a["role"]: a for a in index.get("artifacts", []) if a.get("role")}


def _artifact_from_row(project: Project, row: Any, masters: dict[str, dict] | None = None) -> dict:
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
    # AI_IDE_18 WP7: fold the recorded acoustic facts onto the stem/mix/M&E row
    # so the manifest binds source/audio-input hashes + measured loudness (never
    # a target — measurement only, contract §10).
    if role in _MASTERS_ROLES and masters:
        fact = masters.get(role)
        if fact:
            art["source_refs"] = list(fact.get("source_refs") or [])
            art["audio"] = {
                "loudness": fact.get("loudness"),
                "sample_rate": fact.get("sample_rate"),
                "channels": fact.get("channels"),
                "duration_ms": fact.get("duration_ms"),
                "buses": fact.get("buses"),
                "excludes_dialogue": fact.get("excludes_dialogue"),
                "excludes_voice_bus": fact.get("excludes_voice_bus"),
                "mne_claim": fact.get("mne_claim"),          # bus_exclusion, never content
                "status": fact.get("status"),
                "blocked": fact.get("blocked"),
                "dropped_clips": fact.get("dropped_clips") or [],
                "level_safety": fact.get("level_safety"),
            }
            # CLOSEOUT C5 ruling 1: a master with a dropped expected source can
            # never be 'technically verified' — digital silence never satisfies
            # verification. Downgrade the state and fail the technical axis.
            if fact.get("blocked"):
                art["state"] = BLOCKED
                art["verification"]["technical"] = "FAILED"
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
    # FINAL_ACCEPTANCE F5: a project-file deliverable the manifest POINTS AT but
    # cannot vouch for is a blocking diagnostic, never a silent None. Scope: a
    # REGULAR FILE whose bytes cannot be hashed, or a path that no longer
    # resolves/exists. A directory-based draft (JianYing/CapCut native folders)
    # has no single-file byte identity by construction — its binding rides the
    # per-asset sha256 list + the human opened-in-target-app verification axis,
    # exactly as before.
    unverifiable_detail: str | None = None
    try:
        pabs = project.resolve(project_row.path)
    except ProjectError:
        pabs = None
        unverifiable_detail = "its path does not resolve inside the project"
    if pabs is not None:
        if pabs.is_file():
            try:
                project_file["sha256"] = hash_file(pabs)
            except OSError:
                unverifiable_detail = "its bytes could not be hashed"
        elif not pabs.exists():
            unverifiable_detail = "it no longer exists on disk"
    if unverifiable_detail is not None:
        diagnostics.append({
            "code": "NLE_PROJECT_UNVERIFIABLE",
            "severity": "blocking",
            "detail": f"NLE project file {project_row.path} is registered as a "
                      f"deliverable but {unverifiable_detail} — the binding "
                      "cannot be proven (fail closed)",
        })

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
            # hardening WP5 7.5 (claim 13): an absolute / out-of-project /
            # unresolvable metadata path used to be echoed VERBATIM into the
            # manifest (an absolute-path leak). Refuse with a blocking
            # diagnostic and never emit any fragment of the offending path.
            meta_rel = None
            diagnostics.append({
                "code": "PLATFORM_METADATA_PATH_INVALID",
                "severity": "blocking",
                "detail": "the platform metadata path is absolute, escapes the "
                          "project, or cannot be resolved — use a project-"
                          "relative path (the offending value is not echoed)",
            })

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
                     blocking_diags: bool) -> dict:
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


# base-profile recursion ceiling: variant→master chains are 1 deep in practice;
# anything deeper than this is a mis-configured profile graph, not a delivery.
_MAX_BASE_CHAIN = 8


def _resolve_base_master_digest(project: Project, profile: dict, base_master: Any,
                                *, profile_id: str, chain: frozenset[str]
                                ) -> tuple[str | None, list[dict]]:
    """The base's source timeline digest for the format-only/localized identity.

    Hardening WP5 7.3 (claim 11, CONFIRMED): the 13C version FELL BACK TO
    READING the materialized ``reports/delivery/*.delivery-manifest.json`` —
    the exact "materialized manifest becomes an input" violation (hand-editing
    that report shifted the variant's base identity). That disk-read branch is
    DELETED. Base identity now comes ONLY from:

    (a) the explicit in-memory derivation/digest passed by the caller
        (``base_master`` dict or ``sha256:...`` string), or
    (b) re-deriving the base profile's manifest IN-PROCESS (recursive
        :func:`build_manifest` guarded against self/cyclic/over-deep base
        chains — a cycle is a blocking diagnostic, never a RecursionError).

    Hand-editing or deleting any materialized report is provably inert."""
    if isinstance(base_master, dict):
        return (base_master.get("variant") or {}).get("source_timeline_digest"), []
    if isinstance(base_master, str) and base_master.startswith(HASH_PREFIX):
        return base_master, []

    base_profile = str(profile.get("base_profile") or "master")
    if base_profile == profile_id or base_profile in chain or len(chain) >= _MAX_BASE_CHAIN:
        return None, [{
            "code": "BASE_PROFILE_CYCLE",
            "severity": "blocking",
            "detail": f"base_profile chain cycles or exceeds depth {_MAX_BASE_CHAIN} "
                      f"at {base_profile!r} — a variant's base must resolve to a "
                      "master, not back into itself",
        }]
    try:
        base_manifest = build_manifest(project, base_profile,
                                       _base_chain=chain | {profile_id})
    except DeliveryManifestError as exc:
        return None, [{
            "code": "BASE_PROFILE_INVALID",
            "severity": "blocking",
            "detail": f"base_profile {base_profile!r} could not be derived: "
                      + " ".join(str(exc).split())[:160],
        }]
    # A base whose OWN base chain is broken proves nothing — propagate the
    # cycle/invalid verdict instead of silently adopting its timeline digest
    # (the deeper manifest is discarded; its diagnostics must not vanish).
    base_codes = {d.get("code") for d in (base_manifest.get("diagnostics") or [])}
    if "BASE_PROFILE_CYCLE" in base_codes:
        return None, [{
            "code": "BASE_PROFILE_CYCLE",
            "severity": "blocking",
            "detail": f"base_profile {base_profile!r} sits on a cyclic base "
                      "chain — a variant's base must resolve to a master",
        }]
    if {"BASE_PROFILE_INVALID", "BASE_IDENTITY_MISSING"} & base_codes:
        return None, [{
            "code": "BASE_PROFILE_INVALID",
            "severity": "blocking",
            "detail": f"base_profile {base_profile!r} has no clean base identity "
                      "of its own — resolve its chain first",
        }]
    return (base_manifest.get("variant") or {}).get("source_timeline_digest"), []


def _build_variant(project: Project, profile: dict, profile_id: str,
                   timeline: Any, base_master: Any,
                   chain: frozenset[str] = frozenset()) -> tuple[dict, list[dict]]:
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
        base_digest, base_diags = _resolve_base_master_digest(
            project, profile, base_master, profile_id=profile_id, chain=chain)
        variant["base_master_manifest_digest"] = base_digest
        diagnostics += base_diags
        # a cycle already blocks with its own diagnostic; only add the
        # invariant/missing-base verdict when base resolution itself was clean.
        if not base_diags:
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
        base_digest, base_diags = _resolve_base_master_digest(
            project, profile, base_master, profile_id=profile_id, chain=chain)
        variant["base_master_manifest_digest"] = base_digest
        diagnostics += base_diags
        if not base_diags and base_digest is None:
            # hardening WP5 7.6: LOCALIZED without a derivable base identity
            # blocks — the locale claim has nothing provable to bind to.
            diagnostics.append({
                "code": "BASE_IDENTITY_MISSING",
                "severity": "blocking",
                "detail": "no base master timeline digest could be derived for "
                          "the LOCALIZED variant — nothing to bind the locale to",
            })

    return variant, diagnostics


# ----------------------------------------------------------------- the builder


def build_manifest(project: Project, profile_id: str = "master", *,
                   metadata_file: str | None = None, base_master: Any = None,
                   _base_chain: frozenset[str] = frozenset()) -> dict:
    """Derive the ``manju.delivery-manifest/v1`` for ``profile_id`` — instant,
    read-only, deterministic (NO wall-clock field, every path project-relative,
    no secret). Writes nothing (materialization is a separate, optional step).

    The manifest targets the CURRENT/NEWEST final only — hardening WP5 7.7
    option B (claim 16): the former ``final_ref`` parameter was accepted but
    ignored, so it is REMOVED rather than half-supported.

    A pure composition over existing services: it never re-derives status,
    readiness, staleness, or take selection. ``_base_chain`` is the private
    base-profile recursion guard (WP5 7.3)."""
    from . import exportstatus as ES
    from .shotpackage import project_revision

    config = ES._safe(lambda: project.load_config())
    profiles = _delivery_profiles(config) if config is not None else {}
    if profile_id != "master" and profile_id not in profiles:
        # hardening WP5 7.6 (claim 15): an EXPLICITLY unknown profile used to
        # silently derive a MASTER manifest. The implicit "master" id stays the
        # old-project compat default (absent delivery_profiles → MASTER).
        raise DeliveryManifestError(
            f"unknown delivery profile {profile_id!r} — declare it under "
            "delivery_profiles in project.yaml (the implicit default is 'master')")
    profile = dict(profiles.get(profile_id) or {})
    profile.setdefault("id", profile_id)

    # ONE gather (the single staleness recompile) + the nine status rows from the
    # ONE status owner. No second engine (contract §14, test §13.1.10).
    ctx = ES._gather(project)
    rows = ES.deliverables(project)
    rows_by_kind = {r.kind: r for r in rows}

    variant, variant_diags = _build_variant(project, profile, profile_id,
                                             ctx.timeline, base_master,
                                             chain=_base_chain)
    variant_kind = variant["kind"]

    masters = _masters_facts(project)
    artifacts = [_artifact_from_row(project, r, masters) for r in rows]

    nle, nle_diags = _nle_section(project, config, ctx.timeline, rows_by_kind)

    # hardening WP5 7.1 (claim 9): the nle.project_file and the NLE artifact
    # row describe the SAME file — a hash disagreement between the two is a
    # binding mismatch, surfaced here (and enforced again at bundle time).
    nle_diags += _nle_binding_check(artifacts, nle)

    locale = variant.get("locale") if variant_kind == LOCALIZED else None
    localization, loc_diags = _localization_section(
        project, locale, bool(variant.get("picture_timeline_changed")))

    platform_handoff, ph_diags = _platform_handoff(
        project, config, profile, variant_kind, artifacts, metadata_file)

    # hardening WP5 7.5 (claim 14): platform metadata is a FIRST-CLASS artifact
    # row (sha256/bytes/mime), so its exact bytes join manifest_digest — 13C
    # deliberately excluded them; POST_COMPLETION_HARDENING reverses that.
    # Credential CONTENT still never enters the output (boolean + diagnostics).
    artifacts += _metadata_artifact(project, platform_handoff)

    diagnostics = variant_diags + nle_diags + loc_diags + ph_diags
    blocking = any(d.get("severity") == "blocking" for d in diagnostics)

    required_roles = _required_roles(profile, variant_kind)
    release = _release_section(project, rows, artifacts, required_roles,
                               variant_kind, blocking)

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


def _nle_binding_check(artifacts: list[dict], nle: dict | None) -> list[dict]:
    """WP5 7.1: when artifacts[] and nle.project_file name the same path, their
    recorded hashes must agree — a disagreement is a blocking
    NLE_ARTIFACT_BINDING_MISMATCH, never a silent divergence."""
    if not nle:
        return []
    pf = nle.get("project_file") or {}
    path, sha = pf.get("path"), pf.get("sha256")
    if not path or not sha:
        return []
    for a in artifacts:
        if a.get("path") == path and a.get("sha256") and a["sha256"] != sha:
            return [{
                "code": "NLE_ARTIFACT_BINDING_MISMATCH",
                "severity": "blocking",
                "detail": "the NLE project file hash disagrees between the "
                          "artifact row and nle.project_file for the same path — "
                          "the file changed mid-derivation or the manifest was edited",
            }]
    return []


def _metadata_artifact(project: Project, platform_handoff: dict | None) -> list[dict]:
    """WP5 7.5 (claim 14): the platform metadata file as a first-class artifact
    row — exact sha256/bytes/mime — so manifest_digest covers its bytes."""
    if not platform_handoff or not platform_handoff.get("metadata_file"):
        return []
    rel = platform_handoff["metadata_file"]
    try:
        abspath = project.resolve(rel)
    except ProjectError:
        return []  # _platform_handoff already refused + diagnosed invalid paths
    if not abspath.exists():
        state, sha, size = MISSING, None, None
    else:
        try:
            sha = hash_file(abspath)
            size = abspath.stat().st_size
            state = GENERATED  # user-provided; no staleness machinery owns it
        except OSError:
            state, sha, size = INVALID, None, None
    return [{
        "artifact_id": "platform:metadata",
        "role": "PLATFORM_METADATA",
        "path": rel,
        "sha256": sha,
        "bytes": size,
        "mime": "application/json" if rel.endswith(".json") else "text/plain",
        "content_key": None,
        "source_refs": [],
        "state": state,
        "freshness": None,
        "basis": "user-provided platform metadata (bytes bound by sha256)",
        "verification": {"technical": "PENDING", "human": "PENDING",
                         "evidence_refs": []},
    }]


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

    sha_by_arcname: dict[str, str | None] = {}
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
            sha_by_arcname[a["path"]] = a.get("sha256")

    # hardening WP5 7.1 (claim 9): nle.project_file names the SAME file the NLE
    # artifact row registered — one logical file is packed ONCE. A recorded-hash
    # disagreement is a binding mismatch, never an uncaught duplicate error.
    nle = manifest.get("nle")
    if nle and nle.get("project_file", {}).get("path"):
        pf = nle["project_file"]["path"]
        pf_sha = nle["project_file"].get("sha256")
        if pf in seen:
            registered = sha_by_arcname.get(pf)
            if pf_sha and registered and pf_sha != registered:
                raise DeliveryManifestError(
                    "NLE_ARTIFACT_BINDING_MISMATCH: nle.project_file and the "
                    f"registered artifact disagree on the hash of {pf}")
            # same path + same identity → already packed once; nothing to add.
        else:
            try:
                abspath = project.resolve(pf)
                if abspath.exists() and not abspath.is_symlink():
                    _add(pf, abspath)
                    sha_by_arcname[pf] = pf_sha
            except ProjectError:
                pass
    # platform metadata rides its artifact row (PLATFORM_METADATA) since the
    # hardening; the handoff pointer only backfills manifests that lack the row.
    ph = manifest.get("platform_handoff")
    if ph and ph.get("metadata_file") and ph["metadata_file"] not in seen:
        try:
            abspath = project.resolve(ph["metadata_file"])
            if abspath.exists() and not abspath.is_symlink():
                _add(ph["metadata_file"], abspath)
        except ProjectError:
            pass
    members.sort(key=lambda t: t[0])  # stable entry order
    return members


def _sha256sums_from(streamed: dict[str, str],
                     members: list[tuple[str, Path]]) -> str:
    """SHA256SUMS generated FROM THE STREAMED DIGESTS (FINAL_ACCEPTANCE F5) —
    never a second disk read, so the checksums describe exactly the bytes that
    entered the ZIP. Stable order, project-relative names, ``sha256:``-stripped
    hex to match the coreutils format."""
    lines = [f"{streamed[arcname][len(HASH_PREFIX):]}  {arcname}"
             for arcname, _abspath in members]
    return "\n".join(lines) + ("\n" if lines else "")


def _expected_bindings(manifest: dict,
                       members: list[tuple[str, Path]]
                       ) -> dict[str, tuple[str, int | None]]:
    """The manifest's recorded (sha256, bytes) per member arcname — the CAS
    reference the streamed bytes are verified against (WP5 7.2 / F5). A member
    the manifest carries NO hash for refuses up front: nothing could prove its
    bytes. Structural only — reads no file bytes."""
    recorded: dict[str, tuple[str | None, int | None]] = {}
    for a in manifest.get("artifacts") or []:
        if a.get("path"):
            recorded[a["path"]] = (a.get("sha256"), a.get("bytes"))
    nle = manifest.get("nle") or {}
    pf = (nle.get("project_file") or {})
    if pf.get("path") and pf["path"] not in recorded:
        recorded[pf["path"]] = (pf.get("sha256"), None)

    expected: dict[str, tuple[str, int | None]] = {}
    for arcname, _abspath in members:
        want_sha, want_bytes = recorded.get(arcname, (None, None))
        if not want_sha:
            raise DeliveryManifestError(
                "DELIVERY_ARTIFACT_CHANGED_AFTER_MANIFEST: the manifest records "
                f"no hash for {arcname} — cannot prove the bytes are the ones "
                "the manifest described (fail closed)")
        expected[arcname] = (want_sha, want_bytes)
    return expected


def write_bundle(project: Project, manifest: dict, *, output: Path | None = None
                 ) -> tuple[Path, dict]:
    """Write a delivery bundle ZIP of exactly the manifest-registered files plus
    a SHA256SUMS and the manifest JSON. Reproducible entry set + checksums;
    atomic (a failure leaves any prior bundle intact). Returns (path, manifest')
    where manifest' carries the ``checksums`` binding.

    FINAL_ACCEPTANCE F5 (stream-hash-once): each member's bytes are read
    exactly ONCE, hashed WHILE they stream into the ZIP in bounded chunks
    (finals can be GBs — nothing is buffered whole), and the streamed digest is
    compared to the manifest's recorded sha256/size. Any drift refuses with
    DELIVERY_ARTIFACT_CHANGED_AFTER_MANIFEST, the temp is discarded and any
    prior bundle stays intact. SHA256SUMS is generated FROM the streamed
    digests and the embedded manifest binds that same text — checksums,
    manifest and ZIP therefore consume ONE byte snapshot by construction; the
    old validate→re-hash→re-read pipeline left two windows for a concurrent
    writer to slip changed bytes into the archive.

    Distinct from ``manju pack`` (whole-project archive) — this is only the
    NLE/platform-facing delivery files (contract §11.3)."""
    from ..media.ffmpeg import atomic_output

    members = _bundle_members(project, manifest)
    expected = _expected_bindings(manifest, members)

    dest = output if output is not None else (
        project.exports_dir / "delivery" / f"{_safe_name(manifest['profile_id'])}.bundle.zip")
    dest.parent.mkdir(parents=True, exist_ok=True)

    # atomic: write to a sibling temp, os.replace on clean completion; on any
    # failure (including a streamed-digest mismatch) the temp is unlinked and
    # an existing bundle is left untouched.
    streamed: dict[str, str] = {}
    with atomic_output(dest) as tmp:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            # normalized, fixed archive timestamp for reproducibility (not now()).
            for arcname, abspath in members:
                info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                want_sha, want_bytes = expected[arcname]
                digester = hashlib.sha256()
                size = 0
                try:
                    with open(abspath, "rb") as src, zf.open(info, "w") as zdst:
                        while chunk := src.read(1 << 20):
                            digester.update(chunk)
                            size += len(chunk)
                            zdst.write(chunk)
                except OSError as exc:
                    raise DeliveryManifestError(
                        "DELIVERY_ARTIFACT_CHANGED_AFTER_MANIFEST: "
                        f"{arcname} became unreadable after the manifest was built"
                    ) from exc
                got_sha = HASH_PREFIX + digester.hexdigest()
                if got_sha != want_sha or (want_bytes is not None and size != want_bytes):
                    raise DeliveryManifestError(
                        "DELIVERY_ARTIFACT_CHANGED_AFTER_MANIFEST: "
                        f"{arcname} changed after the manifest was built — rebuild the "
                        "manifest; the previous bundle (if any) is left untouched")
                streamed[arcname] = got_sha

            # SHA256SUMS from the STREAMED digests; the embedded manifest binds
            # the same text — one byte snapshot end to end.
            sums_text = _sha256sums_from(streamed, members)
            manifest = dict(manifest)
            manifest["checksums"] = {
                "path": "SHA256SUMS",
                "sha256": hash_value({"sha256sums": sums_text}),
            }
            manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2)
                              + "\n").encode("utf-8")
            for arcname, data in (("SHA256SUMS", sums_text.encode("utf-8")),
                                  ("delivery-manifest.json", manifest_bytes)):
                info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, data)
    return dest, manifest


def _safe_name(profile_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", profile_id) or "master"
