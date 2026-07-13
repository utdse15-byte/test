"""QC checks (§9) — three layers, by increasing depth.

    existence  referenced files are present and ffprobe-readable
    technical  durations vs timeline (tolerance), resolution, caption sanity,
               final-render checks, optional deep black/silence detection
    content    "eyes for the agent": extract one mid-point frame per shot so an
               agent can judge character/scene consistency; that judgment stays
               OUTSIDE the engine (§9).

QC never crashes on a broken external tool: probing and detectors are wrapped so
a failed detector degrades to "no finding", never to a failed report.
"""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from ..build.stale import ShotBuildStatus, ShotState, evaluate_all
from ..core.container import Project
from ..core.models import CAPTION_ROLES, ProbeInfo, ProjectConfig, Timeline

# tolerances (§9)
_DURATION_TOL_MS = 150      # per-clip source vs timeline duration
_FINAL_DURATION_TOL_MS = 500  # final render vs timeline total
_BLACKDETECT_D = 0.5        # minimum black-span seconds
_SILENCE_DB = -60.0         # mean_volume below this = near silent (final render)

# round-Q QC additions (§9)
_SILENCE_RMS_DB = -50.0     # a voice clip whose whole-clip RMS is at/under this
                            # is effectively silent (TTS produced silence / wrong file)
_CLIP_PEAK_DB = -0.1        # voice sample peak at/above this ≈ digital clipping
_CAPTION_OVERLAP_TOL_MS = 120  # caption bleed up to this is a warn; beyond it two
                               # captions truly share the screen → error
_GARBLED_CTRL_RATIO = 0.30  # >30% control/unprintable chars in a cue → garbled
# a run of ≥3 Latin-1-supplement bytes is the fingerprint of UTF-8 text that was
# decoded as latin-1 (每个 CJK 字 → 形如 "ä½ "/"å¥½" 的三字节乱码序列)
_MOJIBAKE_RE = re.compile("[\u00c0-\u00ff][\u0080-\u00ff]{2,}")


@dataclass
class QCItem:
    level: str    # "error" | "warn" | "info"
    area: str     # "existence" | "technical" | "content"
    subject: str  # shot id / "timeline" / "final"
    message: str
    suggestion: str = ""
    auto_safe: bool = False

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "area": self.area,
            "subject": self.subject,
            "message": self.message,
            "suggestion": self.suggestion,
            "auto_safe": self.auto_safe,
        }


@dataclass
class QCReport:
    items: list[QCItem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(it.level == "error" for it in self.items)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "items": [it.to_dict() for it in self.items]}

    def add(self, *args, **kwargs) -> None:
        self.items.append(QCItem(*args, **kwargs))


# --------------------------------------------------------------------- probe


def _probe_info(path: Path) -> ProbeInfo | None:
    """Prefer ``manju.media.probe``; fall back to a direct ffprobe call so QC
    works even before the media package lands."""
    try:
        from ..media.probe import probe as media_probe  # lazy

        info = media_probe(path)
        if info is not None:
            return info
    except Exception:
        pass
    return _ffprobe(path)


def _ffprobe(path: Path) -> ProbeInfo | None:
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout)
    except Exception:
        return None
    streams = data.get("streams", [])
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})

    duration_ms = None
    dur = fmt.get("duration") or (vstream or {}).get("duration")
    if dur is not None:
        try:
            duration_ms = int(round(float(dur) * 1000))
        except (TypeError, ValueError):
            duration_ms = None

    width = height = None
    fps = None
    if vstream:
        width = vstream.get("width")
        height = vstream.get("height")
        rate = vstream.get("avg_frame_rate") or vstream.get("r_frame_rate")
        if rate and "/" in rate:
            num, den = rate.split("/", 1)
            try:
                den_f = float(den)
                fps = float(num) / den_f if den_f else None
            except (TypeError, ValueError):
                fps = None
    return ProbeInfo(
        duration_ms=duration_ms,
        width=width,
        height=height,
        fps=fps,
        has_audio=astream is not None,
    )


# ----------------------------------------------------------- audio detectors


@dataclass
class AudioStats:
    """Whole-clip audio measurements from a single ``astats`` pass. ``-inf`` (a
    digitally-silent clip) is preserved as ``float("-inf")``."""

    peak_db: float | None  # sample peak, dBFS
    rms_db: float | None   # overall RMS level, dB


def _parse_astats_db(stderr: str, label: str) -> float | None:
    m = re.search(rf"{re.escape(label)}:\s*(-?inf|-?\d+(?:\.\d+)?)", stderr)
    if not m:
        return None
    value = m.group(1)
    return float("-inf") if "inf" in value else float(value)


