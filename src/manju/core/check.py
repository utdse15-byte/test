"""`manju check` (§4, §5) — the agent's safety net.

Schema validation + referential integrity + hard lock verification + secret
scan. Every AI edit must be followed by a check; build refuses to run when
check fails.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml
from pydantic import ValidationError

from .container import BIBLE_FILES, Project
from .locks import verify_locks
from .models import ShotIndex


def _one_line(exc: BaseException) -> str:
    """Collapse a multi-line parser error into one diagnostic line (FIX-D)."""
    return " ".join(str(exc).split())

# Common cloud-key shapes (§8.2: keys must never enter the project directory).
# FIX-C red-team coverage: hyphenated sk-proj-…, GitHub gh?_ tokens, Slack
# xox?-, long Bearer tokens, and UNQUOTED assignment forms (the review's
# concrete miss was `api_key=sk-proj-…` without quotes).
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),                 # OpenAI-style, incl. sk-proj-
    re.compile(r"AKIA[0-9A-Z]{16}"),                       # AWS access key id
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),                 # Google API key
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),             # GitHub ghp_/gho_/ghu_/ghs_/ghr_
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),          # Slack tokens
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{25,}"),      # long bearer tokens
    # assignment forms, quoted OR unquoted; the value must look token-like
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token)"
               r"\s*[:=]\s*['\"]?[A-Za-z0-9/+_.\-]{16,}"),
]

SCAN_SUFFIXES = {".yaml", ".yml", ".json", ".md", ".txt", ".srt", ".ass"}
# goal item 21: media/ used to be skipped wholesale, which also hid the text
# sidecars/notes/prompts real projects keep under media/refs, media/imports,
# media/gen. Bound per-file scan size so pulling media/ back into scope can
# never make `manju check` slow — binary/video/audio files are excluded by
# SUFFIX already, so this only bounds oddly large text files.
MAX_SCAN_BYTES = 2_000_000  # 2 MB


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

    # ---- timeline rules + packaging: truth files the build dereferences
    # unguarded, so a malformed one must surface HERE as a finding (FIX-D),
    # never later as a build/package traceback (round-N review finding).
    try:
        project.load_rules()
    except ValidationError as exc:
        report.errors.append(_fmt_validation_error("timeline/rules.yaml", exc))
    except yaml.YAMLError as exc:
        report.errors.append(
            f"timeline/rules.yaml: YAML 解析失败 — {_one_line(exc)}(建议:检查缩进/冒号/引号)"
        )
    except Exception as exc:
        report.errors.append(f"timeline/rules.yaml: {exc}")
    try:
        project.load_packaging()
    except ValidationError as exc:
        report.errors.append(_fmt_validation_error("timeline/packaging.yaml", exc))
    except yaml.YAMLError as exc:
        report.errors.append(
            f"timeline/packaging.yaml: YAML 解析失败 — {_one_line(exc)}(建议:检查缩进/冒号/引号)"
        )
    except Exception as exc:
        report.errors.append(f"timeline/packaging.yaml: {exc}")

    # FIX-D: a broken truth file is a CHECK FINDING, never a traceback — the
    # diagnostic names the file (yaml embeds it via the stream name) and says
    # what to do next.
    try:
        bible = project.load_bible()
    except yaml.YAMLError as exc:
        report.errors.append(
            f"bible: YAML 解析失败 — {_one_line(exc)}(建议:检查缩进/冒号/引号)"
        )
        bible = {}
    try:
        index = project.load_index()
        shot_ids = project.shot_ids()
    except (yaml.YAMLError, ValidationError) as exc:
        report.errors.append(
            f"shots/index.yaml: 解析失败 — {_one_line(exc)}(建议:检查 order 列表格式)"
        )
        index = ShotIndex()
        shot_ids = []

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
    for fname in BIBLE_FILES:
        path = project.root / "bible" / f"{fname}.yaml"
        if not path.exists():
            continue
        from .yamlio import read_yaml

        try:
            data = read_yaml(path) or {}
        except yaml.YAMLError as exc:  # FIX-D: finding, not traceback
            report.errors.append(
                f"bible/{fname}.yaml: YAML 解析失败 — {_one_line(exc)}"
                "(建议:检查缩进/冒号/引号)"
            )
            continue
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
        # .git/.manju are VCS/runtime internals, never project truth text;
        # renders/ is compiled binary output. media/ is DELIBERATELY NOT
        # skipped here (goal item 21) — media/refs, media/imports, media/gen
        # commonly hold text sidecars/notes/prompts, and binary media is
        # already excluded by SCAN_SUFFIXES above.
        if rel.startswith((".git/", ".manju/", "renders/")):
            continue
        try:
            if path.stat().st_size > MAX_SCAN_BYTES:
                continue
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
