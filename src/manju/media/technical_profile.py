"""``manju.media-technical-profile/v1`` — honest per-file technical facts
(roadmap §5.2–§5.6 / §15.2–§15.4).

Where :mod:`manju.media.probe` keeps only the five fields the engine consumes
today (duration/width/height/float-fps/has-audio) and *discards the rest*, this
module records the technical truth of a media file WITHOUT ever guessing: every
picture / colour / audio / container fact is carried verbatim or as the literal
string ``"unknown"``. A missing colour tag is never invented into ``bt709``; an
odd decimal frame rate is never snapped to a nearby standard (that refusal lives
in :func:`manju.core.timebase.classify_rate`).

Design rules honoured here
--------------------------
* **Unknown is never guessed.** Absent / unparseable facts become ``"unknown"``
  (colour, layouts, profiles) — never a fabricated default. The one honest
  exception is ``picture.rotation``: the *absence* of a display matrix or rotate
  tag genuinely means "displayed as-is" → ``0`` (not a guess).
* **Exact rational math via** :class:`fractions.Fraction`. Frame-rate rationality
  and DAR/SAR geometry are compared exactly (tolerance 0); floats appear only
  inside clearly-labelled ``*_approx`` display fields and the raw verbatim
  strings ffprobe handed us.
* **Pure core.** :func:`normalize_probe_document` takes a parsed ffprobe JSON
  ``dict`` and nothing else — the whole fixture matrix runs against hand-written
  dicts, no media needed. :func:`technical_profile` is the thin I/O shell that
  runs ffprobe (a SEPARATE invocation from ``probe.py`` — that module is
  load-bearing and untouched) and hashes the bytes.
* **Derived + deletable.** :func:`write_profile` / :func:`read_profile` store the
  document content-addressed under ``reports/technical/<hash>.json`` exactly like
  :mod:`manju.media.analysis`. It is NEVER a build/resume/cache/authorization
  input (pinned by a grep test); deleting it loses the report, never the source.
* **Reproducible.** ``profile_digest`` hashes the ``facts`` block ONLY (no
  timestamps, no tool paths, no source hash) so the same input dict yields a
  byte-identical document + digest.
"""

from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..core import timebase
from ..core.hashing import hash_file, hash_value
from ..core.yamlio import atomic_write_text
from .ffmpeg import MediaError

SCHEMA = "manju.media-technical-profile/v1"

# The single honesty sentinel. A fact is either verbatim from ffprobe or this.
UNKNOWN = "unknown"

# ffprobe invocation constants — mirrored from probe.py, NOT imported, so this
# module is self-contained and probe.py stays untouched (#55: a metadata read
# gets a hard, short-ish timeout so a corrupt/network file can never hang).
FFPROBE = "ffprobe"
DEFAULT_PROBE_TIMEOUT_S = 60.0

# read-state validation tokens (mirror media/analysis.py)
_HEX = set("0123456789abcdefABCDEF")

# The COMMON pixel-format table (addendum §2 picture): (bit_depth, chroma) for
# the formats we can name WITHOUT guessing. Anything outside → "unknown".
_PIX_FMT_TABLE: dict[str, tuple[int, str]] = {
    "yuv420p": (8, "4:2:0"),
    "yuvj420p": (8, "4:2:0"),
    "yuv422p": (8, "4:2:2"),
    "yuvj422p": (8, "4:2:2"),
    "yuv444p": (8, "4:4:4"),
    "yuvj444p": (8, "4:4:4"),
    "yuv420p10le": (10, "4:2:0"),
    "yuv422p10le": (10, "4:2:2"),
    "yuv444p10le": (10, "4:4:4"),
    "yuv420p12le": (12, "4:2:0"),
    "yuv422p12le": (12, "4:2:2"),
    "yuv444p12le": (12, "4:4:4"),
    "rgb24": (8, "4:4:4"),
    "rgba": (8, "4:4:4"),
}

# Alpha presence — a deterministic fact for the formats we can name (§5.2 alpha).
_ALPHA_PIX_FMTS = {"rgba", "argb", "abgr", "bgra", "yuva420p", "yuva422p",
                   "yuva444p", "ya8", "ya16le"}
_NO_ALPHA_PIX_FMTS = set(_PIX_FMT_TABLE) | {"bgr24", "gray", "gray10le", "nv12", "nv21"}

