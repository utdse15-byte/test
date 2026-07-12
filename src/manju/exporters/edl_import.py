"""CMX3600 EDL → Manju staged import — PLAN ONLY (never writes, never fetches).

:func:`plan_edl_import` describes what a ``manju import`` of a CMX3600 EDL cut
list *would* propose, without touching the filesystem, without fetching a single
byte, and without ever writing timeline/shots truth. It is the read-only
ANALYSIS counterpart to :mod:`.edl` (the WRITER, FP loop S2) — the honest other
half S2 declined ("there is no EDL import path this loop") built on the house
import-plan precedent (:mod:`.openclap.import_plan` / :mod:`.fcpxml_import`),
whose honesty grammar this module mirrors exactly.

Hard guarantees (the openclap/fcpxml precedent, restated for EDL):

- Zero filesystem mutations. Against a ``--target`` project it may only DESCRIBE
  and list conflicts; it writes nothing, ever.
- No media fetches. A CMX3600 EDL references TAPE REELS and bare CLIP NAMES, not
  file paths — a source can NEVER be resolved to project media by the EDL alone,
  so EVERY distinct source becomes a ``needs_relink`` row that POINTS AT the
  existing relink machinery (:mod:`manju.media.relink` / ``manju relink
  plan|apply``) — never copied, never downloaded.
- Nothing is silently dropped: an unrecognized line is COUNTED in
  ``unknown_rows`` (never a crash); a ``* MANJU:`` note the writer left is
  surfaced VERBATIM; a CMX flat audio channel (A/A1/A2/AA) becomes an honest
  ``cmx_audio_channel`` row (never a fabricated Manju bus — the four buses cannot
  ride CMX's flat A-channel model faithfully, exactly as the conform "edl"
  target says); a wipe/key transition becomes an ``unsupported_transition`` row,
  never a faked cross-dissolve.
- PLAN ONLY. There is no apply path and no truth-write path in this module. The
  plan is DERIVED ADVISORY output (never a build input); it carries a ``digest``
  for tamper-evidence exactly like ``conform-loss`` / the fcpxml import-plan.

Frame math rides the document's OWN frame-code mode
---------------------------------------------------
An EDL declares its frame-code mode in the ``FCM:`` header (``DROP FRAME`` /
``NON-DROP FRAME``) but — unlike FCPXML's ``frameDuration`` — carries NO explicit
frame rate. Every timecode is decoded to an exact integer frame index through
:class:`manju.core.timebase.Timecode` at the document's DF mode. When the caller
does not supply the rate it is inferred honestly and ``rate_assumed`` is flagged:
a DROP FRAME document is 30000/1001 (or 60000/1001 when a frame label ≥ 30 is
seen — the only drop-frame-legal rates), and a NON-DROP document defaults to 24
with a diagnostic (an EDL cannot distinguish 24/25/30 from its timecodes alone).
Pass ``rate=`` to decode against the true edit rate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.hashing import hash_value
from ..core.timebase import Rate, Timecode

if TYPE_CHECKING:
    from ..core.container import Project

__all__ = [
    "PLAN_SCHEMA",
    "EdlImportError",
    "EdlTimecode",
    "EdlEventRow",
    "ParsedEdl",
    "parse_edl",
    "plan_edl_import",
    "verify_plan_digest",
]

#: The plan document's schema id (§4 contract governance). Mirrors the openclap
#: (``manju.openclap-import-plan/v1``) and fcpxml (``manju.fcpxml-import-plan/v1``)
#: precedents. NOT registered by this module — CONTRACTS.yaml is orchestrator-
#: owned; adding the registry row is a STOP-AND-REPORT handoff (see
#: REPORTS/FP_EDL_IMPORT.md).
PLAN_SCHEMA = "manju.edl-import-plan/v1"

#: The two drop-frame-legal edit rates (mirrors timebase._DF_LEGAL). A DROP FRAME
#: EDL is decoded against 30000/1001 unless a frame label ≥ 30 forces 60000/1001.
_DF_30 = Rate.from_fraction(30000, 1001)
_DF_60 = Rate.from_fraction(60000, 1001)
_NDF_DEFAULT = Rate.from_fraction(24, 1)

#: A CMX3600 timecode: ``HH:MM:SS:FF`` (non-drop) or ``HH:MM:SS;FF`` (drop-frame,
#: ``;`` — or ``.`` — before the frames field). Frame fields tolerate 2–3 digits
#: (≥ 100 fps labels).
_TC_RE = re.compile(r"^(\d{1,3})[:;.](\d{1,2})[:;.](\d{1,2})([:;.])(\d{1,3})$")

#: The one clean cross-dissolve mapping (the S2 writer emits a ``D`` event for a
#: ``xfade_fade``); a ``D``-family edit type maps back to an xfade_fade candidate.
_DISSOLVE = "D"

_RELINK_VIA = (
    "a CMX3600 EDL names tape reels / clip names, not project media — an import "
    "resolves sources through the existing relink machinery: `manju relink plan "
    "--root <dir> --out plan.json` then `manju relink apply --plan plan.json` "
    "(manju.media.relink.relink_plan / apply_relink), which never fetches and "
    "re-hashes every candidate at apply. Media is NOT copied or fetched by "
    "import-plan.")


# --------------------------------------------------------------------------- #
# errors + parsed model                                                        #
# --------------------------------------------------------------------------- #


class EdlImportError(Exception):
    """A fail-closed EDL read/plan error (tampered plan, wrong schema). Carries
    structured ``diagnostics`` for the CLI's ``--json`` error envelope — mirrors
    the fcpxml import-plan's ``FcpxmlImportError`` / openclap's ClapReadError."""

    def __init__(self, message: str, diagnostics: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or []


@dataclass(frozen=True)
class EdlTimecode:
    """A parsed CMX timecode — the four label fields + the drop flag its
    separator implied + the verbatim source token. Rate-independent (the frame
    index is derived in the plan against the document's rate)."""

    h: int
    m: int
    s: int
    f: int
    drop: bool
    raw: str


@dataclass(frozen=True)
class EdlEventRow:
    """One physical CMX3600 event row (a dissolve is two rows sharing a number).
    ``from_clip`` / ``to_clip`` are the ``* FROM/TO CLIP NAME:`` comments that
    follow the row."""

    num: int
    reel: str
    channel: str
    edit_type: str
    transition_frames: int | None
    src_in: EdlTimecode | None
    src_out: EdlTimecode | None
    rec_in: EdlTimecode | None
    rec_out: EdlTimecode | None
    from_clip: str | None = None
    to_clip: str | None = None
    raw: str = ""


@dataclass(frozen=True)
class ParsedEdl:
    """A neutral, read-only description of a CMX3600 EDL document."""

    title: str
    fcm: str | None                     # "DROP FRAME" | "NON-DROP FRAME" | None
    events: tuple[EdlEventRow, ...]
    manju_notes: tuple[str, ...]        # verbatim `* MANJU:` lines
    comments: tuple[str, ...]           # other `*` comment lines (surfaced)
    unknown_rows: tuple[str, ...]       # lines that are not CMX rows (counted)
    diagnostics: tuple[dict[str, Any], ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# parse — structural, rate-independent                                         #
# --------------------------------------------------------------------------- #


def _load_text(source: str | Path) -> str:
    """Read an EDL from a path or accept EDL text directly. A multi-line string,
    or one opening with a CMX header keyword, is treated as content; anything
    else is a filesystem path."""
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8")
    if isinstance(source, str):
        head = source.lstrip()
        if "\n" in source or head[:5].upper() in ("TITLE", "FCM: ") or \
                head[:4].upper() == "FCM:":
            return source
        return Path(source).read_text(encoding="utf-8")
    raise EdlImportError(
        "parse_edl expects a filesystem path or EDL text",
        [{"severity": "error", "code": "bad_input", "path": "",
          "message": f"unsupported source type {type(source).__name__}"}])


def _parse_tc(token: str) -> EdlTimecode | None:
    m = _TC_RE.match(token)
    if not m:
        return None
    h, mn, s, sep, f = m.groups()
    return EdlTimecode(int(h), int(mn), int(s), int(f), drop=sep in ";.", raw=token)


_COMMENT_FROM = re.compile(r"^\*\s*FROM CLIP NAME:\s*(.*)$", re.IGNORECASE)
_COMMENT_TO = re.compile(r"^\*\s*TO CLIP NAME:\s*(.*)$", re.IGNORECASE)


def parse_edl(source: str | Path) -> ParsedEdl:
    """Parse a CMX3600 EDL (path or text) into a neutral, read-only
    :class:`ParsedEdl`. Tolerant: a V-only or a V+A1/A2 (Y1) document parses the
    same way; every non-CMX line is COUNTED in ``unknown_rows``, never a crash."""
    text = _load_text(source)
    title = ""
    fcm: str | None = None
    events: list[EdlEventRow] = []
    manju_notes: list[str] = []
    comments: list[str] = []
    unknown: list[str] = []
    diagnostics: list[dict[str, Any]] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        stripped = line.strip()
        if not stripped:
            continue

        upper = stripped.upper()
        if upper.startswith("TITLE:"):
            title = stripped[len("TITLE:"):].strip()
            continue
        if upper.startswith("FCM:"):
            mode = stripped[len("FCM:"):].strip().upper()
            fcm = "DROP FRAME" if "DROP" in mode and "NON" not in mode else \
                "NON-DROP FRAME"
            continue
        if stripped.startswith("*"):
            # a comment: MANJU note (verbatim), or a FROM/TO CLIP NAME that binds
            # to the most-recent event row (the incoming clip of a dissolve).
            if "MANJU:" in upper:
                manju_notes.append(stripped)
                continue
            mf = _COMMENT_FROM.match(stripped)
            mt = _COMMENT_TO.match(stripped)
            if (mf or mt) and events:
                last = events[-1]
                events[-1] = EdlEventRow(
                    num=last.num, reel=last.reel, channel=last.channel,
                    edit_type=last.edit_type, transition_frames=last.transition_frames,
                    src_in=last.src_in, src_out=last.src_out,
                    rec_in=last.rec_in, rec_out=last.rec_out,
                    from_clip=(mf.group(1).strip() if mf else last.from_clip),
                    to_clip=(mt.group(1).strip() if mt else last.to_clip),
                    raw=last.raw)
            else:
                comments.append(stripped)
            continue

        tokens = stripped.split()
        if tokens and tokens[0].isdigit():
            row = _parse_event_row(tokens, stripped)
            if row is not None:
                events.append(row)
                continue
            diagnostics.append({
                "severity": "warning", "code": "malformed_event",
                "path": "", "message": f"event-numbered row did not parse: {stripped!r}"})
        # anything else: honestly counted, never dropped, never a crash.
        unknown.append(stripped)

    if fcm is None:
        diagnostics.append({
            "severity": "warning", "code": "no_fcm_header", "path": "",
            "message": "no FCM: header — frame-code mode assumed NON-DROP FRAME"})

    return ParsedEdl(
        title=title, fcm=fcm, events=tuple(events),
        manju_notes=tuple(manju_notes), comments=tuple(comments),
        unknown_rows=tuple(unknown), diagnostics=tuple(diagnostics))


def _parse_event_row(tokens: list[str], raw: str) -> EdlEventRow | None:
    """A whitespace-tokenized CMX3600 event row → :class:`EdlEventRow`, or None
    when it cannot be read as one (the caller counts it as unknown).

    Layout: ``NUM REEL CHAN EDIT [DUR] SRC_IN SRC_OUT REC_IN REC_OUT``. The four
    trailing tokens are always the timecodes; a single optional token between the
    edit type and the timecodes is the transition (dissolve/wipe) frame count."""
    if len(tokens) < 8:
        return None
    tcs = [_parse_tc(t) for t in tokens[-4:]]
    if any(tc is None for tc in tcs):
        return None
    src_in, src_out, rec_in, rec_out = tcs
    reel, channel, edit_type = tokens[1], tokens[2], tokens[3]
    middle = tokens[4:-4]
    transition_frames: int | None = None
    if middle and middle[0].isdigit():
        transition_frames = int(middle[0])
    return EdlEventRow(
        num=int(tokens[0]), reel=reel, channel=channel, edit_type=edit_type,
        transition_frames=transition_frames,
        src_in=src_in, src_out=src_out, rec_in=rec_in, rec_out=rec_out, raw=raw)


# --------------------------------------------------------------------------- #
# channel + rate helpers                                                        #
# --------------------------------------------------------------------------- #


def _channel_kind(channel: str) -> tuple[bool, bool]:
    """(has_video, has_audio) for a CMX channel token, parsed generically so V,
    A, A1, A2, AA, B and combined forms (``A2/V``) all classify without a table.
    ``B`` = both video and audio."""
    up = channel.upper()
    has_v = "V" in up or "B" in up
    has_a = "A" in up or "B" in up
    return has_v, has_a


def _resolve_rate(parsed: ParsedEdl, rate: Rate | None) -> tuple[Rate, bool, bool, list[dict[str, Any]]]:
    """(rate, drop_frame, rate_assumed, diagnostics). Honest inference when the
    caller gives no rate (an EDL carries no explicit frame rate)."""
    drop_frame = parsed.fcm == "DROP FRAME"
    diags: list[dict[str, Any]] = []
    if rate is not None:
        if drop_frame and rate.fraction not in (_DF_30.fraction, _DF_60.fraction):
            diags.append({
                "severity": "warning", "code": "df_rate_not_legal", "path": "",
                "message": (f"FCM DROP FRAME but supplied rate {rate} is not "
                            "drop-frame-legal (30000/1001 or 60000/1001); frame "
                            "indices may not derive")})
        return rate, drop_frame, False, diags
    if drop_frame:
        # 60000/1001 iff a frame label ≥ 30 is present, else 30000/1001.
        max_f = 0
        for e in parsed.events:
            for tc in (e.src_in, e.src_out, e.rec_in, e.rec_out):
                if tc is not None:
                    max_f = max(max_f, tc.f)
        inferred = _DF_60 if max_f >= 30 else _DF_30
        diags.append({
            "severity": "warning", "code": "rate_assumed", "path": "",
            "message": (f"DROP FRAME EDL carries no explicit rate — assumed "
                        f"{inferred}; pass rate= to decode against the true edit rate")})
        return inferred, True, True, diags
    diags.append({
        "severity": "warning", "code": "rate_assumed", "path": "",
        "message": ("NON-DROP EDL carries no explicit rate — assumed 24 (an EDL "
                    "cannot distinguish 24/25/30 from its timecodes); pass rate= "
                    "to decode against the true edit rate")})
    return _NDF_DEFAULT, False, True, diags


def _tc_frames(tc: EdlTimecode | None, rate: Rate, drop_frame: bool) -> int | None:
    """Exact integer frame index for a timecode at the document's DF mode, or
    ``None`` when it cannot be decoded (recorded as a diagnostic upstream)."""
    if tc is None:
        return None
    try:
        return Timecode(tc.h, tc.m, tc.s, tc.f, rate, drop_frame).to_frames()
    except (ValueError, TypeError):
        return None


def _span(a: int | None, b: int | None) -> int | None:
    return None if a is None or b is None else b - a


# --------------------------------------------------------------------------- #
# plan                                                                          #
# --------------------------------------------------------------------------- #


def _is_dissolve_freeze(rows: tuple[EdlEventRow, ...], i: int) -> bool:
    """True when ``rows[i]`` is a zero-record-duration ``C`` "from" line whose
    same-numbered successor is a ``D`` dissolve — the CMX dissolve mechanic that
    freezes the outgoing frame. It is NOT its own clip window."""
    r = rows[i]
    if r.edit_type.upper() != "C" or i + 1 >= len(rows):
        return False
    nxt = rows[i + 1]
    if nxt.num != r.num or not nxt.edit_type.upper().startswith(_DISSOLVE):
        return False
    # zero record duration on the freeze line
    if r.rec_in is None or r.rec_out is None:
        return False
    return (r.rec_in.h, r.rec_in.m, r.rec_in.s, r.rec_in.f) == \
        (r.rec_out.h, r.rec_out.m, r.rec_out.s, r.rec_out.f)


def plan_edl_import(
    parsed: ParsedEdl, *, rate: Rate | None = None,
    source_sha256: str | None = None, target_project: "Project | None" = None,
) -> dict[str, Any]:
    """Derive the read-only ``manju.edl-import-plan/v1`` document from a parsed
    EDL description.

    Writes nothing. ``rate`` decodes the timecodes to exact frame indices; when
    omitted it is inferred from the FCM header and ``rate_assumed`` is flagged.
    ``source_sha256`` (the digest of the source EDL bytes, computed by the CLI via
    ``core.hashing.hash_file``) is echoed for provenance; ``target_project``, when
    given, is only DESCRIBED against for conflicts. The plan carries its own
    ``digest`` for tamper-evidence (``conform-loss`` precedent)."""
    edit_rate, drop_frame, rate_assumed, rate_diags = _resolve_rate(parsed, rate)
    diagnostics: list[dict[str, Any]] = [dict(d) for d in parsed.diagnostics]
    diagnostics.extend(rate_diags)

    windows: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    audio_events: list[dict[str, Any]] = []
    needs_relink: dict[str, dict[str, Any]] = {}  # source -> row (deduped)

    def _note_relink(source: str) -> None:
        if source and source not in needs_relink:
            needs_relink[source] = {
                "op": "needs_relink", "source": source,
                "classification": "cmx_reel", "relink_via": _RELINK_VIA,
                "note": ("CMX reel/clip source is not a project-relative locator; "
                         "resolve via relink — media NOT copied or fetched")}

    rows = parsed.events
    for i, r in enumerate(rows):
        if _is_dissolve_freeze(rows, i):
            continue  # dissolve mechanic, not a clip window

        src_in = _tc_frames(r.src_in, edit_rate, drop_frame)
        src_out = _tc_frames(r.src_out, edit_rate, drop_frame)
        rec_in = _tc_frames(r.rec_in, edit_rate, drop_frame)
        rec_out = _tc_frames(r.rec_out, edit_rate, drop_frame)
        has_v, has_a = _channel_kind(r.channel)
        # the source identity: the incoming clip's name (dissolve TO / cut FROM),
        # else the reel. Never a resolvable path — always a relink candidate.
        clip_name = r.to_clip or r.from_clip or r.reel
        source = clip_name

        if has_v:
            _note_relink(source)
            windows.append({
                "op": "propose_clip", "event": r.num, "reel": r.reel,
                "channel": r.channel, "clip_name": clip_name, "source": source,
                "media_status": "needs_relink",
                "src_in_frames": src_in, "src_in_tc": r.src_in.raw if r.src_in else None,
                "src_out_frames": src_out, "src_out_tc": r.src_out.raw if r.src_out else None,
                "rec_in_frames": rec_in, "rec_in_tc": r.rec_in.raw if r.rec_in else None,
                "rec_out_frames": rec_out, "rec_out_tc": r.rec_out.raw if r.rec_out else None,
                "duration_frames": _span(rec_in, rec_out),
                "source_duration_frames": _span(src_in, src_out),
            })
        if has_a and not has_v:
            # CMX flat audio channel — surfaced verbatim, NO Manju bus fabricated.
            _note_relink(source)
            audio_events.append({
                "op": "cmx_audio_channel", "disposition": "cmx_audio_channel",
                "event": r.num, "reel": r.reel, "channel": r.channel,
                "clip_name": clip_name, "source": source,
                "src_in_frames": src_in, "src_out_frames": src_out,
                "rec_in_frames": rec_in, "rec_out_frames": rec_out,
                "duration_frames": _span(rec_in, rec_out),
                "note": ("CMX flat audio channel — Manju's four buses "
                         "(voice/music/sfx/ambient) cannot ride it faithfully; "
                         "surfaced verbatim, no bus fabricated"),
            })

        # transitions: a D-family edit is a clean cross-dissolve candidate; any
        # other non-cut edit (W wipe, K key, …) degrades honestly, never faked.
        et = r.edit_type.upper()
        if et.startswith(_DISSOLVE):
            transitions.append({
                "op": "propose_transition", "at_event": r.num,
                "edit_type": r.edit_type, "disposition": "xfade_fade_candidate",
                "proposed_type": "xfade_fade",
                "duration_frames": r.transition_frames,
                "from_clip": r.from_clip, "to_clip": r.to_clip,
                "at_clip": clip_name,
            })
        elif et != "C" and not et.startswith("C"):
            transitions.append({
                "op": "propose_transition", "at_event": r.num,
                "edit_type": r.edit_type, "disposition": "unsupported_transition",
                "duration_frames": r.transition_frames,
                "from_clip": r.from_clip, "to_clip": r.to_clip, "at_clip": clip_name,
                "note": (f"CMX edit type {r.edit_type!r} has no clean Manju "
                         "mapping — proposed as a hard cut, not faked into an xfade"),
            })

    # -- conflicts: describe-only against a populated target ------------------ #
    target_rel: str | None = None
    conflicts: list[dict[str, Any]] = []
    if target_project is not None:
        target_rel = str(target_project.root)
        try:
            existing = set(target_project.shot_ids())
        except Exception:
            existing = set()
        if existing:
            conflicts.append({
                "target": "shots/",
                "reason": (f"target project already has {len(existing)} shot(s); "
                           "import-plan describes only and writes nothing")})

    facts: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "source_sha256": source_sha256,
        "target_project": target_rel,
        "title": parsed.title,
        "frame_code_mode": parsed.fcm,
        "drop_frame": drop_frame,
        "edit_rate": str(edit_rate),
        "rate_assumed": rate_assumed,
        "windows": windows,
        "transitions": transitions,
        "audio_events": audio_events,
        "needs_relink": list(needs_relink.values()),
        "manju_notes": list(parsed.manju_notes),
        "comments": list(parsed.comments),
        "unknown_rows": len(parsed.unknown_rows),
        "unknown_row_samples": list(parsed.unknown_rows[:5]),
        "conflicts": conflicts,
        "diagnostics": diagnostics,
    }
    plan = dict(facts)
    plan["digest"] = hash_value(facts)
    return plan


def verify_plan_digest(plan: dict[str, Any]) -> dict[str, Any]:
    """Verify a plan's ``digest`` (tamper-evidence, ``conform-loss`` precedent).

    Recomputes the digest over every fact except ``digest`` itself and rejects
    any mismatch, a dropped digest, or a wrong schema. Returns the same ``plan``
    on success so callers can chain."""
    if not isinstance(plan, dict):
        raise EdlImportError("not an import-plan document (expected a dict)")
    recorded = plan.get("digest")
    if not isinstance(recorded, str) or not recorded:
        raise EdlImportError(
            "import-plan is missing its digest (tampered or not written by this tool)")
    facts = {k: v for k, v in plan.items() if k != "digest"}
    if hash_value(facts) != recorded:
        raise EdlImportError(
            "import-plan digest does not match its facts (content was tampered)")
    if plan.get("schema") != PLAN_SCHEMA:
        raise EdlImportError(
            f"unsupported import-plan schema {plan.get('schema')!r} (expected {PLAN_SCHEMA})")
    return plan
