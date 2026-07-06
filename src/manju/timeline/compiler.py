"""Timeline compiler (§6) — a pure function.

input  = shot order + each shot's selected take + voice durations + rules
output = timeline.json (same input, same output, always)

Times are integer milliseconds throughout; fps only exists at render time —
EXCEPT for the frame-grid snap (FIX-B): every clip duration is rounded to a
whole number of frames so segment boundaries land exactly on the encoder's
frame grid (rule: ms → nearest whole frame count, minimum 1 frame → back to
nearest integer ms; e.g. 1200ms @ 24fps = 28.8 frames → 29 frames → 1208ms).
Audio drives picture duration: for `duration: auto` shots the voice take's
length plus padding decides the clip length, clamped by rules, then snapped.

Manual takeover (§6): when rules.mode == "manual", timeline.json is human
truth — the compiler refuses to overwrite it and writes
timeline.generated.json for comparison instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.container import Project
from ..core.hashing import hash_value
from ..core.models import (
    AudioClip,
    CaptionLine,
    OverlayClip,
    PackagingSpec,
    ProjectConfig,
    ShotSpec,
    Timeline,
    TimelineMeta,
    TimelineRules,
    TimelineTracks,
    TransitionSpec,
    VideoClip,
)
from .anchors import resolve_anchor
from .packaging import packaging_card_relpath

# probe_fn: absolute media path -> duration in ms (None if unreadable)
ProbeFn = Callable[[Path], int | None]


class CompileError(RuntimeError):
    pass


@dataclass
class ShotInput:
    shot: ShotSpec
    take_name: str
    take_source: str  # project-relative
    take_duration_ms: int | None
    voice_source: str | None = None  # project-relative
    voice_duration_ms: int | None = None
    # word boundaries from the TTS engine ([{start_ms,end_ms,text}, …], voice-
    # relative): captions snap to real speech instead of the weighted split
    voice_timing: list[dict] | None = None


@dataclass
class CompileInput:
    config: ProjectConfig
    rules: TimelineRules
    shots: list[ShotInput] = field(default_factory=list)
    # Optional packaging kit (round-N). None == exactly today's behaviour; an
    # all-disabled spec is treated as None for both compile and fingerprint, so
    # a packaging.yaml that turns nothing on leaves the timeline byte-identical.
    packaging: PackagingSpec | None = None

    def _active_packaging(self) -> dict[str, Any] | None:
        """The part of the packaging spec that actually shapes the timeline:
        intro/outro cards (only when enabled) and info_cards. Cover and teaser
        are export-time only and never enter the compiled timeline. Returns
        None when nothing is active — the fingerprint then omits packaging
        entirely (byte-identical to a compile with packaging=None)."""
        p = self.packaging
        if p is None:
            return None
        active: dict[str, Any] = {}
        if p.intro.enabled:
            active["intro"] = p.intro.model_dump()
        if p.outro.enabled:
            active["outro"] = p.outro.model_dump()
        if p.info_cards:
            active["info_cards"] = [c.model_dump() for c in p.info_cards]
        return active or None

    def fingerprint(self) -> str:
        """Hash of everything that shaped this compile -> meta.compiled_from."""
        payload: dict[str, Any] = {
            "fps": self.config.fps,
            "width": self.config.width,
            "height": self.config.height,
            "rules": self.rules.model_dump(),
            "shots": [
                {
                    "id": s.shot.id,
                    "duration": s.shot.duration,
                    "dialogue": s.shot.dialogue.model_dump(),
                    "take": s.take_name,
                    "take_source": s.take_source,
                    "take_duration_ms": s.take_duration_ms,
                    "voice_source": s.voice_source,
                    "voice_duration_ms": s.voice_duration_ms,
                    "voice_timing": s.voice_timing,
                }
                for s in self.shots
            ],
        }
        pkg = self._active_packaging()
        if pkg is not None:  # absent when nothing is enabled → today's hash
            payload["packaging"] = pkg
        return hash_value(payload)


def snap_to_frame_grid(duration_ms: int, fps: int) -> int:
    """FIX-B frame-grid rounding rule (also documented in the README):

        frames = max(1, round(duration_ms * fps / 1000))
        snapped = max(1, round(frames * 1000 / fps))

    A duration that is not a whole number of frames cannot be rendered
    faithfully — the encoder rounds every segment up/down independently and
    the drift accumulates across the concat (pre-fix: 1200ms @ 24fps became
    29-frame/1216ms segments and a 143/6 final frame rate). Snapping to the
    nearest whole frame count (never below one frame) keeps timeline math,
    captions, audio offsets and the encoder all on the same grid.
    """
    frames = max(1, round(duration_ms * fps / 1000))
    return max(1, round(frames * 1000 / fps))


def _resolve_duration_ms(inp: ShotInput, rules: TimelineRules, fps: int) -> int:
    """Audio drives picture (§6). Explicit numeric duration always wins; the
    result is snapped to the frame grid (FIX-B) as the final step."""
    timing = rules.timing
    if inp.shot.duration != "auto":
        return snap_to_frame_grid(max(1, int(round(float(inp.shot.duration) * 1000))), fps)
    if inp.voice_duration_ms:
        raw = inp.voice_duration_ms + timing.padding_before_ms + timing.padding_after_ms
    elif inp.take_duration_ms:
        raw = inp.take_duration_ms
    else:
        raw = timing.default_shot_ms
    return snap_to_frame_grid(max(timing.min_shot_ms, min(timing.max_shot_ms, raw)), fps)


def _split_caption(text: str, max_chars: int, max_lines: int) -> list[str]:
    """Split dialogue into caption-sized chunks. CJK text has no spaces, so
    the splitter is width-based with a preference for punctuation breaks."""
    text = text.strip()
    if not text:
        return []
    budget = max_chars * max_lines
    if len(text) <= budget:
        return [text]
    chunks: list[str] = []
    rest = text
    breakers = "。!?!?;;,,、 "
    while rest:
        if len(rest) <= budget:
            chunks.append(rest)
            break
        window = rest[:budget]
        cut = max((window.rfind(ch) for ch in breakers), default=-1)
        cut = cut + 1 if cut > budget // 2 else budget
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return [c for c in chunks if c]


def _align_words_to_text(words: list[dict], text: str) -> list[dict]:
    """Reattach the punctuation the TTS word stream drops (Edge zh boundaries
    carry no ",。?"): walk the original dialogue text, match each word token
    in order, and glue the characters between this token's end and the next
    token's start (punctuation/space) onto the word. Any mismatch falls back
    to the raw words — alignment is an enhancement, never a gate."""
    enriched: list[dict] = []
    cursor = 0
    for i, word in enumerate(words):
        token = str(word["text"])
        found = text.find(token, cursor)
        if found < 0:
            return words  # stream and text disagree -> keep the honest raw form
        end = found + len(token)
        next_start = len(text)
        if i + 1 < len(words):
            nxt = text.find(str(words[i + 1]["text"]), end)
            next_start = nxt if nxt >= 0 else end
        tail = text[end:next_start].strip()
        enriched.append({**word, "text": token + tail})
        cursor = end
    return enriched


def _timed_captions(words: list[dict], offset_ms: int, max_chars: int,
                    max_lines: int, *, speaker: str,
                    original_text: str = "") -> list[CaptionLine]:
    """Group TTS word boundaries into caption cues (round M).

    Greedy fill up to the line budget, breaking eagerly after CJK sentence
    enders; each cue's start/end come from the FIRST/LAST word's real times
    (voice-relative), shifted by the voice clip's timeline offset."""
    if original_text:
        words = _align_words_to_text(words, original_text)
    budget = max(1, max_chars * max_lines)
    cues: list[CaptionLine] = []
    group: list[dict] = []
    length = 0

    def flush() -> None:
        nonlocal group, length
        if group:
            cues.append(CaptionLine(
                start_ms=offset_ms + group[0]["start_ms"],
                end_ms=max(offset_ms + group[-1]["end_ms"],
                           offset_ms + group[0]["start_ms"] + 1),
                text="".join(w["text"] for w in group).strip(),
                speaker=speaker,
            ))
        group, length = [], 0

    for word in words:
        token = str(word["text"])
        if length + len(token) > budget:
            flush()
        group.append(word)
        length += len(token)
        if token and token[-1] in "。!?!?…":
            flush()
    flush()
    return [c for c in cues if c.text]


