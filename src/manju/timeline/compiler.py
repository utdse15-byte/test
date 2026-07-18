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
    EditRate,
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
    VoiceTakeSidecar,
)
from ..core.timebase import Rate, Rounding, frames_to_ms, ms_to_frames
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
    # VIRTUAL TRIM window (round-T): the selected take's [in, out) region into
    # its media file, carried from the take sidecar. ``source_in_ms`` seeds the
    # compiled clip's in-point (the render seeks there; it is the incoming HEAD
    # handle); with a window, ``take_duration_ms`` is already the window length,
    # so ``duration: auto`` bounds the clip by the trimmed material. Defaults
    # (0 / None = whole file) leave the compile byte-identical to before.
    source_in_ms: int = 0
    source_out_ms: int | None = None


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
        # Branding overlays (round-Q): fold ONLY enabled, effective branding —
        # an enabled-but-empty logo/watermark/badge emits nothing, so it must
        # not perturb the fingerprint either. All-off → nothing added here →
        # the packaging key is omitted → today's byte-identical hash.
        if p.logo.enabled and p.logo.image:
            active["logo"] = p.logo.model_dump()
        if p.watermark.enabled and (p.watermark.image or p.watermark.text):
            active["watermark"] = p.watermark.model_dump()
        if p.badge.enabled and p.badge.text:
            active["badge"] = p.badge.model_dump()
        if p.cta.enabled and p.cta.text:
            active["cta"] = p.cta.model_dump()
        return active or None

    def fingerprint(self) -> str:
        """Hash of everything that shaped this compile -> meta.compiled_from."""
        # Round U: fold transition_overrides into the fingerprint ONLY when the
        # map is non-empty — an untouched project keeps today's hash (the same
        # non-default-only stance as source_audio / source_window below).
        rules_dump = self.rules.model_dump()
        if not rules_dump.get("transition_overrides"):
            rules_dump.pop("transition_overrides", None)
        payload: dict[str, Any] = {
            "fps": self.config.fps,
            "width": self.config.width,
            "height": self.config.height,
            "rules": rules_dump,
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
                    # Round-T: fold per-shot source-audio into the fingerprint ONLY
                    # when non-default, so a change (footage gain/mute) recompiles
                    # the timeline while an untouched project keeps today's hash.
                    **(
                        {"source_audio": s.shot.source_audio.model_dump()}
                        if (s.shot.source_audio.gain_db or s.shot.source_audio.mute)
                        else {}
                    ),
                    # Round-T virtual trim: fold the take's source WINDOW ONLY when
                    # non-default, so re-trimming (a different in/out, even at the
                    # same length) recompiles while a whole-file take keeps today's
                    # hash. Byte-identical when in==0 and out is None.
                    **(
                        {"source_window": [s.source_in_ms, s.source_out_ms]}
                        if (s.source_in_ms or s.source_out_ms is not None)
                        else {}
                    ),
                }
                for s in self.shots
            ],
        }
        pkg = self._active_packaging()
        if pkg is not None:  # absent when nothing is enabled → today's hash
            payload["packaging"] = pkg
        # R2: fold the EXACT edit rate into the fingerprint ONLY for a rational
        # project (drop-when-int, the same non-default-only stance as packaging /
        # source_window above), so an int project keeps today's byte-identical
        # hash while two projects differing only in edit rate — which compile to
        # different frame boundaries — never share a compiled_from fingerprint.
        rate = self.config.frame_rate
        if rate.exact_int is None:
            payload["rate"] = str(rate)  # canonical "num/den" (never the raw field)
        return hash_value(payload)


