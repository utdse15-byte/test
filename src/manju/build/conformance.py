"""FP Loop E — ``manju.delivery-conformance/v1``: a declarative delivery
conformance REPORT (roadmap §7.1/§7.2).

What it is
----------
A pure, honest verdict document. Given

    * a delivery manifest (:func:`manju.build.delivery.build_manifest` — the ONE
      delivery-list owner; conformance EXTENDS it, it is not a second system),
    * a delivery profile carrying an additive, OPTIONAL ``technical:`` target
      block (declared on the SAME ``delivery_profiles`` entry in project.yaml
      that already drives the manifest — extra=allow, so no models change and no
      second fact source), and
    * the per-file technical facts already recorded by
      :mod:`manju.media.technical_profile` (verbatim-or-``"unknown"``, never
      guessed) plus the measured loudness in the ``exports/masters`` index,

it emits one row per §7.2 checklist item, each with a four-valued status:

    PASS ............... the observed fact satisfies the declared target
    FAIL ............... the observed fact violates the declared target
    UNKNOWN ........... the target is declared but the fact cannot be proven
                        (no stored technical profile, colour genuinely unknown,
                        no measured loudness) — NEVER guessed into a PASS
    NOT_APPLICABLE .... the profile declares no target for this axis

Hard boundaries (roadmap §7.2, §16)
-----------------------------------
* **No aggregate score.** The summary is COUNTS BY STATUS only — the roadmap is
  explicit: 不生成单一总分. There is no total / grade / pass-rate field.
* **Never a build or release input.** Conformance imports the manifest; nothing
  in build/release/readiness imports conformance (grep-pinned). Wiring it into
  ``release_assessment`` gating is a deliberate FUTURE operator decision, not
  this loop.
* **Deterministic.** Same inputs → byte-identical document + digest. No
  wall-clock field; tool/run metadata (if any) stays out of the digest.
* **Deletable derived projection.** :func:`write_conformance_report` writes under
  ``reports/conformance/``; a tampered report is rejected on read; deleting it
  changes nothing downstream.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from ..core.container import Project, ProjectError
from ..core.hashing import hash_file, hash_value
from ..core.yamlio import atomic_write_text

SCHEMA = "manju.delivery-conformance/v1"

# the four honest verdicts (roadmap §7.2)
PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"
NOT_APPLICABLE = "NOT_APPLICABLE"
_STATUSES = (PASS, FAIL, UNKNOWN, NOT_APPLICABLE)

# the sentinel the technical-profile facts use for a fact that was never guessed.
_FACT_UNKNOWN = "unknown"

# caption format token -> the delivery-manifest role that carries it.
_CAPTION_ROLE = {"srt": "CAPTIONS_SRT", "ass": "CAPTIONS_ASS", "vtt": "CAPTIONS_VTT"}


# --------------------------------------------------------------- row helper


def _row(check: str, artifact: str, status: str, *, expected: Any = None,
         observed: Any = None, detail: str = "") -> dict[str, Any]:
    """One checklist row (roadmap §7.2). ``expected``/``observed`` carry the
    declared target and the observed fact; ``detail`` is a human sentence. No
    secret, no absolute path, no wall-clock."""
    return {
        "check": check,
        "artifact": artifact,
        "status": status,
        "expected": expected,
        "observed": observed,
        "detail": detail,
    }


# --------------------------------------------------------------- fact access


def _master_artifact(manifest: dict) -> dict | None:
    for a in manifest.get("artifacts") or []:
        if a.get("role") == "MASTER_VIDEO":
            return a
    return None


def _facts_for(project: Project, artifact: dict | None) -> dict | None:
    """The stored per-file technical facts for an artifact, or ``None`` when the
    artifact has no derivable/stored profile (→ every declared axis is UNKNOWN,
    never guessed). A tampered/mis-filed report reads back as ``None`` too."""
    if not artifact or not artifact.get("sha256"):
        return None
    try:
        from ..media.technical_profile import read_profile

        doc = read_profile(project, artifact["sha256"])
    except Exception:
        return None
    return (doc or {}).get("facts") if doc else None


# --------------------------------------------------------------- generic checks


def _string_check(check: str, artifact_id: str, target: Any, observed: Any,
                  *, facts_present: bool) -> dict:
    """Case-insensitive string equality with the four-valued honesty ladder."""
    if target is None:
        return _row(check, artifact_id, NOT_APPLICABLE, detail="no target declared in profile")
    if not facts_present:
        return _row(check, artifact_id, UNKNOWN, expected=target,
                    detail="no stored technical profile for this artifact — not guessed")
    if observed is None or observed == _FACT_UNKNOWN:
        return _row(check, artifact_id, UNKNOWN, expected=target, observed=observed,
                    detail="the observed value is unknown on the media — not guessed")
    ok = str(target).strip().lower() == str(observed).strip().lower()
    return _row(check, artifact_id, PASS if ok else FAIL, expected=target, observed=observed,
                detail="" if ok else f"expected {target!r}, observed {observed!r}")


def _container_check(technical: dict, facts: dict | None, artifact_id: str) -> dict:
    target = technical.get("container")
    if target is None:
        return _row("container", artifact_id, NOT_APPLICABLE, detail="no target declared in profile")
    if facts is None:
        return _row("container", artifact_id, UNKNOWN, expected=target,
                    detail="no stored technical profile for this artifact — not guessed")
    fmt = (facts.get("container") or {}).get("format_name")
    if not fmt or fmt == _FACT_UNKNOWN:
        return _row("container", artifact_id, UNKNOWN, expected=target, observed=fmt,
                    detail="the container format is unknown on the media — not guessed")
    # ffprobe reports a comma-joined brand list (e.g. "mov,mp4,m4a,3gp").
    brands = {s.strip().lower() for s in str(fmt).split(",") if s.strip()}
    ok = str(target).strip().lower() in brands
    return _row("container", artifact_id, PASS if ok else FAIL, expected=target, observed=fmt,
                detail="" if ok else f"{target!r} is not among the container brands {fmt!r}")


def _resolution_check(vid: dict, facts: dict | None, artifact_id: str) -> dict:
    max_w, max_h = vid.get("max_width"), vid.get("max_height")
    ex_w, ex_h = vid.get("width"), vid.get("height")
    if max_w is None and max_h is None and ex_w is None and ex_h is None:
        return _row("resolution", artifact_id, NOT_APPLICABLE, detail="no target declared in profile")
    if facts is None:
        return _row("resolution", artifact_id, UNKNOWN,
                    detail="no stored technical profile for this artifact — not guessed")
    pic = facts.get("picture") or {}
    cw, ch = pic.get("coded_width"), pic.get("coded_height")
    if cw in (None, _FACT_UNKNOWN) or ch in (None, _FACT_UNKNOWN):
        return _row("resolution", artifact_id, UNKNOWN, observed={"width": cw, "height": ch},
                    detail="the coded dimensions are unknown on the media — not guessed")
    observed = {"width": cw, "height": ch}
    if ex_w is not None or ex_h is not None:
        ok = (ex_w is None or cw == ex_w) and (ex_h is None or ch == ex_h)
        expected = {"width": ex_w, "height": ex_h}
        detail = "" if ok else "coded dimensions differ from the exact target"
    else:
        ok = (max_w is None or cw <= max_w) and (max_h is None or ch <= max_h)
        expected = {"max_width": max_w, "max_height": max_h}
        detail = "" if ok else "coded dimensions exceed the declared ceiling"
    return _row("resolution", artifact_id, PASS if ok else FAIL,
                expected=expected, observed=observed, detail=detail)


def _par_check(vid: dict, facts: dict | None, artifact_id: str) -> dict:
    target = vid.get("pixel_aspect_ratio")
    if target is None:
        return _row("pixel_aspect_ratio", artifact_id, NOT_APPLICABLE, detail="no target declared in profile")
    if facts is None:
        return _row("pixel_aspect_ratio", artifact_id, UNKNOWN, expected=target,
                    detail="no stored technical profile for this artifact — not guessed")
    sar = ((facts.get("picture") or {}).get("sample_aspect_ratio") or {}).get("normalized")
    if not isinstance(sar, dict):
        return _row("pixel_aspect_ratio", artifact_id, UNKNOWN, expected=target, observed=sar,
                    detail="the sample aspect ratio is unknown on the media — not guessed")
    try:
        ok = int(sar["num"]) * int(target["den"]) == int(sar["den"]) * int(target["num"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return _row("pixel_aspect_ratio", artifact_id, UNKNOWN, expected=target, observed=sar,
                    detail="the declared or observed pixel aspect ratio is malformed")
    return _row("pixel_aspect_ratio", artifact_id, PASS if ok else FAIL,
                expected=target, observed=sar, detail="" if ok else "pixel aspect ratio differs")


def _frame_rate_check(vid: dict, facts: dict | None, artifact_id: str) -> dict:
    target = vid.get("frame_rate")
    if target is None:
        return _row("frame_rate", artifact_id, NOT_APPLICABLE, detail="no target declared in profile")
    if facts is None:
        return _row("frame_rate", artifact_id, UNKNOWN, expected=target,
                    detail="no stored technical profile for this artifact — not guessed")
    rate = (facts.get("time") or {}).get("rate")
    if not isinstance(rate, dict):
        return _row("frame_rate", artifact_id, UNKNOWN, expected=target, observed=rate,
                    detail="the frame rate is unknown on the media — not guessed")
    # exact rational compare via core.timebase (24000/1001 ≠ 24, never a float snap).
    from ..core import timebase

    try:
        want = timebase.Rate.from_fraction(int(target["num"]), int(target["den"]))
        got = timebase.Rate.from_fraction(int(rate["num"]), int(rate["den"]))
    except (KeyError, TypeError, ValueError):
        return _row("frame_rate", artifact_id, UNKNOWN, expected=target, observed=rate,
                    detail="the declared or observed frame rate is malformed")
    ok = want == got
    return _row("frame_rate", artifact_id, PASS if ok else FAIL, expected=target, observed=rate,
                detail="" if ok else f"expected {want}, observed {got}")


def _color_check(vid: dict, facts: dict | None, artifact_id: str) -> dict:
    target = vid.get("color")
    if target is None:
        return _row("color", artifact_id, NOT_APPLICABLE, detail="no target declared in profile")
    if facts is None:
        return _row("color", artifact_id, UNKNOWN, expected=target,
                    detail="no stored technical profile for this artifact — not guessed")
    color = facts.get("color") or {}
    axes = ("primaries", "transfer", "matrix", "range")
    observed = {ax: color.get(ax) for ax in axes}
    if not color.get("color_known"):
        return _row("color", artifact_id, UNKNOWN, expected=target, observed=observed,
                    detail="color is unknown on the media (one or more color axes absent, "
                           "not guessed) — the required color tags cannot be verified")
    ok = all(str(target.get(ax, "")).strip().lower() == str(observed.get(ax, "")).strip().lower()
             for ax in axes if ax in target)
    return _row("color", artifact_id, PASS if ok else FAIL, expected=target, observed=observed,
                detail="" if ok else "one or more color axes differ from the declared target")


def _audio_field(check: str, artifact_id: str, target: Any, facts: dict | None,
                 key: str, *, numeric: bool = False) -> dict:
    if target is None:
        return _row(check, artifact_id, NOT_APPLICABLE, detail="no target declared in profile")
    if facts is None:
        return _row(check, artifact_id, UNKNOWN, expected=target,
                    detail="no stored technical profile for this artifact — not guessed")
    audio = facts.get("audio")
    if audio is None:
        return _row(check, artifact_id, FAIL, expected=target, observed=None,
                    detail="the master carries no audio stream but the profile requires audio")
    observed = audio.get(key)
    if observed is None or observed == _FACT_UNKNOWN:
        return _row(check, artifact_id, UNKNOWN, expected=target, observed=observed,
                    detail="the observed value is unknown on the media — not guessed")
    if numeric:
        try:
            ok = int(target) == int(observed)
        except (TypeError, ValueError):
            ok = str(target) == str(observed)
    else:
        ok = str(target).strip().lower() == str(observed).strip().lower()
    return _row(check, artifact_id, PASS if ok else FAIL, expected=target, observed=observed,
                detail="" if ok else f"expected {target!r}, observed {observed!r}")


# --------------------------------------------------------------- loudness (masters)


def _masters_loudness(project: Project) -> dict | None:
    """The measured integrated LUFS / true-peak dBTP from the ``exports/masters``
    index (the ONE loudness fact owner), or ``None`` when no master carries it.
    Prefers the summed program-like mix (RAW_STEM_SUM), then a loudness-normalised
    master, then any master with a measurement. Measurement only — never a target."""
    try:
        from ..media.masters import load_index

        index = load_index(project)
    except Exception:
        index = None
    if not index:
        return None
    arts = index.get("artifacts") or []

    def _pick(a: dict) -> dict | None:
        loud = a.get("loudness") or {}
        if loud.get("integrated_lufs") is not None or loud.get("true_peak_dbtp") is not None:
            return {"role": a.get("role"),
                    "integrated_lufs": loud.get("integrated_lufs"),
                    "true_peak_dbtp": loud.get("true_peak_dbtp")}
        return None

    for a in arts:
        if a.get("role") == "RAW_STEM_SUM":
            got = _pick(a)
            if got:
                return got
    lm = index.get("loudnorm_master")
    if isinstance(lm, dict):
        got = _pick({"role": lm.get("role") or "RAW_STEM_SUM", "loudness": lm.get("loudness") or {}})
        if got:
            return got
    for a in arts:
        got = _pick(a)
        if got:
            return got
    return None


def _lufs_bounds(loud: dict) -> tuple[float | None, float | None]:
    """(min, max) integrated-LUFS bounds from either an explicit min/max or a
    ``target`` ± ``tolerance``."""
    lo = loud.get("integrated_lufs_min")
    hi = loud.get("integrated_lufs_max")
    if lo is not None or hi is not None:
        return (float(lo) if lo is not None else None, float(hi) if hi is not None else None)
    tgt = loud.get("target", loud.get("target_lufs", loud.get("integrated_lufs_target")))
    if tgt is not None:
        tol = float(loud.get("tolerance", loud.get("tol", 0)) or 0)
        return (float(tgt) - tol, float(tgt) + tol)
    return (None, None)


def _loudness_checks(project: Project, aud: dict) -> list[dict]:
    loud = (aud or {}).get("loudness") or {}
    lo, hi = _lufs_bounds(loud)
    has_int = lo is not None or hi is not None
    tp_ceiling = loud.get("true_peak_max_dbtp")
    measured = _masters_loudness(project)
    role = (measured or {}).get("role") or "audio:masters"
    checks: list[dict] = []

    # integrated loudness
    if not has_int:
        checks.append(_row("loudness", "audio:masters", NOT_APPLICABLE, detail="no target declared in profile"))
    elif measured is None or measured.get("integrated_lufs") is None:
        checks.append(_row("loudness", "audio:masters", UNKNOWN, expected={"min": lo, "max": hi},
                           detail="no measured loudness in the masters index — not guessed"))
    else:
        val = float(measured["integrated_lufs"])
        ok = (lo is None or val >= lo) and (hi is None or val <= hi)
        checks.append(_row("loudness", role, PASS if ok else FAIL,
                           expected={"min": lo, "max": hi}, observed=val,
                           detail="" if ok else f"integrated {val} LUFS is outside [{lo}, {hi}]"))

    # true peak
    if tp_ceiling is None:
        checks.append(_row("true_peak", "audio:masters", NOT_APPLICABLE, detail="no target declared in profile"))
    elif measured is None or measured.get("true_peak_dbtp") is None:
        checks.append(_row("true_peak", "audio:masters", UNKNOWN, expected=tp_ceiling,
                           detail="no measured true peak in the masters index — not guessed"))
    else:
        ceil = float(tp_ceiling)
        val = float(measured["true_peak_dbtp"])
        ok = val <= ceil
        checks.append(_row("true_peak", role, PASS if ok else FAIL, expected=ceil, observed=val,
                           detail="" if ok else f"true peak {val} dBTP exceeds the {ceil} dBTP ceiling"))
    return checks


# --------------------------------------------------------------- captions / integrity / manifest rows


def _caption_checks(technical: dict, artifacts: list[dict]) -> list[dict]:
    required = (technical.get("captions") or {}).get("required")
    if not required:
        return [_row("captions", "captions", NOT_APPLICABLE, detail="no required captions declared")]
    by_role = {a.get("role"): a for a in artifacts}
    checks: list[dict] = []
    for fmt in required:
        role = _CAPTION_ROLE.get(str(fmt).strip().lower())
        if role is None:
            checks.append(_row("captions", f"captions:{fmt}", UNKNOWN, expected=fmt,
                               detail=f"unrecognised caption format {fmt!r}"))
            continue
        a = by_role.get(role)
        present = bool(a and a.get("path") and a.get("state") != "MISSING")
        checks.append(_row("captions", a["artifact_id"] if a else f"captions:{fmt}",
                           PASS if present else FAIL, expected=fmt,
                           observed=(a.get("state") if a else "absent"),
                           detail="" if present else f"the required {fmt} caption file is missing"))
    return checks


def _checksum_checks(project: Project, technical: dict, artifacts: list[dict]) -> list[dict]:
    required = bool((technical.get("checksums") or {}).get("required"))
    checks: list[dict] = []
    for a in artifacts:
        path, state = a.get("path"), a.get("state")
        if not path or state == "MISSING":
            continue
        try:
            abspath = project.resolve(path)
        except ProjectError:
            continue
        if not (abspath.exists() and abspath.is_file()):
            continue
        recorded = a.get("sha256")
        if not recorded:
            if required:
                checks.append(_row("checksum", a["artifact_id"], FAIL, observed=None,
                                   detail="the manifest records no sha256 to verify this file against"))
            continue
        try:
            actual = hash_file(abspath)
        except OSError:
            checks.append(_row("checksum", a["artifact_id"], UNKNOWN,
                               detail="the file bytes could not be read to recompute the checksum"))
            continue
        ok = actual == recorded
        checks.append(_row("checksum", a["artifact_id"], PASS if ok else FAIL,
                           expected=recorded, observed=actual,
                           detail="" if ok else "the recomputed sha256 differs from the manifest — "
                                                "the bytes changed after the manifest was built"))
    if not checks:
        checks.append(_row("checksum", "-", NOT_APPLICABLE,
                           detail="no on-disk artifacts with recorded checksums"))
    return checks


def _package_checks(project: Project, artifacts: list[dict]) -> list[dict]:
    """File naming / package structure: every artifact the manifest says exists
    (state ≠ MISSING) must actually be a file on disk. An honestly-absent optional
    deliverable (state MISSING) is not a package member and gets no row."""
    checks: list[dict] = []
    for a in artifacts:
        path, state = a.get("path"), a.get("state")
        if not path or state == "MISSING":
            continue
        try:
            abspath = project.resolve(path)
            on_disk = abspath.exists() and abspath.is_file() and abspath.stat().st_size > 0
        except (ProjectError, OSError):
            on_disk = False
        checks.append(_row("package_structure", a["artifact_id"], PASS if on_disk else FAIL,
                           observed=path,
                           detail="" if on_disk else "the manifest lists this file but it is not on disk"))
    if not checks:
        checks.append(_row("package_structure", "-", NOT_APPLICABLE, detail="no delivered files listed"))
    return checks


def _disclosure_checks(manifest: dict) -> list[dict]:
    """Rights / disclosure — REUSES the existing platform hand-off disclosure
    verdict verbatim (never a second legal judgement; §7.6 checks evidence only)."""
    ph = manifest.get("platform_handoff")
    if not ph:
        return [_row("disclosure", "-", NOT_APPLICABLE,
                     detail="this delivery has no platform hand-off requiring disclosure")]
    ph_checks = ph.get("checks") or []
    cred = next((c for c in ph_checks if c.get("code") == "CREDENTIALS_PRESENT"), None)
    if cred is not None and cred.get("status") == "FAIL":
        return [_row("disclosure", "platform:metadata", FAIL,
                     detail="platform metadata carries a credential / signed URL — remove it")]
    disc = next((c for c in ph_checks if c.get("code") == "DISCLOSURE_REVIEW"), None)
    if disc is None:
        return [_row("disclosure", "platform_handoff", UNKNOWN,
                     detail="the hand-off carries no disclosure verdict")]
    status = {"PASS": PASS, "FAIL": FAIL, "PENDING_HUMAN": UNKNOWN}.get(disc.get("status"), UNKNOWN)
    return [_row("disclosure", "platform_handoff", status, observed=disc.get("status"),
                 detail=disc.get("detail") or "AI-content / sponsorship disclosure verdict (reused)")]


def _unresolved_checks(manifest: dict) -> list[dict]:
    blocking = [d for d in (manifest.get("diagnostics") or []) if d.get("severity") == "blocking"]
    if not blocking:
        return [_row("unresolved_issues", "-", PASS, detail="no blocking delivery diagnostics")]
    codes = sorted({str(d.get("code")) for d in blocking})
    return [_row("unresolved_issues", "-", FAIL, observed=codes,
                 detail="blocking delivery diagnostics present: " + ", ".join(codes))]


# --------------------------------------------------------------- the report


def check_delivery_conformance(project: Project, manifest: dict, profile: dict) -> dict:
    """Produce the ``manju.delivery-conformance/v1`` document for ``manifest``
    against the OPTIONAL ``technical:`` target block on ``profile``.

    Every technical target field is optional; an absent field yields a
    NOT_APPLICABLE row (never a silent PASS). A declared target the media cannot
    prove (no stored profile, colour genuinely unknown, no measured loudness)
    yields UNKNOWN, never a guessed PASS. Deterministic and score-free."""
    technical = (profile or {}).get("technical") or {}
    artifacts = manifest.get("artifacts") or []

    master = _master_artifact(manifest)
    master_id = master["artifact_id"] if master else "master:main"
    facts = _facts_for(project, master)
    vid = technical.get("video") or {}
    aud = technical.get("audio") or {}

    checks: list[dict] = []
    # -- video / container (vs technical-profile facts; UNKNOWN when no profile) --
    checks.append(_container_check(technical, facts, master_id))
    checks.append(_string_check("video_codec", master_id, vid.get("codec"),
                                (facts.get("picture") or {}).get("codec_name") if facts else None,
                                facts_present=facts is not None))
    checks.append(_resolution_check(vid, facts, master_id))
    checks.append(_par_check(vid, facts, master_id))
    checks.append(_frame_rate_check(vid, facts, master_id))
    checks.append(_string_check("pix_fmt", master_id, vid.get("pix_fmt"),
                                (facts.get("picture") or {}).get("pix_fmt") if facts else None,
                                facts_present=facts is not None))
    checks.append(_color_check(vid, facts, master_id))
    # -- audio codec / sample rate / layout --
    checks.append(_audio_field("audio_codec", master_id, aud.get("codec"), facts, "codec_name"))
    checks.append(_audio_field("audio_sample_rate", master_id, aud.get("sample_rate"), facts,
                               "sample_rate", numeric=True))
    checks.append(_audio_field("audio_channel_layout", master_id, aud.get("channel_layout"),
                               facts, "channel_layout"))
    # -- loudness / true peak (measured, from the masters index) --
    checks.extend(_loudness_checks(project, aud))
    # -- captions / checksum / package / disclosure / unresolved --
    checks.extend(_caption_checks(technical, artifacts))
    checks.extend(_checksum_checks(project, technical, artifacts))
    checks.extend(_package_checks(project, artifacts))
    checks.extend(_disclosure_checks(manifest))
    checks.extend(_unresolved_checks(manifest))

    # summary is COUNTS BY STATUS only — the roadmap forbids a single total score.
    summary = {s: 0 for s in _STATUSES}
    for r in checks:
        summary[r["status"]] = summary.get(r["status"], 0) + 1

    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "profile_id": manifest.get("profile_id"),
        "manifest_digest": manifest.get("manifest_digest"),
        "checks": checks,
        "summary": summary,
    }
    doc["conformance_digest"] = _conformance_digest(doc)
    return doc


def _conformance_digest(doc: dict) -> str:
    """Digest over the semantic verdict surface (profile id + the exact manifest
    checked + the ordered check rows). No wall-clock, so the same inputs yield a
    byte-identical digest; the read-side re-derives it to reject tampering."""
    return hash_value({
        "profile_id": doc.get("profile_id"),
        "manifest_digest": doc.get("manifest_digest"),
        "checks": doc.get("checks"),
    })


# --------------------------------------------------------------- materialization


def _report_path(project: Project, profile_id: str | None) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(profile_id or "master")) or "master"
    return project.reports_dir / "conformance" / f"{safe}.delivery-conformance.json"


def write_conformance_report(project: Project, doc: dict, *, output: Path | None = None) -> Path:
    """Atomically write the deletable conformance report under
    ``reports/conformance/`` (optional — never a build/release input). Same inputs
    → semantically identical file (deterministic)."""
    dest = output if output is not None else _report_path(project, doc.get("profile_id"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(dest, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    return dest


def read_conformance_report(path: Path) -> dict | None:
    """Read a stored conformance report. A missing file, unreadable JSON, a wrong
    schema, or a document whose recomputed ``conformance_digest`` does not match
    (tampered / truncated) all yield ``None`` — a structured rejection, never a
    consumed half-truth."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    if not isinstance(data.get("checks"), list):
        return None
    if data.get("conformance_digest") != _conformance_digest(data):
        return None
    return data