def compile_timeline(inp: CompileInput) -> Timeline:
    if not inp.shots:
        raise CompileError("nothing to compile: no shots with a usable selected take")

    rules, config = inp.rules, inp.config
    packaging = inp.packaging
    tracks = TimelineTracks()
    cursor = 0

    # Packaging intro/outro become REAL leading/trailing segments inside the
    # clip accumulation (§13-14): the intro advances `cursor` before shots are
    # placed, so voice starts, caption cues and the total length all shift
    # naturally — no track is post-shifted. Transitions are computed over the
    # combined segment list so the true last segment gets no transition_out.
    intro_on = packaging is not None and packaging.intro.enabled
    outro_on = packaging is not None and packaging.outro.enabled
    n_segments = (1 if intro_on else 0) + len(inp.shots) + (1 if outro_on else 0)

    def _transition(seg_idx: int) -> TransitionSpec | None:
        return None if seg_idx == n_segments - 1 else rules.transition_default

    seg_i = 0
    intro_ms = 0  # content starts here; absolute overlays shift past the intro
    if intro_on:
        card = packaging.intro
        dur = snap_to_frame_grid(card.duration_ms, config.fps)
        intro_ms = dur
        tracks.video.append(
            VideoClip(
                shot="__intro__",
                take="packaging",
                source=packaging_card_relpath(
                    "intro", card, config.width, config.height, config.fps
                ),
                start_ms=cursor,
                duration_ms=dur,
                transition_out=_transition(seg_i),
            )
        )
        cursor += dur
        seg_i += 1

    for s in inp.shots:
        duration_ms = _resolve_duration_ms(s, rules, config.fps)
        tracks.video.append(
            VideoClip(
                shot=s.shot.id,
                take=s.take_name,
                source=s.take_source,
                start_ms=cursor,
                duration_ms=duration_ms,
                transition_out=_transition(seg_i),
            )
        )
        seg_i += 1

        if s.voice_source and s.voice_duration_ms:
            tracks.voice.append(
                AudioClip(
                    source=s.voice_source,
                    start_ms=cursor + rules.timing.padding_before_ms,
                    duration_ms=s.voice_duration_ms,
                    gain_db=rules.audio.voice_gain_db,  # audio policy (§6): voice level
                )
            )

        text = s.shot.dialogue.text
        if rules.captions.enabled and text:
            # captions follow the voice when there is one, else span the shot
            if s.voice_duration_ms:
                cap_start = cursor + rules.timing.padding_before_ms
                cap_total = s.voice_duration_ms
            else:
                cap_start = cursor
                cap_total = duration_ms
            if s.voice_timing and s.voice_duration_ms:
                # word-timed captions (round M): cue boundaries come from the
                # TTS engine's word boundaries — captions snap to real speech
                tracks.captions.extend(
                    _timed_captions(
                        s.voice_timing, cap_start,
                        rules.captions.max_chars_per_line, rules.captions.max_lines,
                        speaker=s.shot.dialogue.speaker,
                        original_text=text,
                    )
                )
            else:
                pieces = _split_caption(
                    text, rules.captions.max_chars_per_line, rules.captions.max_lines
                )
                if pieces:
                    weights = [len(p) for p in pieces]
                    total_w = sum(weights)
                    t = cap_start
                    for piece, w in zip(pieces, weights):
                        span = max(300, int(round(cap_total * w / total_w)))
                        end = min(t + span, cap_start + cap_total)
                        tracks.captions.append(
                            CaptionLine(
                                start_ms=t,
                                end_ms=max(end, t + 1),
                                text=piece,
                                speaker=s.shot.dialogue.speaker,
                            )
                        )
                        t = end

        cursor += duration_ms

    if outro_on:
        card = packaging.outro
        dur = snap_to_frame_grid(card.duration_ms, config.fps)
        tracks.video.append(
            VideoClip(
                shot="__outro__",
                take="packaging",
                source=packaging_card_relpath(
                    "outro", card, config.width, config.height, config.fps
                ),
                start_ms=cursor,
                duration_ms=dur,
                transition_out=_transition(seg_i),  # last segment → None
            )
        )
        cursor += dur
        seg_i += 1

    total_ms = cursor
    # Title card is an overlay layer (§7 step ④): emitted after the video track
    # is assembled so its duration can be clamped to the film's length. It is
    # the film's opening title, so with a packaging intro on it starts at the
    # first CONTENT frame — burning it over the intro card would stack two
    # title cards (round-N review finding).
    if rules.title_card.enabled and rules.title_card.text:
        tracks.overlay.append(
            OverlayClip(
                kind="title_card",
                template=rules.title_card.template,
                text=rules.title_card.text,
                start_ms=intro_ms,
                duration_ms=min(rules.title_card.duration_ms,
                                max(1, total_ms - intro_ms)),
            )
        )

    # Info cards (§13-14 round-N): chapter/role/info overlays riding the same
    # overlay track, anchored on the just-assembled video track via the shared
    # anchor grammar. An anchor that names a shot not on the timeline resolves
    # to None, and one that resolves at/past the film's end can never be seen —
    # both are deterministic skips (QC warns, naming the anchor).
    if packaging is not None:
        for ic in packaging.info_cards:
            start = resolve_anchor(ic.at, ic.offset_ms, tracks.video)
            if start is None or start >= total_ms:
                continue
            span = min(ic.duration_ms, max(1, total_ms - start))
            tracks.overlay.append(
                OverlayClip(
                    kind="info_card",
                    subkind=ic.kind,  # chapter | role | info → burn position
                    template=ic.template,
                    text=ic.text,
                    start_ms=start,
                    duration_ms=span,
                )
            )

    if rules.music.source:
        tracks.music.append(
            AudioClip(
                source=rules.music.source,
                start_ms=0,
                duration_ms=total_ms,  # BGM is trimmed to picture length
                gain_db=rules.music.gain_db,
                ducking=rules.music.ducking,
                fade_out_ms=rules.music.fade_out_ms,
                # ducking shape travels onto the clip so the render is purely a
                # function of the compiled timeline (defaults = today's constants)
                duck_threshold=rules.music.duck_threshold,
                duck_ratio=rules.music.duck_ratio,
                duck_attack_ms=rules.music.duck_attack_ms,
                duck_release_ms=rules.music.duck_release_ms,
            )
        )

    # ---- audio policy (§6): SFX, transition sounds and the ambient bed ----
    # Purity holds: SFX anchors resolve against the already-compiled video
    # track (anchors.resolve_anchor), so the audio tracks stay a deterministic
    # function of the specs. A None resolution (unknown shot) is SKIPPED here
    # and reported by QC — never a hard error.
    audio = rules.audio
    for spec in audio.sfx:
        start = resolve_anchor(spec.at, spec.offset_ms, tracks.video)
        if start is None or start >= total_ms:
            # Unresolvable anchor, or one at/past the end (the render trims the
            # mix to the film): deterministically skipped, QC warns.
            continue
        tracks.sfx.append(
            AudioClip(source=spec.source, start_ms=start, gain_db=spec.gain_db)
        )

    # Transition sound: one hit at every INTERIOR cut (n clips → n−1 sounds),
    # placed on the boundary (== the next clip's start). Not on the last clip.
    if audio.transition_sound:
        for clip in tracks.video[1:]:
            tracks.sfx.append(
                AudioClip(
                    source=audio.transition_sound,
                    start_ms=clip.start_ms,
                    gain_db=audio.transition_gain_db,
                )
            )

    # Ambient bed: one looped clip under the whole film (start 0, full length).
    if audio.ambient.source:
        tracks.ambient.append(
            AudioClip(
                source=audio.ambient.source,
                start_ms=0,
                duration_ms=total_ms,
                gain_db=audio.ambient.gain_db,
                ducking=audio.ambient.ducking,
                fade_out_ms=audio.ambient.fade_out_ms,
                loop=True,
                duck_threshold=audio.ambient.duck_threshold,
                duck_ratio=audio.ambient.duck_ratio,
                duck_attack_ms=audio.ambient.duck_attack_ms,
                duck_release_ms=audio.ambient.duck_release_ms,
            )
        )

    return Timeline(
        meta=TimelineMeta(compiled_from=inp.fingerprint(), mode="compiled"),
        fps=config.fps,
        width=config.width,
        height=config.height,
        duration_ms=total_ms,
        tracks=tracks,
    )


