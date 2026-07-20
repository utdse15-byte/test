"""FCPXML → Manju staged import — PLAN ONLY (never writes, never fetches).

:func:`plan_fcpxml_import` describes what a ``manju import`` of a ``.fcpxml``
document *would* propose, without touching the filesystem, without fetching a
single remote byte, and without ever writing timeline/shots truth. It is the
read-only ANALYSIS counterpart to :mod:`.fcpxml` (the WRITER, FP loops T2/V1) —
the honest other half T2 declined ("no import / import-plan is claimed
anywhere") built on the house openclap precedent
(:mod:`.openclap.import_plan`), whose honesty grammar this module mirrors.

Hard guarantees (the openclap precedent, restated for FCPXML):

- Zero filesystem mutations. Against a ``--target`` project it may only DESCRIBE
  and list conflicts; it writes nothing, ever.
- No media fetches. An external / remote / root-escaping ``src`` is recorded as a
  ``needs_relink`` row that POINTS AT the existing relink machinery
  (:mod:`manju.media.relink` / ``manju relink plan|apply``) — never copied,
  never downloaded.
- Nothing is silently dropped: an unrecognized element is COUNTED in
  ``unknown_elements`` (never a crash), an unknown audio role becomes an honest
  ``unknown_audio_role`` row (never a fabricated bus), an unreferenced asset is
  listed in ``unmapped``.
- PLAN ONLY. There is no apply path and no truth-write path in this module. The
  plan is DERIVED ADVISORY output (never a build input); it carries a
  ``digest`` for tamper-evidence exactly like ``conform-loss``.

Rational time rides NATIVELY (the T2 star, read back)
-----------------------------------------------------
FCPXML expresses every time as an exact rational number of seconds
(``"N/Ds"``) on the sequence ``<format>``'s ``frameDuration`` timescale. This
parser holds every time as an EXACT :class:`fractions.Fraction` of seconds —
**never a float** — and derives whole-frame counts via the document's OWN
frameDuration (``frames = seconds / frameDuration``, exact rational division).
The plan keeps the exact fractions (as canonical rational strings) alongside the
integer frame counts, so an int project (``1/24s``) and a 1001-family project
(``1001/24000s``) both round-trip with zero drift.

DTD-less + FCP-native tolerance
-------------------------------
Parsing is DTD-less :mod:`xml.etree.ElementTree` (no external entity is ever
fetched). A non-``<fcpxml>`` root is refused with a structured error; a document
version outside the verified set parses BEST-EFFORT and the plan SAYS the
version was unverified (no allowlist gate). Extra attributes, foreign attribute
order, whitespace and unknown elements (``<gap>``, ``<conform-rate>``,
``<adjust-transform>``, …) are tolerated — the known geometry is still read and
the unknowns are counted, never a crash.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Mapping

from ..core.hashing import hash_value
from ..core.timebase import Rate

if TYPE_CHECKING:
    from ..core.container import Project

__all__ = [
    "PLAN_SCHEMA",
    "FcpxmlImportError",
    "ParsedFcpxml",
    "parse_fcpxml",
    "plan_fcpxml_import",
    "verify_plan_digest",
]

#: The plan document's schema id (§4 contract governance). Mirrors the openclap
#: precedent (``manju.openclap-import-plan/v1``). NOT registered by this module —
#: CONTRACTS.yaml is orchestrator-owned; adding the registry row is a
#: STOP-AND-REPORT handoff (see REPORTS/FP_FCPXML_IMPORT.md).
PLAN_SCHEMA = "manju.fcpxml-import-plan/v1"

#: The one native cross-dissolve the writer emits (``exporters/fcpxml.py``); a
#: transition carrying this effect uid / name maps cleanly back to xfade_fade.
_CROSS_DISSOLVE_UID = "FFVideoTransitionCrossDissolve"
_CROSS_DISSOLVE_NAME = "Cross Dissolve"

#: V1's audio role map (``exporters/fcpxml.py`` ``_AUDIO_ROLES``) INVERTED: an
#: FCPXML ``audioRole`` back to the owning Manju bus. Unknown roles get an honest
#: row, never a fabricated bus.
_ROLE_TO_BUS: dict[str, str] = {
    "dialogue": "voice",
    "music": "music",
    "effects.sfx": "sfx",
    "effects.ambient": "ambient",
}

#: Element tags the writer emits / this parser understands structurally. Any tag
#: outside this set is COUNTED in ``unknown_elements`` (FCP-native tolerance).
_KNOWN_TAGS = frozenset({
    "fcpxml", "resources", "format", "asset", "effect", "library", "event",
    "project", "sequence", "spine", "asset-clip", "transition", "filter-video",
    "adjust-volume",
})

#: Document versions this import-plan parser has been validated against (the
#: writer emits 1.9). NOT a gate — any version parses best-effort; this only sets
#: the honest ``version_verified`` flag.
_VERIFIED_VERSIONS = frozenset({"1.9"})


# --------------------------------------------------------------------------- #
# errors + parsed model                                                        #
# --------------------------------------------------------------------------- #


class FcpxmlImportError(Exception):
    """A fail-closed FCPXML read/plan error (non-fcpxml root, malformed XML,
    tampered plan). Carries structured ``diagnostics`` (severity/code/message)
    for the CLI's ``--json`` error envelope — mirrors openclap's ClapReadError."""

    def __init__(self, message: str, diagnostics: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or []


@dataclass(frozen=True)
class FcpxmlAsset:
    id: str
    name: str
    src: str
    has_video: bool
    has_audio: bool
    duration: Fraction | None
    format_ref: str


@dataclass(frozen=True)
class FcpxmlAudioClip:
    ref: str
    src: str
    lane: int | None
    audio_role: str
    offset: Fraction | None
    start: Fraction | None
    duration: Fraction | None
    gain: str | None  # verbatim <adjust-volume amount> (e.g. "-6dB")


@dataclass(frozen=True)
class FcpxmlSpineClip:
    ref: str
    name: str
    src: str
    offset: Fraction | None
    start: Fraction | None
    duration: Fraction | None
    connected_audio: tuple[FcpxmlAudioClip, ...] = ()


@dataclass(frozen=True)
class FcpxmlTransition:
    name: str
    effect_uid: str
    offset: Fraction | None
    duration: Fraction | None
    before_clip_index: int  # index into spine_clips of the incoming clip


@dataclass(frozen=True)
class ParsedFcpxml:
    """A neutral, read-only description of an FCPXML document. Every time is an
    EXACT :class:`fractions.Fraction` of seconds (never a float)."""

    version: str
    version_verified: bool
    frame_duration: Fraction | None
    rate: Rate | None
    width: int | None
    height: int | None
    assets: Mapping[str, FcpxmlAsset]
    effects: Mapping[str, dict[str, str]]
    spine_clips: tuple[FcpxmlSpineClip, ...]
    transitions: tuple[FcpxmlTransition, ...]
    sequence_duration: Fraction | None
    unknown_elements: Mapping[str, int]
    diagnostics: tuple[dict[str, Any], ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# time parsing — exact fractions, never floats                                 #
# --------------------------------------------------------------------------- #


def _parse_time(value: str | None) -> Fraction | None:
    """An FCPXML rational-seconds string → EXACT :class:`Fraction` seconds.

    ``"0s"`` → 0, ``"48/24s"`` → 48/24, ``"1001/24000s"`` → 1001/24000,
    ``"5s"`` → 5. Returns ``None`` for a missing/malformed value (recorded as a
    diagnostic by the caller). Uses :class:`Fraction` throughout — no float ever
    touches a time.
    """
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("s"):
        s = s[:-1]
    try:
        return Fraction(s)  # exact for "48/24", "1001/24000", "5" and "3.5"
    except (ValueError, ZeroDivisionError):
        return None


def _frames(seconds: Fraction | None, frame_duration: Fraction | None) -> Fraction | None:
    """Whole-frame count for ``seconds`` on the document's own ``frame_duration``
    (exact rational division ``seconds / frameDuration``). ``None`` when either
    input is missing."""
    if seconds is None or frame_duration is None or frame_duration == 0:
        return None
    return seconds / frame_duration


def _frac_str(fr: Fraction | None) -> str | None:
    """Canonical rational string for JSON/digest (``"3/2"``, ``"2"``); ``None``
    passes through."""
    return None if fr is None else str(fr)


def _frames_field(fr: Fraction | None) -> int | str | None:
    """A frame count for the plan: an ``int`` when whole (the common case for a
    document written on its own grid), else the exact rational string (an honest
    off-grid signal), else ``None``."""
    if fr is None:
        return None
    return int(fr) if fr.denominator == 1 else str(fr)


# --------------------------------------------------------------------------- #
# parse                                                                        #
# --------------------------------------------------------------------------- #


# Audit 16 / OPT-ROBUST F2: a generous byte cap on FCPXML input. A real
# editorial FCPXML export is KBs to low MBs; 64 MiB is far above any honest
# document. Both residuals this guards are LOCAL and plan-only (no writes, no
# network — DTD-less stdlib ET resolves no external entity), so this is
# defense-in-depth, not a live hole: (1) the cap fail-closes the multi-GB-OOM
# vector before the whole file is materialized for the parser; (2) refusing a
# DOCTYPE/ENTITY prolog closes the platform-libexpat-dependent "billion laughs"
# residual with a clean structured error instead of a parser-version-dependent
# one. Overridable in tests to exercise the cap without giant files.
_MAX_FCPXML_BYTES = 64 * 1024 * 1024  # 64 MiB
_PROLOG_SCAN = 65536  # a DOCTYPE, if any, is in the prolog — scan only its head


def _too_large_error(nbytes: int, where: str) -> "FcpxmlImportError":
    return FcpxmlImportError(
        f"FCPXML input too large: {nbytes} bytes exceeds the "
        f"{_MAX_FCPXML_BYTES}-byte cap ({_MAX_FCPXML_BYTES // (1024 * 1024)} MiB)",
        [{"severity": "error", "code": "input_too_large", "path": "",
          "message": f"{where} is {nbytes} bytes; the cap is {_MAX_FCPXML_BYTES} "
                     f"({_MAX_FCPXML_BYTES // (1024 * 1024)} MiB) — refusing before parse"}])


def _guard_text(text: str) -> str:
    """Enforce the byte cap and refuse a DOCTYPE/ENTITY prolog on already-read
    text (an inline XML string, or a file whose on-disk size understated it)."""
    # len(text) chars <= UTF-8 byte count, so a char count over the cap is
    # already over the byte cap and refuses without the encode; otherwise the
    # exact byte count is bounded (<= the cap in chars) so the encode is cheap.
    if len(text) > _MAX_FCPXML_BYTES:
        raise _too_large_error(len(text), "the input")
    nbytes = len(text.encode("utf-8"))
    if nbytes > _MAX_FCPXML_BYTES:
        raise _too_large_error(nbytes, "the input")
    prolog = text[:_PROLOG_SCAN].upper()
    if "<!DOCTYPE" in prolog or "<!ENTITY" in prolog:
        raise FcpxmlImportError(
            "FCPXML input declares a DOCTYPE/ENTITY prolog — refused "
            "(DTD/entity documents are not accepted)",
            [{"severity": "error", "code": "doctype_forbidden", "path": "",
              "message": "a <!DOCTYPE or <!ENTITY prolog is refused before parse; "
                         "manju's FCPXML intake is DTD-less and resolves no entities"}])
    return text


def _load_text(source: str | Path) -> str:
    """Read an FCPXML document from a path or accept an XML string directly.

    Input caps (Audit 16 / OPT-ROBUST F2): a byte cap (checked on a file via
    ``stat()`` BEFORE the read, so a multi-GB file is never materialized) and a
    DOCTYPE/ENTITY-prolog refusal. Normal documents are unaffected — the parse
    path below is byte-identical to before."""
    if isinstance(source, Path):
        _guard_file_size(source)
        return _guard_text(_read_utf8(source))
    if isinstance(source, str):
        if source.lstrip().startswith("<"):
            return _guard_text(source)
        p = Path(source)
        _guard_file_size(p)
        return _guard_text(_read_utf8(p))
    raise FcpxmlImportError(
        "parse_fcpxml expects a filesystem path or an XML string",
        [{"severity": "error", "code": "bad_input", "path": "",
          "message": f"unsupported source type {type(source).__name__}"}])


def _read_utf8(p: Path) -> str:
    """Read a file as UTF-8, turning a non-UTF-8 file into the module's
    documented structured :class:`FcpxmlImportError` instead of letting a raw
    ``UnicodeDecodeError`` escape ``parse_fcpxml`` (whose only ``try`` catches
    ``ET.ParseError``). An FCPXML exported by another tool in a legacy codepage
    is exactly the untrusted intake this parser promises to reject gracefully.
    A valid UTF-8 file reads byte-identically to before."""
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise FcpxmlImportError(
            f"FCPXML file is not valid UTF-8: {p}",
            [{"severity": "error", "code": "bad_encoding", "path": str(p),
              "message": f"UTF-8 decode failed at byte {exc.start}: {exc.reason}"}],
        ) from exc
    except OSError as exc:
        # a typo'd/unreadable path is the same untrusted-intake refusal — the
        # CLI wrapper only speaks FcpxmlImportError, so a raw FileNotFoundError
        # escaped as a traceback
        raise FcpxmlImportError(
            f"cannot read FCPXML file: {p}",
            [{"severity": "error", "code": "unreadable", "path": str(p),
              "message": " ".join(str(exc).split())}],
        ) from exc


def _guard_file_size(p: Path) -> None:
    """Refuse an over-cap file by its on-disk size, before reading a single byte.
    A missing/unstattable path is left for the subsequent read to report."""
    try:
        size = p.stat().st_size
    except OSError:
        return
    if size > _MAX_FCPXML_BYTES:
        raise _too_large_error(size, str(p))


def _audio_clip(el: ET.Element, assets: Mapping[str, FcpxmlAsset]) -> FcpxmlAudioClip:
    ref = el.get("ref", "")
    lane_raw = el.get("lane")
    try:
        lane = int(lane_raw) if lane_raw is not None else None
    except ValueError:
        lane = None
    av = el.find("adjust-volume")
    gain = av.get("amount") if av is not None else None
    return FcpxmlAudioClip(
        ref=ref,
        src=assets[ref].src if ref in assets else "",
        lane=lane,
        audio_role=el.get("audioRole", ""),
        offset=_parse_time(el.get("offset")),
        start=_parse_time(el.get("start")),
        duration=_parse_time(el.get("duration")),
        gain=gain,
    )


def parse_fcpxml(source: str | Path) -> ParsedFcpxml:
    """Parse an FCPXML document (path or XML string) into a neutral, read-only
    :class:`ParsedFcpxml`. DTD-less; refuses a non-``<fcpxml>`` root and
    malformed XML with a structured :class:`FcpxmlImportError`; tolerates
    FCP-native variance (extra attrs, unknown elements) without crashing."""
    text = _load_text(source)
    try:
        # bytes input honours the document's encoding declaration; DTD-less ET
        # never resolves an external entity, so nothing reaches the network.
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise FcpxmlImportError(
            f"not well-formed XML: {exc}",
            [{"severity": "error", "code": "malformed_xml", "path": "",
              "message": f"XML parse error: {exc}"}]) from exc

    if not isinstance(root.tag, str) or root.tag != "fcpxml":
        raise FcpxmlImportError(
            f"root element is {root.tag!r}, not <fcpxml> — not an FCPXML document",
            [{"severity": "error", "code": "not_fcpxml", "path": "",
              "message": f"expected an <fcpxml> root, found {root.tag!r}"}])

    diagnostics: list[dict[str, Any]] = []
    version = root.get("version", "") or ""
    version_verified = version in _VERIFIED_VERSIONS
    if not version_verified:
        diagnostics.append({
            "severity": "warning", "code": "version_unverified", "path": "",
            "message": (f"FCPXML version {version!r} is unverified — this "
                        f"import-plan parser was validated against "
                        f"{sorted(_VERIFIED_VERSIONS)}; parsed best-effort")})

    # -- unknown-element census (single pass, comments/PIs skipped) ----------- #
    unknown: dict[str, int] = {}
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag not in _KNOWN_TAGS:
            unknown[el.tag] = unknown.get(el.tag, 0) + 1

    # -- resources: formats / assets / effects -------------------------------- #
    resources = root.find("resources")
    formats: dict[str, ET.Element] = {}
    assets: dict[str, FcpxmlAsset] = {}
    effects: dict[str, dict[str, str]] = {}
    if resources is not None:
        for fmt in resources.findall("format"):
            formats[fmt.get("id", "")] = fmt
        for a in resources.findall("asset"):
            assets[a.get("id", "")] = FcpxmlAsset(
                id=a.get("id", ""), name=a.get("name", ""), src=a.get("src", ""),
                has_video=a.get("hasVideo") == "1", has_audio=a.get("hasAudio") == "1",
                duration=_parse_time(a.get("duration")), format_ref=a.get("format", ""))
        for e in resources.findall("effect"):
            effects[e.get("id", "")] = {"name": e.get("name", ""), "uid": e.get("uid", "")}

    # -- sequence: frameDuration (the document's own grid) + geometry --------- #
    sequence = root.find(".//sequence")
    frame_duration: Fraction | None = None
    width: int | None = None
    height: int | None = None
    seq_duration: Fraction | None = None
    if sequence is not None:
        seq_duration = _parse_time(sequence.get("duration"))
        fmt_el = formats.get(sequence.get("format", "")) or (
            next(iter(formats.values()), None))
        if fmt_el is not None:
            frame_duration = _parse_time(fmt_el.get("frameDuration"))
            width = _int_or_none(fmt_el.get("width"))
            height = _int_or_none(fmt_el.get("height"))
    if frame_duration is None:
        diagnostics.append({
            "severity": "warning", "code": "no_frame_duration", "path": "",
            "message": "no sequence frameDuration found — frame counts cannot be "
                       "derived (windows carry exact seconds only)"})

    rate: Rate | None = None
    if frame_duration is not None and frame_duration > 0:
        try:
            rate = Rate(Fraction(1) / frame_duration)
        except (ValueError, TypeError):
            rate = None

    # -- spine walk (document order): clips, connected audio, transitions ----- #
    spine_clips: list[FcpxmlSpineClip] = []
    transitions: list[FcpxmlTransition] = []
    spine = sequence.find("spine") if sequence is not None else None
    if spine is not None:
        for el in spine:
            if not isinstance(el.tag, str):
                continue  # comment / PI
            if el.tag == "asset-clip":
                ref = el.get("ref", "")
                connected = tuple(
                    _audio_clip(child, assets)
                    for child in el.findall("asset-clip"))
                spine_clips.append(FcpxmlSpineClip(
                    ref=ref, name=el.get("name", ""),
                    src=assets[ref].src if ref in assets else "",
                    offset=_parse_time(el.get("offset")),
                    start=_parse_time(el.get("start")),
                    duration=_parse_time(el.get("duration")),
                    connected_audio=connected))
            elif el.tag == "transition":
                fv = el.find("filter-video")
                uid = ""
                if fv is not None and fv.get("ref") in effects:
                    uid = effects[fv.get("ref", "")].get("uid", "")
                transitions.append(FcpxmlTransition(
                    name=el.get("name", ""), effect_uid=uid,
                    offset=_parse_time(el.get("offset")),
                    duration=_parse_time(el.get("duration")),
                    before_clip_index=len(spine_clips)))  # the incoming clip

    return ParsedFcpxml(
        version=version, version_verified=version_verified,
        frame_duration=frame_duration, rate=rate, width=width, height=height,
        assets=assets, effects=effects,
        spine_clips=tuple(spine_clips), transitions=tuple(transitions),
        sequence_duration=seq_duration, unknown_elements=dict(unknown),
        diagnostics=tuple(diagnostics))


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# src containment classification (mirrors openclap's locator safety)           #
# --------------------------------------------------------------------------- #


def _escapes_root(rel: str) -> bool:
    """True if a relative POSIX locator ever climbs above the project root."""
    depth = 0
    for part in PurePosixPath(rel).parts:
        if part == "..":
            depth -= 1
            if depth < 0:
                return True
        elif part not in ("", "."):
            depth += 1
    return False


def _classify_src(src: str, project: "Project | None") -> tuple[str, str]:
    """Classify an asset ``src`` into (status, note). ``inside_project`` is the
    only non-external status; everything else is media an import would have to
    relink, never resolved/fetched here."""
    if not src:
        return "unknown", "empty src — nothing to relink"
    head = src.split("/", 1)[0]
    if src.lower().startswith(("http://", "https://", "ftp://")) or "://" in head:
        return "remote_url", "remote URL — NOT fetched by import-plan"
    if src.startswith("/") or src.lower().startswith("file:") or (
            len(src) >= 2 and src[1] == ":" and src[0].isalpha()):
        return "absolute", "absolute path outside the project boundary — NOT resolved"
    if _escapes_root(src):
        return "escapes_root", "relative locator escapes the project root — NOT resolved"
    if project is not None:
        try:
            project.resolve(src)  # central containment guard (symlink-aware)
        except Exception:
            return "uncontained", "failed the project containment check — NOT resolved"
    return "inside_project", "project-relative locator; media NOT copied by plan"


_RELINK_VIA = (
    "external media is NOT brought in by import-plan; use the existing relink "
    "machinery — `manju relink plan --root <dir> --out plan.json` then "
    "`manju relink apply --plan plan.json` (manju.media.relink.relink_plan / "
    "apply_relink) — which never fetches and re-hashes every candidate at apply")


# --------------------------------------------------------------------------- #
# plan                                                                         #
# --------------------------------------------------------------------------- #


def plan_fcpxml_import(
    parsed: ParsedFcpxml, *, source_sha256: str | None = None,
    target_project: "Project | None" = None,
) -> dict[str, Any]:
    """Derive the read-only ``manju.fcpxml-import-plan/v1`` document from a parsed
    FCPXML description.

    Writes nothing. ``source_sha256`` (the digest of the source FCPXML bytes, if
    the caller has them — the CLI computes ``core.hashing.hash_file``) is echoed
    for provenance; ``target_project``, when given, is only DESCRIBED against for
    conflicts. The returned plan carries its own ``digest`` for tamper-evidence
    (``conform-loss`` precedent).
    """
    fd = parsed.frame_duration
    diagnostics: list[dict[str, Any]] = [dict(d) for d in parsed.diagnostics]

    windows: list[dict[str, Any]] = []
    needs_relink: dict[str, dict[str, Any]] = {}  # src -> row (deduped)
    referenced: set[str] = set()

    def _note_relink(src: str, asset_ref: str) -> str:
        status, note = _classify_src(src, target_project)
        if status != "inside_project" and src not in needs_relink:
            needs_relink[src] = {
                "op": "needs_relink", "src": src, "asset_ref": asset_ref,
                "classification": status, "relink_via": _RELINK_VIA,
                "note": f"{note}; media is NOT copied or fetched by import-plan"}
        return status

    for c in parsed.spine_clips:
        referenced.add(c.src)
        status = _note_relink(c.src, c.ref)
        windows.append({
            "op": "propose_clip", "name": c.name, "src": c.src, "asset_ref": c.ref,
            "media_status": "inside_project" if status == "inside_project"
            else "needs_relink",
            "offset_frames": _frames_field(_frames(c.offset, fd)),
            "offset_seconds": _frac_str(c.offset),
            "start_frames": _frames_field(_frames(c.start, fd)),
            "start_seconds": _frac_str(c.start),
            "duration_frames": _frames_field(_frames(c.duration, fd)),
            "duration_seconds": _frac_str(c.duration),
        })

    # -- transitions: Cross Dissolve → xfade_fade candidate, else unsupported -- #
    transitions: list[dict[str, Any]] = []
    for t in parsed.transitions:
        at_clip = (parsed.spine_clips[t.before_clip_index].name
                   if 0 <= t.before_clip_index < len(parsed.spine_clips) else None)
        row: dict[str, Any] = {
            "name": t.name, "effect_uid": t.effect_uid, "at_clip": at_clip,
            "offset_frames": _frames_field(_frames(t.offset, fd)),
            "duration_frames": _frames_field(_frames(t.duration, fd)),
        }
        if t.effect_uid == _CROSS_DISSOLVE_UID or t.name == _CROSS_DISSOLVE_NAME:
            row["disposition"] = "xfade_fade_candidate"
            row["proposed_type"] = "xfade_fade"
        else:
            row["disposition"] = "unsupported_transition"
            row["note"] = (f"transition {t.name!r} (effect {t.effect_uid!r}) has no "
                           "clean Manju mapping — proposed as a hard cut, not faked")
        transitions.append(row)

    # -- connected audio: audioRole INVERTED → bus, unknown → honest row ------- #
    audio: list[dict[str, Any]] = []
    for c in parsed.spine_clips:
        for a in c.connected_audio:
            referenced.add(a.src)
            _note_relink(a.src, a.ref)
            bus = _ROLE_TO_BUS.get(a.audio_role)
            base = {
                "src": a.src, "asset_ref": a.ref, "audio_role": a.audio_role,
                "lane": a.lane, "parent_clip": c.name,
                "offset_frames": _frames_field(_frames(a.offset, fd)),
                "offset_seconds": _frac_str(a.offset),
                "start_frames": _frames_field(_frames(a.start, fd)),
                "duration_frames": _frames_field(_frames(a.duration, fd)),
                "gain": a.gain,
            }
            if bus is not None:
                audio.append({"disposition": "bus_mapping", "bus": bus, **base})
            else:
                audio.append({
                    "disposition": "unknown_audio_role", **base,
                    "note": (f"audioRole {a.audio_role!r} is not one of V1's "
                             "roles (dialogue/music/effects.sfx/effects.ambient) "
                             "— surfaced verbatim, no bus fabricated")})

    # -- unmapped: assets declared but referenced by no clip ------------------ #
    unmapped: list[dict[str, Any]] = []
    for aid, asset in parsed.assets.items():
        if asset.src not in referenced:
            unmapped.append({
                "item": aid,
                "reason": (f"asset {asset.name!r} ({asset.src!r}) is declared but "
                           "referenced by no clip — listed, not planned")})

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

    unknown_rows = [{"tag": t, "count": n}
                    for t, n in sorted(parsed.unknown_elements.items())]

    facts: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "source_sha256": source_sha256,
        "target_project": target_rel,
        "fcpxml_version": parsed.version,
        "version_verified": parsed.version_verified,
        "edit_rate": str(parsed.rate) if parsed.rate is not None else None,
        "frame_duration_seconds": _frac_str(parsed.frame_duration),
        "windows": windows,
        "transitions": transitions,
        "audio_suggestions": audio,
        "needs_relink": list(needs_relink.values()),
        "unmapped": unmapped,
        "unknown_elements": unknown_rows,
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
    object on success so callers can chain."""
    if not isinstance(plan, dict):
        raise FcpxmlImportError("not an import-plan document (expected a dict)")
    recorded = plan.get("digest")
    if not isinstance(recorded, str) or not recorded:
        raise FcpxmlImportError(
            "import-plan is missing its digest (tampered or not written by this tool)")
    facts = {k: v for k, v in plan.items() if k != "digest"}
    if hash_value(facts) != recorded:
        raise FcpxmlImportError(
            "import-plan digest does not match its facts (content was tampered)")
    if plan.get("schema") != PLAN_SCHEMA:
        raise FcpxmlImportError(
            f"unsupported import-plan schema {plan.get('schema')!r} (expected {PLAN_SCHEMA})")
    return plan
