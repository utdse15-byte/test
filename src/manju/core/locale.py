"""Locale overlays — multilingual versions without forking the picture (WP4).

A locale is an overlay under ``locales/<lang>/``:

    locales/en/
      lines.yaml   # S001: {text: "…", base_hash: <sha of base dialogue.text>}
      voices.yaml  # optional per-speaker voice_id overrides

Locale text NEVER enters ``spec_payload`` — picture pipeline is 100% shared.
Only voice_payload / caption compilation see the overlay.

Translation staleness: ``base_hash`` = sha256 of base dialogue.text at
translation time. Base moved → 翻译过期 (advisory; engine never auto-translates).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..core.container import Project, ProjectError
from ..core.hashing import hash_value
from ..core.yamlio import atomic_write_text, read_yaml, write_yaml

_LANG_RE = re.compile(r"^[a-z]{2,8}(-[A-Za-z0-9]+)*$")

# locales/<lang>/meta.yaml — an OPTIONAL, EXPLICIT per-locale declaration file.
# Today it carries one field: ``direction: rtl|ltr`` (the human's statement of
# the language's text direction). BOTH sets are closed and validated so a
# typo'd key or a bogus value can never slip through — the engine reads only
# what a human wrote and refuses the rest. Direction is NEVER inferred from the
# language code (no CLDR tables, no guessing): an RTL language with no meta.yaml
# has no declared direction, full stop.
_LOCALE_META_KEYS = ("direction",)
_LOCALE_DIRECTIONS = ("ltr", "rtl")


def locales_dir(project: Project) -> Path:
    return project.root / "locales"


def locale_dir(project: Project, lang: str) -> Path:
    return locales_dir(project) / validate_lang(lang)


def base_text_hash(text: str) -> str:
    return hash_value({"text": text or ""})


def validate_lang(lang: str) -> str:
    """Refuse anything that is not a safe single path segment (BCP-47-ish).

    Every locale path joiner (``locales/<lang>/``, captions/finals overlays,
    voice take dirs) must call this — a raw ``--lang ..`` must never escape
    the intended tree (project-wide bug scan 2026-07-15 P0-3).
    """
    lang = (lang or "").strip()
    if not lang or lang in (".", "..") or "/" in lang or "\\" in lang:
        raise ProjectError(
            f"locale id 非法: {lang!r} — 使用 BCP-47 风格如 en / en-US / ja"
        )
    if not _LANG_RE.match(lang):
        raise ProjectError(
            f"locale id 非法: {lang!r} — 使用 BCP-47 风格如 en / en-US / ja"
        )
    return lang


def list_locales(project: Project) -> list[str]:
    d = locales_dir(project)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and (p / "lines.yaml").exists())


def load_lines(project: Project, lang: str) -> dict[str, dict[str, Any]]:
    path = locale_dir(project, lang) / "lines.yaml"
    if not path.exists():
        return {}
    data = read_yaml(path) or {}
    if not isinstance(data, dict):
        return {}
    return {str(k): (v if isinstance(v, dict) else {"text": str(v)}) for k, v in data.items()}


def load_voices(project: Project, lang: str) -> dict[str, dict[str, Any]]:
    path = locale_dir(project, lang) / "voices.yaml"
    if not path.exists():
        return {}
    data = read_yaml(path) or {}
    return data if isinstance(data, dict) else {}


def load_locale_meta(project: Project, lang: str) -> dict[str, Any]:
    """Read the OPTIONAL declared ``locales/<lang>/meta.yaml``.

    A missing (or empty) file → ``{}`` — the honest default: the locale
    declares nothing. Today the ONLY supported field is ``direction: rtl|ltr``,
    the human's EXPLICIT statement of text direction; it is NEVER inferred from
    the language code (no CLDR tables, no guessing). Anything the human did not
    mean is a STRUCTURED rejection (the S4 ``cache_toolchain_keys`` precedent —
    name the allowed set, name the offending value, say WHY) rather than a
    silently-ignored typo:

    * a non-mapping document is rejected (meta.yaml is a key→value declaration);
    * an unknown key is rejected (the field set is closed);
    * a ``direction`` outside ``{rtl, ltr}`` is rejected.
    """
    path = locale_dir(project, lang) / "meta.yaml"
    if not path.exists():
        return {}
    data = read_yaml(path) or {}
    if not isinstance(data, dict):
        raise ProjectError(
            f"locales/{lang}/meta.yaml: 必须是键值映射(实际 {type(data).__name__})"
            f" — 目前支持的字段:{list(_LOCALE_META_KEYS)}(例如 direction: rtl)"
        )
    unknown = [str(k) for k in data if k not in _LOCALE_META_KEYS]
    if unknown:
        raise ProjectError(
            f"locales/{lang}/meta.yaml: 未知字段 {sorted(unknown)} — "
            f"目前只接受 {list(_LOCALE_META_KEYS)}(排版方向必须由人显式声明,"
            f"引擎绝不从语言代码推断);未知字段一律在读取层拒绝,"
            f"以免悄悄忽略打错的声明"
        )
    meta: dict[str, Any] = {}
    if "direction" in data:
        direction = data["direction"]
        if direction not in _LOCALE_DIRECTIONS:
            raise ProjectError(
                f"locales/{lang}/meta.yaml: direction={direction!r} 非法 — "
                f"只接受 {list(_LOCALE_DIRECTIONS)}(rtl=从右到左,ltr=从左到右);"
                f"这是人对该语言排版方向的显式声明,绝不从语言代码推断"
            )
        meta["direction"] = direction
    return meta


def add_locale(project: Project, lang: str) -> dict[str, Any]:
    """Scaffold locales/<lang>/lines.yaml — one row per shot WITH dialogue.

    TRISURFACE F-11 (round 4): scaffolding every shot minted hash-of-empty
    placeholder rows for dialogue-less shots, and `locale status` then read
    ``missing=N`` forever — green unreachable, and "you owe a translation"
    indistinguishable from "there is nothing to translate". Dialogue-less
    shots are NOT debt: they are skipped here, read ``not_needed`` in
    :func:`line_status` (legacy empty rows included, no migration), and a
    shot that gains dialogue later becomes ``missing`` exactly then."""
    lang = validate_lang(lang)
    d = locale_dir(project, lang)
    d.mkdir(parents=True, exist_ok=True)
    lines_path = d / "lines.yaml"
    existing = load_lines(project, lang) if lines_path.exists() else {}
    lines: dict[str, Any] = dict(existing)
    added = []
    for sid in project.shot_ids():
        if sid in lines:
            continue
        shot = project.load_shot(sid)
        text = shot.dialogue.text or ""
        if not text.strip():
            continue  # nothing to translate — no row, no debt (F-11)
        lines[sid] = {
            "text": "",
            "base_hash": base_text_hash(text),
            # comment-like keys are just data; humans fill text
        }
        added.append(sid)
    write_yaml(lines_path, lines)
    voices_path = d / "voices.yaml"
    if not voices_path.exists():
        write_yaml(voices_path, {})
    return {"lang": lang, "path": project.relpath(lines_path), "added": added,
            "total": len(lines)}


def line_status(project: Project, lang: str, shot_id: str) -> dict[str, Any]:
    """not_needed / missing / 翻译过期 / ok for one line.

    ``not_needed`` (F-11): the shot has no base dialogue, so there is nothing
    to translate — reported for absent rows AND for legacy hash-of-empty
    scaffold rows, so pre-decision projects read honestly without migration.
    A translation LEFT BEHIND by removed dialogue still reads 翻译过期 via the
    stored-hash mismatch below — that row is real and needs a human look."""
    lines = load_lines(project, lang)
    entry = lines.get(shot_id)
    shot = project.load_shot(shot_id)
    base_text = shot.dialogue.text or ""
    current_hash = base_text_hash(base_text)
    has_base = bool(base_text.strip())
    if entry is None:
        return {"shot": shot_id,
                "state": "missing" if has_base else "not_needed", "text": "",
                "base_hash": current_hash}
    text = str(entry.get("text") or "").strip()
    stored = str(entry.get("base_hash") or "")
    if not text:
        return {"shot": shot_id,
                "state": "missing" if has_base else "not_needed", "text": "",
                "base_hash": current_hash, "stored_base_hash": stored}
    if stored and stored != current_hash:
        return {"shot": shot_id, "state": "翻译过期", "text": text,
                "base_hash": current_hash, "stored_base_hash": stored}
    return {"shot": shot_id, "state": "ok", "text": text,
            "base_hash": current_hash, "stored_base_hash": stored}


def _current_tts_descriptor() -> dict | None:
    """Same best-effort provider descriptor evaluate_voice / synthesis use."""
    try:
        from ..providers.tts import get_tts_provider, voice_provider_descriptor
        return voice_provider_descriptor(get_tts_provider(None))
    except Exception:
        return None


def _locale_voice_state(project: Project, lang: str, shot_id: str,
                        line: dict[str, Any]) -> str:
    """missing / fresh / stale / manual / not_needed for a locale voice take.

    Hash comparison must use the SAME provider descriptor + effective
    locale_voice_id that synthesis recorded — otherwise Edge takes always
    look stale under VOICE_VERSION=2.
    """
    text = str(line.get("text") or "").strip()
    if line.get("state") in ("missing", "not_needed") and not text:
        return "not_needed"
    voices = project.voice_takes(shot_id, lang=lang)
    if not voices:
        return ("missing" if text or line.get("state") not in ("missing", "not_needed")
                else "not_needed")
    media, sc = voices[-1]
    if sc is None:
        return "manual"
    try:
        from ..core.spec import compute_voice_hash
        shot = overlay_shot_for_voice(project, project.load_shot(shot_id), lang)
        bible = project.load_bible()
        take_version = sc.voice_hash_version or 1
        descriptor = _current_tts_descriptor() if take_version >= 2 else None
        current = compute_voice_hash(
            shot, bible, version=take_version, provider=descriptor,
        )
        if sc.voice_hash == current:
            return "fresh"
        return "stale"
    except Exception:
        return "fresh" if sc else "manual"


def _base_timeline_caption_structure(project: Project) -> dict[str, Any]:
    """Structure that shapes locale caption timing: fingerprint, shot order,
    and per-cue windows from the BASE timeline (not locale text)."""
    out: dict[str, Any] = {
        "shot_order": list(project.shot_ids()),
        "compiled_from": None,
        "cues": [],
    }
    try:
        tl = project.load_timeline()
    except Exception:
        tl = None
    if tl is None:
        return out
    out["compiled_from"] = tl.meta.compiled_from or None
    out["cues"] = [
        {
            "shot": c.shot or "",
            "start_ms": c.start_ms,
            "end_ms": c.end_ms,
            "speaker": c.speaker or "",
        }
        for c in (tl.tracks.captions or [])
    ]
    return out


def _locale_lines_content_key(project: Project, lang: str) -> str:
    """Hash for locale caption freshness.

    Includes translation lines + caption rules + voices.yaml AND the base
    timeline fingerprint / shot order / cue timing structure — so reordering
    shots or changing cue windows marks locale captions stale even when
    English text is unchanged.
    """
    try:
        rules = project.load_rules().captions.model_dump()
    except Exception:
        rules = {}
    return hash_value({
        "lines": load_lines(project, lang),
        "captions_rules": rules,
        "voices": load_voices(project, lang),
        "base_timeline": _base_timeline_caption_structure(project),
    })


def _locale_artifact_freshness(project: Project, lang: str) -> dict[str, Any]:
    """Caption/final freshness via content keys when possible.

    - captions: sidecar ``captions.key.json`` vs current full key (lines +
      base timeline structure + rules).
    - final: newest final ``.key.json`` vs recomputed locale content key.
      On recompute failure → ``有问题`` / ``unknown`` with ``reason`` — never
      silently ``ok``.
    """
    from ..media.render import _read_key_sidecar

    cap_dir = project.captions_dir / "locales" / lang
    srt = cap_dir / "captions.srt"
    ass = cap_dir / "captions.ass"
    cap_key_path = cap_dir / "captions.key.json"
    expected_cap = _locale_lines_content_key(project, lang)
    cap_reason: str | None = None
    if not srt.exists():
        cap_fresh = "missing"
        cap_reason = "captions.srt absent"
    elif cap_key_path.exists():
        try:
            stored = json_loads_key(cap_key_path)
            if stored == expected_cap:
                cap_fresh = "ok"
            else:
                cap_fresh = "stale"
                cap_reason = "content key differs (lines/timeline/rules moved)"
        except Exception as exc:
            cap_fresh = "有问题"
            cap_reason = f"captions.key.json unreadable: {' '.join(str(exc).split())[:120]}"
    else:
        # No sidecar: presence-only, labeled honestly as unknown key
        if ass.exists():
            cap_fresh = "unknown"
            cap_reason = "no captions.key.json — presence only, re-export locale captions"
        else:
            cap_fresh = "missing"
            cap_reason = "captions.ass absent"

    final_dir = project.final_dir / "locales" / lang
    finals = list(final_dir.glob("final_v*.mp4")) if final_dir.exists() else []
    newest = None
    if finals:
        import re
        versions = [
            (int(m.group(1)), p)
            for p in finals
            if (m := re.match(r"final_v(\d+)$", p.stem))
        ]
        newest = max(versions, key=lambda t: t[0])[1] if versions else None
    final_fresh = "missing"
    final_reason: str | None = "no locale final" if newest is None else None
    if newest is not None:
        existing = _read_key_sidecar(newest)
        if existing is None:
            final_fresh = "有问题"
            final_reason = "final exists but .key.json missing"
        else:
            try:
                from ..build.locale_build import apply_locale_overlay
                from ..core.hashing import cache_key
                from ..media.render import final_content_key
                tl = project.load_timeline()
                if tl is None:
                    final_fresh = "unknown"
                    final_reason = "no base timeline.json to recompute final key"
                else:
                    over = apply_locale_overlay(project, tl, lang)
                    ass_p = ass if ass.exists() else None
                    base_key = final_content_key(
                        project, over, ass_file=ass_p, target="final",
                    )
                    want = cache_key({"locale": lang, "base_key": base_key})
                    if existing == want:
                        final_fresh = "ok"
                        final_reason = None
                    else:
                        final_fresh = "stale"
                        final_reason = "final content key differs"
            except Exception as exc:
                final_fresh = "有问题"
                final_reason = (
                    f"cannot recompute final key: "
                    f"{' '.join(str(exc).split())[:160]}"
                )

    return {
        "captions": {
            "path": project.relpath(srt) if srt.exists() else None,
            "ass": project.relpath(ass) if ass.exists() else None,
            "freshness": cap_fresh,
            "content_key": expected_cap,
            **({"reason": cap_reason} if cap_reason else {}),
        },
        "final": {
            "path": project.relpath(newest) if newest else None,
            "freshness": final_fresh,
            **({"reason": final_reason} if final_reason else {}),
        },
    }


def json_loads_key(path) -> str:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return str(data.get("content_key") or data.get("final_key") or "")


def write_locale_captions_key(project: Project, lang: str) -> Path:
    """Persist the content key next to locale captions (for status freshness)."""
    d = project.captions_dir / "locales" / lang
    d.mkdir(parents=True, exist_ok=True)
    path = d / "captions.key.json"
    key = _locale_lines_content_key(project, lang)
    atomic_write_text(
        path,
        json.dumps({"content_key": key, "lang": lang}, ensure_ascii=False, indent=2)
        + "\n",
    )
    return path


def locale_status(project: Project, lang: str | None = None) -> dict[str, Any]:
    """Per-locale structure report: lines + per-shot voice + caption/final."""
    langs = [lang] if lang else list_locales(project)
    if lang and lang not in list_locales(project) and not locale_dir(project, lang).exists():
        raise ProjectError(f"locale 不存在: {lang} — manju locale add {lang}")
    out: dict[str, Any] = {"locales": {}}
    for lg in langs:
        if not lg:
            continue
        rows = []
        voice_by: dict[str, str] = {}
        for sid in project.shot_ids():
            ls = line_status(project, lg, sid)
            rows.append(ls)
            voice_by[sid] = _locale_voice_state(project, lg, sid, ls)
        counts = {"ok": 0, "missing": 0, "翻译过期": 0, "not_needed": 0}
        for r in rows:
            counts[r["state"]] = counts.get(r["state"], 0) + 1
        artifacts = _locale_artifact_freshness(project, lg)
        out["locales"][lg] = {
            "lines": rows,
            "counts": counts,
            "voice": voice_by,
            "captions": artifacts["captions"],
            "final": artifacts["final"],
            "voices_yaml": load_voices(project, lg),
        }
    return out


def overlay_shot_for_voice(project: Project, shot, lang: str | None):
    """Return a shot copy whose dialogue.text is the locale overlay when
    present and non-empty. Also folds optional ``locales/<lang>/voices.yaml``
    into ``generation.params.locale_voice_id`` — the **effective** voice id
    for Edge TTS and for ``voice_payload`` hashing (locale beats base bible
    ``voice_id``). Picture fields untouched. lang=None → original.
    """
    if not lang:
        return shot
    lines = load_lines(project, lang)
    entry = lines.get(shot.id) or {}
    text = str(entry.get("text") or "").strip()
    data = shot.model_dump()
    if text:
        data["dialogue"] = {**(data.get("dialogue") or {}), "text": text}
    # voices.yaml: {speaker_id: {voice_id: "en-US-JennyNeural"}}
    # Stash on generation.params so providers that read voice_id from bible
    # can be overridden by resolving through resolve_locale_voice_id.
    voices = load_voices(project, lang)
    speaker = (data.get("dialogue") or {}).get("speaker") or shot.dialogue.speaker
    ventry = voices.get(speaker) if isinstance(voices, dict) else None
    if isinstance(ventry, dict) and ventry.get("voice_id"):
        params = dict((data.get("generation") or {}).get("params") or {})
        params["locale_voice_id"] = str(ventry["voice_id"])
        params["locale_lang"] = lang
        gen = dict(data.get("generation") or {})
        gen["params"] = params
        data["generation"] = gen
    from ..core.models import ShotSpec
    return ShotSpec.model_validate(data)


def resolve_locale_voice_id(project: Project, shot, lang: str | None) -> str | None:
    """Explicit locale voice_id from voices.yaml or generation.params."""
    if not lang:
        return None
    params = getattr(getattr(shot, "generation", None), "params", None) or {}
    if params.get("locale_voice_id"):
        return str(params["locale_voice_id"])
    speaker = shot.dialogue.speaker
    voices = load_voices(project, lang)
    ventry = voices.get(speaker) if isinstance(voices, dict) else None
    if isinstance(ventry, dict) and ventry.get("voice_id"):
        return str(ventry["voice_id"])
    return None


def check_locales(project: Project) -> list[str]:
    """Findings for manju check: unknown shot ids, malformed YAML."""
    findings: list[str] = []
    known = set(project.shot_ids())
    for lang in list_locales(project):
        try:
            lines = load_lines(project, lang)
        except Exception as exc:
            findings.append(f"locales/{lang}/lines.yaml: 无法解析 — {exc}")
            continue
        for sid in lines:
            if sid not in known:
                findings.append(
                    f"locales/{lang}/lines.yaml: 未知镜头 id {sid!r}"
                )
        try:
            load_voices(project, lang)
        except Exception as exc:
            findings.append(f"locales/{lang}/voices.yaml: 无法解析 — {exc}")
    return findings
