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

import re
from pathlib import Path
from typing import Any

from ..core.container import Project, ProjectError
from ..core.hashing import hash_value
from ..core.yamlio import read_yaml, write_yaml

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


def _locale_voice_state(project: Project, lang: str, shot_id: str,
                        line: dict[str, Any]) -> str:
    """missing / fresh / stale / manual / not_needed for a locale voice take."""
    text = str(line.get("text") or "").strip()
    if line.get("state") == "missing" and not text:
        return "not_needed"
    voices = project.voice_takes(shot_id, lang=lang)
    if not voices:
        return "missing" if text or line.get("state") != "missing" else "not_needed"
    media, sc = voices[-1]
    if sc is None:
        return "manual"
    # Compare voice_hash against overlay shot text
    try:
        from ..core.spec import VOICE_VERSION, compute_voice_hash
        shot = overlay_shot_for_voice(project, project.load_shot(shot_id), lang)
        bible = project.load_bible()
        take_version = sc.voice_hash_version or 1
        current = compute_voice_hash(
            shot, bible, version=take_version, provider=None,
        )
        if sc.voice_hash == current:
            return "fresh"
        return "stale"
    except Exception:
        return "fresh" if sc else "manual"


def _locale_artifact_freshness(project: Project, lang: str) -> dict[str, Any]:
    """Caption/final path + presence for a locale."""
    cap_dir = project.captions_dir / "locales" / lang
    srt = cap_dir / "captions.srt"
    ass = cap_dir / "captions.ass"
    final_dir = project.final_dir / "locales" / lang
    finals = sorted(final_dir.glob("final_v*.mp4")) if final_dir.exists() else []
    newest = None
    if finals:
        import re
        versions = [
            (int(m.group(1)), p)
            for p in finals
            if (m := re.match(r"final_v(\d+)$", p.stem))
        ]
        newest = max(versions, key=lambda t: t[0])[1] if versions else None
    key_ok = False
    if newest is not None:
        key_path = newest.with_suffix(".key.json")
        key_ok = key_path.exists()
    return {
        "captions": {
            "path": project.relpath(srt) if srt.exists() else None,
            "ass": project.relpath(ass) if ass.exists() else None,
            "freshness": "ok" if srt.exists() and ass.exists() else "missing",
        },
        "final": {
            "path": project.relpath(newest) if newest else None,
            "freshness": (
                "ok" if newest and key_ok
                else "missing" if newest is None
                else "有问题"
            ),
        },
    }


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
    voice_id onto the speaker bible path via dialogue-side params used by
    Edge ``_voice_for`` (bible voice_id still wins if set; locale override
    is applied as a generation-style hint on the speaker entry when the
    shot's speaker is listed). Picture fields untouched. lang=None → original.
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