def snap_to_frame_grid(duration_ms: int, fps: int) -> int:
    """FIX-B frame-grid rounding rule (also documented in the README):

        frames = max(1, round(duration_ms * fps / 1000))
        snapped = max(1, round(frames * 1000 / fps))

    Integer-fps only (the whole-number edit-rate path). The opt-in rational
    (1001-family) sibling is :func:`snap_to_frame_grid_rational` /
    :class:`_RationalFrameGrid` below — this function is UNTOUCHED by R2 and
    every int project keeps snapping through it exactly as before.

    A duration that is not a whole number of frames cannot be rendered
    faithfully — the encoder rounds every segment up/down independently and
    the drift accumulates across the concat (pre-fix: 1200ms @ 24fps became
    29-frame/1216ms segments and a 143/6 final frame rate). Snapping to the
    nearest whole frame count (never below one frame) keeps timeline math,
    captions, audio offsets and the encoder all on the same grid.

    Round W (issue #2/#6, defensive): ``fps`` is now validated > 0 at every
    model boundary that produces one (ProjectConfig, Timeline), so a healthy
    caller never reaches this with fps<=0. This is the last line of defence
    for a caller that still manages to (a raw int from a hand-rolled caller, a
    ``model_construct``-ed object, or a future call site) — floor it to 1fps
    instead of raising ZeroDivisionError, so a corrupt/degenerate input
    degrades to a wrong-but-finite number rather than crashing/hanging.
    """
    fps = fps if fps and fps > 0 else 1
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


# ---------------------------------------------------------- rational grid (R2)
# The opt-in sibling of snap_to_frame_grid for a rational (1001-family) edit
# rate. The ms timeline is a stable hand-editable contract and cannot exactly
# carry 24000/1001 (timebase documents the ≤½ms/call bound), so per-clip ms
# durations are the DIFFERENCE of successive EXACT rational boundaries rounded
# to the ms grid — per-clip ms wobbles ±1ms around the true frame duration, but
# the CUMULATIVE boundary error of any prefix stays ≤ ½ms forever (the sum of
# rounded-boundary diffs telescopes to a single rounded total). Frame counts
# come from the SAME duration-resolution rules as _resolve_duration_ms (explicit
# wins; audio-drives-picture; min/max clamps) applied in FRAME space.


def snap_to_frame_grid_rational(frame_counts: list[int], rate: Rate) -> list[int]:
    """Cumulative-boundary ms durations for a sequence of whole-frame clip
    lengths at an exact rational ``rate`` — the rational sibling of
    :func:`snap_to_frame_grid`.

    Walks the clip sequence tracking the EXACT rational boundary position
    (``cum_frames × 1000·den/num`` ms, an exact Fraction inside timebase); each
    clip's emitted ms = ``round(exact_end) − round(exact_start)``. Because the
    emitted durations are differences of rounded boundaries, their running sum
    equals ``round(exact_total)`` at every prefix, so the cumulative timing error
    never exceeds ½ ms no matter how long the film — unlike rounding each clip's
    frame duration to ms INDEPENDENTLY, whose error accumulates past a whole
    frame (see the contrast pin in tests/test_fp_ratemig2.py)."""
    out: list[int] = []
    cum = 0
    for f in frame_counts:
        start = frames_to_ms(cum, rate, Rounding.ROUND_HALF_UP)
        cum += f
        end = frames_to_ms(cum, rate, Rounding.ROUND_HALF_UP)
        out.append(end - start)
    return out


def _resolve_duration_frames(inp: ShotInput, rules: TimelineRules, rate: Rate) -> int:
    """Frame-space twin of :func:`_resolve_duration_ms` for the rational path:
    the SAME rules (explicit numeric duration wins; else audio-drives-picture;
    min/max clamps) resolved to a whole FRAME count instead of snapped ms.
    ``frames = max(1, ms_to_frames(raw, rate))`` with the min/max clamps
    converted to frames via the same ROUND_HALF_UP rounding."""
    timing = rules.timing
    if inp.shot.duration != "auto":
        raw = max(1, int(round(float(inp.shot.duration) * 1000)))
        return max(1, ms_to_frames(raw, rate, Rounding.ROUND_HALF_UP))
    if inp.voice_duration_ms:
        raw = inp.voice_duration_ms + timing.padding_before_ms + timing.padding_after_ms
    elif inp.take_duration_ms:
        raw = inp.take_duration_ms
    else:
        raw = timing.default_shot_ms
    frames = ms_to_frames(raw, rate, Rounding.ROUND_HALF_UP)
    min_frames = ms_to_frames(timing.min_shot_ms, rate, Rounding.ROUND_HALF_UP)
    max_frames = ms_to_frames(timing.max_shot_ms, rate, Rounding.ROUND_HALF_UP)
    return max(1, max(min_frames, min(max_frames, frames)))


