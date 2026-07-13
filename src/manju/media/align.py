"""Align imported human voiceover to script text (WP3).

Produces ``<take>.timing.json`` in the Edge TTS schema
(``[{start_ms, end_ms, text}, …]``) so the compiler's existing word-timed
caption path just works. Timing sidecars are **derived and regenerable** —
re-running ``align`` overwrites them (exception to append-only, same stance
as ``.key.json``). Manual takes stay MANUAL; alignment never touches
staleness.

Three on-ramps (mirroring ``manju transcribe``):
1. ``--asr <provider>`` — cloud ASR → segments/words, spend-gated
2. ``--from-srt <file>`` — user-supplied cue timings
3. Default — deterministic text anchoring: CJK sentence split of
   ``dialogue.text`` spread over the take's real duration by piece length
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ..core.container import Project, ProjectError
from ..core.yamlio import atomic_write_text

# Match threshold for transcript ↔ dialogue anchoring
_MATCH_THRESHOLD = 0.6

# CJK / common sentence enders — same spirit as asr.distribute_text
_SENT_END = re.compile(r"(?<=[。！？!?…])")


def _inside_project(media: Path, project: Project) -> bool:
    """Real directory containment for the foreign-source decision (W1 §3.2).

    The old ``str(media).startswith(str(project.root))`` admitted the SIBLING
    directory ``<root>_evil/`` as "inside" — the foreign file was then never
    import-copied and ``project.relpath`` crashed on it. Resolve-based
    containment is the same discipline as ``Project.resolve``."""
    try:
        return Path(media).resolve().is_relative_to(project.root.resolve())
    except OSError:
        return False


def _normalize(text: str) -> str:
    """Strip punctuation/whitespace for character-level ratio matching."""
    return re.sub(r"[\s\W_]+", "", text or "", flags=re.UNICODE)


def split_dialogue(text: str) -> list[str]:
    """Split dialogue into pieces at CJK sentence enders; single piece floor."""
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENT_END.split(text) if p and p.strip()]
    return parts or [text]


def _spread_over_duration(pieces: list[str], duration_ms: int) -> list[dict[str, Any]]:
    """Length-weighted spread — same honesty as asr.distribute_text."""
    if not pieces:
        return []
    duration_ms = max(1, int(duration_ms))
    weights = [max(1, len(p)) for p in pieces]
    total_w = sum(weights)
    out: list[dict[str, Any]] = []
    t = 0
    for i, (piece, w) in enumerate(zip(pieces, weights)):
        if i == len(pieces) - 1:
            end = duration_ms
        else:
            span = max(50, int(round(duration_ms * w / total_w)))
            end = min(t + span, duration_ms)
        out.append({"start_ms": t, "end_ms": max(end, t + 1), "text": piece})
        t = end
    return out


def _write_timing(media: Path, words: list[dict[str, Any]]) -> Path:
    """Write/overwrite ``<media>.timing.json`` (regenerable derived metadata)."""
    path = media.with_suffix(".timing.json")
    # Edge schema: array of {start_ms, end_ms, text}
    clean = [
        {
            "start_ms": int(w["start_ms"]),
            "end_ms": int(w["end_ms"]),
            "text": str(w["text"]),
        }
        for w in words
        if str(w.get("text") or "").strip()
    ]
    atomic_write_text(path, json.dumps(clean, ensure_ascii=False, indent=2) + "\n")
    return path


def _resolve_take(project: Project, shot_id: str, take: str | None) -> Path:
    voices = project.voice_takes(shot_id)
    if not voices:
        raise ProjectError(f"{shot_id}: 没有配音 take — 先 import/ingest 或 manju voice")
    if take:
        for media, _sc in voices:
            if media.stem == take or media.name == take:
                return media
        raise ProjectError(f"{shot_id}: 找不到配音 take {take!r}")
    return voices[-1][0]  # newest


def _anchor_segments(
    dialogue_text: str,
    segments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Match transcript segments against dialogue; below threshold keep raw
    timings with a 中文 advisory. Returns (timing_words, advisories)."""
    advisories: list[str] = []
    if not segments:
        return [], ["没有识别到任何片段"]
    joined = "".join(str(s.get("text") or "") for s in segments)
    ratio = SequenceMatcher(None, _normalize(dialogue_text), _normalize(joined)).ratio()
    if ratio < _MATCH_THRESHOLD:
        advisories.append(
            f"识别文本与剧本差异较大(相似度 {ratio:.2f}<{_MATCH_THRESHOLD}),"
            "已按识别结果对齐"
        )
        # keep raw segment timings
        return [
            {
                "start_ms": int(s.get("start_ms", 0)),
                "end_ms": int(s.get("end_ms", 0)),
                "text": str(s.get("text") or ""),
            }
            for s in segments
        ], advisories
    # Good match: use segment timings as-is (already time-aligned)
    return [
        {
            "start_ms": int(s.get("start_ms", 0)),
            "end_ms": int(s.get("end_ms", 0)),
            "text": str(s.get("text") or ""),
        }
        for s in segments
    ], advisories


