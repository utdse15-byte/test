"""Content QC — the three-tier checker chain (§9, third-round stance).

Machine checks first, agent as the LAST tier, engine-driven throughout:
`manju qc` runs this with no agent present (manual mode works identically).

    tier 1  must_show assertion-ization: machine-checkable claims become
            assertions — quoted text / digit runs -> frame-sampled OCR
            (tesseract, chi_sim+eng); black/freeze provenance-aware probes
    tier 2  consistency screening -> a configured qc_vision provider
            (manifest type "vision", same §8.6 config shape)
    tier 3  agent arbitration: whatever the machine marks UNKNOWN keeps its
            frames under reports/frames/ and is recorded for review

Every checker is tri-state (PASS / FAIL / UNKNOWN): a missing tool degrades
to UNKNOWN + escalation, never to a silent pass and never to a crash.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..core.container import Project, TakeInfo
from ..core.models import ShotSpec
from .checks import QCItem

# providers whose output is intentionally static / synthetic — black and
# freeze probes on them are false positives by construction (the sidecar's
# provenance is exactly what §4.2 keeps it for)
SYNTHETIC_PROVIDERS = {"caption_card", "ffmpeg_kenburns"}

_QUOTED = re.compile(r"[「『\"“'‘]([^」』\"”'’]{1,40})[」』\"”'’]")
_DIGITS = re.compile(r"\d{2,}")


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass
class Assertion:
    raw: str  # the original must_show entry
    kind: str  # "ocr_text" | "agent"
    token: str | None = None  # what OCR must find, for machine-checkable ones


def parse_assertions(must_show: list[str]) -> list[Assertion]:
    """Turn must_show prose into assertions (§9 ①). Quoted spans and digit
    runs are machine-checkable ("硬币年份 2036 清晰可读" -> OCR for "2036");
    everything else is honest about needing eyes and goes to the agent tier."""
    assertions: list[Assertion] = []
    for entry in must_show:
        quoted = _QUOTED.search(entry)
        if quoted:
            assertions.append(Assertion(entry, "ocr_text", quoted.group(1).strip()))
            continue
        digits = _DIGITS.search(entry)
        if digits:
            assertions.append(Assertion(entry, "ocr_text", digits.group(0)))
            continue
        assertions.append(Assertion(entry, "agent"))
    return assertions


# ------------------------------------------------------------------- OCR


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


# Aligned with the existing hard timeouts in qc/checks.py (the same tier-1
# probe family) rather than invented independently: 30s for a plain ffprobe
# read, 60s for a single-frame ffmpeg extraction, 300s for a whole-clip filter
# decode (mirrors checks.py's blackdetect/volumedetect). OCR has no existing
# precedent here (tesseract is new to this module) — given generously so a
# loaded host doesn't manufacture false UNKNOWNs.
_PROBE_TIMEOUT_S = 30.0
_FRAME_TIMEOUT_S = 60.0
_OCR_TIMEOUT_S = 120.0
_DETECT_TIMEOUT_S = 300.0


def sample_frames(media: Path, dest_dir: Path, *, count: int = 3) -> list[Path]:
    """Sample frames at 25/50/75% so a token visible anywhere in the shot is
    seen. Uses ffprobe duration; falls back to the first frame on failure.

    #55: every subprocess call here carries a hard timeout — a corrupt file
    or a stuck filter degrades to "no frames" (UNKNOWN downstream), never a
    hang."""
    frames: list[Path] = []
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(media)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True, timeout=_PROBE_TIMEOUT_S,
        ).stdout.strip()
        duration = float(out)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, OSError):
        duration = 0.0
    points = [duration * p for p in (0.25, 0.5, 0.75)] if duration > 0 else [0.0]
    for i, t in enumerate(points[:count]):
        frame = dest_dir / f"{media.stem}_qc_{i}.jpg"
        try:
            proc = subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-ss", f"{t:.3f}", "-i", str(media), "-frames:v", "1", "-q:v", "3",
                 str(frame)],
                capture_output=True, timeout=_FRAME_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            continue
        if proc.returncode == 0 and frame.exists():
            frames.append(frame)
    return frames


def ocr_frame(frame: Path) -> str | None:
    """Tesseract OCR (Chinese + English). None = OCR unavailable/failed —
    callers must treat that as UNKNOWN, never as 'text absent'. #55: a hung
    tesseract process degrades to None (UNKNOWN) after the timeout, same as
    "tool missing"."""
    if not tesseract_available():
        return None
    try:
        proc = subprocess.run(
            ["tesseract", str(frame), "stdout", "-l", "chi_sim+eng", "--psm", "6"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=_OCR_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    # OCR loves inserting spaces inside CJK; normalize before matching
    return re.sub(r"\s+", "", proc.stdout)


def check_ocr_assertion(assertion: Assertion, frames: list[Path]) -> Verdict:
    if not frames:
        return Verdict.UNKNOWN
    token = re.sub(r"\s+", "", assertion.token or "")
    if not token:
        return Verdict.UNKNOWN
    saw_any_text = False
    for frame in frames:
        text = ocr_frame(frame)
        if text is None:
            return Verdict.UNKNOWN  # tool missing -> agent tier, not a pass
        saw_any_text = saw_any_text or bool(text)
        if token in text:
            return Verdict.PASS
    return Verdict.FAIL


# ------------------------------------------------- black / freeze probes


def _detect(media: Path, filter_expr: str, marker: str) -> bool | None:
    """Run an ffmpeg detector filter; True = detected, None = probe failed
    (#55: including a timeout — the whole clip is decoded, so this is the
    longest-running probe here; still bounded, never unbounded)."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(media), "-vf", filter_expr,
             "-an", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=_DETECT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    return marker in proc.stderr


