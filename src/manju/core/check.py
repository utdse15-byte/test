"""`manju check` (§4, §5) — the agent's safety net.

Schema validation + referential integrity + hard lock verification + secret
scan. Every AI edit must be followed by a check; build refuses to run when
check fails.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml
from pydantic import ValidationError

from .container import BIBLE_FILES, Project, ProjectError
from .locks import verify_locks
from .models import LookSpec, ShotIndex
from .yamlio import read_yaml


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


def _prop_refs(shot_raw: dict[str, Any]) -> list[str]:
    """Prop ids referenced from continuity.locks (``prop:<id>`` entries, §4.1
    grammar — mirrors core/appearances.py's reader, kept local so check.py
    does not depend on a report module's private helper)."""
    continuity = shot_raw.get("continuity")
    locks = continuity.get("locks") if isinstance(continuity, dict) else None
    refs: list[str] = []
    for entry in locks or []:
        if isinstance(entry, str) and entry.startswith("prop:"):
            pid = entry.split(":", 1)[1].strip()
            if pid and pid not in refs:
                refs.append(pid)
    return refs


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
    # rules/packaging are kept (not just probed) — #18 below validates the
    # asset paths they carry, and that needs the parsed objects.
    rules = None
    try:
        rules = project.load_rules()
    except ValidationError as exc:
        report.errors.append(_fmt_validation_error("timeline/rules.yaml", exc))
    except yaml.YAMLError as exc:
        report.errors.append(
            f"timeline/rules.yaml: YAML 解析失败 — {_one_line(exc)}(建议:检查缩进/冒号/引号)"
        )
    except Exception as exc:
        report.errors.append(f"timeline/rules.yaml: {exc}")
    packaging = None
    try:
        packaging = project.load_packaging()
    except ValidationError as exc:
        report.errors.append(_fmt_validation_error("timeline/packaging.yaml", exc))
    except yaml.YAMLError as exc:
        report.errors.append(
            f"timeline/packaging.yaml: YAML 解析失败 — {_one_line(exc)}(建议:检查缩进/冒号/引号)"
        )
    except Exception as exc:
        report.errors.append(f"timeline/packaging.yaml: {exc}")

    # ---- #18: timeline/packaging asset references (BGM/SFX/ambient/
    # transition-sound/logo/watermark) — render dereferences these paths
    # unguarded, so a missing/wrong-type/out-of-project asset must surface
    # HERE, not as a render-time ffmpeg failure. Missing -> error when the
    # carrying feature is actually enabled/active, else -> info-level
    # warning (the path is inert today but worth flagging early).
    def _check_asset(rel: str | None, feature: str, *, enabled: bool) -> None:
        if not rel:
            return
        bucket = report.errors if enabled else report.warnings
        try:
            resolved = project.resolve(rel)
        except ProjectError:
            bucket.append(f"{feature}: 素材路径越界项目目录 — {rel!r}(§3 项目边界)")
            return
        if not resolved.exists():
            bucket.append(f"{feature}: 素材文件不存在 — {rel!r}")
        elif not resolved.is_file():
            bucket.append(f"{feature}: 素材路径不是文件 — {rel!r}")

    if rules is not None:
        _check_asset(rules.music.source, "timeline/rules.yaml: music.source(BGM)", enabled=True)
        for i, sfx in enumerate(rules.audio.sfx):
            _check_asset(sfx.source, f"timeline/rules.yaml: audio.sfx[{i}].source(SFX)",
                        enabled=True)
        _check_asset(rules.audio.transition_sound,
                    "timeline/rules.yaml: audio.transition_sound", enabled=True)
        _check_asset(rules.audio.ambient.source,
                    "timeline/rules.yaml: audio.ambient.source", enabled=True)
    if packaging is not None:
        _check_asset(packaging.logo.image, "timeline/packaging.yaml: logo.image",
                    enabled=packaging.logo.enabled)
        _check_asset(packaging.watermark.image, "timeline/packaging.yaml: watermark.image",
                    enabled=packaging.watermark.enabled)

    # ---- #26: timeline/routing.yaml enters the hard check gate — a bad
    # routing file (unknown strategy/tier/provider id, shape error) today
    # only surfaces as an exception at real generation time, when money may
    # already be about to move. Optional file: no routing.yaml anywhere is
    # always clean (the registry then keeps the byte-identical §8.4 path).
    # The per-shot dry-run below (explicit generation.provider refs) reuses
    # the same import; both degrade to "skip routing checks" on import
    # failure rather than ever crashing `manju check` itself.
    _route_resolve = None
    _RoutingError: type[Exception] = Exception
    try:
        from ..providers.routing import RoutingError as _RoutingError
        from ..providers.routing import resolve as _route_resolve
        from ..providers.routing import validate_config as _route_validate

        for problem in _route_validate(project):
            report.errors.append(problem)
    except Exception as exc:  # never let routing validation crash check itself
        report.errors.append(f"timeline/routing.yaml: 校验失败 — {' '.join(str(exc).split())}")

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
            report.warnings.append(
                f"shot '{sid}' exists on disk but is not in index.yaml order — "
                f"index 顺序是唯一的顺序权威:build/timeline 会 EXCLUDE(排除)'{sid}',"
                f"该镜头不会进入编译产物,除非把它加入 shots/index.yaml order,"
                f"或运行 `manju build --include-unindexed` 显式包含"
            )

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

        # ---- #57: continuity.locks `prop:<id>` refs — free-text list the
        # schema does not validate, but a structured `prop:<id>` entry is a
        # real reference (§4.1 grammar) and deserves the SAME hard door as
        # scene/characters above: a prop id absent from bible/props.yaml is a
        # dangling continuity reference, not a style choice.
        for prop_id in _prop_refs(raw):
            if prop_id not in bible:
                report.errors.append(
                    f"{label}: continuity.locks 引用的道具 'prop:{prop_id}' 未在 bible 中找到"
                )

        # ---- #26: per-shot routing dry-run for an EXPLICIT provider ref —
        # runs the SAME resolution `manju build` would, before any money
        # moves. A genuinely broken routing.yaml (unknown active strategy
        # etc.) surfaces here too. NOTE: naming a provider with no manifest
        # at all is deliberately NOT flagged — §8.4 graceful degradation
        # (generate_with_fallback) already tolerates that and falls through
        # the chain to caption_card by design (test_failures.py pins it);
        # naming a DISABLED provider explicitly IS already a hard build-time
        # error elsewhere (providers/registry.py) — check now catches that
        # same case early instead of only at real generation time.
        if shot.generation.provider and _route_resolve is not None:
            try:
                res = _route_resolve(project, shot)
            except _RoutingError as exc:
                report.errors.append(
                    f"{label}: routing 解析 generation.provider="
                    f"{shot.generation.provider!r} 失败 — {exc}"
                )
            else:
                skip = next(
                    (s for s in res.skipped if s.get("provider") == shot.generation.provider),
                    None,
                )
                if skip is not None and "disabled" in str(skip.get("reason", "")):
                    report.errors.append(
                        f"{label}: generation.provider={shot.generation.provider!r} "
                        f"已被禁用 — {skip.get('reason')}"
                    )

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
    # bible-file-by-file (unmerged) read, reused below for both the lock walk
    # AND the cross-file duplicate-id check (#1) AND the style.yaml look
    # shape check (#34) — one parse pass per file, three findings from it.
    bible_by_file: dict[str, dict[str, Any]] = {}
    for fname in BIBLE_FILES:
        path = project.root / "bible" / f"{fname}.yaml"
        if not path.exists():
            continue
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
        bible_by_file[fname] = data
        for key, entry in data.items():
            if isinstance(entry, dict) and entry.get("locked"):
                locked = entry["locked"]
                if isinstance(locked, list):
                    locked = {str(p): "" for p in locked}
                for violation in verify_locks(entry, locked, f"bible/{fname}.yaml:{key}"):
                    report.errors.append(str(violation))

    # ---- #1: bible cross-file duplicate ids silently overwrite each other
    # (Project.load_bible merges BIBLE_FILES in order, last file wins). Name
    # BOTH files and the id — the ambiguity is exactly which entity a
    # reference resolves to, so both candidates matter to the fix.
    _id_owner: dict[str, str] = {}
    for fname in BIBLE_FILES:
        for key in bible_by_file.get(fname, {}):
            first = _id_owner.get(key)
            if first is not None and first != fname:
                report.errors.append(
                    f"bible: id '{key}' 在 bible/{first}.yaml 和 bible/{fname}.yaml "
                    f"中重复定义 — 后加载的 bible/{fname}.yaml 会静默覆盖 bible/{first}.yaml"
                    "(每个 id 必须全局唯一;给其中一个改名或删除重复项)"
                )
            else:
                _id_owner.setdefault(key, fname)

    # ---- #34: bible/style.yaml's `look:` mapping is read by render.load_look,
    # which degrades a missing/malformed look to "no look" SILENTLY (a build
    # never crashes over a style typo). check now makes that degradation
    # VISIBLE — a warning, not an error, because the render-degrades-gracefully
    # behaviour itself is intentional and must stay.
    style_data = bible_by_file.get("style")
    if isinstance(style_data, dict) and "look" in style_data:
        look_raw = style_data["look"]
        if not isinstance(look_raw, dict):
            report.warnings.append(
                "bible/style.yaml: look 配置不是映射(mapping)— 渲染会静默降级为无 look"
                "(§ round-T);检查 look: 缩进"
            )
        else:
            try:
                LookSpec.model_validate(look_raw)
            except ValidationError as exc:
                issues = "; ".join(
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                    for e in exc.errors()[:5]
                )
                report.warnings.append(
                    f"bible/style.yaml: look 配置不合法({issues})— "
                    "渲染会静默降级为无 look(§ round-T);修正后重新 check"
                )

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