def align_shot(
    project: Project,
    shot_id: str,
    *,
    take: str | None = None,
    from_srt: Path | None = None,
    asr: str | None = None,
    assume_yes: bool = False,
    probe_fn=None,
) -> dict[str, Any]:
    """Align one shot's newest (or named) voice take. Returns a report dict."""
    from ..media.probe import probe_duration_ms

    probe_fn = probe_fn or probe_duration_ms
    shot = project.load_shot(shot_id)
    dialogue = (shot.dialogue.text or "").strip()
    if not dialogue:
        raise ProjectError(f"{shot_id}: dialogue.text 为空,无法对齐")

    media = _resolve_take(project, shot_id, take)
    duration_ms = max(1, int(probe_fn(media) or 1000))
    advisories: list[str] = []
    source = "text_anchor"

    if from_srt is not None:
        from ..providers.asr import parse_srt

        source = "from_srt"
        segs = parse_srt(Path(from_srt).read_text(encoding="utf-8"))
        segments = [
            {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
            for s in segs
        ]
        words, adv = _anchor_segments(dialogue, segments)
        advisories.extend(adv)
    elif asr is not None:
        source = "asr"
        from ..build.graph import spend_gate
        from ..providers.asr import AsrUnavailable, get_asr_provider

        name = None if asr in ("", "default", "true", "1") else str(asr)
        try:
            provider = get_asr_provider(name)
            manifest = getattr(provider, "manifest", None)
            cost = getattr(manifest, "cost", None) if manifest is not None else None
            if cost is not None and float(getattr(cost, "per_call", 0) or 0) > 0:
                spend_gate(
                    project, float(cost.per_call), cost.currency,
                    assume_yes=assume_yes,
                    hint=f"确认后重试: manju align {shot_id} --asr --yes",
                )
            segs = provider.transcribe(media)
            segments = [
                {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
                for s in segs
            ]
        except (AsrUnavailable, Exception) as exc:
            raise ProjectError(f"ASR 失败: {' '.join(str(exc).split())[:400]}") from exc
        words, adv = _anchor_segments(dialogue, segments)
        advisories.extend(adv)
    else:
        # Default: free deterministic text anchoring
        pieces = split_dialogue(dialogue)
        words = _spread_over_duration(pieces, duration_ms)

    path = _write_timing(media, words)

    # AI_IDE_18 WP2: alignment evidence companion sidecar (media/timing.py).
    # Binds the EXACT source-audio hash + aligner identity so a later same-name
    # replacement reads STALE, and records a genuine failure as UNALIGNED rather
    # than letting an empty/failed pass masquerade as timed (§5 pins). The bare
    # <take>.timing.json above stays byte-identical for the caption consumers.
    from . import timing as _timing

    speaker = (shot.dialogue.speaker or "").strip()
    if (source in ("asr", "from_srt")) and not words:
        _timing.write_unaligned(
            media, provider=source,
            reason="识别/字幕未产生任何可对齐片段(保留 UNALIGNED,不伪造均匀时间)")
        align_status = _timing.UNALIGNED
    else:
        ev_cues = [{**w, "speaker": speaker} if speaker else dict(w) for w in words]
        _timing.write_evidence(media, ev_cues, provider=source, status=_timing.ALIGNED)
        align_status = _timing.ALIGNED

    return {
        "shot": shot_id,
        "take": media.stem,
        "timing": project.relpath(path),
        "evidence": project.relpath(_timing.align_path(media)),
        "cues": len(words),
        "source": source,
        "align_status": align_status,
        "duration_ms": duration_ms,
        "advisories": advisories,
        "manual": True,  # never changes MANUAL status
    }


def plan_multi_shot(
    project: Project,
    media: Path,
    shot_ids: list[str],
    *,
    from_srt: Path | None = None,
    asr: str | None = None,
    assume_yes: bool = False,
    probe_fn=None,
) -> dict[str, Any]:
    """Plan multi-shot VO split: order-preserving greedy match of each shot's
    dialogue against the transcript. Returns a plan table; nothing applied."""
    from ..media.probe import probe_duration_ms
    from ..providers.asr import parse_srt

    probe_fn = probe_fn or probe_duration_ms
    media = Path(media)
    if not media.exists():
        raise ProjectError(f"media not found: {media}")
    total_ms = max(1, int(probe_fn(media) or 1000))

    # Build transcript segments
    if from_srt is not None:
        segs = parse_srt(Path(from_srt).read_text(encoding="utf-8"))
        segments = [
            {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
            for s in segs
        ]
    elif asr is not None:
        # Honest v1: multi-shot ASR is NOT inlined (reproducibility + spend
        # control). Force the two-step path the guide recommends.
        raise ProjectError(
            "multi-shot --asr 未内置执行 — 请先: "
            "manju transcribe <media> --asr <provider> [--yes] "
            "得到 captions/transcripts/<name>.srt, 再: "
            "manju align --media <media> --shots … --from-srt <that.srt> "
            "[--apply]. 单镜头 ASR 仍可用: manju align S001 --asr"
        )
    else:
        # No transcript: length-weighted windows by dialogue length
        texts = []
        for sid in shot_ids:
            shot = project.load_shot(sid)
            texts.append((sid, (shot.dialogue.text or "").strip()))
        pieces = [t for _, t in texts]
        weights = [max(1, len(t)) for t in pieces] or [1]
        total_w = sum(weights)
        rows = []
        t = 0
        for i, (sid, text) in enumerate(texts):
            if i == len(texts) - 1:
                end = total_ms
            else:
                span = max(100, int(round(total_ms * weights[i] / total_w)))
                end = min(t + span, total_ms)
            conf = 0.5 if text else 0.0
            rows.append({
                "shot": sid,
                "start_ms": t,
                "end_ms": end,
                "matched_text": text,
                "confidence": conf,
                "state": "ok" if conf >= _MATCH_THRESHOLD else "pending",
            })
            t = end
        return {
            "media": str(media),
            "duration_ms": total_ms,
            "rows": rows,
            "unmatched": [],
            "source": "text_weight",
        }

    # Greedy order-preserving match of dialogue texts against consecutive segments
    rows: list[dict[str, Any]] = []
    unmatched: list[str] = []
    seg_i = 0
    for sid in shot_ids:
        shot = project.load_shot(sid)
        text = (shot.dialogue.text or "").strip()
        if not text or seg_i >= len(segments):
            unmatched.append(sid)
            rows.append({
                "shot": sid, "start_ms": None, "end_ms": None,
                "matched_text": text, "confidence": 0.0, "state": "pending",
            })
            continue
        # Take consecutive segments until ratio peaks or we exhaust
        best_j = seg_i
        best_ratio = 0.0
        acc = ""
        for j in range(seg_i, len(segments)):
            acc += str(segments[j].get("text") or "")
            r = SequenceMatcher(None, _normalize(text), _normalize(acc)).ratio()
            if r >= best_ratio:
                best_ratio = r
                best_j = j
            # stop if we overshot badly after a peak
            if r < best_ratio - 0.15 and best_ratio >= _MATCH_THRESHOLD:
                break
        start = int(segments[seg_i].get("start_ms", 0))
        end = int(segments[best_j].get("end_ms", start + 1))
        state = "ok" if best_ratio >= _MATCH_THRESHOLD else "pending"
        rows.append({
            "shot": sid,
            "start_ms": start,
            "end_ms": end,
            "matched_text": text,
            "confidence": round(best_ratio, 3),
            "state": state,
        })
        seg_i = best_j + 1

    return {
        "media": str(media),
        "duration_ms": total_ms,
        "rows": rows,
        "unmatched": unmatched,
        "source": "from_srt" if from_srt else "asr",
    }


def apply_multi_shot(
    project: Project,
    plan: dict[str, Any],
    *,
    rows: list[int] | None = None,
    actor: str = "human",
) -> dict[str, Any]:
    """Slice media per accepted plan row, register manual voice takes with
    slice-local timing, write an ingest-style batch record."""
    import shutil
    import subprocess
    from datetime import datetime, timezone

    from ..core.events import append_event
    from ..core.models import VoiceTakeSidecar
    from ..core.spec import VOICE_VERSION, compute_voice_hash

    media = Path(plan["media"])
    if not media.is_absolute():
        candidate = project.root / media
        media = candidate if candidate.exists() else Path(plan["media"])
    # Copy foreign source into imports first
    if not _inside_project(media, project):
        imports = project.root / "media" / "imports"
        imports.mkdir(parents=True, exist_ok=True)
        dest_imp = imports / media.name
        if not dest_imp.exists():
            shutil.copy2(media, dest_imp)
        media = dest_imp

    all_rows = plan.get("rows") or []
    indices = set(rows) if rows is not None else {
        i for i, r in enumerate(all_rows)
        if r.get("state") == "ok" and r.get("start_ms") is not None
    }
    applied = []
    skipped = []
    for i, row in enumerate(all_rows):
        if i not in indices:
            skipped.append({"index": i, "shot": row.get("shot"), "reason": "not selected"})
            continue
        if row.get("state") == "pending" or row.get("start_ms") is None:
            skipped.append({"index": i, "shot": row.get("shot"),
                            "reason": "pending/low confidence — not applied without confirm"})
            continue
        sid = row["shot"]
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        # ffmpeg slice (re-encode floor for reliability)
        import tempfile
        with tempfile.TemporaryDirectory(prefix=f"align_{sid}_") as tmp:
            out = Path(tmp) / f"{sid}.wav"
            ss = start_ms / 1000.0
            dur = max(0.05, (end_ms - start_ms) / 1000.0)
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", str(ss), "-t", str(dur), "-i", str(media),
                "-ac", "1", "-ar", "44100", str(out),
            ]
            subprocess.run(cmd, check=False, capture_output=True)
            if not out.exists() or out.stat().st_size == 0:
                skipped.append({"index": i, "shot": sid, "reason": "ffmpeg slice failed"})
                continue
            shot = project.load_shot(sid)
            bible = project.load_bible()
            # Manual take: voice_hash still recorded but take is treated as
            # manual when… actually register_voice_take always writes sidecar.
            # Guide: "register each slice via register_voice_take as a manual
            # take WITH a timing sidecar". Manual = no sidecar OR we could use
            # a special marker. Looking at evaluate_voice: sidecar None = MANUAL.
            # But we need timing sidecar. So: register with sidecar, then the
            # take is FRESH/STALE by hash — for human VO the hash of dialogue
            # matches so FRESH. That's fine; "manual" in the multi-shot case
            # means human audio, not sidecar-less. Guide acceptance: "Voice
            # state for these takes remains MANUAL". So we should drop without
            # voice sidecar... but then how do we know it's intentional?
            # Re-read: "register each slice via the existing register_voice_take
            # path as a manual take WITH a timing sidecar"
            # Conflict with evaluate_voice: MANUAL = no sidecar.
            # Practical approach: write media as voice_take_NN.wav WITHOUT
            # .sidecar.yaml (MANUAL), WITH .timing.json.
            tdir = project.takes_dir(sid)
            tdir.mkdir(parents=True, exist_ok=True)
            name = project.next_voice_take_name(sid)
            dest = tdir / f"{name}.wav"
            shutil.copy2(out, dest)
            # timing shifted to slice-local (start at 0)
            local_words = [{
                "start_ms": 0,
                "end_ms": end_ms - start_ms,
                "text": row.get("matched_text") or "",
            }]
            _write_timing(dest, local_words)
            # NO .sidecar.yaml → MANUAL forever
            applied.append({
                "shot": sid, "take": name,
                "start_ms": start_ms, "end_ms": end_ms,
            })
            append_event(project.root, actor, "align", {
                "shot": sid, "take": name, "source": str(media.name),
            })

    # Batch record (ingest-style)
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    batch_dir = project.root / "reports" / "ingest_batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    batch = {
        "id": batch_id,
        "kind": "align_multi",
        "media": project.relpath(media) if _inside_project(media, project) else str(media),
        "applied": applied,
        "skipped": skipped,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": actor,
    }
    from ..core.yamlio import write_yaml
    write_yaml(batch_dir / f"{batch_id}.yaml", batch)
    return {"batch": batch_id, "applied": applied, "skipped": skipped}
