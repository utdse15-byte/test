"""AI_IDE_18 WP2 — alignment evidence sidecar (reader/writer).

The existing ``<take>.timing.json`` is a bare Edge-schema array
``[{start_ms,end_ms,text}, …]`` that the compiler's word-timed caption path and
``locale_build`` read directly. To stay strictly additive — those consumers keep
reading a byte-identical array — the richer alignment EVIDENCE the contract §5
asks for lands in a COMPANION sidecar ``<take>.align.json``:

    header:
        source_media_hash ..... sha256 of the EXACT audio the alignment bound to
        aligner: {provider, profile_digest}
        status ................ ALIGNED | UNALIGNED
        reason ................ why, when UNALIGNED
    cues[]: {start_ms, end_ms, text,
             phoneme?[], viseme?[], speaker?, confidence?}

Two contract pins live here (addendum ruling 3):

- ``UNALIGNED``: when forced alignment genuinely fails, the evidence records
  ``status=UNALIGNED`` with a reason and NO cues — never a fabricated uniform
  spread masquerading as real timing (§5: "对齐失败保留 UNALIGNED,不自动伪造均匀时间").
- STALE on read: the header pins the source audio hash, so a SAME-NAME audio
  replacement (a new take dropped over the old filename) is detected the next
  time the evidence is read — the recorded hash no longer matches the bytes on
  disk (§5: "对齐绑定 exact audio"; "同名音频替换 stale").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.hashing import hash_file
from ..core.yamlio import atomic_write_text

SCHEMA = "manju.alignment-evidence/v1"

ALIGNED = "ALIGNED"
UNALIGNED = "UNALIGNED"
STALE = "STALE"
MISSING = "MISSING"

# Which per-cue evidence fields are optional add-ons (kept only when present, so
# a plain word-timed cue is unchanged). ``phoneme``/``viseme`` are evidence
# LISTS a Provider/tool supplied; never fabricated locally.
_OPTIONAL_CUE_FIELDS = ("phoneme", "viseme", "speaker", "confidence")


def align_path(media: Path) -> Path:
    """The evidence sidecar path for a take (companion to ``.timing.json``)."""
    return Path(media).with_suffix(".align.json")


def _clean_cue(cue: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "start_ms": int(cue.get("start_ms", 0) or 0),
        "end_ms": int(cue.get("end_ms", 0) or 0),
        "text": str(cue.get("text") or ""),
    }
    for field in _OPTIONAL_CUE_FIELDS:
        if cue.get(field) is not None:
            val = cue[field]
            if field in ("phoneme", "viseme"):
                out[field] = list(val)
            elif field == "confidence":
                try:
                    out[field] = round(float(val), 4)
                except (TypeError, ValueError):
                    continue
            else:
                out[field] = str(val)
    return out


def write_evidence(media: Path, cues: list[dict[str, Any]], *,
                   provider: str, profile_digest: str | None = None,
                   status: str = ALIGNED,
                   source_media_hash: str | None = None) -> Path:
    """Write the alignment evidence for ``media``. The source hash defaults to
    the take's CURRENT bytes — that pin is what later reads compare against to
    detect a same-name replacement."""
    media = Path(media)
    if source_media_hash is None and media.exists():
        source_media_hash = hash_file(media)
    payload = {
        "schema": SCHEMA,
        "header": {
            "source_media_hash": source_media_hash,
            "aligner": {"provider": str(provider),
                        "profile_digest": profile_digest},
            "status": status,
            "cue_count": len(cues),
        },
        "cues": [_clean_cue(c) for c in cues],
    }
    path = align_path(media)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def write_unaligned(media: Path, *, provider: str, reason: str,
                    profile_digest: str | None = None) -> Path:
    """Record a genuine alignment FAILURE — status UNALIGNED, a reason, and NO
    fabricated cues (contract §5 pin)."""
    media = Path(media)
    source_media_hash = hash_file(media) if media.exists() else None
    payload = {
        "schema": SCHEMA,
        "header": {
            "source_media_hash": source_media_hash,
            "aligner": {"provider": str(provider),
                        "profile_digest": profile_digest},
            "status": UNALIGNED,
            "reason": str(reason),
            "cue_count": 0,
        },
        "cues": [],
    }
    path = align_path(media)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def read_evidence(media: Path) -> dict[str, Any] | None:
    """Parse the evidence sidecar, or None when absent/corrupt."""
    path = align_path(media)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def evidence_status(media: Path) -> str:
    """The live status of a take's alignment evidence.

    Returns ``MISSING`` (no sidecar), ``UNALIGNED`` (recorded failure),
    ``STALE`` (the pinned ``source_media_hash`` no longer matches the take's
    bytes — a same-name replacement), or ``ALIGNED``. The staleness check is the
    whole point of pinning the hash: it is evaluated on READ, against the real
    file, so a silent swap can never pass as fresh."""
    data = read_evidence(media)
    if data is None:
        return MISSING
    header = data.get("header") or {}
    if header.get("status") == UNALIGNED:
        return UNALIGNED
    pinned = header.get("source_media_hash")
    media = Path(media)
    if pinned and media.exists():
        try:
            if hash_file(media) != pinned:
                return STALE
        except OSError:
            return STALE
    return header.get("status") or ALIGNED


def is_stale(media: Path) -> bool:
    return evidence_status(media) == STALE