# --------------------------------------------------------- project plumbing


def _find_voice(project: Project, shot_id: str) -> Path | None:
    """The NEWEST voice take wins: media is append-only (§3), so the highest
    voice_take_NN is the latest decision. (Pre-round-B this picked the oldest
    — sorted-first — which contradicted the append-only semantics.)"""
    voices = project.voice_takes(shot_id)
    return voices[-1][0] if voices else None


def _load_voice_timing(voice: Path) -> list[dict] | None:
    """Word boundaries recorded by the TTS engine (<take>.timing.json).
    Purity holds: the file content participates in the fingerprint, so a
    timing change re-fingerprints the compile like any other input."""
    import json

    timing_path = voice.with_suffix(".timing.json")
    if not timing_path.exists():
        return None
    try:
        words = json.loads(timing_path.read_text(encoding="utf-8"))
        return words if isinstance(words, list) and words else None
    except (json.JSONDecodeError, OSError):
        return None


def gather_compile_input(project: Project, probe_fn: ProbeFn) -> CompileInput:
    """Assemble the compiler's input from the project. Raises CompileError
    listing every shot that cannot go on the timeline (missing/unselected)."""
    from ..build.stale import evaluate_all  # local import: build depends on timeline too

    config = project.load_config()
    rules = project.load_rules()
    shots: list[ShotInput] = []
    problems: list[str] = []

    for status in evaluate_all(project):
        if not status.usable:
            problems.append(f"{status.shot_id}: {status.state.value}"
                            + (f" ({status.note})" if status.note else ""))
            continue
        take = status.take
        assert take is not None and take.media_path is not None
        voice = _find_voice(project, status.shot_id)
        take_dur = take.sidecar.probe.duration_ms if take.sidecar.probe else None
        if take_dur is None:
            take_dur = probe_fn(take.media_path)
        shots.append(
            ShotInput(
                shot=project.load_shot(status.shot_id),
                take_name=take.name,
                take_source=project.relpath(take.media_path),
                take_duration_ms=take_dur,
                voice_source=project.relpath(voice) if voice else None,
                voice_duration_ms=probe_fn(voice) if voice else None,
                voice_timing=_load_voice_timing(voice) if voice else None,
            )
        )

    if problems:
        raise CompileError(
            "cannot compile timeline, unresolved shots:\n  " + "\n  ".join(problems)
        )
    # Packaging is loaded here (defaults to an all-disabled spec when absent),
    # so every compile call site — build graph, cli, tests — gets it for free
    # while the CompileInput field itself stays optional (§13-14).
    return CompileInput(
        config=config, rules=rules, shots=shots, packaging=project.load_packaging()
    )


def build_timeline(project: Project, probe_fn: ProbeFn) -> tuple[Timeline, Path, bool]:
    """Compile and write. Returns (timeline, written_path, overwrote_truth).

    mode=manual makes timeline.json human truth: we only ever write
    timeline.generated.json next to it (§6).
    """
    inp = gather_compile_input(project, probe_fn)
    timeline = compile_timeline(inp)
    manual = project.load_rules().mode == "manual"
    path = project.save_timeline(timeline, generated_only=manual)
    return timeline, path, not manual
