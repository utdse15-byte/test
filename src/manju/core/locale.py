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


def locales_dir(project: Project) -> Path:
    return project.root / "locales"


def locale_dir(project: Project, lang: str) -> Path:
    return locales_dir(project) / lang


def base_text_hash(text: str) -> str:
    return hash_value({"text": text or ""})


def validate_lang(lang: str) -> str:
    lang = (lang or "").strip()
    if not lang or not _LANG_RE.match(lang):
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


def add_locale(project: Project, lang: str) -> dict[str, Any]:
    """Scaffold locales/<lang>/lines.yaml with every shot id + empty text +
    current base_hash."""
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
    """missing / 翻译过期 / ok for one line."""
    lines = load_lines(project, lang)
    entry = lines.get(shot_id)
    shot = project.load_shot(shot_id)
    base_text = shot.dialogue.text or ""
    current_hash = base_text_hash(base_text)
    if entry is None:
        return {"shot": shot_id, "state": "missing", "text": "",
                "base_hash": current_hash}
    text = str(entry.get("text") or "").strip()
    stored = str(entry.get("base_hash") or "")
    if not text:
        return {"shot": shot_id, "state": "missing", "text": "",
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
    if line.get("state") == "missing" and not text:
        return "not_needed"
    voices = project.voice_takes(shot_id, lang=lang)
    if not voices:
        return "missing" if text or line.get("state") != "missing" else "not_needed"
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


def _locale_lines_content_key(project: Project, lang: str) -> str:
    """Hash of lines.yaml + caption rules — moves when translation or style moves."""
    from ..core.hashing import hash_value
    try:
        rules = project.load_rules().captions.model_dump()
    except Exception:
        rules = {}
    return hash_value({
        "lines": load_lines(project, lang),
        "captions_rules": rules,
        "voices": load_voices(project, lang),
    })


def _locale_artifact_freshness(project: Project, lang: str) -> dict[str, Any]:
    """Caption/final freshness via content keys when possible, else presence.

    - captions: compare sidecar ``captions.key.json`` (written at locale
      caption export) to current lines/rules/voices hash.
    - final: compare newest final's ``.key.json`` to a recomputed locale
      content key when a base timeline exists; else presence only.
    """
    from ..core.hashing import hash_value
    from ..media.render import _read_key_sidecar

    cap_dir = project.captions_dir / "locales" / lang
    srt = cap_dir / "captions.srt"
    ass = cap_dir / "captions.ass"
    cap_key_path = cap_dir / "captions.key.json"
    expected_cap = _locale_lines_content_key(project, lang)
    if not srt.exists():
        cap_fresh = "missing"
    elif cap_key_path.exists():
        try:
            stored = json_loads_key(cap_key_path)
            cap_fresh = "ok" if stored == expected_cap else "stale"
        except Exception:
            cap_fresh = "ok" if ass.exists() else "有问题"
    else:
        # No sidecar yet (pre-key exports): presence only, labeled honestly
        cap_fresh = "ok" if ass.exists() else "missing"

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
    if newest is not None:
        existing = _read_key_sidecar(newest)
        if existing is None:
            final_fresh = "有问题"
        else:
            # Recompute locale final key when possible
            try:
                from ..build.locale_build import apply_locale_overlay
                from ..core.hashing import cache_key
                from ..media.render import final_content_key
                tl = project.load_timeline()
                if tl is not None:
                    over = apply_locale_overlay(project, tl, lang)
                    ass_p = ass if ass.exists() else None
                    base_key = final_content_key(
                        project, over, ass_file=ass_p, target="final",
                    )
                    want = cache_key({"locale": lang, "base_key": base_key})
                    final_fresh = "ok" if existing == want else "stale"
                else:
                    final_fresh = "ok"  # presence + key file, no timeline to recompute
            except Exception:
                final_fresh = "ok"  # degrade to presence

    return {
        "captions": {
            "path": project.relpath(srt) if srt.exists() else None,
            "ass": project.relpath(ass) if ass.exists() else None,
            "freshness": cap_fresh,
            "content_key": expected_cap,
        },
        "final": {
            "path": project.relpath(newest) if newest else None,
            "freshness": final_fresh,
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
        counts = {"ok": 0, "missing": 0, "翻译过期": 0}
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