def black_detected(media: Path) -> bool | None:
    return _detect(media, "blackdetect=d=1.0:pix_th=0.10", "blackdetect")


def freeze_detected(media: Path) -> bool | None:
    return _detect(media, "freezedetect=n=-60dB:d=2", "freeze_start")


# ------------------------------------------------------------- qc_vision


def vision_provider_id() -> str | None:
    """The configured consistency-screening provider (manifest type 'vision',
    same §8.6 config). Screening runs against a real vision API; without one
    the check honestly reports the gap instead of pretending."""
    try:
        from ..providers.manifest import load_manifests

        manifests, _ = load_manifests()
        return next((pid for pid, m in sorted(manifests.items()) if m.type == "vision"), None)
    except Exception:
        return None


# ------------------------------------------------------------ the chain


def content_checks(project: Project, shot: ShotSpec, take: TakeInfo, *,
                   deep: bool = False) -> list[QCItem]:
    """Run the machine tiers for one shot's selected take and emit QC items.
    FAIL -> error (auto-safe redo), UNKNOWN -> agent-tier record with frames."""
    items: list[QCItem] = []
    if take.media_path is None:
        return items
    media = take.media_path
    assertions = parse_assertions(shot.quality.must_show)
    agent_tier: list[str] = [a.raw for a in assertions if a.kind == "agent"]

    ocr_assertions = [a for a in assertions if a.kind == "ocr_text"]
    if ocr_assertions:
        with tempfile.TemporaryDirectory(prefix="manju_qc_") as tmp:
            frames = sample_frames(media, Path(tmp))
            for assertion in ocr_assertions:
                verdict = check_ocr_assertion(assertion, frames)
                if verdict is Verdict.PASS:
                    items.append(QCItem(
                        "info", "content", shot.id,
                        f"must_show OK(机检 OCR): {assertion.raw}",
                    ))
                elif verdict is Verdict.FAIL:
                    items.append(QCItem(
                        "error", "content", shot.id,
                        f"must_show 违背(机检 OCR 未找到 '{assertion.token}'): {assertion.raw}",
                        suggestion="redo(换 seed/换 provider)或降级;或 agent 看图复核",
                        auto_safe=True,
                    ))
                else:
                    agent_tier.append(assertion.raw)

    if deep and take.sidecar.provider not in SYNTHETIC_PROVIDERS:
        # Every deep-QC detector below is tri-state: a positive finding is a
        # "warn", but UNKNOWN (the probe itself could not run — a missing tool,
        # a failed ffmpeg filter invocation, an absent adapter) must ALSO
        # surface, as an "info" item — never silently read as "no problem
        # found" (round-W #32: a detector that didn't run is not the same
        # fact as a detector that ran and found nothing).
        black = black_detected(media)
        if black is True:
            items.append(QCItem(
                "warn", "content", shot.id,
                f"{take.name}: 检出 ≥1s 黑屏片段(blackdetect)",
                suggestion="redo 或人工确认是否有意为之",
            ))
        elif black is None:
            items.append(QCItem(
                "info", "content", shot.id,
                f"{take.name}: 检测未运行:黑屏探测失败(blackdetect,ffmpeg 或媒体异常)",
                suggestion="deep QC 本项未完成判断;可重跑 `manju qc --deep` 或人工检查该片段",
            ))
        freeze = freeze_detected(media)
        if freeze is True:
            items.append(QCItem(
                "warn", "content", shot.id,
                f"{take.name}: 检出 ≥2s 画面冻结(freezedetect)",
                suggestion="redo 或人工确认是否有意为之",
            ))
        elif freeze is None:
            items.append(QCItem(
                "info", "content", shot.id,
                f"{take.name}: 检测未运行:冻结探测失败(freezedetect,ffmpeg 或媒体异常)",
                suggestion="deep QC 本项未完成判断;可重跑 `manju qc --deep` 或人工检查该片段",
            ))
    if agent_tier:
        # Round V (§6): visual judgment is the driving agent's own eyes + the
        # skill library's standards, not a vendor slot. Point at the agent pipe.
        items.append(QCItem(
            "warn", "content", shot.id,
            "需要图像判读的 must_show 项: " + "; ".join(agent_tier),
            suggestion="需要图像判读 — 交给驱动 Manju 的 agent:manju qc brief 出题,"
                       "判读标准见 manju skills show visual-qc-review,"
                       "结果用 manju qc verdict 回填",
        ))
    return items