# Audio storage bit-depth by sample format (float formats report their storage
# width — 32 for flt/fltp — which is honest: that is the sample container size).
_SAMPLE_FMT_BITS = {
    "u8": 8, "u8p": 8,
    "s16": 16, "s16p": 16,
    "s32": 32, "s32p": 32,
    "flt": 32, "fltp": 32,
    "dbl": 64, "dblp": 64,
    "s64": 64, "s64p": 64,
}

# ffmpeg's field_order vocabulary. Anything else / absent → "unknown" (we never
# guess progressive from silence).
_FIELD_ORDERS = {"progressive", "tt", "bb", "tb", "bt"}


# --------------------------------------------------------------- small honest coercers


def _int_or_unknown(value: Any) -> int | str:
    """``int`` when ``value`` parses to a whole number, else ``UNKNOWN``.

    ``bool`` is refused (it is an ``int`` subclass but never a real count), as are
    ``None``, ``"N/A"`` and anything non-numeric.
    """
    if value is None or isinstance(value, bool):
        return UNKNOWN
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            f = float(value)
        except (TypeError, ValueError):
            return UNKNOWN
        if f != f:  # NaN
            return UNKNOWN
        return int(f)


def _str_or_unknown(value: Any) -> str:
    """Verbatim stripped string, or ``UNKNOWN`` for empty / ``N/A`` / ``unknown``."""
    if value is None:
        return UNKNOWN
    s = str(value).strip()
    if not s or s.upper() in ("N/A", "UNKNOWN"):
        return UNKNOWN
    return s


def _to_int_ms(seconds: Any) -> int | None:
    """Seconds (str/float) → integer milliseconds, or ``None`` when unusable.

    Mirrors probe.py's private helper (kept local so probe.py is untouched).
    """
    if seconds is None:
        return None
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    if value != value or value < 0:  # NaN / negative guard
        return None
    return int(round(value * 1000))


