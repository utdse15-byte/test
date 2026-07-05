"""`manju check` (§4, §5) — the agent's safety net.

Schema validation + referential integrity + hard lock verification + secret
scan. Every AI edit must be followed by a check; build refuses to run when
check fails.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from .container import Project
from .locks import verify_locks

# Common cloud-key shapes (§8.2: keys must never enter the project directory)
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"][A-Za-z0-9/+_\-]{16,}['\"]"),
]

SCAN_SUFFIXES = {".yaml", ".yml", ".json", ".md", ".txt", ".srt", ".ass"}


@dataclass
class CheckReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}


def _fmt_validation_error(label: str, exc: ValidationError) -> str:
    issues = "; ".join(
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
    )
    return f"{label}: schema invalid — {issues}"


def run_check(project: Project) -> CheckReport:
    report = CheckReport()

    # ---- project config
    try:
        project.load_config()
    except ValidationError as exc:
        report.errors.append(_fmt_validation_error("project.yaml", exc))
    except Exception as exc:  # unreadable file etc.
        report.errors.append(f"project.yaml: {exc}")

    bible = project.load_bible()
    index = project.load_index()
    shot_ids = project.shot_ids()

    # ---- index integrity
    for sid in index.order:
        if not project.shot_path(sid).exists():
            report.errors.append(f"shots/index.yaml: order references missing shot '{sid}'")
    for sid in shot_ids:
        if sid not in index.order and project.shot_path(sid).exists():
            report.warnings.append(f"shot '{sid}' exists on disk but is not in index.yaml order")

    # ---- shots: schema, references, takes, locks
    for sid in shot_ids:
        if not project.shot_path(sid).exists():
            continue
        label = f"shots/{sid}.yaml"
        try:
            raw = project.load_shot_raw(sid)
            shot = project.load_shot(sid)
        except ValidationError as exc:
            report.errors.append(_fmt_validation_error(label, exc))
            continue
        except Exception as exc:
            report.errors.append(f"{label}: {exc}")
            continue

        if shot.id != sid:
            report.errors.append(f"{label}: id field '{shot.id}' does not match filename")
        if shot.scene and shot.scene not in bible:
            report.errors.append(f"{label}: scene '{shot.scene}' not found in bible")
        for character in shot.characters:
            if character not in bible:
                report.errors.append(f"{label}: character '{character}' not found in bible")

        if shot.status.selected_take:
            take = project.get_take(sid, shot.status.selected_take)
            if take is None:
                report.errors.append(
                    f"{label}: selected_take '{shot.status.selected_take}' does not exist"
                )
            elif take.media_path is None:
                report.errors.append(
                    f"{label}: selected_take '{shot.status.selected_take}' has no media file"
                )

        for violation in verify_locks(raw, shot.locked, label):
            report.errors.append(str(violation))

    # ---- bible locks
    for fname in ("characters", "scenes", "props", "style"):
        path = project.root / "bible" / f"{fname}.yaml"
        if not path.exists():
            continue
        from .yamlio import read_yaml

        data = read_yaml(path) or {}
        if not isinstance(data, dict):
            report.errors.append(f"bible/{fname}.yaml: must be a mapping")
            continue
        for key, entry in data.items():
            if isinstance(entry, dict) and entry.get("locked"):
                locked = entry["locked"]
                if isinstance(locked, list):
                    locked = {str(p): "" for p in locked}
                for violation in verify_locks(entry, locked, f"bible/{fname}.yaml:{key}"):
                    report.errors.append(str(violation))

    # ---- toolbelt write-back rule (§2.5): media under media/gen must be
    # registered takes (sidecar present). An agent that processed media with
    # toolbelt tools and dropped the result in place bypasses the hash
    # discipline — flag it and point at the correct on-ramp.
    from .container import MEDIA_EXTS

    for take_dir in sorted(p for p in project.gen_dir.glob("*") if p.is_dir()):
        for media in sorted(take_dir.iterdir()):
            if not media.is_file() or media.suffix.lower() not in MEDIA_EXTS:
                continue
            if media.stem.startswith("voice"):
                continue  # voice takes are discovered by name, not sidecar (§6)
            if not (take_dir / f"{media.stem}.yaml").exists():
                report.warnings.append(
                    f"media/gen/{take_dir.name}/{media.name}: unregistered product — "
                    f"register it via `manju select {take_dir.name} --file <path>` "
                    "(toolbelt write-back rule, §2.5)"
                )

    # ---- secret scan (keys never enter the project directory)
    for path in project.root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        rel = path.relative_to(project.root).as_posix()
        if rel.startswith((".git/", ".manju/", "media/", "renders/")):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                report.errors.append(
                    f"{rel}: looks like an API key/secret — keys must live in env vars, "
                    "never in the project (§8.2)"
                )
                break

    return report
