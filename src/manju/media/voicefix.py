"""Voice repair loop (round U, goal item 11) — regenerate a bad voice, realign
its captions, and let the film re-mix itself through content keys.

The whole loop, wired as ``manju repair --op voice --shot <id> [--dry-run]``:

  1. KEEP the picture — the shot's selected VIDEO take is never re-generated or
     touched; only the voice is remade.
  2. REGENERATE the voice through the EXACT TTS resolution the build uses
     (``build/graph._plan_voice`` → ``providers.tts.get_tts_provider``,
     manifest-driven: the first configured ``type: tts`` manifest unless one is
     named). The new voice take is APPENDED (append-only, §3) via
     ``register_voice_take``; its lineage sidecar then records
     ``repaired_from: <old take>`` + the ``voice_hash`` it satisfies +
     ``audio_repaired: true`` (the MARK).
  3. REALIGN this shot's caption cues to the new voice duration, PROPORTIONALLY
     around the shot's caption anchor — the same scaling the compiler's weighted
     split produces when ``cap_total`` changes (§6, timeline/compiler). HARD
     CONSTRAINT: cues under MANUAL takeover are NEVER silently moved. The
     manual-takeover flag is ``rules.captions.mode == "manual"`` with a human
     ``captions/captions.srt`` on disk — exactly the semantics gui/captions_edit
     enforces (the SRT is human truth; the compiler's version is kept aside in
     ``captions.generated.srt``). Those cues are left verbatim and a 中文
     advisory names the ones that now need a human's attention.
  4. REMIX happens on the NEXT build, naturally, through content keys — this loop
     never writes a render output. The changed voice bytes flow into
     ``render._audio_input_hashes`` and the compiled-timeline fingerprint
     (voice_source + voice_duration_ms), so ``final_content_key`` moves and the
     shot's audio-affected outputs re-render; a subsequent build's compile points
     at the newest (repaired) voice take (§3 newest-wins).
  5. MARK / debuggability — the new sidecar carries ``audio_repaired: true``; any
     step failure writes a structured ``reports/failures.jsonl`` record
     (step/subject/cause/evidence/hint, core/failures) and every real run appends
     an actor-attributed ``events.jsonl`` entry (core/events).

``--dry-run`` prints the full plan in 中文 and mutates NOTHING — no take, no
event, no failure record, no caption write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.container import Project, ProjectError
from ..core.events import append_event
from ..core.models import Timeline, TimelineRules, VoiceTakeSidecar
from ..core.spec import compute_voice_hash
from ..core.yamlio import read_yaml, write_yaml

# probe_fn: absolute media path -> duration in ms (None if unreadable). Injectable
# so the loop is testable without ffprobe (mirrors timeline.compiler.ProbeFn).
ProbeFn = Callable[[Path], "int | None"]


@dataclass
class CueMove:
    """One caption cue's before/after timing. For a realigned cue the ``new_*``
    are the proportionally-retimed values; for a locked cue they equal the
    ``old_*`` (left verbatim — see the manual-takeover constraint)."""

    index: int  # 1-based cue index in the current captions truth
    old_start_ms: int
    old_end_ms: int
    new_start_ms: int
    new_end_ms: int
    text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class VoiceRepairResult:
    shot_id: str
    ok: bool = True
    dry_run: bool = False
    provider: str | None = None
    old_take: str | None = None
    new_take: str | None = None
    old_voice_ms: int | None = None
    new_voice_ms: int | None = None
    voice_hash: str | None = None
    audio_repaired: bool = False
    captions_mode: str = "compiled"
    manual_locked: bool = False
    realigned: list[CueMove] = field(default_factory=list)   # cues moved (compiled)
    locked_cues: list[CueMove] = field(default_factory=list)  # left verbatim (manual)
    advisories: list[str] = field(default_factory=list)       # 中文, human-facing
    plan: list[str] = field(default_factory=list)             # 中文 plan (dry-run + summary)
    failure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["realigned"] = [c.as_dict() for c in self.realigned]
        d["locked_cues"] = [c.as_dict() for c in self.locked_cues]
        return d


# --------------------------------------------------------------- TTS resolution


def _resolve_tts(provider: str | None):
    """Resolve the TTS provider EXACTLY as the build does (build/graph._plan_voice
    → the first configured ``type: tts`` manifest, or a named one). Imported at
    call time so a test that monkeypatches ``manju.providers.tts.get_tts_provider``
    is honoured — the same way the build path picks it up."""
    from ..providers.tts import TtsUnavailable, get_tts_provider, tts_providers

    providers = tts_providers()
    if not providers and not provider:
        raise TtsUnavailable(
            "no TTS provider configured — fill a tts manifest (§8.6, type: tts, "
            "adapter: generic_tts) or use the keyless EdgeTtsProvider; a voice "
            "cannot be repaired without one"
        )
    name = provider or sorted(providers)[0]
    return name, get_tts_provider(name)


# ------------------------------------------------------------- caption plumbing


def _current_cues(project: Project, timeline: Timeline | None,
                  manual_locked: bool) -> list[dict[str, Any]]:
    """The cue list (start/end/text + 1-based index) from the CURRENT captions
    truth: under manual takeover the human ``captions.srt`` (parsed the same way
    gui/captions_edit reads it), otherwise the compiled timeline's caption track.
    Kept inline (not importing the GUI layer into media/) but semantically the
    same source of truth."""
    cues: list[dict[str, Any]] = []
    if manual_locked:
        from ..providers.asr import parse_srt

        srt = project.captions_dir / "captions.srt"
        try:
            segs = parse_srt(srt.read_text(encoding="utf-8"))
        except OSError:
            segs = []
        cues = [{"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
                for s in segs]
    elif timeline is not None:
        cues = [{"start_ms": c.start_ms, "end_ms": c.end_ms, "text": c.text}
                for c in timeline.tracks.captions]
    for i, c in enumerate(cues, start=1):
        c["index"] = i
    return cues


def _shot_cues(project: Project, timeline: Timeline | None, shot_id: str,
               rules: TimelineRules, manual_locked: bool
               ) -> tuple[list[dict[str, Any]], int | None]:
    """The cues that belong to ``shot_id`` plus the shot's caption anchor
    (``cap_start``). Delegates to :func:`manju.timeline.cuemap.shot_cues_with_anchor`
    (WP1): prefers the compiler-stamped ``CaptionLine.shot`` field, falls back
    to the video-clip time-window for legacy timelines. ``cap_start`` is where
    the voiced caption region begins (``clip.start + padding_before``), the
    fixed point the proportional retime scales around. Returns ``([], None)``
    when there is no timeline to place the shot against."""
    from ..timeline.cuemap import shot_cues_with_anchor

    cues = _current_cues(project, timeline, manual_locked)
    return shot_cues_with_anchor(
        timeline, shot_id, rules, cues=cues,
    )


# --------------------------------------------------------------------- the loop


def repair_voice(project: Project, shot_id: str, *, dry_run: bool = False,
                 provider: str | None = None, actor: str = "engine",
                 probe_fn: ProbeFn | None = None) -> VoiceRepairResult:
    """The full voice repair loop — see the module docstring. Returns a
    :class:`VoiceRepairResult`; never raises for an expected failure (a bad shot,
    a TTS outage) — those are recorded and reported on ``result``."""
    from ..media.probe import probe_duration_ms

    probe_fn = probe_fn or probe_duration_ms
    result = VoiceRepairResult(shot_id=shot_id, dry_run=dry_run, provider=provider)

    # ---- load the shot; a voice repair needs a line to speak
    try:
        shot = project.load_shot(shot_id)
    except ProjectError as exc:
        return _fail(project, result, "找不到镜头,无法修复配音",
                     " ".join(str(exc).split())[:400],
                     "确认 shot id 是否正确(manju status)", actor)
    if not str(shot.dialogue.text).strip():
        return _fail(project, result, "镜头没有台词(dialogue.text 为空),无法配音",
                     f"shot={shot_id}",
                     "先在 shots/<id>.yaml 填 dialogue.text,或此镜头本就无需配音", actor)

    bible = project.load_bible()
    result.voice_hash = compute_voice_hash(shot, bible)

    # ---- resolve TTS exactly like the build (manifest-driven). A missing
    # provider is fatal for a real run but tolerated in a dry-run (the plan can
    # still say what WOULD happen).
    tts = None
    try:
        provider_id, tts = _resolve_tts(provider)
        result.provider = provider_id
    except Exception as exc:
        if not dry_run:
            return _fail(project, result, "无法解析 TTS 供应商",
                         " ".join(str(exc).split())[:800],
                         "配置一个 tts manifest(§8.6, type: tts)或使用 EdgeTtsProvider",
                         actor)
        result.provider = provider or "(未配置 TTS)"
        result.advisories.append(
            f"当前未解析到 TTS 供应商:{' '.join(str(exc).split())[:200]}")

    # ---- current (old) voice take + its duration (the realignment baseline)
    voices = project.voice_takes(shot_id)
    old_media = voices[-1][0] if voices else None
    result.old_take = old_media.stem if old_media else None
    result.old_voice_ms = probe_fn(old_media) if old_media is not None else None

    # ---- caption context: read the CURRENT (old-voice-timed) cues for this shot
    rules = project.load_rules()
    result.captions_mode = rules.captions.mode
    timeline = project.load_timeline()
    manual_locked = (rules.captions.mode == "manual"
                     and (project.captions_dir / "captions.srt").exists())
    result.manual_locked = manual_locked
    shot_cues, cap_start = _shot_cues(project, timeline, shot_id, rules, manual_locked)

    # ---- the 中文 plan (dry-run prints it; a real run keeps it as a summary)
    _plan_lines(result, shot, result.provider, manual_locked, shot_cues, cap_start)

    if dry_run:
        result.ok = True
        return result  # READ-ONLY: nothing above mutated the project

    # ---- (2) REGENERATE: synthesize + append (register_voice_take, §3)
    assert tts is not None  # a real run resolved it or already returned _fail
    try:
        new_media = tts.synthesize(project, shot, bible)
    except Exception as exc:
        return _fail(project, result, f"配音合成失败({result.provider})",
                     " ".join(str(exc).split())[:1000],
                     "确认 TTS manifest/密钥可用;或改用其他 tts 供应商(manju providers)",
                     actor, step_subject=shot_id)
    result.new_take = new_media.stem
    result.new_voice_ms = probe_fn(new_media)

    # ---- (5) MARK: enrich the just-written sidecar with the repair lineage
    _mark_repaired(project, shot_id, new_media, result)

    # ---- (3) REALIGN captions (or, under manual takeover, advise only)
    _realign_captions(result, shot_id, shot_cues, cap_start,
                      old_ms=result.old_voice_ms, new_ms=result.new_voice_ms,
                      manual_locked=manual_locked)

    # ---- events: actor-attributed (mirrors the ffmpeg repair-op event shape)
    append_event(project.root, actor, "repair", {
        "shot": shot_id, "op": "voice",
        "source_take": result.old_take, "new_take": result.new_take,
        "provider": result.provider, "audio_repaired": True,
        "cues_realigned": len(result.realigned),
        "cues_locked": len(result.locked_cues),
    })
    result.ok = True
    return result


# ------------------------------------------------------------------- MARK / mark


def _mark_repaired(project: Project, shot_id: str, new_media: Path,
                   result: VoiceRepairResult) -> None:
    """Complete the just-registered voice take's sidecar with the repair lineage:
    ``repaired_from`` (the take it replaced), ``audio_repaired: true`` (the MARK),
    and the ``voice_hash`` it satisfies. This is not a §3 violation — the media is
    append-only and untouched; we are finishing the metadata of the take THIS op
    just created (the provider's register_voice_take wrote the base sidecar)."""
    sidecar_path = project.takes_dir(shot_id) / f"{new_media.stem}.sidecar.yaml"
    try:
        sidecar = VoiceTakeSidecar.model_validate(read_yaml(sidecar_path) or {})
    except Exception:
        # a mock/provider that did not leave a sidecar: synthesize a minimal one
        # so the repair lineage is still recorded on disk.
        sidecar = VoiceTakeSidecar(provider=result.provider or "repair",
                                   voice_hash=result.voice_hash or "")
    sidecar.repaired_from = result.old_take
    sidecar.audio_repaired = True
    write_yaml(sidecar_path, sidecar.model_dump(exclude_none=True))
    result.audio_repaired = True
    result.voice_hash = sidecar.voice_hash or result.voice_hash


# --------------------------------------------------------------- REALIGN captions


def _realign_captions(result: VoiceRepairResult, shot_id: str,
                      shot_cues: list[dict[str, Any]], cap_start: int | None, *,
                      old_ms: int | None, new_ms: int | None,
                      manual_locked: bool) -> None:
    """Proportionally retime this shot's cues to the new voice duration, unless
    they are under manual takeover (then leave them and advise)."""
    if not shot_cues:
        if not manual_locked:
            result.advisories.append(
                f"镜头 {shot_id} 暂无字幕行,字幕将在下次构建时按新语音生成。")
        return

    if manual_locked:
        # HARD CONSTRAINT: human-edited cues are NEVER silently moved. Leave them
        # verbatim (new == old) and name them for a human to校准 (the exact
        # manual-takeover flag: rules.captions.mode == manual + a human
        # captions.srt, per gui/captions_edit).
        idxs: list[str] = []
        for c in shot_cues:
            result.locked_cues.append(CueMove(
                c["index"], c["start_ms"], c["end_ms"],
                c["start_ms"], c["end_ms"], c.get("text", "")))
            idxs.append(f"#{c['index']}")
        result.advisories.append(
            f"人工接管字幕未改动:镜头 {shot_id} 的字幕 {', '.join(idxs)} 仍按旧语音"
            f"({_ms(old_ms)})定时,新配音为 {_ms(new_ms)};请在 /subtitles 或直接编辑 "
            "captions.srt 人工校准这些字幕的时间轴(引擎不会替你移动人工字幕)。")
        return

    # compiled captions: proportional retime around cap_start. The compiler's
    # weighted split scales each cue's span by cap_total (§6), so scaling the
    # region from old_ms→new_ms about cap_start reproduces exactly what the next
    # build's recompile will emit — surfaced here, applied there.
    if not old_ms or not new_ms or cap_start is None:
        result.advisories.append(
            f"镜头 {shot_id} 的字幕将在下次构建时按新语音重新生成"
            "(缺少旧/新语音时长,无法预估比例)。")
        return
    factor = new_ms / old_ms
    for c in shot_cues:
        ns = cap_start + int(round((c["start_ms"] - cap_start) * factor))
        ne = cap_start + int(round((c["end_ms"] - cap_start) * factor))
        result.realigned.append(CueMove(
            c["index"], c["start_ms"], c["end_ms"], ns, max(ne, ns + 1),
            c.get("text", "")))
    if result.realigned:
        result.advisories.append(
            f"镜头 {shot_id} 的 {len(result.realigned)} 行字幕已按 "
            f"{old_ms}→{new_ms}ms 比例重排(下次构建时随新语音生效)。")


# -------------------------------------------------------------------- 中文 plan


def _plan_lines(result: VoiceRepairResult, shot: Any, provider_id: str | None,
                manual_locked: bool, shot_cues: list[dict[str, Any]],
                cap_start: int | None) -> None:
    text = str(shot.dialogue.text).strip()
    preview = text[:20] + ("…" if len(text) > 20 else "")
    result.plan.append(
        f"重新合成配音:镜头 {shot.id},供应商 {provider_id or '(未配置)'}"
        f"(说话人 {shot.dialogue.speaker or '?'};台词「{preview}」)")
    sel = getattr(getattr(shot, "status", None), "selected_take", None)
    result.plan.append(
        f"保留画面:{('当前选用 ' + sel) if sel else '现有视频镜头'} 不变"
        "(绝不重新生成画面)")
    if not shot_cues:
        result.plan.append("字幕:该镜头暂无字幕行(或尚无时间线),下次构建按新语音生成")
    elif manual_locked:
        idxs = ", ".join(f"#{c['index']}" for c in shot_cues)
        result.plan.append(
            f"字幕:{idxs} 处于人工接管(manual takeover),不会自动移动;"
            "修复后需人工校准其时间轴")
    else:
        idxs = ", ".join(f"#{c['index']}" for c in shot_cues)
        result.plan.append(f"字幕:{idxs} 将按新语音时长比例重排(下次构建生效)")
    result.plan.append(
        "重混:配音变更改变该镜头音频相关产物的内容键 → 成片(final)将重新渲染;"
        "画面未变的视频段按内容键复用缓存")


# ----------------------------------------------------------------- failure sink


def _fail(project: Project, result: VoiceRepairResult, cause: str, evidence: str,
          hint: str, actor: str, *, step_subject: str | None = None
          ) -> VoiceRepairResult:
    """Record a structured failure (core/failures) and mark the result. A dry-run
    never reaches here for a spend/synthesis error; it only fails on a bad plan
    input (missing shot / no line) and — to honour "mutate nothing" — a dry-run
    skips the on-disk record, keeping only the in-memory reason."""
    result.ok = False
    subject = step_subject or result.shot_id
    result.failure = {"step": "voice", "subject": subject, "cause": cause, "hint": hint}
    result.advisories.append(f"失败:{cause}")
    if not result.dry_run:
        try:
            from ..core.failures import Failure, record_failure

            rec = record_failure(project, Failure(
                step="voice", subject=subject, cause=cause, evidence=evidence,
                hint=hint, actor=actor, detail={"op": "voice"}))
            result.failure["id"] = rec.get("id")
        except Exception:
            pass
    return result


def _ms(v: int | None) -> str:
    return f"{v}ms" if v is not None else "未知时长"