# --------------------------------------------------------------- built-in presets


# Versioned, declarative delivery TECHNICAL presets (roadmap §7.1). These are DATA
# the user COPIES into a project.yaml ``delivery_profiles`` entry — they are never
# silently applied, and platform-volatile limits are NOT hard-coded here (a
# profile must be updatable + versioned; §7.1). Each is a full delivery-profile
# fragment carrying an additive ``technical:`` block.
_BUILTIN_PRESETS: dict[str, dict[str, Any]] = {
    "web-1080p": {
        "variant_kind": "master",
        "technical": {
            "container": "mp4",
            "video": {
                "codec": "h264",
                "max_width": 1920, "max_height": 1080,
                "pix_fmt": "yuv420p",
                "pixel_aspect_ratio": {"num": 1, "den": 1},
                "color": {"primaries": "bt709", "transfer": "bt709",
                          "matrix": "bt709", "range": "tv"},
            },
            "audio": {
                "codec": "aac", "sample_rate": 48000, "channel_layout": "stereo",
                "loudness": {"target": -14.0, "tolerance": 1.0, "true_peak_max_dbtp": -1.0},
            },
            "captions": {"required": ["srt"]},
            "checksums": {"required": True},
        },
    },
    "social-vertical": {
        "variant_kind": "master",
        "technical": {
            "container": "mp4",
            "video": {
                "codec": "h264",
                "width": 1080, "height": 1920,
                "frame_rate": {"num": 30, "den": 1},
                "pix_fmt": "yuv420p",
                "pixel_aspect_ratio": {"num": 1, "den": 1},
                "color": {"primaries": "bt709", "transfer": "bt709",
                          "matrix": "bt709", "range": "tv"},
            },
            "audio": {
                "codec": "aac", "sample_rate": 48000, "channel_layout": "stereo",
                "loudness": {"target": -14.0, "tolerance": 1.0, "true_peak_max_dbtp": -1.0},
            },
            "captions": {"required": ["srt"]},
            "checksums": {"required": True},
        },
    },
    "archive-mezzanine": {
        "variant_kind": "master",
        "technical": {
            "container": "mov",
            "video": {
                "codec": "prores",
                "pix_fmt": "yuv422p10le",
                "pixel_aspect_ratio": {"num": 1, "den": 1},
                "color": {"primaries": "bt709", "transfer": "bt709",
                          "matrix": "bt709", "range": "tv"},
            },
            "audio": {
                "codec": "pcm_s24le", "sample_rate": 48000, "channel_layout": "stereo",
            },
            "checksums": {"required": True},
        },
    },
}


def builtin_technical_presets() -> dict[str, dict[str, Any]]:
    """Return deep COPIES of the built-in delivery TECHNICAL presets (web-1080p /
    social-vertical / archive-mezzanine). Copies, so a caller mutating one never
    poisons the shared data; the user pastes a preset under ``delivery_profiles``
    in project.yaml (never silently applied)."""
    return copy.deepcopy(_BUILTIN_PRESETS)