class _IntFrameGrid:
    """Integer-fps dispatch: a thin, STATELESS delegator to the untouched
    ``snap_to_frame_grid`` / ``_resolve_duration_ms`` so the int path stays
    byte-identical (same numbers, same call sequence). Records no
    ``duration_frames`` (None → dropped by the serializer)."""

    rational = False

    def __init__(self, fps: int) -> None:
        self._fps = fps

    def card_duration(self, duration_ms: int) -> tuple[int, int | None]:
        return snap_to_frame_grid(duration_ms, self._fps), None

    def shot_duration(self, inp: ShotInput, rules: TimelineRules) -> tuple[int, int | None]:
        return _resolve_duration_ms(inp, rules, self._fps), None


class _RationalFrameGrid:
    """Rational (1001-family) dispatch: the STATEFUL cumulative-boundary walker.
    Instantiated ONLY when ``project.frame_rate`` is not a whole integer — an int
    project never constructs one (the spy pin), so its compile is untouched.
    Each ``*_duration`` call resolves a whole frame count, advances the exact
    boundary, and returns ``(emitted_ms, frames)`` so the clip records both its
    snapped ms and its exact ``duration_frames``."""

    rational = True

    def __init__(self, rate: Rate) -> None:
        self._rate = rate
        self._cum_frames = 0

    def _emit(self, frames: int) -> int:
        start = frames_to_ms(self._cum_frames, self._rate, Rounding.ROUND_HALF_UP)
        self._cum_frames += frames
        end = frames_to_ms(self._cum_frames, self._rate, Rounding.ROUND_HALF_UP)
        return end - start

    def card_duration(self, duration_ms: int) -> tuple[int, int]:
        frames = max(1, ms_to_frames(duration_ms, self._rate, Rounding.ROUND_HALF_UP))
        return self._emit(frames), frames

    def shot_duration(self, inp: ShotInput, rules: TimelineRules) -> tuple[int, int]:
        frames = _resolve_duration_frames(inp, rules, self._rate)
        return self._emit(frames), frames


def _frame_grid(config: ProjectConfig) -> _IntFrameGrid | _RationalFrameGrid:
    """Dispatch the compile onto the int (byte-identical) or rational grid by
    asking the project for its exact edit rate. A whole-number rate
    (``exact_int is not None``, every int project) takes today's code path and
    never enters the rational walker."""
    rate = config.frame_rate
    if rate.exact_int is not None:
        return _IntFrameGrid(config.fps)
    return _RationalFrameGrid(rate)


def _split_caption(text: str, max_chars: int, max_lines: int) -> list[str]:
    """Split dialogue into caption-sized chunks. CJK text has no spaces, so
    the splitter is width-based with a preference for punctuation breaks."""
    text = text.strip()
    if not text:
        return []
    # Round W (issue #2/#6, defensive): CaptionRules.max_chars_per_line/
    # max_lines are now validated >= 1 at the model boundary, so a healthy
    # rules.yaml always yields budget >= 1. This floor is the last line of
    # defence for a caller passing raw ints (or a hand-rolled/legacy timeline
    # rules payload that bypassed the model) — at budget=0 the loop below cuts
    # 0 characters per iteration and never terminates (mirrors the floor
    # ``_timed_captions`` already applies a few lines down).
    budget = max(1, max_chars * max_lines)
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


