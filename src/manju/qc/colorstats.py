"""Deterministic color analysis & shot matching (AI_IDE_15 §10 WP6).

A PURE, read-only color-statistics layer over exact media/frame bytes. It absorbs
Forge Film's cross-model color-calibration IDEA — but never lets "histogram
similarity" masquerade as full aesthetic consistency, and never silently edits a
downstream i2v reference (§10). The flow is:

    exact frame bytes
    → color statistics (histogram / luma / white-balance / channel means)   [PIL]
    → ffprobe color metadata (space / transfer / matrix / range; UNKNOWN)    [probe]
    → reference-shot / style-frame compare (hash-bound)
    → READ-ONLY single-variable suggestions (LUT / exposure / WB / curve)
    → a human adopts → a NEW derived take via the EXISTING append-only repair-op
      path (media/repair_ops.py) or an explicit Timeline effect — the original
      take is NEVER overwritten.

Discipline (contract §10):
  * everything here is deterministic and derived — bound to the input's content
    hash; a reference compare is bound to the reference's content hash too;
  * an automatic histogram match is preview/advice ONLY — it changes no source
    bytes and no downstream request/reference (proven by :func:`histogram_match_
    preview`, which reads but never writes);
  * color metadata that cannot be identified is UNKNOWN, never guessed;
  * numpy is unavailable in this environment, so the statistics are computed from
    PIL's integer channel histograms (exact, no float accumulation drift).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..core.hashing import cache_key, hash_file, short_hash


class ColorStatsUnavailable(RuntimeError):
    """The deterministic color-stats layer needs Pillow (PIL) for exact integer
    channel histograms (numpy is absent by design, §10 WP6), and Pillow is not
    installed. A member of the house adapter-wall family
    (:class:`manju.exporters.native_draft.ExporterUnavailable`,
    :class:`manju.providers.tts.TtsUnavailable`,
    :class:`manju.media.ttspreview.PreviewUnavailable`): a missing optional
    dependency is a STRUCTURED, actionable condition — never a raw
    ``ImportError`` traceback through the CLI. Being a ``RuntimeError`` subclass,
    the CLI's existing RuntimeError / ``_fail`` handlers render it as one clean
    line. The fix it names is the optional extra: ``pip install
    "manju[colorstats]"``."""


SCHEMA = "manju.qc.colorstats/v1"
PREVIEW_SCHEMA = "manju.qc.colorstats.preview/v1"
COMPARE_SCHEMA = "manju.qc.colorstats.compare/v1"

# ffprobe never ran / a field is absent or a placeholder → this exact token.
UNKNOWN = "UNKNOWN"
# "gbr" is ffprobe's "raw RGB, no YCbCr matrix" sentinel — NOT an identified video
# matrix, so it reads as UNKNOWN for continuity purposes (never guessed).
_UNKNOWN_TOKENS = {None, "", "unknown", "N/A", "na", "reserved", "unspecified", "gbr"}
# still-image codecs carry no VIDEO color-continuity metadata — a frame's RGB
# container defaults (gbr / pc) are not the clip's tagged space/transfer/matrix/
# range. For a still frame every field is honestly UNKNOWN: probe the real clip.
_STILL_IMAGE_CODECS = {"png", "mjpeg", "bmp", "gif", "tiff", "webp", "apng",
                       "ppm", "pgm", "pam", "jpeg", "jpegls"}

# single-variable color-repair vocabulary (§10: LUT / 曝光 / 白平衡 / 曲线 / shot-match).
COLOR_VARIABLES = ("lut", "exposure", "white_balance", "curve", "shot_match")

# perceptual thresholds on the 0..255 channel-mean scale — deliberately coarse:
# a suggestion is advice, not a metric claim. Below these, two frames are "matched"
# for the purpose of a single-variable correction.
_LUMA_EPS = 2.0
_WB_EPS = 2.0
_PROBE_TIMEOUT_S = 30.0
_REC709 = (0.2126, 0.7152, 0.0722)


# ------------------------------------------------------------------ statistics


def _mean_from_hist(hist: list[int]) -> float:
    total = sum(hist)
    if total <= 0:
        return 0.0
    return sum(i * c for i, c in enumerate(hist)) / total


def color_stats(image_path: str | Path) -> dict[str, Any]:
    """Deterministic per-frame color statistics from the EXACT image bytes.

    Returns histogram (per RGB channel, 256 bins), channel means, Rec.709 luma
    mean, and a white-balance descriptor (r/g and b/g ratios + a warm/cool index).
    Bound to ``input_sha256`` — identical bytes always yield an identical result.
    PIL-only (integer histograms), so there is no float-accumulation nondeterminism.
    """
    # Adapter wall (§2.5): Pillow is an OPTIONAL extra, not a base dep. Its
    # absence is a structured, bilingual, actionable failure — never a raw
    # ModuleNotFoundError leaking through the CLI (mirrors edge_tts's ImportError
    # wall and the exporters' ExporterUnavailable).
    try:
        from PIL import Image
    except ImportError as exc:
        raise ColorStatsUnavailable(
            "颜色统计需要 Pillow(PIL)做精确整数直方图,但 Pillow 未安装 —— "
            '运行 pip install "manju[colorstats]" 安装该可选依赖后重试'
            "(其余 QC 检查与交付出口不受影响)。 "
            "Pillow is not installed: colorstats needs it for exact integer "
            "channel histograms — install the optional extra with "
            'pip install "manju[colorstats]" (the rest of QC and every delivery '
            "exit are unaffected)."
        ) from exc

    path = Path(image_path)
    with Image.open(path) as im:
        rgb = im.convert("RGB")
        size = list(rgb.size)
        flat = rgb.histogram()  # 768: R[0:256] G[256:512] B[512:768]
    hist = {"r": flat[0:256], "g": flat[256:512], "b": flat[512:768]}
    mean = {ch: _mean_from_hist(hist[ch]) for ch in ("r", "g", "b")}
    luma = sum(w * mean[ch] for w, ch in zip(_REC709, ("r", "g", "b")))
    g = mean["g"] or 1e-9
    warm_index = mean["r"] - mean["b"]
    if warm_index > _WB_EPS:
        temperature = "warm"
    elif warm_index < -_WB_EPS:
        temperature = "cool"
    else:
        temperature = "neutral"
    return {
        "schema": SCHEMA,
        "input_sha256": hash_file(path),
        "size": size,
        "histogram": hist,
        "channel_mean": mean,
        "luma_mean": luma,
        "white_balance": {
            "r_over_g": mean["r"] / g,
            "b_over_g": mean["b"] / g,
            "warm_index": warm_index,
            "temperature": temperature,
        },
    }


# ------------------------------------------------------------ color metadata


def color_metadata(media_path: str | Path) -> dict[str, str]:
    """The clip's color space / transfer / matrix / range from ffprobe (§10).

    Mapped from the ffprobe video-stream fields: ``space`` ← ``color_primaries``,
    ``transfer`` ← ``color_transfer``, ``matrix`` ← ``color_space`` (ffprobe's
    ``color_space`` is the YCbCr matrix coefficients), ``range`` ← ``color_range``.
    Any field ffprobe cannot identify (absent / placeholder), a still image with
    no video color tags, or an unreadable file → :data:`UNKNOWN` — never guessed.
    """
    path = Path(media_path)
    fields = {"space": UNKNOWN, "transfer": UNKNOWN, "matrix": UNKNOWN, "range": UNKNOWN}
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries",
             "stream=color_space,color_transfer,color_primaries,color_range,codec_name",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return fields
    if proc.returncode != 0:
        return fields
    try:
        streams = (json.loads(proc.stdout or "{}").get("streams") or [])
    except json.JSONDecodeError:
        return fields
    if not streams:
        return fields
    s = streams[0]
    # a still-image frame has no video color-continuity metadata — every field
    # UNKNOWN (probe the actual clip for real tags), never the RGB defaults.
    if str(s.get("codec_name") or "").lower() in _STILL_IMAGE_CODECS:
        return fields

    def norm(v: Any) -> str:
        if isinstance(v, str):
            v = v.strip()
        return UNKNOWN if (v in _UNKNOWN_TOKENS
                           or (isinstance(v, str) and v.lower() in _UNKNOWN_TOKENS)) else v

    return {
        "space": norm(s.get("color_primaries")),
        "transfer": norm(s.get("color_transfer")),
        "matrix": norm(s.get("color_space")),
        "range": norm(s.get("color_range")),
    }


# --------------------------------------------------- reference compare / suggest


def compare_to_reference(image_path: str | Path,
                         reference_path: str | Path) -> dict[str, Any]:
    """Compare a current frame's color to a hash-bound reference/style frame and
    emit READ-ONLY single-variable correction suggestions (§10).

    The result binds BOTH content hashes (``input_sha256`` / ``reference_sha256``)
    so a suggestion can never be silently reused against a different reference.
    Each suggestion names ONE variable from :data:`COLOR_VARIABLES` (never a
    multi-variable "just fix it"). ``adopt_via`` points at the EXISTING
    append-only repair-op path — adopting produces a NEW derived take, never an
    in-place overwrite; nothing here executes."""
    cur = color_stats(image_path)
    ref = color_stats(reference_path)
    luma_delta = cur["luma_mean"] - ref["luma_mean"]
    warm_delta = cur["white_balance"]["warm_index"] - ref["white_balance"]["warm_index"]

    suggestions: list[dict[str, Any]] = []
    if abs(warm_delta) > _WB_EPS:
        suggestions.append(_suggestion(
            "white_balance",
            f"白平衡相对参考偏{'暖' if warm_delta > 0 else '冷'}"
            f"(warm_index Δ={warm_delta:.1f})",
            {"warm_index_delta": warm_delta}))
    if abs(luma_delta) > _LUMA_EPS:
        suggestions.append(_suggestion(
            "exposure",
            f"整体曝光相对参考{'偏亮' if luma_delta > 0 else '偏暗'}"
            f"(luma Δ={luma_delta:.1f})",
            {"luma_delta": luma_delta}))
    match = not suggestions
    if not match and not suggestions:
        # (unreachable given the guard above, kept explicit) holistic fallback.
        suggestions.append(_suggestion("shot_match", "整体 shot-match 对齐参考", {}))

    input_sha = cur["input_sha256"]
    ref_sha = ref["input_sha256"]
    return {
        "schema": COMPARE_SCHEMA,
        "input_sha256": input_sha,
        "reference_sha256": ref_sha,
        "match": match,
        "deltas": {"luma": luma_delta, "warm_index": warm_delta},
        "suggestions": suggestions,
        # adopting a suggestion is a HUMAN step through the existing append-only
        # repair-op path — a new derived take, never an overwrite (§10 pins).
        # W4 honesty fix: the document used to advertise `manju repair --op
        # grade`, an op that DOES NOT EXIST (retime|extend|trim|inout|croppad|
        # voice) — a reader typing it got an error. UNKNOWN is never dressed as
        # PASS and a pointer is never dressed as a command: state the fact.
        "adopt_via": {
            "path": "repair_op",
            "implemented": False,
            "command": None,
            "append_only": True,
            "overwrites_source": False,
            "input_sha256": input_sha,
            "reference_sha256": ref_sha,
            "note": "尚无 grade 修复操作 — 手动路径:在外部按建议校色,登记为新 take"
                    "(追加式,绝不覆盖原 take),再重新跑角色/产品文字/闪烁/交付 QC"
                    "(§10),避免颜色接近但语义被破坏",
        },
        "requires_confirmation": True,
        "do_not_execute_automatically": True,
    }


def _suggestion(variable: str, reason: str, measured: dict) -> dict[str, Any]:
    return {
        "variable": variable,
        "single_variable": True,   # §9.3 one-variable rule
        "reason": reason,
        "measured": measured,
        "preview_available": True,
        "do_not_execute_automatically": True,
    }


def histogram_match_preview(source_path: str | Path,
                            reference_path: str | Path) -> dict[str, Any]:
    """A histogram-match PREVIEW descriptor. Proves the §10 non-negotiable: an
    automatic histogram match is preview/advice ONLY — it changes NO source bytes
    and NO downstream i2v reference/request. This function READS the two frames'
    hashes and returns provenance; it writes nothing, so the caller's source and
    any downstream request digest are provably unaffected."""
    src_sha = hash_file(Path(source_path))
    ref_sha = hash_file(Path(reference_path))
    return {
        "schema": PREVIEW_SCHEMA,
        "is_preview": True,
        "mutates_source": False,
        "mutates_downstream_request": False,
        "source_sha256": src_sha,
        "reference_sha256": ref_sha,
        "preview_digest": short_hash(cache_key(PREVIEW_SCHEMA, src_sha, ref_sha)),
        "note": ("histogram match 仅作 preview/建议:不改变 source 字节,"
                 "也不偷改下游 i2v reference/request(§10)"),
    }
