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


def sample_frames(media: Path, dest_dir: Path, *, count: int = 3) -> list[Path]:
    """Sample frames at 25/50/75% so a token visible anywhere in the shot is
    seen. Uses ffprobe duration; falls back to the first frame on failure."""
    frames: list[Path] = []
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(media)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        duration = float(out)
    except (subprocess.CalledProcessError, ValueError, OSError):
        duration = 0.0
    points = [duration * p for p in (0.25, 0.5, 0.75)] if duration > 0 else [0.0]
    for i, t in enumerate(points[:count]):
        frame = dest_dir / f"{media.stem}_qc_{i}.jpg"
        proc = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{t:.3f}", "-i", str(media), "-frames:v", "1", "-q:v", "3",
             str(frame)],
            capture_output=True,
        )
        if proc.returncode == 0 and frame.exists():
            frames.append(frame)
    return frames


def ocr_frame(frame: Path) -> str | None:
    """Tesseract OCR (Chinese + English). None = OCR unavailable/failed —
    callers must treat that as UNKNOWN, never as 'text absent'."""
    if not tesseract_available():
        return None
    proc = subprocess.run(
        ["tesseract", str(frame), "stdout", "-l", "chi_sim+eng", "--psm", "6"],
        capture_output=True, text=True,
    )
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
    """Run an ffmpeg detector filter; True = detected, None = probe failed."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(media), "-vf", filter_expr,
         "-an", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return marker in proc.stderr


def black_detected(media: Path) -> bool | None:
    return _detect(media, "blackdetect=d=1.0:pix_th=0.10", "blackdetect")


def freeze_detected(media: Path) -> bool | None:
    return _detect(media, "freezedetect=n=-60dB:d=2", "freeze_start")


# ------------------------------------------------------------ mcp-video


def mcp_video_gate(media: Path, *, min_score: float = 80.0) -> tuple[Verdict, str]:
    """§2.5 ②: prefer mcp-video's quality gate when the library is present;
    absence or any internal error degrades to UNKNOWN behind the adapter wall."""
    try:
        from mcp_video import assert_quality  # type: ignore[import-not-found]
    except ImportError:
        return Verdict.UNKNOWN, "mcp-video not installed"
    try:
        result = assert_quality(str(media), min_score=min_score)
    except Exception as exc:  # adapter wall: their failure is our UNKNOWN
        return Verdict.UNKNOWN, f"mcp-video gate errored: {exc}"
    passed = result.get("passed", result.get("ok"))
    score = result.get("score")
    note = f"mcp-video score={score}" if score is not None else "mcp-video gate"
    if passed is True:
        return Verdict.PASS, note
    if passed is False:
        return Verdict.FAIL, note + f" (< {min_score})"
    return Verdict.UNKNOWN, note + " (unrecognized result shape)"


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
        if black_detected(media):
            items.append(QCItem(
                "warn", "content", shot.id,
                f"{take.name}: 检出 ≥1s 黑屏片段(blackdetect)",
                suggestion="redo 或人工确认是否有意为之",
            ))
        if freeze_detected(media):
            items.append(QCItem(
                "warn", "content", shot.id,
                f"{take.name}: 检出 ≥2s 画面冻结(freezedetect)",
                suggestion="redo 或人工确认是否有意为之",
            ))
        verdict, note = mcp_video_gate(media)
        if verdict is Verdict.FAIL:
            items.append(QCItem(
                "warn", "content", shot.id, f"{take.name}: {note}",
                suggestion="mcp-video 质量门未过;redo 或人工复核",
            ))

    if agent_tier:
        vision = vision_provider_id()
        hint = (f"qc_vision provider '{vision}' 已配置,随生成阶段执行一致性初筛"
                if vision else
                "未配置 qc_vision provider(§8.6 type: vision);由 agent 直接终审")
        items.append(QCItem(
            "warn", "content", shot.id,
            "待 agent 终审的 must_show 项: " + "; ".join(agent_tier),
            suggestion=f"看 reports/frames/{shot.id}.jpg 判定;{hint}",
        ))
    return items