def _is_valid_word_timing(words: Any) -> bool:
    """A well-formed word-timing list: non-empty, and EVERY entry a dict carrying
    a ``text`` plus numeric ``start_ms``/``end_ms`` — the exact keys
    :func:`_timed_captions` indexes (``word["text"]``,
    ``group[0]["start_ms"]``, ``group[-1]["end_ms"]``). Word-timing is an
    ENHANCEMENT, never a gate (see ``_align_words_to_text``): a legacy /
    hand-edited / partially-written / older-format ``<take>.timing.json`` whose
    entries are malformed must DEGRADE to the weighted-split fallback, never
    crash the compile/build with a KeyError/TypeError downstream."""
    return (
        isinstance(words, list) and bool(words)
        and all(
            isinstance(w, dict) and "text" in w
            and isinstance(w.get("start_ms"), (int, float))
            and isinstance(w.get("end_ms"), (int, float))
            for w in words
        )
    )


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
                    original_text: str = "",
                    shot_id: str = "") -> list[CaptionLine]:
    """Group TTS word boundaries into caption cues (round M).

    Greedy fill up to the line budget, breaking eagerly after CJK sentence
    enders; each cue's start/end come from the FIRST/LAST word's real times
    (voice-relative), shifted by the voice clip's timeline offset.

    ``shot_id`` (WP1) is stamped onto every cue so impact / voice-repair can
    attribute cues without temporal reconstruction.
    """
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
                shot=shot_id,
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


def _branding_overlays(packaging: PackagingSpec, total_ms: int) -> list[OverlayClip]:
    """Branding overlays (round-Q): logo / watermark / badge / cta as overlay-
    track entries with resolved windows. PURE — windows come from ``total_ms``
    only and the logo/image-watermark ``source`` is a HUMAN asset path recorded
    verbatim (never read, never generated here). All default-off, so an
    untouched packaging spec adds nothing (the compiled timeline stays
    byte-identical). Cta's window is the closing ``[total - at_end_ms, total]``.
    """
    out: list[OverlayClip] = []
    if total_ms <= 0:
        return out

    def _window(from_ms: int, duration_ms: int | None) -> tuple[int, int] | None:
        start = max(0, from_ms)
        if start >= total_ms:  # a window past the end could never be seen
            return None
        span = total_ms - start if duration_ms is None else min(duration_ms, total_ms - start)
        return (start, span) if span > 0 else None

    logo = packaging.logo
    if logo.enabled and logo.image:
        win = _window(logo.from_ms, logo.duration_ms)
        if win is not None:
            out.append(OverlayClip(
                kind="logo", source=logo.image, corner=logo.corner,
                size_pct=logo.size_pct, margin_pct=logo.margin_pct,
                opacity=logo.opacity, start_ms=win[0], duration_ms=win[1],
            ))

    wm = packaging.watermark
    if wm.enabled and (wm.image or wm.text):  # watermark spans the whole film
        out.append(OverlayClip(
            kind="watermark", text=wm.text, source=wm.image,
            size_pct=wm.size_pct, opacity=wm.opacity, position=wm.position,
            start_ms=0, duration_ms=total_ms,
        ))

    badge = packaging.badge
    if badge.enabled and badge.text:
        win = _window(badge.from_ms, badge.duration_ms)
        if win is not None:
            out.append(OverlayClip(
                kind="badge", text=badge.text, corner=badge.corner,
                start_ms=win[0], duration_ms=win[1],
            ))

    cta = packaging.cta
    if cta.enabled and cta.text:
        start = max(0, total_ms - cta.at_end_ms)
        span = total_ms - start
        if span > 0:
            out.append(OverlayClip(
                kind="cta", text=cta.text, position=cta.position,
                start_ms=start, duration_ms=span,
            ))
    return out