def _probe_audio_stats(path: Path) -> AudioStats | None:
    """One ffmpeg ``astats`` pass → peak + RMS (dB). Returns ``None`` when the
    file has no audio or the detector fails — QC degrades to 'no finding',
    never to a crash (module docstring contract)."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
             "-af", "astats=measure_perchannel=none", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    stderr = proc.stderr or ""
    peak = _parse_astats_db(stderr, "Peak level dB")
    rms = _parse_astats_db(stderr, "RMS level dB")
    if peak is None and rms is None:
        return None
    return AudioStats(peak_db=peak, rms_db=rms)


def _fmt_db(value: float | None) -> str:
    if value is None or value == float("-inf"):
        return "-inf"
    return f"{value:.1f}"


# ----------------------------------------------------------- garbled text


def _is_control_char(ch: str) -> bool:
    """True for control/format/surrogate/unassigned chars, except the ordinary
    whitespace a caption legitimately carries."""
    return unicodedata.category(ch)[0] == "C" and ch not in "\n\r\t"


def _garbled_reason(text: str) -> str | None:
    """Return a short reason string if ``text`` looks like 乱码 (garbled), else
    ``None``. Pure text heuristics — no false positives on valid CJK / accented
    Latin / emoji (each isolated accent stays under the run + ratio thresholds)."""
    if not text:
        return None
    if "�" in text:  # U+FFFD replacement char = bytes lost on decode
        return "包含 U+FFFD 替换符(解码丢字/编码不符)"
    if _MOJIBAKE_RE.search(text):
        return "疑似 UTF-8 被按 latin-1 解码的乱码序列(如 Ã/å 连片)"
    total = len(text)
    bad = sum(1 for ch in text if _is_control_char(ch))
    if total and bad / total > _GARBLED_CTRL_RATIO:
        return f"不可打印/控制字符占比 {bad / total:.0%} 超过 30%"
    return None


# ----------------------------------------------------------------- run_qc


def run_qc(
    project: Project,
    timeline: Timeline | None = None,
    *,
    deep: bool = False,
    extract_frames: bool = True,
) -> QCReport:
    report = QCReport()
    config = project.load_config()
    if timeline is None:
        timeline = project.load_timeline()

    statuses = evaluate_all(project)
    probe_cache: dict[Path, ProbeInfo | None] = {}
    astats_cache: dict[Path, AudioStats | None] = {}

    def probe(path: Path) -> ProbeInfo | None:
        if path not in probe_cache:
            probe_cache[path] = _probe_info(path)
        return probe_cache[path]

    def astats(path: Path) -> AudioStats | None:
        # same per-run cache pattern as ``probe`` — one astats pass per file
        if path not in astats_cache:
            astats_cache[path] = _probe_audio_stats(path)
        return astats_cache[path]

    selected: dict[str, tuple[Path, ProbeInfo | None]] = {}

    _existence_shots(project, report, statuses, probe, selected)
    _staleness_info(report, statuses)
    _voice_info(project, report)
    _transition_override_advisories(project, report)
    if timeline is not None:
        _existence_timeline(project, report, timeline, probe)
        _existence_audio_policy(project, report, timeline)
        _audio_advisories(project, report, timeline)
        _technical_clips(project, report, timeline, probe)
        _technical_captions(project, report, timeline)
        _technical_timeline_conflicts(project, report, timeline)
        _technical_garbled_captions(project, report, timeline)
        _technical_voice_audio(project, report, timeline, astats)
    _packaging_checks(project, report, timeline)
    _technical_take_resolution(project, report, config, selected)
    _final_render(project, report, config, timeline, deep)
    if extract_frames:
        _content_frames(project, report, selected)
    _content_checkers(project, report, statuses, deep)
    _content_agent_verdicts(project, report)
    _content_qc_coverage(project, report)
    _mention_checks(project, report)

    return report


def _content_agent_verdicts(project, report) -> None:
    """Round V (§6, goal item 6); widened round X (agent XB, user pain #2):
    fold the driving agent's/human's visual verdicts (reports/qc_agent.jsonl)
    into the content tier — both per-SHOT verdicts and cross-shot CONSISTENCY
    verdicts (§ qc/agent_review module docstring). Matching verdicts surface as
    [AI判读] items at the mapped level; a regenerated take (or unit member)
    stales its verdict into one info nudge. Deterministic and crash-proof
    (never breaks QC itself)."""
    try:
        from .agent_review import agent_verdict_items

        report.items.extend(agent_verdict_items(project))
    except Exception as exc:  # a broken log never breaks QC
        report.add("info", "content", "qc_agent",
                   f"AI 判读记录读取失败,已跳过:{exc}")


def _content_qc_coverage(project, report) -> None:
    """Round X (agent XB, user pain #2): ONE info item surfacing AI-judgment
    coverage gaps — shots and consistency units that have NEVER been AI/human
    -judged (as opposed to merely stale, which already gets its own item(s)
    above). Silent when there are no gaps; degrades to a skip note on any
    failure, never fails QC itself."""
    try:
        from .agent_review import qc_coverage

        cov = qc_coverage(project)
        gaps = cov.get("summary", {}).get("gaps", 0)
        if gaps:
            report.add(
                "info", "content", "qc_coverage",
                f"{gaps} 个镜头/一致性组合未经 AI 判读",
                suggestion="manju qc brief 出题(单镜)/ manju qc brief --mode consistency "
                           "出题(一致性组合)/ manju qc coverage 查看明细",
            )
    except Exception as exc:  # a broken coverage read never breaks QC
        report.add("info", "content", "qc_coverage",
                   f"AI 判读覆盖率读取失败,已跳过:{exc}")


def _content_checkers(project, report, statuses, deep: bool) -> None:
    """§9 third-round stance: the content tier is a machine checker chain the
    engine runs itself — no agent required. must_show assertions always run
    for shots that declare them; black/freeze/mcp-video probes join on deep."""
    from .content import content_checks

    for st in statuses:
        if st.take is None or st.take.media_path is None:
            continue
        try:
            shot = project.load_shot(st.shot_id)
            report.items.extend(content_checks(project, shot, st.take, deep=deep))
        except Exception as exc:  # a broken checker never breaks QC itself
            report.add("warn", "content", st.shot_id,
                       f"content checker chain errored: {exc}",
                       suggestion="agent 终审;或查看 manju doctor")


# --------------------------------------------------------- existence layer


def _existence_shots(project, report, statuses, probe, selected) -> None:
    for st in statuses:
        if st.selected_take is None:
            continue
        take = st.take
        if take is None or take.media_path is None or not take.media_path.exists():
            report.add(
                "error", "existence", st.shot_id,
                f"selected take {st.selected_take!r} has no media file on disk (missing)",
                suggestion="regenerate the shot or re-import media",
            )
            continue
        info = probe(take.media_path)
        if info is None:
            report.add(
                "error", "existence", st.shot_id,
                f"selected take media not readable by ffprobe: "
                f"{project.relpath(take.media_path)}",
                suggestion="the file may be corrupt — regenerate or re-import",
            )
            continue
        selected[st.shot_id] = (take.media_path, info)


def _staleness_info(report, statuses: list[ShotBuildStatus]) -> None:
    """Surface build/staleness state as info items (§4.3, §9)."""
    notes = {
        ShotState.STALE: "shot spec changed after the selected take was generated (stale)",
        ShotState.MISSING: "no takes yet — build will generate",
        ShotState.NEEDS_SELECTION: "takes exist but none is selected",
    }
    for st in statuses:
        msg = notes.get(st.state)
        if msg:
            report.add("info", "existence", st.shot_id, msg)


def _voice_info(project, report) -> None:
    """Voice staleness as QC info (M3): the film still builds with a stale
    voice — the selected line simply predates the current text (§4.3)."""
    try:
        from ..build.voice import VoiceState, evaluate_all_voices

        for vs in evaluate_all_voices(project):
            if vs.state == VoiceState.STALE:
                report.add(
                    "info", "existence", vs.shot_id,
                    "voice take predates the current dialogue/voice reference (stale)",
                    suggestion=f"manju voice {vs.shot_id} 重配音(§4.3:默认不自动重做)",
                )
            elif vs.state == VoiceState.MISSING:
                report.add(
                    "info", "existence", vs.shot_id,
                    "dialogue has no voice take yet",
                    suggestion="配置 tts manifest 后 build 自动补齐,或手放 voice_take_NN.wav",
                )
    except Exception:
        pass  # advisory only


def _existence_timeline(project, report, timeline: Timeline, probe) -> None:
    def check(source: str, label: str, *, readable: bool) -> None:
        try:
            path = project.resolve(source)
        except Exception:
            report.add("error", "existence", "timeline",
                       f"{label} source path is invalid: {source}")
            return
        if not path.exists():
            report.add("error", "existence", "timeline",
                       f"{label} source missing: {source}")
            return
        if readable and probe(path) is None:
            report.add("error", "existence", "timeline",
                       f"{label} source not readable by ffprobe: {source}")

    for clip in timeline.tracks.video:
        if clip.shot.startswith("__"):
            continue  # packaging intro/outro cards → _packaging_checks owns them
        check(clip.source, f"video {clip.shot}/{clip.take}", readable=True)
    for clip in timeline.tracks.voice:
        check(clip.source, "voice", readable=False)
    for clip in timeline.tracks.music:
        check(clip.source, "music", readable=False)


def _existence_audio_policy(project, report, timeline: Timeline) -> None:
    """Audio policy checks (§9), read from rules against the compiled video
    track so both halves of a skip are surfaced:

      - a missing SFX / ambient / transition-sound source file → error;
      - an SFX anchor that names a shot NOT on the timeline → warn (the
        compiler deterministically drops that SFX, so without this the clip
        would silently vanish).
    """
    from ..timeline.anchors import resolve_anchor

    audio = project.load_rules().audio
    video_clips = list(timeline.tracks.video)

    def check_file(source: str, label: str) -> None:
        try:
            path = project.resolve(source)
        except Exception:
            report.add("error", "existence", "timeline",
                       f"{label} source path is invalid: {source}",
                       suggestion="use a project-relative path under the project root")
            return
        if not path.exists():
            report.add("error", "existence", "timeline",
                       f"{label} source missing: {source}",
                       suggestion="drop the audio file in media/imports/ and point "
                                  "the audio policy at it (rules.yaml → audio)")

    for i, spec in enumerate(audio.sfx):
        check_file(spec.source, f"sfx #{i}")
        resolved = resolve_anchor(spec.at, spec.offset_ms, video_clips)
        if resolved is None:
            report.add(
                "warn", "technical", "timeline",
                f"sfx #{i} anchor {spec.at!r} does not resolve — no shot on the "
                "timeline matches it, so this SFX is skipped",
                suggestion='anchor grammar: "" (absolute), "shot:<id>", '
                           '"shot:<id>:start" or "shot:<id>:end"; check the shot id',
            )
        elif resolved >= timeline.duration_ms:
            report.add(
                "warn", "technical", "timeline",
                f"sfx #{i} anchor {spec.at!r} resolves at/past the end of the "
                f"film ({resolved}ms ≥ {timeline.duration_ms}ms) — it would be "
                "inaudible, so it is skipped",
                suggestion="reduce offset_ms or anchor to an earlier point",
            )
    if audio.ambient.source:
        check_file(audio.ambient.source, "ambient")
    if audio.transition_sound:
        check_file(audio.transition_sound, "transition sound")


def _audio_advisories(project, report, timeline: Timeline) -> None:
    """Rule-based audio-policy suggestions (§0: deliberately rule-based, no LLM
    in-tool). Info-level nudges toward a fuller default audio policy, each with
    a concrete rules.yaml action. All are gated on a voice track actually being
    on the timeline, so a silent film — or a project with no timeline at all
    (this function is only called when one exists) — is never nagged."""
    if not timeline.tracks.voice:
        return  # no voice on the timeline → no audio-policy advice to give

    rules = project.load_rules()
    music = rules.music
    ambient = rules.audio.ambient
    # count real content shots (packaging intro/outro cards carry "__" shot ids)
    n_shots = sum(1 for c in timeline.tracks.video if not c.shot.startswith("__"))

    if not music.source:
        report.add(
            "info", "technical", "timeline",
            "voice is on the timeline but no background music is configured — "
            "a BGM bed lifts the film's overall feel",
            suggestion="set rules.yaml → music.source to a project-relative "
                       "audio file (e.g. media/imports/bgm.mp3)",
        )
    elif not music.ducking:
        # music is set and voice is present, but the BGM will not dip under speech
        report.add(
            "info", "technical", "timeline",
            "background music is configured over voice, but music ducking is "
            "off — the BGM can fight the dialogue",
            suggestion="set rules.yaml → music.ducking: true so the BGM dips "
                       "under speech (tune rules.yaml → music.duck_* to taste)",
        )

    if not ambient.source and n_shots >= 4:
        report.add(
            "info", "technical", "timeline",
            f"the film has {n_shots} shots and voice but no ambient bed — a low "
            "room-tone/atmos bed under the whole film smooths the scene cuts",
            suggestion="set rules.yaml → audio.ambient.source to a looped "
                       "room-tone/atmosphere file",
        )


# --------------------------------------------------------- packaging layer


def _transition_override_advisories(project, report) -> None:
    """Round U: sanity for ``rules.transition_overrides`` — an unknown out-edge
    key or a never-curated type is an advisory, never a crash (the same stance
    as TRANSITION_TYPES: human/agent-edited truth; the compiler keeps unknown
    keys inert and the render degrades unknown types to dip-to-black)."""
    try:
        rules = project.load_rules()
    except Exception:
        return  # unreadable rules are `manju check`'s finding, not QC's
    overrides = getattr(rules, "transition_overrides", None) or {}
    if not overrides:
        return
    from ..core.models import TRANSITION_TYPES

    known = set(project.shot_ids()) | {"__intro__", "__outro__"}
    for key, spec in overrides.items():
        if key not in known:
            report.add(
                "warn", "technical", key,
                f"transition_overrides 中的 “{key}” 不是任何镜头 id(也不是 __intro__/__outro__),"
                "该转场覆盖不会生效",
                suggestion="检查 timeline/rules.yaml 里 transition_overrides 的键名是否和镜头 id 一致",
            )
        elif spec is not None and spec.type not in TRANSITION_TYPES:
            report.add(
                "warn", "technical", key,
                f"transition_overrides[{key}] 使用了未收录的转场类型 “{spec.type}”,"
                "渲染时将按黑场过渡(dip-to-black)处理",
                suggestion=f"可用类型:{', '.join(TRANSITION_TYPES)}",
            )


def _packaging_checks(project, report, timeline) -> None:
    """Round-N packaging QC (§13-14): an enabled intro/outro whose content-
    addressed card asset is missing on disk is an error pointing at `manju
    build`; an info_card anchored on a shot not on the timeline is a warning
    naming the anchor (the compiler already skipped it deterministically)."""
    try:
        packaging = project.load_packaging()
    except Exception:
        return  # a broken packaging.yaml is a `manju check` finding, not QC's job
    config = project.load_config()

    from ..timeline.packaging import packaging_card_relpath

    for kind, card in (("intro", packaging.intro), ("outro", packaging.outro)):
        if not card.enabled:
            continue
        rel = packaging_card_relpath(kind, card, config.width, config.height, config.fps)
        if not project.resolve(rel).exists():
            report.add(
                "error", "existence", "packaging",
                f"{kind} card asset is missing on disk: {rel}",
                suggestion="run `manju build` to render the packaging card (§13-14)",
            )

    # Round-Q branding: a logo / image watermark references a HUMAN asset. When
    # the section is enabled and names an image, that file must exist on disk —
    # a missing one is an error (opacity/size ranges are validated at the model,
    # not here). A text-only watermark (no image) has nothing to check.
    def _branding_image(enabled: bool, image: str, label: str) -> None:
        if not (enabled and image):
            return
        try:
            exists = project.resolve(image).exists()
        except Exception:
            report.add(
                "error", "existence", "packaging",
                f"{label} image path is invalid: {image}",
                suggestion="use a project-relative path under the project root",
            )
            return
        if not exists:
            report.add(
                "error", "existence", "packaging",
                f"{label} image is missing on disk: {image}",
                suggestion="drop the image under media/imports/ and point "
                           "packaging.yaml at it (packaging.yaml → logo/watermark.image)",
            )

    _branding_image(packaging.logo.enabled, packaging.logo.image, "logo")
    _branding_image(packaging.watermark.enabled, packaging.watermark.image, "watermark")

    if timeline is not None and packaging.info_cards:
        from ..timeline.anchors import resolve_anchor

        for i, ic in enumerate(packaging.info_cards):
            resolved = resolve_anchor(ic.at, ic.offset_ms, timeline.tracks.video)
            if resolved is None:
                report.add(
                    "warn", "content", "packaging",
                    f"info_card #{i} anchor {ic.at!r} names a shot not on the "
                    "timeline — the card was skipped",
                    suggestion="fix the `at:` shot id in packaging.yaml, or remove the card",
                )
            elif resolved >= timeline.duration_ms:
                report.add(
                    "warn", "content", "packaging",
                    f"info_card #{i} anchor {ic.at!r} resolves at/past the end "
                    f"of the film ({resolved}ms ≥ {timeline.duration_ms}ms) — "
                    "it could never be seen, so it is skipped",
                    suggestion="reduce offset_ms or anchor to an earlier point",
                )


# --------------------------------------------------------- technical layer


def _technical_clips(project, report, timeline: Timeline, probe) -> None:
    # Frame-grid hint (review open issue #2): the compiler always emits
    # frame-aligned durations (FIX-B), so an off-grid clip means a
    # hand-authored/edited timeline — name the cause instead of letting the
    # final fps/duration checks fail mysteriously later.
    from ..timeline.compiler import snap_to_frame_grid

    for clip in timeline.tracks.video:
        snapped = snap_to_frame_grid(clip.duration_ms, timeline.fps)
        if snapped != clip.duration_ms:
            report.add(
                "warn", "technical", clip.shot,
                f"clip duration {clip.duration_ms}ms is not frame-aligned at "
                f"{timeline.fps}fps ({clip.duration_ms * timeline.fps / 1000:.2f} frames) "
                "— hand-authored timeline? nearest grid value is "
                f"{snapped}ms",
                suggestion="snap durations to the FIX-B rule: "
                           "round(round(ms*fps/1000)*1000/fps); see README 'Frame-grid rule'",
            )

    for clip in timeline.tracks.video:
        try:
            path = project.resolve(clip.source)
        except Exception:
            continue
        if not path.exists():
            continue
        info = probe(path)
        if info is None or info.duration_ms is None:
            continue
        # A source shorter than its clip is only a warn: normalize pads it (§9).
        if info.duration_ms + _DURATION_TOL_MS < clip.duration_ms:
            deficit = clip.duration_ms - info.duration_ms
            report.add(
                "warn", "technical", clip.shot,
                f"source is shorter than the clip: source "
                f"{info.duration_ms}ms vs clip {clip.duration_ms}ms "
                f"(normalize will pad, but check the intent)",
                suggestion=(
                    f"lengthen the take to fit: `manju repair --op extend --shot "
                    f"{clip.shot} --ms {deficit} --mode freeze` (or --mode pad_black); "
                    "or regenerate at the clip duration"
                ),
            )


def _technical_captions(project, report, timeline: Timeline) -> None:
    rules = project.load_rules().captions
    budget = max(1, rules.max_chars_per_line * rules.max_lines)
    lines = timeline.tracks.captions

    for i, cap in enumerate(lines):
        text = (cap.text or "").strip()
        if not text:
            report.add("error", "technical", "timeline",
                       f"caption #{i} has empty text",
                       suggestion="remove the caption or supply text")
        if cap.end_ms <= cap.start_ms:
            report.add("error", "technical", "timeline",
                       f"caption #{i} has non-positive duration "
                       f"(start={cap.start_ms}ms end={cap.end_ms}ms)",
                       suggestion="fix the caption interval so end > start")
        if len(text) > budget:
            report.add("warn", "technical", "timeline",
                       f"caption #{i} is {len(text)} chars, over the "
                       f"{budget}-char budget ({rules.max_chars_per_line}"
                       f"×{rules.max_lines})",
                       suggestion="shorten the line or split the caption")
        # FP loop I (§5.5): optional role vocabulary — an unknown string is a
        # STRUCTURED warn (never silent, never a blocker; TRANSITION_TYPES
        # stance — the model stays lenient because timeline.json is
        # hand-editable truth). Known roles and absent roles add nothing.
        role = getattr(cap, "role", None)
        if role is not None and role not in CAPTION_ROLES:
            report.add("warn", "technical", "timeline",
                       f"caption #{i} 使用了未收录的 role “{role}”"
                       "(仅记录+建议,不影响构建/导出/质检结论)",
                       suggestion=f"可用角色:{', '.join(CAPTION_ROLES)};"
                                  "或删除该 role(role 为可选字段)")

    # Minor caption bleed (≤ tol) is a warn; a substantial overlap is a hard
    # timeline conflict raised as an error in _technical_timeline_conflicts.
    ordered = sorted(enumerate(lines), key=lambda t: (t[1].start_ms, t[1].end_ms))
    for (pi, prev), (ci, cur) in zip(ordered, ordered[1:]):
        overlap = prev.end_ms - cur.start_ms
        if 0 < overlap <= _CAPTION_OVERLAP_TOL_MS:
            report.add("warn", "technical", "timeline",
                       f"captions #{pi} and #{ci} overlap by {overlap}ms "
                       f"(#{pi} ends {prev.end_ms}ms, #{ci} starts {cur.start_ms}ms)",
                       suggestion="adjust caption timings so they do not overlap")


def _technical_take_resolution(project, report, config: ProjectConfig, selected) -> None:
    for shot_id, (_, info) in selected.items():
        if info is None or info.width is None or info.height is None:
            continue
        if (info.width, info.height) != (config.width, config.height):
            report.add(
                "info", "technical", shot_id,
                f"take resolution {info.width}x{info.height} differs from project "
                f"{config.width}x{config.height} (normalize handles scaling/padding)",
                suggestion=(
                    f"bake the aspect fix into a new take: `manju repair --op croppad "
                    f"--shot {shot_id} --mode center_crop` (or --mode pad_blur for the "
                    "blurred-background vertical treatment)"
                ),
            )


def _technical_timeline_conflicts(project, report, timeline: Timeline) -> None:
    """Pure timeline math (§9): things that make the film play wrong regardless
    of the media — zero/negative-duration clips, video clips that overlap on the
    single video track beyond their transition, and captions that truly share
    the screen. All errors: a conflicting timeline is definitely broken.

    Round W (issue #2/#6): Timeline/VideoClip/AudioClip stay LENIENT at the
    model layer on purpose — timeline.json is compiled AND hand-editable
    (§6 manual mode) — so a bad numeric value must load into a real, named
    finding here instead of a raw parse-time crash that stops at the first
    bad field with no report at all (same stance as TRANSITION_TYPES).
    """
    fps = timeline.fps or 24
    frame_ms = 1000.0 / fps if fps else 0.0
    clips = list(timeline.tracks.video)

    if timeline.fps <= 0:
        report.add(
            "error", "technical", "timeline",
            f"timeline.fps 是非正数({timeline.fps})— 逐帧对齐(frame-grid snap)会除零",
            suggestion="修正 timeline.json 的 fps,或用 manju build 重新编译",
        )
    if timeline.width <= 0 or timeline.height <= 0:
        report.add(
            "error", "technical", "timeline",
            f"timeline 分辨率非法({timeline.width}x{timeline.height})",
            suggestion="修正 timeline.json 的 width/height,或用 manju build 重新编译",
        )

    for clip in clips:
        if clip.duration_ms <= 0:
            report.add(
                "error", "technical", clip.shot,
                f"video clip has non-positive duration ({clip.duration_ms}ms)",
                suggestion="remove the clip or give it a positive, frame-aligned duration",
            )

    # Audio clips (voice/music/sfx/ambient): a negative duration_ms is not a
    # real length (None stays legal — it means "play to the source's end").
    for track_name, audio_clips in (
        ("voice", timeline.tracks.voice), ("music", timeline.tracks.music),
        ("sfx", timeline.tracks.sfx), ("ambient", timeline.tracks.ambient),
    ):
        for ac in audio_clips:
            if ac.duration_ms is not None and ac.duration_ms < 0:
                report.add(
                    "error", "technical", track_name,
                    f"{track_name} clip {ac.source!r} has a negative duration "
                    f"({ac.duration_ms}ms)",
                    suggestion="fix the duration_ms in timeline.json (>= 0, or "
                               "remove it to play to the source's natural end)",
                )

    # Overlap on the single video track. The compiler lays clips end-to-end
    # (overlap 0); a crossfade legitimately overlaps by its transition length,
    # so anything beyond prev.transition_out (+1 frame slack) is a real clash.
    ordered = sorted(clips, key=lambda c: c.start_ms)
    for prev, cur in zip(ordered, ordered[1:]):
        if prev.duration_ms <= 0 or cur.duration_ms <= 0:
            continue
        overlap = (prev.start_ms + prev.duration_ms) - cur.start_ms
        allowed = prev.transition_out.duration_ms if prev.transition_out else 0
        if overlap > allowed + frame_ms + 0.5:
            report.add(
                "error", "technical", cur.shot,
                f"video clips {prev.shot!r} and {cur.shot!r} overlap by {overlap}ms "
                f"(transition allows {allowed}ms) — two clips play at once",
                suggestion=(
                    f"push {cur.shot} to start at {prev.start_ms + prev.duration_ms}ms, "
                    "or give the earlier clip a transition_out of matching duration"
                ),
            )

    # Captions collide on one line region: a bleed over the tolerance means two
    # cues are on screen together (the compiler never produces this).
    caps = list(timeline.tracks.captions)
    ordered_caps = sorted(enumerate(caps), key=lambda t: (t[1].start_ms, t[1].end_ms))
    for (pi, prev), (ci, cur) in zip(ordered_caps, ordered_caps[1:]):
        overlap = prev.end_ms - cur.start_ms
        if overlap > _CAPTION_OVERLAP_TOL_MS:
            report.add(
                "error", "technical", "timeline",
                f"captions #{pi} and #{ci} overlap by {overlap}ms on the same line "
                "region — they render on top of each other",
                suggestion="retime or split the captions so their windows do not overlap",
            )


def _technical_garbled_captions(project, report, timeline: Timeline) -> None:
    """Garbled-subtitle (乱码) detection (§9): U+FFFD, UTF-8-as-latin-1 mojibake,
    or a control-char-heavy cue → error naming the cue. Runs on the compiled
    captions and, when captions are human-owned (rules.captions.mode == manual),
    on captions.srt too."""
    for i, cap in enumerate(timeline.tracks.captions):
        why = _garbled_reason(cap.text or "")
        if why:
            report.add(
                "error", "technical", "timeline",
                f"caption #{i} looks garbled (乱码): {why}",
                suggestion="以 UTF-8 重新导出/重编码字幕源(检查 TTS/ASR 或字幕文件编码)",
            )

    try:
        manual = project.load_rules().captions.mode == "manual"
    except Exception:
        manual = False
    srt_path = project.captions_dir / "captions.srt"
    if manual and srt_path.exists():
        try:
            from ..providers.asr import parse_srt

            # errors="replace" turns undecodable bytes into U+FFFD, which the
            # detector then flags — exactly the mis-encoded-file case (§9).
            segs = parse_srt(srt_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            segs = []
        for i, seg in enumerate(segs):
            why = _garbled_reason(seg.text or "")
            if why:
                report.add(
                    "error", "technical", "timeline",
                    f"captions.srt cue #{i} looks garbled (乱码): {why}",
                    suggestion="以 UTF-8 重新保存 captions.srt(该字幕已标记 manual,为人工真相)",
                )


def _technical_voice_audio(project, report, timeline: Timeline, astats) -> None:
    """Per-voice-clip audio QC (§9), one astats pass per file (cached):
      - whole-clip RMS at/under −50dB → error (TTS produced silence / wrong file);
      - sample peak at/above −0.1dBFS → warn (digital clipping).
    Gated on a voice track being present; missing files are the existence
    layer's job, an unreadable detector degrades to no finding."""
    for k, clip in enumerate(timeline.tracks.voice):
        try:
            path = project.resolve(clip.source)
        except Exception:
            continue
        if not path.exists():
            continue  # _existence_timeline already reports missing voice sources
        stats = astats(path)
        if stats is None:
            continue  # no audio / detector failed → no finding
        label = f"voice clip #{k} ({project.relpath(path)})"
        if stats.rms_db is None or stats.rms_db <= _SILENCE_RMS_DB:
            report.add(
                "error", "technical", "timeline",
                f"{label} is entirely silent (RMS {_fmt_db(stats.rms_db)}dB ≤ "
                f"{_SILENCE_RMS_DB}dB) — TTS produced silence, or the voice track "
                "points at the wrong file",
                suggestion="重配音该镜头(manju voice <shot>),或修正 voice 轨指向的文件",
            )
        elif stats.peak_db is not None and stats.peak_db != float("-inf") \
                and stats.peak_db >= _CLIP_PEAK_DB:
            report.add(
                "warn", "technical", "timeline",
                f"{label} peaks at {stats.peak_db:.2f}dBFS (≥ {_CLIP_PEAK_DB}dBFS) — "
                "audio is clipping",
                suggestion="降低该 voice 的 gain_db 或重生成;final 混音会 loudnorm,"
                           "但源已削波无法还原",
            )


def _final_render(project, report, config: ProjectConfig, timeline, deep: bool) -> None:
    final = _newest_final(project)
    if final is None:
        return
    info = _probe_info(final)
    if info is None:
        report.add("error", "technical", "final",
                   f"final render not readable by ffprobe: {final.name}")
        return

    if info.width is not None and info.height is not None:
        if (info.width, info.height) != (config.width, config.height):
            report.add(
                "error", "technical", "final",
                f"final render resolution {info.width}x{info.height} != project "
                f"{config.width}x{config.height}",
                suggestion="re-render at the project resolution",
            )

    # Frame-rate contract (FIX-B): the final's r_frame_rate must equal the
    # project fps exactly — a drifted rate (e.g. 143/6 from concat of
    # non-frame-aligned segments) desyncs everything downstream.
    if info.fps is not None and config.fps and abs(info.fps - config.fps) > 1e-6:
        report.add(
            "error", "technical", "final",
            f"final r_frame_rate {info.fps} != project fps {config.fps}",
            suggestion="re-render (the pipeline forces fps=<project fps> in the "
                       "final chain; a mismatch means an old or foreign final)",
        )

    # Duration contract (FIX-B): |final - timeline| ≤ 1 frame.
    if timeline is not None and info.duration_ms is not None and config.fps:
        frame_ms = 1000.0 / config.fps
        drift = abs(info.duration_ms - timeline.duration_ms)
        if drift > frame_ms + 0.5:  # +0.5ms for probe rounding
            report.add(
                "error", "technical", "final",
                f"final duration {info.duration_ms}ms differs from timeline "
                f"{timeline.duration_ms}ms by {drift:.1f}ms (> 1 frame = {frame_ms:.1f}ms)",
                suggestion="re-render; the timeline and final are out of sync",
            )

    if deep:
        _deep_detectors(report, final)


def _deep_detectors(report, final: Path) -> None:
    """Optional deep pass: blackdetect + volumedetect (§9). Never fails QC."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(final),
             "-vf", f"blackdetect=d={_BLACKDETECT_D}:pix_th=0.10",
             "-an", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        )
        if "black_start" in proc.stderr:
            spans = re.findall(r"black_start:(\S+)\s+black_end:(\S+)", proc.stderr)
            report.add(
                "warn", "technical", "final",
                f"blackdetect found {len(spans) or 'some'} all-black span(s) "
                f"(>= {_BLACKDETECT_D}s)",
                suggestion="inspect the render for dropped/black clips",
            )
    except Exception:
        pass  # a broken detector never fails QC

    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(final),
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        )
        m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", proc.stderr)
        if m and float(m.group(1)) < _SILENCE_DB:
            report.add(
                "warn", "technical", "final",
                f"mean_volume {m.group(1)}dB is below {_SILENCE_DB}dB — near silent",
                suggestion="check the audio mix / voice track",
            )
    except Exception:
        pass


def _newest_final(project: Project) -> Path | None:
    if not project.final_dir.exists():
        return None
    return project.newest_final_path()  # the one numeric resolver


# ----------------------------------------------------------- content layer


def _content_frames(project, report, selected) -> None:
    """Extract one mid-point frame per selected take — eyes for the agent (§9)."""
    frames_dir = project.reports_dir / "frames"
    for shot_id, (media_path, info) in selected.items():
        dest = frames_dir / f"{shot_id}.jpg"
        mid_s = 0.0
        if info is not None and info.duration_ms:
            mid_s = max(0.0, (info.duration_ms / 2.0) / 1000.0)
        if _extract_frame(media_path, dest, mid_s):
            report.add(
                "info", "content", shot_id,
                f"mid-point frame for visual review: {project.relpath(dest)}",
            )
        else:
            report.add(
                "info", "content", shot_id,
                f"could not extract a review frame from {project.relpath(media_path)}",
            )


def _extract_frame(media_path: Path, dest: Path, mid_s: float) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{mid_s:.3f}", "-i", str(media_path),
             "-frames:v", "1", "-q:v", "3", str(dest)],
            capture_output=True, timeout=60,
        )
    except Exception:
        return False
    return dest.exists()


# --------------------------------------------------------------- staleness


def stale_summary(project: Project) -> list[ShotBuildStatus]:
    """Per-shot build/staleness status (§4.3). QC also surfaces these as info
    items; callers wanting the structured list use this directly."""
    return evaluate_all(project)


# --------------------------------------------------------- @mentions (round U)


def _mention_checks(project, report) -> None:
    """@mention advisories (round U, goal item 6). Both are info-level and only
    ever fire when a shot's free text carries an ``@handle``, so a project that
    uses no @mentions gets a byte-identical QC report:

      (1) an UNRESOLVED @mention → info naming the nearest-matching asset ids;
      (2) a RESOLVED character/scene mention NOT yet registered in
          ``shot.characters`` / ``shot.scene`` → info pointing at
          ``manju mentions --apply`` (mentions are a registration aid, not a
          hidden runtime binding — the build never reads them).

    Degrades silently: a broken matrix / unreadable shot never fails QC."""
    try:
        from ..core.assets import asset_matrix
        from ..core.mentions import (
            nearest_ids,
            resolve_mentions,
            shot_mention_text,
        )
    except Exception:
        return
    try:
        matrix = asset_matrix(project)
    except Exception:
        return

    for sid in project.shot_ids():
        try:
            shot = project.load_shot(sid)
        except Exception:
            continue
        text = shot_mention_text(shot)
        if "@" not in text:
            continue
        resolved, unresolved = resolve_mentions(text, matrix)

        seen_unresolved: set[str] = set()
        for m in unresolved:
            if m.raw in seen_unresolved:
                continue
            seen_unresolved.add(m.raw)
            hint = nearest_ids(m.raw, matrix)
            tail = ("最相近:" + ", ".join(hint)) if hint else "资产矩阵中暂无相近条目"
            report.add(
                "info", "content", sid,
                f"@{m.raw} 无法解析到任何资产(矩阵中无此 id 或别名)",
                suggestion=f"检查拼写,或在 bible 中登记该 id/别名;{tail}",
            )

        registered_chars = set(shot.characters)
        advised_chars: set[str] = set()
        advised_scene = False
        for m, kind, aid in resolved:
            if kind == "character" and aid not in registered_chars \
                    and aid not in advised_chars:
                advised_chars.add(aid)
                report.add(
                    "info", "content", sid,
                    f"@{m.raw} 已解析为角色 {aid},但未登记进 shot.characters",
                    suggestion=f"manju mentions --apply {sid} "
                               "(把 @ 提及写入镜头登记字段;锁定字段不自动写,改用 proposals/)",
                )
            elif kind == "scene" and aid != (shot.scene or "") and not advised_scene:
                advised_scene = True
                report.add(
                    "info", "content", sid,
                    f"@{m.raw} 已解析为场景 {aid},但 shot.scene={shot.scene or '—'}",
                    suggestion=f"manju mentions --apply {sid} "
                               "(把 @ 提及写入镜头登记字段;锁定字段不自动写,改用 proposals/)",
                )
