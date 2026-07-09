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


def locale_status(project: Project, lang: str | None = None) -> dict[str, Any]:
    langs = [lang] if lang else list_locales(project)
    if lang and lang not in list_locales(project) and not locale_dir(project, lang).exists():
        raise ProjectError(f"locale 不存在: {lang} — manju locale add {lang}")
    out: dict[str, Any] = {"locales": {}}
    for lg in langs:
        if not lg:
            continue
        rows = []
        for sid in project.shot_ids():
            rows.append(line_status(project, lg, sid))
        counts = {"ok": 0, "missing": 0, "翻译过期": 0}
        for r in rows:
            counts[r["state"]] = counts.get(r["state"], 0) + 1
        out["locales"][lg] = {"lines": rows, "counts": counts}
    return out


def overlay_shot_for_voice(project: Project, shot, lang: str | None):
    """Return a shot copy whose dialogue.text is the locale overlay when
    present and non-empty. Picture fields untouched. lang=None → original."""
    if not lang:
        return shot
    lines = load_lines(project, lang)
    entry = lines.get(shot.id) or {}
    text = str(entry.get("text") or "").strip()
    if not text:
        return shot
    data = shot.model_dump()
    data["dialogue"] = {**(data.get("dialogue") or {}), "text": text}
    from ..core.models import ShotSpec
    return ShotSpec.model_validate(data)


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
