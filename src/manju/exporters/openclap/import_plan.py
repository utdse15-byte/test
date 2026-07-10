"""OpenClap → Manju staged import — PLAN ONLY (never writes, never downloads).

:func:`build_import_plan` describes what a ``manju import`` of a ``.clap`` file
*would* create, without touching the filesystem, without fetching a single
remote byte, and without ever auto-selecting a take. It is the read-only
counterpart to :mod:`.exporter`.

Hard guarantees (from the WP spec):

- Zero filesystem mutations. Against an existing ``--target`` project it may
  only DESCRIBE and list conflicts.
- No media downloads. A remote URL / data URI is recorded as a LOCATOR with an
  explicit "not downloaded, digest unknown" note; a data URI's already-local
  bytes MAY be digested in memory (size + sha256), still writing nothing.
- Semantic-annotation segments (CAMERA / STYLE / WEATHER / …) are NEVER planned
  as playable clips — they become annotation suggestions or ``unmapped`` rows.
- Nothing is silently dropped: every object we do not turn into an operation is
  listed exhaustively in ``unmapped``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import profile
from .model import ClapDocument, ClapSegment

if TYPE_CHECKING:
    from ...core.container import Project

__all__ = ["build_import_plan", "PLAN_SCHEMA"]

PLAN_SCHEMA = "manju.openclap-import-plan/v1"

# clap media category -> the Manju media role an import would register it under.
_MEDIA_ROLE = {
    "VIDEO": "video",
    "STORYBOARD": "storyboard",
    "DIALOGUE": "voice",
    "MUSIC": "music",
    "SOUND": "sfx",
}
# Which media categories get their own shot (the picture-bearing ones).
_SHOT_BEARING = {"VIDEO", "STORYBOARD"}

_MEDIA_SUFFIX = {
    "video": ".mp4", "storyboard": ".png", "voice": ".wav",
    "music": ".wav", "sfx": ".wav",
}


def _locator_name(kind: str, value: str, fallback: str, role: str,
                  used: set[str]) -> str:
    """A safe, UNIQUE ``media/imports`` basename for a locator.

    Never a path, never a URL — and never a name the plan already claimed:
    distinct segments routinely share a basename (every shot's take is
    ``take_01.mp4``), and a plan that maps two sources onto one target
    describes an import that would overwrite media. Collisions are
    disambiguated with the segment-derived ``fallback`` stem."""
    stem = ""
    if kind in ("project_path", "absolute_path"):
        stem = Path(value.replace("\\", "/")).name
    elif kind == "remote_url":
        stem = value.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    if not stem or "/" in stem or "\\" in stem:
        stem = fallback + _MEDIA_SUFFIX.get(role, "")
    if stem in used:
        p = Path(stem)
        stem = f"{fallback}__{p.stem}{p.suffix}"
        n = 2
        while stem in used:
            stem = f"{fallback}__{p.stem}_{n}{p.suffix}"
            n += 1
    used.add(stem)
    return stem


def _digest_data_uri(value: str) -> dict[str, Any] | None:
    """Size + sha256 of a data URI's bytes, computed IN MEMORY (never written).

    Returns ``None`` when the URI is malformed. Only base64 and percent-encoded
    payloads are handled; nothing here reaches the network or the disk.
    """
    try:
        header, _, payload = value[len("data:"):].partition(",")
        if not _:
            return None
        if ";base64" in header.lower():
            raw = base64.b64decode(payload, validate=False)
        else:
            from urllib.parse import unquote_to_bytes

            raw = unquote_to_bytes(payload)
    except (binascii.Error, ValueError):
        return None
    return {"size_bytes": len(raw), "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()}


def _locator_record(seg: ClapSegment, diagnostics: list[dict[str, Any]],
                    target: "Project | None") -> dict[str, Any] | None:
    """Classify a segment's ``assetUrl`` into a locator record + safety notes.

    NEVER dereferences a remote URL and NEVER reads an absolute/escaping path;
    an out-of-bounds locator is recorded with a diagnostic instead of resolved.
    """
    url = seg.asset_url
    if url is None:
        return None
    kind = profile.classify_locator(url)
    rec: dict[str, Any] = {"kind": kind, "value": url}

    if kind == "remote_url":
        rec["note"] = "media NOT downloaded, digest unknown"
    elif kind == "data_uri":
        digest = _digest_data_uri(url)
        if digest is not None:
            rec.update(digest)
            rec["note"] = "data URI digested in-memory; not written"
        else:
            rec["note"] = "data URI could not be decoded; not written"
    elif kind == "absolute_path":
        rec["note"] = "absolute locator NOT resolved (outside project boundary)"
        diagnostics.append({
            "severity": "warning", "code": "locator_absolute",
            "message": f"segment {seg.id!r} assetUrl is an absolute path; not resolved",
            "path": str(url),
        })
    elif kind == "project_path":
        if profile.locator_escapes_root(url):
            rec["note"] = "relative locator escapes project root; NOT resolved"
            diagnostics.append({
                "severity": "warning", "code": "locator_escapes_root",
                "message": f"segment {seg.id!r} assetUrl escapes the project root",
                "path": str(url),
            })
        else:
            rec["note"] = "project-relative locator; media not copied by plan"
            if target is not None:
                try:
                    target.resolve(url)  # central containment guard (symlink-aware)
                except Exception:
                    rec["note"] = "locator failed containment check; NOT resolved"
                    diagnostics.append({
                        "severity": "warning", "code": "locator_uncontained",
                        "message": f"segment {seg.id!r} assetUrl not contained in target",
                        "path": str(url),
                    })
    else:
        rec["note"] = "unrecognized locator shape; not resolved"
    return rec


def build_import_plan(document: ClapDocument, *, source_sha256: str,
                      target_project: "Project | None" = None) -> dict[str, Any]:
    """Build the read-only import plan for a parsed ``.clap`` document.

    ``source_sha256`` is the digest of the ``.clap`` FILE bytes (the caller
    computes it with ``core.hashing.hash_file``). ``target_project`` — when
    given — is only DESCRIBED against; this function makes zero writes.
    """
    operations: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = [d.to_dict() for d in document.diagnostics]

    target_rel: str | None = None
    existing_shots: set[str] = set()
    if target_project is not None:
        target_rel = str(target_project.root)
        try:
            existing_shots = set(target_project.shot_ids())
        except Exception:
            existing_shots = set()
        if existing_shots:
            conflicts.append({
                "target": "shots/",
                "reason": f"target project already has {len(existing_shots)} shot(s); "
                          "import-plan describes only and writes nothing",
            })

    shot_n = 0
    used_names: set[str] = set()
    for seg in document.segments:
        canon = seg.category
        raw_cat = seg.raw.get("category")

        # Unknown category → preserved verbatim, listed, never planned as a clip.
        if canon is None:
            unmapped.append({
                "item": str(seg.id),
                "reason": f"unknown segment category {raw_cat!r} (preserved verbatim; "
                          "not planned)",
            })
            continue

        # Semantic annotation → NOT a playable clip.
        if canon in profile.SEMANTIC_CATEGORIES:
            unmapped.append({
                "item": str(seg.id),
                "reason": f"semantic annotation ({canon}); would become a shot/scene "
                          "annotation suggestion, not a playable clip",
            })
            continue

        # Playable media category.
        role = _MEDIA_ROLE.get(canon, "media")
        if canon in _SHOT_BEARING:
            shot_n += 1
            shot_target = f"shots/S{shot_n:03d}.yaml"
            op: dict[str, Any] = {
                "op": "create_shot",
                "target": shot_target,
                "from_segment": str(seg.id),
                "summary": (seg.raw.get("prompt") or seg.raw.get("label")
                            or f"{canon} segment"),
                "media_role": None,
            }
            operations.append(op)
            if shot_target.split("/", 1)[-1].removesuffix(".yaml") in existing_shots:
                conflicts.append({
                    "target": shot_target,
                    "reason": "target project already has a shot at this id",
                })

        locator = _locator_record(seg, diagnostics, target_project)
        if locator is not None:
            fallback = str(seg.id).replace("/", "_").replace(":", "_")
            name = _locator_name(locator["kind"], locator["value"], fallback, role,
                                 used_names)
            operations.append({
                "op": "register_media",
                "target": f"media/imports/{name}",
                "from_segment": str(seg.id),
                "media_role": role,
                "locator": {k: v for k, v in locator.items() if k in ("kind", "value")},
                "locator_detail": locator,
                "note": locator.get("note", ""),
            })
        elif canon not in _SHOT_BEARING:
            # A playable-but-picture-less segment with no asset (e.g. a MUSIC cue
            # with no source) is still surfaced rather than silently dropped.
            unmapped.append({
                "item": str(seg.id),
                "reason": f"{canon} segment has no assetUrl to register",
            })

    # Descriptive objects are not staged by this PLAN — list them, never drop.
    for ent in document.entities:
        unmapped.append({
            "item": str(ent.id),
            "reason": f"entity ({ent.category or ent.raw_category}) — bible import is "
                      "not staged by import-plan (describe-only)",
        })
    for scene in document.scenes:
        unmapped.append({
            "item": str(scene.id),
            "reason": "scene object — scene/bible import is not staged by import-plan",
        })
    for wf in document.workflows:
        unmapped.append({
            "item": str(wf.id),
            "reason": "workflow object — provider workflows are not staged by import-plan",
        })

    return {
        "schema": PLAN_SCHEMA,
        "source_sha256": source_sha256,
        "target_project": target_rel,
        "operations": operations,
        "unmapped": unmapped,
        "conflicts": conflicts,
        "diagnostics": diagnostics,
    }