def compile_timeline(inp: CompileInput) -> Timeline:
    if not inp.shots:
        raise CompileError("nothing to compile: no shots with a usable selected take")

    rules, config = inp.rules, inp.config
    packaging = inp.packaging
    tracks = TimelineTracks()
    cursor = 0
    # R2 rational edit rate: dispatch the whole compile onto the int
    # (byte-identical) or the rational cumulative-boundary frame grid by asking
    # the project for its exact edit rate. An int project builds an _IntFrameGrid
    # (delegating to the untouched snap_to_frame_grid/_resolve_duration_ms) and
    # NEVER constructs the rational walker (the spy pin); a 1001-family project
    # walks the exact rational boundary and stamps each clip's duration_frames.
    grid = _frame_grid(config)

    # Packaging intro/outro become REAL leading/trailing segments inside the
    # clip accumulation (§13-14): the intro advances `cursor` before shots are
    # placed, so voice starts, caption cues and the total length all shift
    # naturally — no track is post-shifted. Transitions are computed over the
    # combined segment list so the true last segment gets no transition_out.
    intro_on = packaging is not None and packaging.intro.enabled
    outro_on = packaging is not None and packaging.outro.enabled
    n_segments = (1 if intro_on else 0) + len(inp.shots) + (1 if outro_on else 0)

    # Round U: per-boundary overrides (rules.transition_overrides) — keyed by
    # the OUT-edge segment's id. The compiler places the override verbatim on
    # the clip (the render already treats "cut"/None as no fade + no xfade and
    # unknown types as dip-to-black), so behaviour stays a pure function of the
    # compiled timeline. The true last segment never carries a transition_out.
    overrides = rules.transition_overrides

    def _transition(seg_idx: int, seg_id: str) -> TransitionSpec | None:
        if seg_idx == n_segments - 1:
            return None
        if seg_id in overrides:
            return overrides[seg_id]  # explicit null = hard cut
        return rules.transition_default

    seg_i = 0
    intro_ms = 0  # content starts here; absolute overlays shift past the intro
    if intro_on:
        card = packaging.intro
        dur, dur_frames = grid.card_duration(card.duration_ms)
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
                duration_frames=dur_frames,
                transition_out=_transition(seg_i, "__intro__"),
            )
        )
        cursor += dur
        seg_i += 1

    for s in inp.shots:
        duration_ms, duration_frames = grid.shot_duration(s, rules)
        tracks.video.append(
            VideoClip(
                shot=s.shot.id,
                take=s.take_name,
                source=s.take_source,
                start_ms=cursor,
                duration_ms=duration_ms,
                duration_frames=duration_frames,
                transition_out=_transition(seg_i, s.shot.id),
                # Round-T: the footage's own-audio level/mute travels onto the
                # clip so the render is purely a function of the compiled
                # timeline (defaults 0dB/unmuted = today's behaviour).
                source_gain_db=s.shot.source_audio.gain_db,
                source_mute=s.shot.source_audio.mute,
                # Round-T virtual trim: the window in-point seeds the clip so the
                # render seeks there and the pre-``in`` footage is the incoming
                # HEAD handle a cross-dissolve needs. Default 0 = read from the
                # head (byte-identical). ``duration_ms`` already came from the
                # window length via ``take_duration_ms`` (rules still clamp), so
                # the material after ``in+duration_ms`` is the spare TAIL handle.
                source_in_ms=s.source_in_ms,
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
            if _is_valid_word_timing(s.voice_timing) and s.voice_duration_ms:
                # word-timed captions (round M): cue boundaries come from the
                # TTS engine's word boundaries — captions snap to real speech
                tracks.captions.extend(
                    _timed_captions(
                        s.voice_timing, cap_start,
                        rules.captions.max_chars_per_line, rules.captions.max_lines,
                        speaker=s.shot.dialogue.speaker,
                        original_text=text,
                        shot_id=s.shot.id,
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
                                shot=s.shot.id,
                            )
                        )
                        t = end

        cursor += duration_ms

    if outro_on:
        card = packaging.outro
        dur, dur_frames = grid.card_duration(card.duration_ms)
        tracks.video.append(
            VideoClip(
                shot="__outro__",
                take="packaging",
                source=packaging_card_relpath(
                    "outro", card, config.width, config.height, config.fps
                ),
                start_ms=cursor,
                duration_ms=dur,
                duration_frames=dur_frames,
                transition_out=_transition(seg_i, "__outro__"),  # last segment → None
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

        # Branding overlays (round-Q): logo/watermark/badge/cta ride the same
        # overlay track, appended after the info cards. Their windows are a pure
        # function of total_ms; all-off adds nothing (byte-identical timeline).
        tracks.overlay.extend(_branding_overlays(packaging, total_ms))

    if rules.music.source:
        tracks.music.append(
            AudioClip(
                source=rules.music.source,
                start_ms=0,
                duration_ms=total_ms,  # BGM is trimmed to picture length
                gain_db=rules.music.gain_db,
                ducking=rules.music.ducking,
                fade_out_ms=rules.music.fade_out_ms,
                # BGM in-point + fade-in (round-T): seek into the source and ramp
                # up from silence, carried onto the clip (defaults 0 = today's).
                start_offset_ms=rules.music.start_offset_ms,
                fade_in_ms=rules.music.fade_in_ms,
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
                start_offset_ms=audio.ambient.start_offset_ms,
                fade_in_ms=audio.ambient.fade_in_ms,
                duck_threshold=audio.ambient.duck_threshold,
                duck_ratio=audio.ambient.duck_ratio,
                duck_attack_ms=audio.ambient.duck_attack_ms,
                duck_release_ms=audio.ambient.duck_release_ms,
            )
        )

    # R2: a rational project also echoes its exact {num, den} onto the timeline
    # (surfaced under the exporter-facing rate key by the model serializer) so
    # downstream consumers/exporters see the truth without re-loading
    # project.yaml. None for an int project → dropped by the serializer →
    # byte-identical timeline. fps stays the int nominal mirror.
    rate = config.frame_rate
    rate_echo = None if rate.exact_int is not None else EditRate(
        num=rate.numerator, den=rate.denominator
    )
    return Timeline(
        meta=TimelineMeta(compiled_from=inp.fingerprint(), mode="compiled"),
        fps=config.fps,
        width=config.width,
        height=config.height,
        duration_ms=total_ms,
        tracks=tracks,
        rate_echo=rate_echo,
    )


# --------------------------------------------------------- project plumbing


def _find_voice_take(project: Project, shot_id: str, *,
                     lang: str | None = None
                     ) -> tuple[Path, VoiceTakeSidecar | None] | None:
    """The NEWEST voice take as ``(media, sidecar)`` — media is append-only
    (§3), so the highest voice_take_NN is the latest decision. (Pre-round-B
    this picked the oldest — sorted-first — which contradicted the append-only
    semantics.)

    ``lang`` (WP4): look under ``media/gen/<shot>/locales/<lang>/`` first;
    if empty, fall back to the base voice dir (so a partial locale still
    compiles). ``lang=None`` is the pre-WP4 path (byte-identical).

    Unlike :func:`_find_voice` this keeps the sidecar, so the compiler can read
    the synthesis-time probe cache (``VoiceTakeSidecar.probe``) instead of
    re-ffprobing the voice file live on every compile (audit FP-L2)."""
    if lang:
        voices = project.voice_takes(shot_id, lang=lang)
        if voices:
            return voices[-1]
    voices = project.voice_takes(shot_id)
    return voices[-1] if voices else None


def _find_voice(project: Project, shot_id: str, *,
                lang: str | None = None) -> Path | None:
    """The NEWEST voice take's media path (see :func:`_find_voice_take` for the
    newest-wins / locale-fallback resolution). Kept as the path-only view for
    ``build/locale_build`` and callers that never needed the sidecar."""
    take = _find_voice_take(project, shot_id, lang=lang)
    return take[0] if take else None


def _voice_duration_ms(take: tuple[Path, VoiceTakeSidecar | None] | None,
                       probe_fn: ProbeFn) -> int | None:
    """Voice duration for the timeline, CACHE-FIRST (audit FP-L2).

    Reuse the ``duration_ms`` the synthesis path already probed into the voice
    take's sidecar — providers/tts, providers/edge_tts and media/voicefix all
    fill ``VoiceTakeSidecar.probe`` via ``providers.base.probe_media``, the very
    same ffprobe read the video path caches in ``TakeSidecar.probe`` — else fall
    back to a LIVE ``probe_fn`` call. This is byte-identical to the pre-cache
    behaviour for a legacy take whose sidecar carries no probe:
    ``probe_duration_ms(path)`` and ``probe_media(path).duration_ms`` are the
    same ffprobe reading of the same bytes.

    The probe cache is APPEND-ONLY project truth — a take's media never changes
    after registration (§3) — so a cached duration can never go stale; there is
    no invalidation path to get wrong. Mirrors the video path's
    ``take.sidecar.probe.duration_ms`` read a few lines below."""
    if take is None:
        return None
    path, sidecar = take
    dur: int | None = None
    if sidecar is not None and sidecar.probe is not None:
        dur = sidecar.probe.duration_ms
    if dur is None:
        dur = probe_fn(path)
    return dur


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
    except (json.JSONDecodeError, OSError):
        return None
    # Only a WELL-FORMED word list is handed on; a malformed/legacy sidecar
    # degrades to None (weighted-split fallback) rather than crashing the compile.
    return words if _is_valid_word_timing(words) else None


def gather_compile_input(project: Project, probe_fn: ProbeFn, *,
                          include_unindexed: bool = False,
                          allow_missing_takes: bool = False,
                          lang: str | None = None) -> CompileInput:
    """Assemble the compiler's input from the project. Raises CompileError
    listing every shot that cannot go on the timeline (missing/unselected).

    ``include_unindexed=False`` (default, round W review #5): only shots in
    ``shots/index.yaml`` order can reach the timeline — index order is the
    order authority, so a draft shot that exists on disk but was never added
    to the index can never silently affect the compiled film. Pass
    ``include_unindexed=True`` (``manju build --include-unindexed``) to opt
    back into the old behaviour for one call.

    ``allow_missing_takes=False`` (default): missing/unselected shots are
    hard errors. ``True`` (WP2 audition only) inserts slate placeholders
    (``take_name='__slate__'``) so voice-driven durations still compile;
    the real ``timeline.json`` path must never pass this flag.

    ``lang`` (WP4, default None): use locale voice takes + overlay dialogue
    text for captions. Video takes stay shared. ``None`` is byte-identical.
    """
    from ..build.stale import evaluate_all  # local import: build depends on timeline too

    config = project.load_config()
    rules = project.load_rules()
    shots: list[ShotInput] = []
    problems: list[str] = []

    def _shot_for_compile(sid: str):
        shot = project.load_shot(sid)
        if lang:
            try:
                from ..core.locale import overlay_shot_for_voice
                return overlay_shot_for_voice(project, shot, lang)
            except Exception:
                return shot
        return shot

    for status in evaluate_all(project, indexed_only=not include_unindexed):
        if not status.usable:
            if allow_missing_takes:
                shot = _shot_for_compile(status.shot_id)
                voice_take = _find_voice_take(project, status.shot_id, lang=lang)
                voice = voice_take[0] if voice_take else None
                # Cache-first (audit FP-L2): the synthesis-time sidecar probe
                # when present, else the same live probe_fn call as before —
                # byte-identical for legacy takes that carry no probe.
                voice_dur = _voice_duration_ms(voice_take, probe_fn)
                # Duration driven by voice + padding when present, else default
                slate_dur = voice_dur or rules.timing.default_shot_ms
                if voice_dur:
                    slate_dur = (
                        voice_dur
                        + rules.timing.padding_before_ms
                        + rules.timing.padding_after_ms
                    )
                shots.append(
                    ShotInput(
                        shot=shot,
                        take_name="__slate__",
                        take_source=f"__slate__/{status.shot_id}",
                        take_duration_ms=slate_dur,
                        voice_source=project.relpath(voice) if voice else None,
                        voice_duration_ms=voice_dur,
                        voice_timing=_load_voice_timing(voice) if voice else None,
                    )
                )
                continue
            problems.append(f"{status.shot_id}: {status.state.value}"
                            + (f" ({status.note})" if status.note else ""))
            continue
        take = status.take
        assert take is not None and take.media_path is not None
        voice_take = _find_voice_take(project, status.shot_id, lang=lang)
        voice = voice_take[0] if voice_take else None
        take_dur = take.sidecar.probe.duration_ms if take.sidecar.probe else None
        if take_dur is None:
            take_dur = probe_fn(take.media_path)
        # Round-T virtual trim: a windowed take's own media is the WHOLE source
        # file (a hardlink), so the material the compiler may lay down is the
        # WINDOW length, not the file length — derive it from in/out (rules still
        # clamp in _resolve_duration_ms). Defaults (in 0 / out None) leave
        # take_dur exactly as before, so whole-file takes are byte-identical.
        in_ms = take.sidecar.source_in_ms or 0
        out_ms = take.sidecar.source_out_ms
        # Round W (issue #22): TakeSidecar's model validator already rejects
        # in<0 / out<=in at LOAD time (core/models.py), and container.Project
        # .takes() degrades a sidecar that fails it to a safe whole-file window
        # + take.error (surfaced by manju check/QC — see core/check.py and
        # qc/checks.py). This is the last line of defence: a reversed/zero-
        # length window used to be silently squashed to 1ms here instead of
        # ever being reported (the review's concrete complaint) — it is now a
        # named compile problem instead, exactly like a missing/unselected take.
        if in_ms < 0 or (out_ms is not None and out_ms <= in_ms):
            problems.append(
                f"{status.shot_id}: take '{take.name}' 的裁剪窗口非法 "
                f"(source_in_ms={in_ms}, source_out_ms={out_ms})— 应满足 in>=0 且 "
                "out>in;用 manju repair --op inout 重新设置窗口,或修正该 take 的 sidecar 文件"
            )
            continue
        if out_ms is not None:
            take_dur = out_ms - in_ms
        elif in_ms and take_dur is not None:
            take_dur = max(1, take_dur - in_ms)
        # WP4 duration policy: when a base voice exists, prefer its duration
        # for the VIDEO slot so locale builds keep segment-cache geometry;
        # locale voice still drives the voice track source/timing below.
        # Cache-first here too (audit FP-L2): the base voice take is reachable
        # WITH its sidecar via _find_voice_take(..., lang=None), so the locale
        # path reads the synthesis probe cache instead of live-probing.
        voice_take_for_duration = voice_take
        if lang:
            base_voice_take = _find_voice_take(project, status.shot_id, lang=None)
            if base_voice_take is not None:
                voice_take_for_duration = base_voice_take
        shots.append(
            ShotInput(
                shot=_shot_for_compile(status.shot_id),
                take_name=take.name,
                take_source=project.relpath(take.media_path),
                take_duration_ms=take_dur,
                voice_source=project.relpath(voice) if voice else None,
                voice_duration_ms=_voice_duration_ms(
                    voice_take_for_duration, probe_fn
                ),
                # Locale timing from locale take when present
                voice_timing=_load_voice_timing(voice) if voice else None,
                source_in_ms=in_ms,
                source_out_ms=out_ms,
            )
        )

    if problems:
        raise CompileError(
            "cannot compile timeline, unresolved shots:\n  " + "\n  ".join(problems)
        )
    if not shots:
        raise CompileError("nothing to compile: no shots with a usable selected take")
    # Packaging is loaded here (defaults to an all-disabled spec when absent),
    # so every compile call site — build graph, cli, tests — gets it for free
    # while the CompileInput field itself stays optional (§13-14).
    return CompileInput(
        config=config, rules=rules, shots=shots, packaging=project.load_packaging()
    )


def build_timeline(project: Project, probe_fn: ProbeFn, *,
                    include_unindexed: bool = False) -> tuple[Timeline, Path, bool]:
    """Compile and write. Returns (timeline, written_path, overwrote_truth).

    mode=manual makes timeline.json human truth: we only ever write
    timeline.generated.json next to it (§6).
    """
    inp = gather_compile_input(project, probe_fn, include_unindexed=include_unindexed)
    timeline = compile_timeline(inp)
    manual = project.load_rules().mode == "manual"
    path = project.save_timeline(timeline, generated_only=manual)
    return timeline, path, not manual