def _parse_ratio(raw: Any) -> Fraction | None:
    """Parse a ``"W:H"`` (or ``"W/H"``) aspect string to an exact positive
    :class:`~fractions.Fraction`, or ``None`` when undefined / unparseable.

    ``"0:1"`` (ffprobe's "undefined SAR/DAR") and any zero/negative term yield
    ``None`` — an undefined ratio is a known gap, never coerced to 1:1.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.upper() == "N/A":
        return None
    sep = ":" if ":" in s else ("/" if "/" in s else None)
    if sep is None:
        return None
    a, _, b = s.partition(sep)
    try:
        num, den = int(a), int(b)
    except ValueError:
        return None
    if den == 0 or num <= 0:
        return None
    return Fraction(num, den)


def _frac_json(fr: Fraction) -> dict[str, int]:
    """An exact fraction as ``{"num", "den"}`` (lowest terms)."""
    return {"num": fr.numerator, "den": fr.denominator}


def _frac_ms_approx(fr: Fraction) -> float:
    """A human-readable millisecond approximation of an exact ``Fraction`` (ms).

    Deterministic (``float`` of a given ``Fraction`` is IEEE-754 stable, then
    rounded), and it never enters ``profile_digest`` (facts-only) so it can carry
    a float without endangering byte-for-byte reproducibility of the digest.
    """
    return round(float(fr), 6)


def _extract_rotation(video: dict) -> int | str:
    """Integer degrees of display rotation from the side-data display matrix or a
    ``rotate`` tag; ``0`` when no rotation metadata exists (displayed as-is — not
    a guess); ``UNKNOWN`` only when a present value is unparseable."""
    for sd in (video.get("side_data_list") or []):
        if isinstance(sd, dict) and sd.get("rotation") is not None:
            try:
                return int(round(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                return UNKNOWN
    tags = video.get("tags") or {}
    if isinstance(tags, dict) and tags.get("rotate") is not None:
        try:
            return int(round(float(tags["rotate"]))) % 360
        except (TypeError, ValueError):
            return UNKNOWN
    return 0


def _tag_language(stream: dict) -> str:
    tags = stream.get("tags") or {}
    if isinstance(tags, dict):
        return _str_or_unknown(tags.get("language"))
    return UNKNOWN


# --------------------------------------------------------------- fact builders


def _build_time(video: dict, fmt: dict) -> tuple[dict[str, Any], object, str]:
    """The ``time`` fact block. Returns ``(facts, rate_or_UNKNOWN, rate_mode)`` so
    the diagnostics pass can reuse the exact :class:`~manju.core.timebase.Rate`."""
    r_raw = video.get("r_frame_rate")
    avg_raw = video.get("avg_frame_rate")
    rate = timebase.classify_rate(r_raw)  # refuses odd decimals → UNKNOWN
    mode = timebase.rate_mode(
        r_raw if r_raw is None else str(r_raw),
        avg_raw if avg_raw is None else str(avg_raw),
    )
    duration_ms = _to_int_ms(fmt.get("duration"))
    if duration_ms is None:
        duration_ms = _to_int_ms(video.get("duration"))
    facts: dict[str, Any] = {
        "r_frame_rate": _str_or_unknown(r_raw),
        "avg_frame_rate": _str_or_unknown(avg_raw),
        "rate": _frac_json(rate.fraction) if isinstance(rate, timebase.Rate) else UNKNOWN,
        "rate_mode": mode,
        "duration_ms": duration_ms if duration_ms is not None else UNKNOWN,
        "nb_frames": _int_or_unknown(video.get("nb_frames")),
        "start_time": _str_or_unknown(video.get("start_time") if video.get("start_time")
                                      is not None else fmt.get("start_time")),
        "range_semantics": timebase.RANGE_SEMANTICS,
    }
    return facts, rate, mode


def _build_picture(video: dict) -> tuple[dict[str, Any], Fraction | None, Fraction | None,
                                         int | None, int | None]:
    """The ``picture`` fact block plus the parsed geometry the DAR/SAR diagnostic
    reuses: ``(facts, sar, dar, coded_w, coded_h)``."""
    cw_raw = video.get("coded_width") if video.get("coded_width") is not None else video.get("width")
    ch_raw = video.get("coded_height") if video.get("coded_height") is not None else video.get("height")
    coded_w = cw_raw if isinstance(cw_raw, int) and not isinstance(cw_raw, bool) else None
    if coded_w is None:
        try:
            coded_w = int(cw_raw)
        except (TypeError, ValueError):
            coded_w = None
    coded_h = ch_raw if isinstance(ch_raw, int) and not isinstance(ch_raw, bool) else None
    if coded_h is None:
        try:
            coded_h = int(ch_raw)
        except (TypeError, ValueError):
            coded_h = None

    sar = _parse_ratio(video.get("sample_aspect_ratio"))
    dar = _parse_ratio(video.get("display_aspect_ratio"))

    pix_fmt = _str_or_unknown(video.get("pix_fmt"))
    pix_key = pix_fmt if pix_fmt != UNKNOWN else None

    # bit depth: bits_per_raw_sample first (verbatim), else the COMMON table.
    bit_depth: int | str = UNKNOWN
    bprs = _int_or_unknown(video.get("bits_per_raw_sample"))
    if isinstance(bprs, int) and bprs > 0:
        bit_depth = bprs
    elif pix_key in _PIX_FMT_TABLE:
        bit_depth = _PIX_FMT_TABLE[pix_key][0]

    chroma: str = _PIX_FMT_TABLE[pix_key][1] if pix_key in _PIX_FMT_TABLE else UNKNOWN

    if pix_key in _ALPHA_PIX_FMTS:
        has_alpha: bool | str = True
    elif pix_key in _NO_ALPHA_PIX_FMTS:
        has_alpha = False
    else:
        has_alpha = UNKNOWN

    field_raw = _str_or_unknown(video.get("field_order"))
    field_order = field_raw if field_raw in _FIELD_ORDERS else UNKNOWN

    facts: dict[str, Any] = {
        "coded_width": coded_w if coded_w is not None else UNKNOWN,
        "coded_height": coded_h if coded_h is not None else UNKNOWN,
        "sample_aspect_ratio": {
            "raw": _str_or_unknown(video.get("sample_aspect_ratio")),
            "normalized": _frac_json(sar) if sar is not None else UNKNOWN,
        },
        "display_aspect_ratio": {
            "raw": _str_or_unknown(video.get("display_aspect_ratio")),
            "normalized": _frac_json(dar) if dar is not None else UNKNOWN,
        },
        "rotation": _extract_rotation(video),
        "pix_fmt": pix_fmt,
        "bit_depth": bit_depth,
        "chroma_subsampling": chroma,
        "has_alpha": has_alpha,
        "field_order": field_order,
        "codec_name": _str_or_unknown(video.get("codec_name")),
        "profile": _str_or_unknown(video.get("profile")),
        "level": _int_or_unknown(video.get("level")),
    }
    return facts, sar, dar, coded_w, coded_h


def _build_color(video: dict) -> dict[str, Any]:
    """The ``color`` fact block — every axis verbatim or ``UNKNOWN``, plus the
    ``color_known`` gate (all four present)."""
    primaries = _str_or_unknown(video.get("color_primaries"))
    transfer = _str_or_unknown(video.get("color_transfer"))
    matrix = _str_or_unknown(video.get("color_space"))
    rng = _str_or_unknown(video.get("color_range"))
    known = UNKNOWN not in (primaries, transfer, matrix, rng)
    return {
        "primaries": primaries,
        "transfer": transfer,
        "matrix": matrix,
        "range": rng,
        "color_known": known,
    }


def _build_audio(audio: dict | None) -> dict[str, Any] | None:
    """The ``audio`` fact block, or ``None`` when the file has no audio stream."""
    if audio is None:
        return None
    sample_fmt = _str_or_unknown(audio.get("sample_fmt"))
    bit_depth: int | str = UNKNOWN
    bps = _int_or_unknown(audio.get("bits_per_raw_sample"))
    if not isinstance(bps, int) or bps <= 0:
        bps = _int_or_unknown(audio.get("bits_per_sample"))
    if isinstance(bps, int) and bps > 0:
        bit_depth = bps
    elif sample_fmt in _SAMPLE_FMT_BITS:
        bit_depth = _SAMPLE_FMT_BITS[sample_fmt]
    return {
        "codec_name": _str_or_unknown(audio.get("codec_name")),
        "sample_rate": _int_or_unknown(audio.get("sample_rate")),
        "sample_fmt": sample_fmt,
        "channels": _int_or_unknown(audio.get("channels")),
        "channel_layout": _str_or_unknown(audio.get("channel_layout")),
        "bit_depth": bit_depth,
    }


def _build_container(streams: list[dict], fmt: dict) -> dict[str, Any]:
    """The ``container`` fact block: format identity + a per-stream disposition /
    language projection (§5.6)."""
    per_stream: list[dict[str, Any]] = []
    for s in streams:
        if not isinstance(s, dict):
            continue
        disp = s.get("disposition") or {}
        per_stream.append({
            "index": _int_or_unknown(s.get("index")),
            "codec_type": _str_or_unknown(s.get("codec_type")),
            "disposition": {
                "default": _int_or_unknown(disp.get("default")) if isinstance(disp, dict) else UNKNOWN,
                "forced": _int_or_unknown(disp.get("forced")) if isinstance(disp, dict) else UNKNOWN,
            },
            "language": _tag_language(s),
        })
    return {
        "format_name": _str_or_unknown(fmt.get("format_name")),
        "bit_rate": _int_or_unknown(fmt.get("bit_rate")),
        "nb_streams": _int_or_unknown(fmt.get("nb_streams")),
        "streams": per_stream,
    }


# --------------------------------------------------------------- diagnostics


def _diagnostics(facts: dict[str, Any], *, rate: object, rate_mode: str,
                 sar: Fraction | None, dar: Fraction | None,
                 coded_w: int | None, coded_h: int | None,
                 edit_fps: int | None) -> list[dict[str, Any]]:
    """The additive, structured diagnostics list (edit-grid drift, geometry
    mismatch, colour honesty, VFR suspicion). None are blocking."""
    diags: list[dict[str, Any]] = []

    # RATE_UNREPRESENTABLE_ON_EDIT_GRID — the source rate cannot sit on the
    # integer edit grid without cumulative drift (NTSC on an int grid is the
    # canonical case, but any true≠edit mismatch drifts).
    if edit_fps is not None and isinstance(rate, timebase.Rate) \
            and rate.fraction != Fraction(edit_fps):
        one_frame = timebase.one_frame_drift_at(edit_fps, rate)
        duration_ms = facts["time"]["duration_ms"]
        entry: dict[str, Any] = {
            "code": "RATE_UNREPRESENTABLE_ON_EDIT_GRID",
            "severity": "warning",
            "detail": (f"true rate {rate} is not representable on an integer "
                       f"{edit_fps} fps edit grid without cumulative drift"),
            "edit_fps": edit_fps,
            "true_rate": _frac_json(rate.fraction),
            "one_frame_drift_at_ms": _frac_json(one_frame),
            "one_frame_drift_at_ms_approx": _frac_ms_approx(one_frame),
        }
        if isinstance(duration_ms, int):
            drift = timebase.grid_drift_ms(duration_ms, edit_fps, rate)
            entry["grid_drift_ms_over_clip"] = _frac_json(drift)
            entry["grid_drift_ms_over_clip_approx"] = _frac_ms_approx(drift)
        else:
            entry["grid_drift_ms_over_clip"] = UNKNOWN
            entry["grid_drift_ms_over_clip_approx"] = UNKNOWN
        diags.append(entry)

    # DAR_SAR_GEOMETRY_MISMATCH — DAR must equal (w/h)·SAR exactly (tolerance 0).
    if sar is not None and dar is not None and coded_w and coded_h:
        expected = Fraction(coded_w, coded_h) * sar
        if expected != dar:
            diags.append({
                "code": "DAR_SAR_GEOMETRY_MISMATCH",
                "severity": "warning",
                "detail": ("display_aspect_ratio does not equal "
                           "(coded_width/coded_height)·sample_aspect_ratio"),
                "coded": {"w": coded_w, "h": coded_h},
                "sample_aspect_ratio": _frac_json(sar),
                "display_aspect_ratio": _frac_json(dar),
                "expected_display_aspect_ratio": _frac_json(expected),
            })

    # COLOR_UNKNOWN — advisory (never blocking): one or more colour axes missing.
    color = facts["color"]
    if not color["color_known"]:
        missing = [ax for ax in ("primaries", "transfer", "matrix", "range")
                   if color[ax] == UNKNOWN]
        diags.append({
            "code": "COLOR_UNKNOWN",
            "severity": "advisory",
            "detail": "one or more colour axes are unknown; not guessed",
            "missing": missing,
        })

    # VFR_SUSPECTED — r_frame_rate and avg_frame_rate disagree.
    if rate_mode == "vfr_suspected":
        diags.append({
            "code": "VFR_SUSPECTED",
            "severity": "warning",
            "detail": "r_frame_rate and avg_frame_rate differ — variable frame rate suspected",
            "r_frame_rate": facts["time"]["r_frame_rate"],
            "avg_frame_rate": facts["time"]["avg_frame_rate"],
        })

    return diags


# --------------------------------------------------------------- the pure core


def normalize_probe_document(ffprobe_json: dict, *, edit_fps: int | None = None,
                             source_media_hash: str | None = None,
                             source_ref: str | None = None) -> dict[str, Any]:
    """PURE normalizer: a parsed ffprobe JSON ``dict`` → a
    ``manju.media-technical-profile/v1`` document.

    No I/O, no media, no clock: this is the unit-testable core the whole fixture
    matrix drives with hand-written ffprobe dicts. ``edit_fps`` (when given) turns
    on the edit-grid drift diagnostic; ``source_media_hash`` / ``source_ref`` bind
    the header when the caller has the bytes (they never enter ``profile_digest``,
    so the document is byte-identical for the same input regardless of binding).
    """
    streams = ffprobe_json.get("streams") or []
    if not isinstance(streams, list):
        streams = []
    fmt = ffprobe_json.get("format") or {}
    if not isinstance(fmt, dict):
        fmt = {}
    video = next((s for s in streams if isinstance(s, dict)
                  and s.get("codec_type") == "video"), None) or {}
    audio = next((s for s in streams if isinstance(s, dict)
                  and s.get("codec_type") == "audio"), None)

    time_facts, rate, mode = _build_time(video, fmt)
    pic_facts, sar, dar, coded_w, coded_h = _build_picture(video)
    facts: dict[str, Any] = {
        "time": time_facts,
        "picture": pic_facts,
        "color": _build_color(video),
        "audio": _build_audio(audio),
        "container": _build_container(streams, fmt),
    }

    diagnostics = _diagnostics(
        facts, rate=rate, rate_mode=mode, sar=sar, dar=dar,
        coded_w=coded_w, coded_h=coded_h, edit_fps=edit_fps,
    )

    return {
        "schema": SCHEMA,
        "header": {
            "schema": SCHEMA,
            "source_media_hash": source_media_hash,
            "source_ref": source_ref,
        },
        "facts": facts,
        "diagnostics": diagnostics,
        # digest over FACTS ONLY — reproducible byte-for-byte for the same input.
        "profile_digest": hash_value(facts),
    }


# --------------------------------------------------------------- ffprobe shell


def technical_profile(path: Path, *, edit_fps: int | None = None,
                      source_ref: str | None = None,
                      timeout: float | None = DEFAULT_PROBE_TIMEOUT_S) -> dict[str, Any]:
    """Probe ``path`` with the same ffprobe invocation pattern as
    :func:`manju.media.probe.probe` (a SEPARATE call — probe.py is untouched),
    then normalize. Raises :class:`~manju.media.ffmpeg.MediaError` on an
    unreadable file, non-JSON output, or timeout, mirroring ``probe()``.

    The bytes are hashed once (``hash_file``) to bind the header to the exact
    media; ``source_ref`` defaults to the path string (the CLI passes a
    project-relative ref so no absolute path is stored).
    """
    path = Path(path)
    cmd = [
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MediaError(
            f"ffprobe timed out (>{timeout}s) for {path} — corrupt media or a "
            f"network-mounted path (#55)"
        ) from exc
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-15:])
        raise MediaError(f"ffprobe failed for {path}:\n{tail}")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc

    return normalize_probe_document(
        data, edit_fps=edit_fps,
        source_media_hash=hash_file(path),
        source_ref=source_ref if source_ref is not None else str(path),
    )


# --------------------------------------------------------------- storage (derived)


def technical_dir(project: Any) -> Path:
    """``reports/technical/`` — a deletable derived projection, never a build input."""
    return Path(project.root) / "reports" / "technical"


def _hash_stem(source_hash: str) -> str:
    return str(source_hash).split(":")[-1]


def report_path(project: Any, source_hash: str) -> Path:
    return technical_dir(project) / f"{_hash_stem(source_hash)}.json"


def _looks_like_hash(value: Any) -> bool:
    """A source hash is a non-empty ``algo:hex`` token or a bare hex digest."""
    if not isinstance(value, str) or len(value) < 8:
        return False
    stem = value.split(":")[-1]
    return len(stem) >= 8 and all(c in _HEX for c in stem)


def validate_profile(data: Any, source_hash: str | None = None) -> list[dict[str, Any]]:
    """Structured validation of a stored technical-profile document. Returns a
    list of blocking diagnostics; an empty list means a well-formed document.
    Checks schema, the source-hash FORM, the minimal structure, and — when
    ``source_hash`` is given — that the document is content-addressed to the media
    it is stored under (a tampered / mis-filed report fails). This is what makes a
    malformed / tampered report a *rejection*, never a silent consumption."""
    if not isinstance(data, dict):
        return [{"code": "PROFILE_NOT_OBJECT", "severity": "blocking",
                 "detail": "technical profile is not a JSON object"}]
    diags: list[dict[str, Any]] = []
    if data.get("schema") != SCHEMA:
        diags.append({"code": "PROFILE_SCHEMA_MISMATCH", "severity": "blocking",
                      "detail": f"schema {data.get('schema')!r} is not {SCHEMA!r}"})
    header = data.get("header")
    if not isinstance(header, dict):
        diags.append({"code": "PROFILE_HEADER_MISSING", "severity": "blocking",
                      "detail": "header is absent or not an object"})
        return diags
    smh = header.get("source_media_hash")
    if not _looks_like_hash(smh):
        diags.append({"code": "PROFILE_SOURCE_HASH_MALFORMED", "severity": "blocking",
                      "detail": "header.source_media_hash is missing or malformed"})
    elif source_hash is not None and _hash_stem(smh) != _hash_stem(source_hash):
        diags.append({"code": "PROFILE_SOURCE_HASH_MISMATCH", "severity": "blocking",
                      "detail": "profile is not content-addressed to the requested "
                                "media (tampered or mis-filed)"})
    if not isinstance(data.get("facts"), dict):
        diags.append({"code": "PROFILE_FACTS_MISSING", "severity": "blocking",
                      "detail": "facts block is absent or not an object"})
    return diags


def write_profile(project: Any, doc: dict[str, Any]) -> Path:
    """Materialise the derived profile content-addressed by the bound source hash.
    Atomic; deletable; never read by a build/authorization path."""
    src_hash = (doc.get("header") or {}).get("source_media_hash")
    if not src_hash:
        raise MediaError("cannot store a technical profile with no source hash")
    path = report_path(project, src_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    return path


def read_profile(project: Any, source_hash: str) -> dict[str, Any] | None:
    """Read a stored technical profile. A missing file, unreadable JSON, or a
    document that fails :func:`validate_profile` (malformed / tampered / mis-filed)
    all yield ``None`` — a structured rejection, never a consumed half-truth."""
    path = report_path(project, source_hash)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if validate_profile(data, source_hash):
        return None
    return data
