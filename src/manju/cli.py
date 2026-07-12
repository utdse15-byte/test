"""manju CLI (§11). Every read command supports --json — the AI collaboration
surface is files + this CLI (§2); MCP is a thin wrapper over the same core.

Hard rules enforced here (§5, §10): `unlock` only works on an interactive
terminal with confirmation and is never exposed over MCP; nothing under
media/imports is ever deleted; renders/final only grows.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import typer

from .core.check import run_check
from .core.container import Project, ProjectError
from .core.events import append_event, tail_events
from .core.hashing import HASH_PREFIX, hash_file
from .core.locks import seal_lock

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Manju One — a build system for video. 一键出片:manju build")

ACTOR = os.environ.get("MANJU_ACTOR", "human")

# round X agent XE: touch ~/.manju/recents.json at most ONCE per process — a
# cheap module-level flag, not a config knob (guards against recursion/perf:
# some commands resolve the project more than once per invocation). Each real
# `manju ...` run is a fresh process, so this is naturally "once per
# invocation"; in-process test harnesses that invoke the Typer app repeatedly
# reset it explicitly (`monkeypatch.setattr(cli, "_RECENTS_TOUCHED", False)`).
_RECENTS_TOUCHED = False


def _touch_recents_once(project: Project) -> None:
    global _RECENTS_TOUCHED
    if _RECENTS_TOUCHED:
        return
    _RECENTS_TOUCHED = True
    try:
        from .core.recents import touch_recent

        touch_recent(project)
    except Exception:
        pass  # recents is a convenience shelf, never load-bearing (§3)


def _project(path: Optional[Path] = None) -> Project:
    try:
        project = Project.find(path or Path.cwd())
    except ProjectError as exc:
        # Route through _fail so --json callers get the structured {"error": …}
        # shape on this (the #1 first-run) error path too. code is stable.
        _fail(str(exc), code="no_project")
        raise  # unreachable (_fail raises), keeps the type checker happy
    _touch_recents_once(project)
    return project


def _emit(data, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _fail(message: str, *, code: str = "error") -> None:
    """Abort with a non-zero exit and a human error message.

    When the running command was invoked with ``--json`` (every such command
    binds it to a parameter named ``as_json``), emit the SAME error as a stable
    ``{"error": …, "code": …}`` object on stdout instead of colored prose — so an
    agent parsing ``--json`` output always gets structured JSON, whether the
    command succeeded or failed (RFC 7807-style; §3e). ``code`` is a stable
    machine token an agent can branch on; ``message`` stays the human line. The
    caller frames are walked for the ``as_json`` local so no call site has to
    thread it through.
    """
    # Discover whether the running command was invoked with --json. Every such
    # command (and the helpers they delegate to) binds it to a local named
    # ``as_json``; Typer does not push a Click context we can peek, so walk the
    # caller frames and take the nearest ``as_json`` — robust and call-site-free.
    as_json = False
    try:
        frame = sys._getframe(1)
        depth = 0
        while frame is not None and depth < 30:
            if "as_json" in frame.f_locals:
                as_json = bool(frame.f_locals["as_json"])
                break
            frame = frame.f_back
            depth += 1
    except Exception:
        as_json = False
    # Round W (review #82): the frame walk is an implementation detail a
    # refactor could break; the argv fallback keeps the JSON error contract
    # honest even then (a command invoked with --json NEVER gets colored prose).
    if not as_json and "--json" in sys.argv[1:]:
        as_json = True
    if as_json:
        typer.echo(json.dumps({"error": message, "code": code}, ensure_ascii=False))
    else:
        typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _record_failure(project, step: str, subject: str, cause: str, *,
                    evidence: str = "", hint: str = "", log_path: Optional[str] = None) -> None:
    """Record a CLI-path failure (goal 10). Best-effort: never blocks the exit."""
    try:
        from .core.failures import Failure, record_failure

        record_failure(project, Failure(step=step, subject=subject, cause=cause,
                                        evidence=evidence, hint=hint, log_path=log_path,
                                        actor=ACTOR))
    except Exception:
        pass


def _interactive() -> bool:
    """Single choke point for the interactive-terminal-only gate (§5):
    unlock and gc --hard refuse to run without a human at a tty."""
    return sys.stdin.isatty()


@contextmanager
def _write_lock(project: Project):
    """The project write lock (§9 round W) for mutating CLI commands that do
    NOT already route through build/graph.py's own ``build_lock`` (build,
    redo, voice, qc already acquire it inside the engine call). select,
    import, gc, lock/unlock, rollback, snapshot and the explicit repair ops
    take it here so two processes never interleave writes to the same
    project. Read commands (status/check/explain/...) stay lock-free.

    BuildLock never blocks/waits — ``acquire()`` either succeeds immediately
    or raises :class:`BuildLocked` immediately (a stale lock is stolen once,
    then it is the same immediate success/fail) — so there is no hang to
    time out; contention is always a clear, fast, one-line error naming the
    holder (pid/actor/started), never a wedged terminal."""
    from .runtime.buildlock import BuildLock, BuildLocked

    lock = BuildLock(project.root, actor=ACTOR)
    try:
        lock.acquire()
    except BuildLocked as exc:
        holder = exc.holder
        _fail(
            f"项目正被占用 (project busy): pid={holder.get('pid', '?')} "
            f"actor={holder.get('actor', '?')} started={holder.get('started', '?')} "
            "— 另一个 manju 进程正在写这个项目,请稍后重试;若确认没有进程在跑,"
            f"删除 {exc.lock_path} 后重试。",
            code="build_locked",
        )
    try:
        yield lock
    finally:
        lock.release()


# --------------------------------------------------------------------- new


@app.command()
def new(
    name: str,
    vertical: bool = typer.Option(True, "--vertical/--horizontal"),
    path: Optional[Path] = typer.Option(None, help="parent directory (default: cwd)"),
    preset: Optional[str] = typer.Option(
        None, "--preset", help="pre-fill from a preset kit (see `manju presets`)"
    ),
    shots: int = typer.Option(0, "--shots", help="scaffold N skeleton shots (S001…)"),
    check_hook: bool = typer.Option(
        False, "--check-hook",
        help="install a git pre-commit hook running manju check",
    ),
):
    """Create a <name>.manju project directory (§3).

    With --preset, the kit pre-fills project.yaml / rules.yaml / packaging.yaml
    and the story scaffolds, then stops touching the project — everything it
    wrote is plain, hand-editable text (P3). No --preset ⇒ the generic scaffold.

    --shots N drops N commented skeleton shots that pass check out of the box
    (骨架待填,引擎从不代写内容); --check-hook installs an opt-in pre-commit
    gate so broken truth cannot enter history (§1-②).
    """
    dest = (path or Path.cwd()) / name
    spec = None
    if preset is not None:
        from .presets import PresetError, load_preset

        try:
            spec = load_preset(preset)
        except PresetError as exc:
            _fail(str(exc))

    project = Project.create(dest, name=name, vertical=vertical)
    if spec is not None:
        from .presets import apply_preset

        apply_preset(project, spec)
        append_event(project.root, ACTOR, "new", {"name": name, "preset": spec.name})
        cfg = project.load_config()
        typer.secho(
            f"created {project.root}  [preset: {spec.name} · {cfg.width}x{cfg.height}]",
            fg=typer.colors.GREEN,
        )
    else:
        append_event(project.root, ACTOR, "new", {"name": name})
        typer.secho(f"created {project.root}", fg=typer.colors.GREEN)
    if shots > 0:
        from .core.container import scaffold_shots

        created = scaffold_shots(project, shots)
        typer.echo(f"  scaffolded {len(created)} shots: {', '.join(created)} "
                   "(骨架待填,引擎从不代写内容)")
    if check_hook:
        from .core.gitops import install_check_hook

        hook = install_check_hook(project.root)
        if hook:
            typer.echo(f"  pre-commit hook: {hook} (坏真相进不了历史,§1-②)")
        else:
            typer.secho("  ⚠ check hook not installed (no repo, or a foreign "
                        "pre-commit hook exists)", fg=typer.colors.YELLOW)


# ----------------------------------------------------------------- presets


@app.command()
def presets(as_json: bool = typer.Option(False, "--json")):
    """List the preset kits available to `manju new --preset` (P3)."""
    from .presets import display_width, list_presets, pad

    specs = list_presets()
    if as_json:
        _emit([s.to_public_dict() for s in specs], True)
        return
    # Width-aware columns: CJK titles are double-width, so pad by display
    # columns (pad/display_width), not code points, or the table drifts.
    name_w = max(display_width(s.name) for s in specs)
    title_w = max(display_width(s.title) for s in specs)
    typer.secho(f"{pad('NAME', name_w)}  {pad('TITLE', title_w)}  ASPECT  DESCRIPTION",
                fg=typer.colors.CYAN)
    for s in specs:
        typer.echo(f"{pad(s.name, name_w)}  {pad(s.title, title_w)}  "
                   f"{pad(s.aspect(), 6)}  {s.description}")
    typer.secho("用法 / usage: manju new <名字> --preset <name>", fg=typer.colors.BRIGHT_BLACK)


# ------------------------------------------------------------------ create
# The creation funnel (round V, goal item 2): 立意→梗概→节拍→剧本→分镜→生成计划
# →生成 as staged data. `manju create` (no arg) is the funnel checklist; `manju
# create <stage>` scaffolds a stage template. The engine scaffolds, never authors
# (§2) — the templates collapse to zero content, so writing one does not complete
# the stage.

_FUNNEL_MARK = {"done": "✓", "current": "▶", "todo": "○"}
_FUNNEL_COLOR = {"done": typer.colors.GREEN, "current": typer.colors.CYAN,
                 "todo": typer.colors.BRIGHT_BLACK}


@app.command()
def create(
    stage: Optional[str] = typer.Argument(
        None, help="brief|synopsis|beats — scaffold that stage's template; "
                   "omit to print the funnel checklist"),
    force: bool = typer.Option(False, "--force", help="overwrite an existing file"),
    as_json: bool = typer.Option(False, "--json"),
):
    """创作漏斗 / creation funnel (goal item 2).

    `manju create` (无参数) 打印七阶段清单(立意→梗概→节拍→剧本→分镜→生成计划→生成)
    并高亮当前阶段与下一步;`manju create <stage>` 为 brief/synopsis/beats 写模板
    (已存在则拒绝覆盖,除非 --force)。剧本用 `manju new` 已脚手架的 story/script.md。
    """
    from .build.funnel import FunnelError, funnel_status, scaffold_stage

    project = _project()
    if stage is None:
        info = funnel_status(project)
        if as_json:
            _emit(info, True)
            return
        typer.secho(f"创作漏斗 / creation funnel  ({info['done']}/{info['total']} 完成)",
                    fg=typer.colors.CYAN, bold=True)
        for s in info["stages"]:
            mark = _FUNNEL_MARK.get(s["state"], "·")
            typer.secho(f"  {mark} {s['cn']}({s['id']})  {s['evidence']}",
                        fg=_FUNNEL_COLOR.get(s["state"]))
        cur = info.get("current")
        if cur is None:
            typer.secho("下一步  全部完成 ✅ — 可 manju build 出片", fg=typer.colors.GREEN)
        else:
            entry = next(s for s in info["stages"] if s["id"] == cur)
            typer.secho(f"下一步  【{entry['cn']}】{entry['next_action']}", fg=typer.colors.CYAN)
        return

    try:
        result = scaffold_stage(project, stage, force=force, actor=ACTOR)
    except FunnelError as exc:
        _fail(str(exc))
    if as_json:
        _emit(result, True)
        return
    typer.secho(f"已生成模板 {result['path']} — 编辑它填入内容(引擎从不代写,§2)",
                fg=typer.colors.GREEN)
    typer.secho("查看进度:manju create   ·   写作参考:manju skills show creation-funnel",
                fg=typer.colors.BRIGHT_BLACK)


# ------------------------------------------------------------------ status


@app.command()
def status(as_json: bool = typer.Option(False, "--json")):
    """Takeover entry point: phase, gaps, spend, next step (§10)."""
    from .build.status import project_status
    from .core.failures import failures_since_last_build

    project = _project()
    info = project_status(project)
    # goal 10: surface failures since the last GREEN build at the takeover entry
    # point — one number here, the reasons one command away (`manju failures`).
    recent_failures = failures_since_last_build(project)
    info["failures_since_build"] = len(recent_failures)
    if as_json:
        _emit(info, True)
        return
    typer.echo(f"项目  {info['project']}  {info['resolution']}  mode={info['mode']}")
    typer.echo(f"镜头  共 {info['shots_total']}: " + ", ".join(
        f"{k}={len(v)}" for k, v in info["shots_by_state"].items()) if info["shots_by_state"] else "镜头  0")
    if info.get("voice_by_state"):
        typer.echo("配音  " + ", ".join(
            f"{k}={len(v)}" for k, v in info["voice_by_state"].items()))
    tl = info["timeline"]
    typer.echo(f"时间线  {'✓ ' + str(tl['duration_ms']) + 'ms (' + str(tl['mode']) + ')' if tl['exists'] else '—'}")
    typer.echo(f"成片  {info['latest_final'] or '—'}")
    if info["qc"]:
        typer.echo(f"QC   errors={info['qc'].get('errors')} warnings={info['qc'].get('warnings')}")
    # goal 79: never print a currency-mislabeled number — when spend spans
    # more than one currency, show every currency's own total (12 CNY + 2 USD)
    # instead of a single merged figure.
    by_currency = info.get("spend_by_currency") or []
    if len(by_currency) > 1:
        spend_txt = " + ".join(f"{c['cost']:g} {c['currency'] or '?'}" for c in by_currency)
    else:
        spend_txt = f"{info['total_cost']} {info['currency'] or ''}".strip()
    typer.echo(f"花费  {spend_txt}"
               + (f" / 预算 {info['budget_limit']}" if info["budget_limit"] else ""))
    if info.get("qc_focus"):
        typer.secho("质检重点  " + " · ".join(info["qc_focus"]), fg=typer.colors.MAGENTA)
    if recent_failures:
        typer.secho(f"最近失败  {len(recent_failures)} — manju failures 看原因",
                    fg=typer.colors.RED)
    typer.secho(f"下一步  {info['next_step']}", fg=typer.colors.CYAN)


# ------------------------------------------------------------------- check


@app.command()
def check(as_json: bool = typer.Option(False, "--json")):
    """Schema + references + locks + secret scan (§4, §5). The safety net."""
    report = run_check(_project())
    if as_json:
        _emit(report.to_dict(), True)
    else:
        for w in report.warnings:
            typer.secho(f"⚠ {w}", fg=typer.colors.YELLOW)
        for e in report.errors:
            typer.secho(f"✗ {e}", fg=typer.colors.RED)
        typer.secho("check ok" if report.ok else "check failed",
                    fg=typer.colors.GREEN if report.ok else typer.colors.RED)
    if not report.ok:
        raise typer.Exit(1)


# ------------------------------------------------------------------ import


# Text drops route to story/imports/ (adaptable source), everything else to
# media/imports/ (real footage/audio). Detected by SUFFIX only — no content
# sniffing (a .md is a script, a .txt is a novel; a .mp4 is media).
TEXT_IMPORT_SUFFIXES = {".txt", ".md"}

# round X agent XF: `--on-duplicate` governs a hit against the private
# LIBRARY (never the project-internal dup check above, which always imports
# + warns — see the docstring below). skip = default, do not copy the bytes
# into imports/, just advise reuse; import = copy anyway, advise; link =
# copy anyway but FROM the library's own blob, noting provenance.
ON_DUPLICATE_STRATEGIES = ("skip", "import", "link")


@app.command("import")
def import_(
    files: list[Path],
    as_json: bool = typer.Option(False, "--json"),
    on_duplicate: str = typer.Option(
        "skip", "--on-duplicate",
        help="内容已在个人素材库中时的处理: skip(默认,不导入,提示复用) | "
             "import(仍导入,附带提示) | link(从素材库复制,标注来源)"),
):
    """Register a file into the project. Real footage/audio → media/imports
    (sacred: never deleted, §3). Text (.txt/.md) → story/imports/<name>.md
    instead, as adaptable source text the AI director can turn into a script
    (novel→script). Both honour the same no-overwrite _2 suffixing.

    Every media file's content hash is ALSO checked against the private
    asset library (``core.library``, ~/.manju/library — round X agent XF):
    a hit reports "素材库中已有(标签: …)" and, by default (--on-duplicate
    skip), the file is NOT copied into media/imports at all — `manju lib use`
    already gives you that content, tags included. --on-duplicate import
    copies anyway (just keeps the advisory); --on-duplicate link copies too,
    but reads the bytes FROM the library's own blob (still byte-identical)
    and notes the provenance. This is separate from the project-internal
    duplicate check below (already-imported content), which always imports
    and warns — that one is about avoiding a SECOND identical copy inside
    THIS project, not about the reusable shelf."""
    if on_duplicate not in ON_DUPLICATE_STRATEGIES:
        _fail(f"--on-duplicate 必须是 {'/'.join(ON_DUPLICATE_STRATEGIES)} 之一,"
              f"实际 {on_duplicate!r}")
    project = _project()
    registered: list[str] = []
    story_imports: list[str] = []  # the text drops, for the adapt-me hint
    dup_notes: list[str] = []      # duplicate-content advisories (media only)
    lib_notes: list[str] = []      # library-reuse advisories (media only)
    skipped_lib: list[str] = []    # files skipped because of a library hit
    with _write_lock(project):
        from .core.library import Library, LibraryError, _hex

        try:
            lib = Library()
            lib_by_hash = {e["hash"]: e for e in lib.assets()}
        except LibraryError:
            lib, lib_by_hash = None, {}

        for f in files:
            if not f.exists():
                _fail(f"not found: {f} — 这个路径上没有文件。核对拼写和当前目录"
                      "(路径相对你运行命令的位置),再 `manju import <文件>` 重试。")
            if f.suffix.lower() in TEXT_IMPORT_SUFFIXES:
                # story/imports/<stem>.md — a text drop the agent adapts, not media.
                project.story_imports_dir.mkdir(parents=True, exist_ok=True)
                dest = project.story_imports_dir / f"{f.stem}.md"
                n = 2
                while dest.exists():  # never overwritten, same as media imports
                    dest = project.story_imports_dir / f"{f.stem}_{n}.md"
                    n += 1
                shutil.copy2(f, dest)
                rel = project.relpath(dest)
                registered.append(rel)
                story_imports.append(rel)
                continue

            # library-reuse advisory (round X agent XF) — checked BEFORE the
            # project-internal dup check since it can skip the copy entirely.
            copy_src = f
            if lib_by_hash:
                try:
                    from .core.hashing import hash_file

                    fh = hash_file(f)
                except OSError:
                    fh = None
                lib_hit = lib_by_hash.get(fh) if fh else None
                if lib_hit is not None:
                    hash8 = _hex(lib_hit["hash"])[:8]
                    tags_str = ", ".join(lib_hit.get("tags") or []) or "无标签"
                    note = f"{f.name}: 素材库中已有(标签: {tags_str})— hash8={hash8}"
                    if on_duplicate == "skip":
                        lib_notes.append(
                            note + f" — 已跳过导入,可用 `manju lib use {hash8}` 复用"
                            "(加 --on-duplicate import/link 可强制导入)")
                        skipped_lib.append(f.name)
                        continue
                    if on_duplicate == "link" and lib is not None:
                        blob = lib.blob_path(lib_hit)
                        if blob.exists():
                            copy_src = blob
                            note += " — 已从素材库复制(provenance: library)"
                    lib_notes.append(note)

            # duplicate-content advisory (§3: imports are sacred, dedup is never
            # destructive — we import anyway and say so). Media path only; text
            # drops above are adapted, not deduped.
            try:
                from .media.preview import find_duplicate_import

                dup = find_duplicate_import(project.imports_dir, f)
                if dup is not None:
                    dup_notes.append(
                        f"{f.name}: 内容与 {project.relpath(dup)} 完全相同 (duplicate content)"
                    )
            except Exception:
                pass
            dest = project.imports_dir / f.name
            n = 2
            while dest.exists():  # imports are never overwritten either
                dest = project.imports_dir / f"{f.stem}_{n}{f.suffix}"
                n += 1
            shutil.copy2(copy_src, dest)
            registered.append(project.relpath(dest))
        # §11: derived previews (thumbnail/waveform) into the disposable runtime
        # dir; the segment cache is the lazy proxy (§7 ①). Best-effort only. Text
        # drops have no preview — only the media imports get one.
        previews: dict[str, str] = {}
        try:
            from .media.preview import make_preview

            for rel in registered:
                if rel in story_imports:
                    continue
                preview = make_preview(project.resolve(rel), project.runtime_dir / "thumbs")
                if preview is not None:
                    previews[rel] = project.relpath(preview)
        except ImportError:
            pass
        append_event(project.root, ACTOR, "import",
                     {"files": registered, "story_imports": story_imports,
                      "previews": previews, "on_duplicate": on_duplicate,
                      "skipped_library_duplicates": skipped_lib})
    if as_json:
        _emit({"imported": registered, "story_imports": story_imports,
               "previews": previews, "duplicates": dup_notes,
               "library_duplicates": lib_notes, "skipped_library": skipped_lib,
               "on_duplicate": on_duplicate}, True)
    else:
        for r in registered:
            typer.secho(f"imported {r}", fg=typer.colors.GREEN)
        for src_rel, thumb in previews.items():
            typer.echo(f"  preview: {thumb}")
        for note in dup_notes:
            typer.secho(f"⚠ {note}", fg=typer.colors.YELLOW)
        for note in lib_notes:
            typer.secho(f"⚠ {note}", fg=typer.colors.CYAN)
        for r in story_imports:
            typer.secho(f"  提示 hint: {r} 是文本稿,可改编成剧本(novel→script):"
                        "读它 → 写 story/outline.md、story/script.md → 再拆 shots/",
                        fg=typer.colors.BRIGHT_BLACK)


# ------------------------------------------------------------------ ingest
# 批量入库 batch ingest (round X): the door for "a batch of assets processed
# externally, mapped back onto specific project steps in one reviewed move".
# `manju import` is one file -> media/imports; `ingest` is a directory/file
# list -> per-file classification (take/voice/ref/import) by filename
# convention, dry-run by default, `--apply` to execute. Same trusted
# machine-level door as `import` (absolute paths allowed) — excluded from
# MCP for the same reason (build/ingest.py's docstring).

_INGEST_ACTION_ZH = {
    "take": "新 take", "voice": "新配音 take", "shot_ref": "镜头参考图",
    "bible_ref": "角色/场景/道具参考图", "import": "普通导入", "skip_duplicate": "跳过(重复)",
}


def _print_ingest_table(plan, result=None) -> None:
    from .presets import display_width, pad

    rows = plan.rows
    if not rows:
        typer.secho("没有匹配的文件 (no files matched)", fg=typer.colors.BRIGHT_BLACK)
        return
    name_w = max(display_width(r.name) for r in rows)
    action_w = max(display_width(_INGEST_ACTION_ZH.get(r.action, r.action)) for r in rows)
    target_w = max(display_width(r.target) for r in rows)
    for i, row in enumerate(rows):
        action_zh = _INGEST_ACTION_ZH.get(row.action, row.action)
        color = typer.colors.YELLOW if row.action == "skip_duplicate" else (
            typer.colors.BRIGHT_BLACK if row.action == "import" else typer.colors.CYAN)
        status = ""
        if result is not None:
            r = result.results[i] if i < len(result.results) else None
            if r is None:
                status = "  [未执行]"
            elif r.ok:
                status = "  ✓"
            else:
                status = f"  ✗ {r.error}"
        typer.secho(
            f"{pad(row.name, name_w)}  {pad(action_zh, action_w)}  "
            f"{pad(row.target, target_w)}  {row.reason}{status}",
            fg=color,
        )
        if row.library_hint and row.action != "skip_duplicate":
            typer.secho(f"    ↳ {row.library_hint}", fg=typer.colors.BRIGHT_BLACK)


@app.command()
def ingest(
    paths: list[Path] = typer.Argument(..., help="目录或文件列表(外部产出的一批素材)"),
    role: str = typer.Option("auto", "--role", help="auto | take | voice | ref"),
    shot: Optional[str] = typer.Option(
        None, "--shot", help="强制把这批文件都归到这一个镜头(如外部重生成了几条候选)"),
    on_duplicate: str = typer.Option(
        "skip", "--on-duplicate",
        help="文件内容已在素材库中时的处理: skip(默认,跳过并提示复用) | "
             "import(仍按正常分类导入,附带提示) | link(同 import,但从素材库复制,标注来源)"),
    apply: bool = typer.Option(
        False, "--apply", help="执行计划;默认只预演(dry-run),不改动任何文件"),
    as_json: bool = typer.Option(False, "--json"),
):
    """批量入库 batch ingest — 把外部产出的一批素材(目录或文件列表),按文件名约定
    映射到具体的镜头/步骤(take/配音 take/参考图/普通导入),一次审阅后落地(§11)。

    命名约定(锚定在文件名开头):``S001.mp4``/``S001_take.mp4``/``S001_v2.mov`` →
    S001 的新 take;``S001.wav``/``S001_voice.mp3`` → S001 的新配音;
    ``S001_ref.png``/``S001_ref2.png`` → S001 的参考图;``linxia_ref.png`` →
    bible 角色/场景/道具 linxia 的参考图;其余按普通素材导入 media/imports,并注明原因。
    内容已存在于本项目的文件会被跳过(素材只增不改,§3);内容已在个人素材库中的文件按
    --on-duplicate 处理(默认 skip,见上)。

    默认只打印计划(dry-run,不改动任何文件);加 --apply 才真正执行,一行失败即停止,
    并如实报告已经落地的部分。"""
    from .build.ingest import IngestError, apply_ingest, plan_ingest

    project = _project()
    try:
        plan = plan_ingest(project, paths, role=role, shot=shot, on_duplicate=on_duplicate)
    except IngestError as exc:
        _fail(str(exc), code="ingest_invalid")
        raise  # unreachable

    if not apply:
        if as_json:
            _emit(plan.to_dict(), True)
        else:
            _print_ingest_table(plan)
            typer.secho("(dry-run — 加 --apply 才会真正写入;未改动任何文件)",
                       fg=typer.colors.BRIGHT_BLACK)
        return

    with _write_lock(project):
        result = apply_ingest(project, plan, actor=ACTOR,
                              source=", ".join(str(p) for p in paths))

    if as_json:
        _emit({**plan.to_dict(), **result.to_dict()}, True)
    else:
        _print_ingest_table(plan, result=result)
        typer.secho(f"批次已记录 / batch recorded: {result.batch_id}"
                    f"(manju ingest-review {result.batch_id} 查看/评审)",
                    fg=typer.colors.BRIGHT_BLACK)
    if result.stopped_at is not None:
        _fail(
            f"批量入库在第 {result.stopped_at + 1}/{len(plan.rows)} 行失败并停止 — "
            "此前的行已经落地,不受影响(见上方 ✓);修正后重新运行 "
            "`manju ingest ... --apply`,已落地的内容会被去重跳过",
            code="ingest_partial_failure",
        )


# ------------------------------------------------------------ ingest review
# 批量入库评审 (round AA, goal items 1+2): every `ingest --apply` run persists a
# reviewable batch record (reports/ingest_batches/<id>.yaml, build/batches.py)
# with a per-row match confidence (matched/pending/unmatched/conflict/manual).
#
# These live as their OWN top-level `ingest-*` commands rather than as
# `manju ingest batches`/`manju ingest review` subcommands: `ingest` already
# declares a REQUIRED variadic `paths` argument, and Click's MultiCommand
# parser consumes ALL leftover positional tokens into a group's own variadic
# argument before it ever looks for a subcommand name — so `manju ingest
# batches` would silently become `paths=["batches"]`, never dispatching (see
# `manju qc`/`manju skills`/... which all keep their bare-invocation
# callback argument-free for exactly this reason). Distinct top-level names
# sidestep the ambiguity entirely, same as this codebase already keeps
# `route`/`routing` as separate commands instead of nesting one under the
# other.


def _print_batches_table(batches: list[dict]) -> None:
    from .presets import display_width, pad

    if not batches:
        typer.secho("没有已入库的批次 (no ingest batches yet)", fg=typer.colors.BRIGHT_BLACK)
        return
    id_w = max(display_width(b["batch"]) for b in batches)
    for b in batches:
        counts = ", ".join(f"{k}={v}" for k, v in sorted(b["counts"].items())) or "(空)"
        typer.echo(f"{pad(b['batch'], id_w)}  {b['created']}  actor={b['actor']}  "
                   f"{b['items']} 项  [{counts}]")


@app.command("ingest-batches")
def ingest_batches_cmd(as_json: bool = typer.Option(False, "--json")):
    """列出已入库批次(新→旧),每个批次附评审状态计数(round AA,goal item 2)。"""
    from .build.batches import list_batches

    project = _project()
    batches = list_batches(project)
    if as_json:
        _emit({"batches": batches}, True)
        return
    _print_batches_table(batches)


_REVIEW_ZH = {
    "pending": "待评审", "confirmed": "已确认", "flagged": "已标记",
    "discarded": "已撤销", "auto": "自动(去重)",
}


def _print_batch_review_table(batch_data: dict) -> None:
    from .presets import display_width, pad

    items = batch_data.get("items") or []
    if not items:
        typer.secho("这个批次没有条目 (batch has no items)", fg=typer.colors.BRIGHT_BLACK)
        return
    name_w = max(display_width(it["name"]) for it in items)
    action_w = max(display_width(_INGEST_ACTION_ZH.get(it["action"], it["action"])) for it in items)
    target_w = max(display_width(it["target"]) for it in items)
    for it in items:
        action_zh = _INGEST_ACTION_ZH.get(it["action"], it["action"])
        review_zh = _REVIEW_ZH.get(it["review"], it["review"])
        staged = "  [已自动选用 take]" if it.get("staged") else ""
        typer.echo(
            f"[{it['index']:>3}] {pad(it['name'], name_w)}  {pad(action_zh, action_w)}  "
            f"{pad(it['target'], target_w)}  match={it['match']}  {review_zh}{staged}"
        )
        if it.get("note"):
            typer.secho(f"       备注 note: {it['note']}", fg=typer.colors.BRIGHT_BLACK)


@app.command("ingest-review")
def ingest_review_cmd(
    batch: str = typer.Argument(..., help="批次 id,见 `manju ingest-batches`"),
    as_json: bool = typer.Option(False, "--json"),
):
    """查看一个入库批次的逐项评审状态(index/name/action/target/match/staged/review/note)。"""
    from .build.batches import BatchError, load_batch

    project = _project()
    try:
        data = load_batch(project, batch)
    except BatchError as exc:
        _fail(str(exc), code="batch_not_found")
        raise  # unreachable
    if as_json:
        _emit(data, True)
        return
    typer.secho(
        f"批次 {data.get('batch')}  created={data.get('created')}  "
        f"actor={data.get('actor')}  source={data.get('source') or '(无)'}",
        fg=typer.colors.CYAN,
    )
    _print_batch_review_table(data)


def _matched_pending_indices(data: dict) -> list[int]:
    """Every item in *data* that is a clean single-candidate hit AND has not
    already been auto-reviewed (skip_duplicate rows) — what `--all-matched`
    bulk-confirms."""
    return [
        it["index"] for it in (data.get("items") or [])
        if it.get("match") == "matched" and it.get("review") != "auto"
    ]


def _run_batch_review(batch: str, items: list[int], *, decision: str, note: str, as_json: bool) -> None:
    from .build.batches import BatchError, review_item

    project = _project()
    if not items:
        _fail("至少指定一个 --item(confirm 也可以用 --all-matched)", code="ingest_review_no_items")
        raise  # unreachable

    results = []
    with _write_lock(project):
        for idx in sorted(set(items)):
            try:
                results.append(
                    review_item(project, batch, idx, decision=decision, note=note, actor=ACTOR)
                )
            except BatchError as exc:
                _fail(str(exc), code="batch_review_invalid")
                raise  # unreachable

    if as_json:
        _emit({"batch": batch, "decision": decision, "results": results}, True)
    else:
        for r in results:
            undo = f"  ({r['undo']})" if r.get("undo") else ""
            typer.secho(f"[{r['index']}] {decision} ✓{undo}", fg=typer.colors.GREEN)


@app.command("ingest-confirm")
def ingest_confirm_cmd(
    batch: str = typer.Argument(..., help="批次 id"),
    item: list[int] = typer.Option([], "--item", help="要确认的条目 index(可重复指定多次)"),
    all_matched: bool = typer.Option(
        False, "--all-matched", help="确认这个批次里所有 match=matched 且尚未评审的条目"),
    note: str = typer.Option("", "--note"),
    as_json: bool = typer.Option(False, "--json"),
):
    """确认(confirm)一个/多个批次条目 —— 标记为人工已核实无误。"""
    from .build.batches import BatchError, load_batch

    project = _project()
    items = list(item)
    if all_matched:
        try:
            data = load_batch(project, batch)
        except BatchError as exc:
            _fail(str(exc), code="batch_not_found")
            raise  # unreachable
        items = sorted(set(items) | set(_matched_pending_indices(data)))
    _run_batch_review(batch, items, decision="confirm", note=note, as_json=as_json)


@app.command("ingest-flag")
def ingest_flag_cmd(
    batch: str = typer.Argument(..., help="批次 id"),
    item: list[int] = typer.Option([], "--item", help="要标记的条目 index(可重复指定多次)"),
    note: str = typer.Option("", "--note"),
    as_json: bool = typer.Option(False, "--json"),
):
    """标记(flag)一个/多个批次条目为需要人工再看(不改动任何已落地的文件)。"""
    _run_batch_review(batch, list(item), decision="flag", note=note, as_json=as_json)


@app.command("ingest-discard")
def ingest_discard_cmd(
    batch: str = typer.Argument(..., help="批次 id"),
    item: list[int] = typer.Option([], "--item", help="要撤销的条目 index(可重复指定多次)"),
    note: str = typer.Option("", "--note"),
    as_json: bool = typer.Option(False, "--json"),
):
    """撤销(discard)一个/多个批次条目的评审状态 —— 若该条目自动选用了 take(空镜头,
    goal item 2 的自动选用),且镜头此后没有被重新选择,一并撤销该次自动选用
    (不会删除已落地的素材本身,§3 素材只增不改)。"""
    _run_batch_review(batch, list(item), decision="discard", note=note, as_json=as_json)


# ------------------------------------------------------------------- build


@app.command()
def build(
    target: str = typer.Option(
        "final",
        help="proxy | final | exports | qc | audition | animatic — qc does NOT render "
             "first: it checks the newest EXISTING final on disk against a freshly "
             "recompiled timeline (same as the standalone `manju qc`, plus "
             "gap-filling generation). Run --target final beforehand for QC "
             "on a fresh render; the output/--json names which artifact it "
             "checked (qc_final). audition = 先听后看: voice+captions+music "
             "on slate video, no picture generation (WP2). animatic = 关键帧 + "
             "kenburns pan/hold + 现有音轨/字幕的确定性预演,派生产物,不是视频 take"
             "(AI_IDE_16 §6)."),
    gen: str = typer.Option("missing", help="missing | auto | off"),
    regen_stale: bool = typer.Option(False, "--regen-stale"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(False, "--force",
                               help="re-render even if the final content key matches (FIX-A)"),
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="approve ask_before-gated spend (§8.3)"),
    mode: Optional[str] = typer.Option(
        None, "--mode",
        help="build mode: quality | balanced | speed (goal 14) — biases the "
             "provider strategy, retries and generation concurrency; overrides "
             "project.yaml build.mode. Omit to use the project default."),
    include_unindexed: bool = typer.Option(
        False, "--include-unindexed",
        help="also build shots that exist on disk but are not in "
             "shots/index.yaml order (default: excluded — index order is the "
             "order authority, §5 review #5). `manju check` still warns about them."),
    lang: Optional[str] = typer.Option(
        None, "--lang",
        help="WP4 locale overlay: voice+captions+final under locales/<lang>; "
             "video segments shared with base (no picture regeneration)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """One-command build: fill gaps → timeline → render → QC → exports (§11).

    A plan that spends real money stops as waiting_user unless --yes (the §8.3
    ask_before gate); `--dry-run` shows the plan and estimate without spending.
    See `manju spend` for where the money actually went.

    --target qc does NOT render first — it is "check what's already there",
    exactly like the standalone `manju qc` (plus gap-filling generation and a
    fresh timeline compile). It QCs the newest EXISTING renders/final/*.mp4;
    run `manju build --target final` first for QC against a fresh render.
    The output (and result.qc_final in --json) names the exact artifact.

    --mode quality|balanced|speed (goal 14) turns one knob for provider strategy
    bias + retry budget + generation concurrency; explicit per-shot providers and
    routing rules are never overridden. `manju routing explain` previews it."""
    from .build.graph import run_build
    from .core.models import BUILD_MODE_NAMES

    if target not in ("proxy", "final", "exports", "qc", "audition", "animatic"):
        _fail(f"unknown target: {target}")
    if mode is not None and mode not in BUILD_MODE_NAMES:
        _fail(f"--mode must be one of {BUILD_MODE_NAMES}, got {mode!r}")
    project = _project()
    result = run_build(project, target=target, gen=gen,
                       regen_stale=regen_stale, dry_run=dry_run, force=force,
                       actor=ACTOR, assume_yes=yes, mode=mode,
                       include_unindexed=include_unindexed, lang=lang)
    if as_json:
        # WP5: dry-run --json emits the same plan envelope as GUI /api/plan
        if dry_run:
            from .gui.plan import action_plan

            try:
                envelope = action_plan(project, "build", {
                    "target": target, "gen": gen,
                    "regen_stale": regen_stale, "force": force, "mode": mode,
                    "include_unindexed": include_unindexed,
                    "lang": lang,
                })
            except Exception:
                envelope = result.to_dict()
            else:
                # Cache-reuse visibility from explain
                try:
                    from .build.explain import explain as _explain
                    exp = _explain(project)
                    envelope["renders"] = {
                        t: (exp.get("renders") or {}).get(t, {}).get("verdict")
                        for t in ("final", "proxy")
                    }
                except Exception:
                    pass
                # Voice cost honesty note
                if any(r.get("kind") == "voice" for r in envelope.get("rows") or []):
                    envelope["note"] = (
                        "voice per_call only — speech duration unknown pre-synthesis"
                    )
            _emit(envelope, True)
        else:
            _emit(result.to_dict(), True)
    else:
        if dry_run:
            typer.echo(f"计划任务 {len(result.plan)} 项,预估成本 {result.estimated_cost}")
            for p in result.plan:
                if p.get("kind") == "voice":
                    typer.echo(f"  {p['shot']}: 配音 {p['reason']} → {p['provider']} "
                               f"≈{p['estimated_cost']}")
                else:
                    typer.echo(f"  {p['shot']}: {p['reason']} → {p['provider']} "
                               f"×{p['candidates']} ({p['duration_ms']}ms) ≈{p['estimated_cost']}")
        for w in result.warnings:
            typer.secho(f"⚠ {w}", fg=typer.colors.YELLOW)
        for e in result.errors:
            typer.secho(f"✗ {e}", fg=typer.colors.RED)
        # goal 10: a compact pointer to the structured failure records this build
        # produced (errors that stopped a step, not the degradations) — the full
        # reason/evidence/hint is one command away.
        _build_errs = [f for f in result.failures if f.get("level") == "error"]
        if _build_errs:
            typer.secho(f"失败 {len(_build_errs)} — manju failures 看原因/证据/建议",
                        fg=typer.colors.RED)
        if result.stale:
            typer.secho(
                f"⚠ stale(spec 已变,默认不重做,§4.3): {', '.join(result.stale)} — "
                "用 --regen-stale 或 manju redo 重做", fg=typer.colors.YELLOW)
        if result.generated:
            typer.echo("生成: " + ", ".join(result.generated))
        if result.timeline_path and not dry_run:
            typer.echo(f"时间线: {result.timeline_path}")
        if result.render_path:
            typer.echo(f"渲染: {result.render_path}")
        if result.qc_ok is not None:
            typer.echo(f"QC: {'通过' if result.qc_ok else '有错误,见 reports/qc.md'}")
            # issue #81: --target qc never renders — say exactly which
            # on-disk artifact QC checked, so "旧产物" is never a surprise.
            if target == "qc":
                if result.qc_final:
                    typer.echo(f"  QC 对象(未重新渲染,检查的是现有产物):{result.qc_final}")
                else:
                    typer.echo("  QC 对象:尚无已渲染成片(renders/final 为空)"
                               "— 先运行 manju build --target final/proxy")
        for k, v in result.exports.items():
            typer.echo(f"导出[{k}]: {v}")
        typer.secho("build ok" if result.ok else "build failed",
                    fg=typer.colors.GREEN if result.ok else typer.colors.RED)
    if not result.ok:
        raise typer.Exit(1)


# -------------------------------------------------------------------- redo


@app.command()
def redo(
    shot_id: Optional[str] = typer.Argument(None),
    shots: Optional[str] = typer.Option(
        None, "--shots", help="batch: comma-separated shot ids (S001,S003)"),
    all_stale: bool = typer.Option(False, "--all-stale", help="batch: redo every STALE shot"),
    all_missing: bool = typer.Option(False, "--all-missing", help="batch: redo every MISSING shot"),
    all_shots: bool = typer.Option(
        False, "--all", help="batch: redo every shot (still excludes manual/locked)"),
    candidates: Optional[int] = typer.Option(None),
    provider: Optional[str] = typer.Option(None),
    seed: Optional[int] = typer.Option(None),
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="approve ask_before-gated spend (§8.3)"),
    from_take: Optional[str] = typer.Option(
        None, "--from-take",
        help="reuse a prior take's recipe (provider+params+seed; R12 Runway pattern)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Force new takes (append-only; existing selection stands).

    One shot: `manju redo S002`. --from-take <take> replays that take's recorded
    recipe (provider + params, seed included); --provider/--seed still override.

    Batch: `--shots S001,S003`, `--all-stale`, `--all-missing`, `--all` (mutually
    exclusive with each other and with a positional shot id). The whole batch
    runs under one build lock and one aggregated spend gate; manual/locked shots
    are skipped with a reason (§4.3/§5).

    A priced redo stops as waiting_user unless --yes (the same §8.3 gate as build)."""
    from .build.graph import BuildError, WaitingUser, redo_batch, redo_shot

    batch_flags = [bool(shots), all_stale, all_missing, all_shots]
    if any(batch_flags):
        if shot_id is not None:
            _fail("redo: a positional shot id is mutually exclusive with "
                  "--shots/--all-stale/--all-missing/--all")
        if sum(batch_flags) > 1:
            _fail("redo: --shots/--all-stale/--all-missing/--all are mutually exclusive")
        if from_take is not None:
            _fail("redo: --from-take reuses ONE take's recipe — it cannot combine "
                  "with a batch selector")
        shot_ids = [s.strip() for s in shots.split(",") if s.strip()] if shots else None
        try:
            result = redo_batch(_project(), shots=shot_ids, all_stale=all_stale,
                                all_missing=all_missing, all_shots=all_shots,
                                candidates=candidates, provider=provider, seed=seed,
                                actor=ACTOR, assume_yes=yes)
        except (BuildError, WaitingUser) as exc:
            _fail(str(exc))
        if as_json:
            _emit(result.to_dict(), True)
        else:
            _print_batch_result(result, "redo")
        return

    if shot_id is None:
        _fail("redo: 要重生成哪个镜头?给一个镜头 id(`manju redo S002`),"
              "或用批量选择器 (--all-stale / --all-missing / --all / --shots S001,S003)。")
    try:
        takes = redo_shot(_project(), shot_id, candidates=candidates,
                          provider=provider, seed=seed, from_take=from_take,
                          actor=ACTOR, assume_yes=yes)
    except (BuildError, WaitingUser) as exc:
        _fail(str(exc))
    if as_json:
        _emit({"shot": shot_id, "takes": takes}, True)
    else:
        typer.secho(f"{shot_id}: new takes {', '.join(takes)}", fg=typer.colors.GREEN)


def _print_batch_result(result, verb: str) -> None:
    """Human render of a BatchResult (redo/voice) — ran / skipped / failed, each
    reason on its own line so no exclusion is silent (§4.3)."""
    for sid in result.ran:
        names = ", ".join(result.takes.get(sid, []))
        typer.secho(f"✓ {sid}: {verb} → {names}", fg=typer.colors.GREEN)
    for item in result.skipped:
        typer.secho(f"– {item['shot']} 跳过 skipped: {item['reason']}",
                    fg=typer.colors.YELLOW)
    for item in result.failed:
        typer.secho(f"✗ {item['shot']} 失败 failed: {item['reason']}", fg=typer.colors.RED)
    typer.echo(
        f"{verb}: {len(result.ran)} ran, {len(result.skipped)} skipped, "
        f"{len(result.failed)} failed; 预估 {result.estimated_cost} "
        f"{result.currency or ''}".rstrip())


# ------------------------------------------------------------------ select


@app.command()
def select(
    shot_id: str,
    take: Optional[str] = typer.Argument(None),
    file: Optional[Path] = typer.Option(None, "--file", help="register a human file as a manual take and select it"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Pick a take (the decision is one line of text — §3)."""
    from .core.idents import UnsafeIdentifierError, validate_safe_segment
    from .core.writes import WriteRejected, select_take_checked

    project = _project()
    try:
        validate_safe_segment(shot_id, label="shot_id")  # goal item 11
    except UnsafeIdentifierError as exc:
        _fail(str(exc))
    with _write_lock(project):
        if file:
            from .providers.manual import register_manual_take

            info = register_manual_take(project, shot_id, file)
            take = info.name
        if not take:
            _fail(f"select: 没提供 take。{shot_id} 要选哪一条生成结果?"
                  f"传 take 名(`manju select {shot_id} <take>`),"
                  "或用 --file 把一个人工文件登记成 take 再选。")
        if project.get_take(shot_id, take) is None:
            # round-W #35: a media-less "ghost" sidecar is not a pickable take —
            # the listing only offers names `select` could actually accept.
            have = [t.name for t in project.takes(shot_id, skip_ghosts=True)]
            avail = ("现有 takes:" + ", ".join(have)) if have else \
                "该镜头还没有任何 take,先 `manju build` 或 `manju redo` 生成。"
            _fail(f"{shot_id} has no take '{take}' — {avail}")
        try:
            select_take_checked(project, shot_id, take, actor=ACTOR, via="cli")
        except WriteRejected as exc:
            _fail(str(exc))
    if as_json:
        _emit({"shot": shot_id, "take": take, "ok": True}, True)
    else:
        typer.secho(f"{shot_id}: selected {take}", fg=typer.colors.GREEN)


# ------------------------------------------------------------- lock/unlock


@app.command()
def lock(
    shot_id: str,
    field: str,
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="approve ask_before=lock_change when present"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Seal a field's current value with a hash (§5). Build enforces it."""
    project = _project()
    # WP5: make lock_change honest — when the token is in ask_before, require --yes
    config = project.load_config()
    if "lock_change" in (config.ask_before or []) and not yes:
        _fail(
            "waiting_user: lock_change 在 ask_before 中 — 锁定会改变协作契约"
            f"(外向制品/权限,非金钱)。确认后重试: manju lock {shot_id} {field} --yes",
            code="waiting_user",
        )
    with _write_lock(project):
        raw = project.load_shot_raw(shot_id)
        try:
            digest = seal_lock(raw, field)
        except (KeyError, IndexError) as exc:
            _fail(f"cannot lock: {exc}")

        def mutate(d):
            locked = d.get("locked")
            if not isinstance(locked, dict):
                locked = {str(p): "" for p in locked} if isinstance(locked, list) else {}
            locked[field] = digest
            d["locked"] = locked

        project.update_shot_raw(shot_id, mutate)
        append_event(project.root, ACTOR, "lock", {"shot": shot_id, "field": field})
    if as_json:
        _emit({"shot": shot_id, "field": field, "hash": digest}, True)
    else:
        typer.secho(f"{shot_id}: locked {field}", fg=typer.colors.GREEN)


@app.command()
def unlock(shot_id: str, field: str):
    """Interactive terminal only + confirmation; never exposed over MCP (§5)."""
    if not _interactive():
        _fail("unlock is interactive-only: run it yourself in a terminal (§5). "
              "AI agents: write a proposal to proposals/ instead.")
    if not typer.confirm(f"确认解锁 {shot_id}.{field}?"):
        raise typer.Exit(1)
    project = _project()

    def mutate(d):
        locked = d.get("locked") or {}
        if isinstance(locked, list):  # hand-written unsealed form -> normalize
            locked = {str(p): "" for p in locked}
        locked.pop(field, None)
        d["locked"] = locked

    with _write_lock(project):
        project.update_shot_raw(shot_id, mutate)
        append_event(project.root, ACTOR, "unlock", {"shot": shot_id, "field": field})
    typer.secho(f"{shot_id}: unlocked {field}", fg=typer.colors.YELLOW)


# ----------------------------------------------------------------- propose


@app.command()
def propose(
    title: str,
    body: Optional[str] = typer.Option(None, "--body", help="proposal body text"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Write proposals/NNNN_<slug>.md — the legitimate channel to request a
    locked-content change (§5). Twin of the MCP `propose` tool; the numbering
    and slug scheme are REUSED from manju.mcp.tools so the two share one
    claim path — an atomic O_EXCL create loop (#51 round W): two concurrent
    proposers (an agent session racing a human terminal, two agent sessions)
    never collide on the same file.
    With no --body on a pipe, the body is read from stdin; otherwise it may be
    empty."""
    from .core.yamlio import atomic_write_text
    from .mcp.tools import _claim_proposal_path, _slugify  # shared numbering/slug

    project = _project()
    if body is None:
        body = "" if sys.stdin.isatty() else sys.stdin.read()
    _number, path = _claim_proposal_path(project.proposals_dir, _slugify(title))
    atomic_write_text(path, f"# {title}\n\n{body}\n")
    rel = project.relpath(path)
    append_event(project.root, ACTOR, "propose", {"title": title, "path": rel})
    if as_json:
        _emit({"path": rel}, True)
    else:
        typer.secho(f"proposal → {rel}", fg=typer.colors.GREEN)


# ------------------------------------------------------------------- skills

skills_app = typer.Typer(no_args_is_help=False,
                         help="技能库 (round V): packaged domain expertise the "
                              "driving agent loads on demand — index first, "
                              "one skill's full text via show。")
app.add_typer(skills_app, name="skills")


@skills_app.callback(invoke_without_command=True)
def skills_list(ctx: typer.Context,
                as_json: bool = typer.Option(False, "--json")):
    """List every visible skill (project > user > bundled; project can never
    override the core protocol skill — goal item 46)."""
    if ctx.invoked_subcommand is not None:
        return
    from .core.skills import core_skill_shadow_warning, list_skills

    project = _project_or_none()
    rows = list_skills(project)
    warning = core_skill_shadow_warning(project)
    if as_json:
        _emit({"skills": [r.to_dict() for r in rows], "core_skill_shadow_warning": warning}, True)
        return
    if warning:
        typer.secho(warning, fg=typer.colors.YELLOW)
    if not rows:
        typer.echo("技能库为空 — 在 skills/<id>/SKILL.md 添加技能")
        return
    typer.secho("技能库 / skills(project > user > bundled)", fg=typer.colors.CYAN)
    for r in rows:
        src = "" if r.source == "bundled" else f"  [{r.source}]"
        typer.echo(f"  {r.id:<22} {r.when_to_use or r.description}{src}")
    typer.secho("  → manju skills show <id> 查看全文", fg=typer.colors.BRIGHT_BLACK)


@skills_app.command("show")
def skills_show(skill_id: str,
                as_json: bool = typer.Option(False, "--json")):
    """One skill's full SKILL.md text."""
    from .core.skills import load_skill

    project = _project_or_none()
    try:
        info = load_skill(project, skill_id)
    except KeyError as exc:
        _fail(str(exc))
    text = info.path.read_text(encoding="utf-8") if info.path else ""
    # round AA (goal item 8): a skill's content was actually SERVED — the
    # usage signal core/evaluate.py reports on. Only inside a project (no
    # events.jsonl to write to otherwise); best-effort, never blocks the show.
    if project is not None:
        try:
            append_event(project.root, ACTOR, "skill_used", {"skill": skill_id, "via": "cli"})
        except Exception:
            pass
    if as_json:
        _emit({"skill": info.to_dict(), "text": text}, True)
    else:
        typer.echo(text)


def _project_or_none():
    """skills work outside a project too (bundled + user tiers only)."""
    try:
        return Project.find(Path.cwd())
    except ProjectError:
        return None


# -------------------------------------------------------------------- auto


# A tiny embedded playbook for when the bundled SKILL.md cannot be found (§10).
_MINI_PLAYBOOK = (
    "按照 Manju 操作手册工作(内置精简版):\n"
    "1. 开工先跑 manju status --json,读 events.jsonl 尾部与 project.yaml 的 mode/ask_before。\n"
    "2. 每次编辑后必跑 manju check;check 不过不许 build。\n"
    "3. 一键构建:manju build(补缺→时间线→渲染→QC),再按需 manju export。\n"
    "4. 花钱/长耗时(视频生成、final render)按 ask_before 先询问,问前先 manju build --dry-run。\n"
    "5. 禁区:不调 unlock、不动 media/imports、不覆盖 final;想改锁定内容写 proposals/。"
)


@app.command()
def auto(
    prompt_text: str,
    agent: Optional[str] = typer.Option(
        None, "--agent",
        help='agent CLI: a known name (claude/codex/gemini/qwen/aider) or a '
             'template like "claude -p {prompt}"; also MANJU_AGENT / '
             'project.yaml:agent',
    ),
):
    """Autopilot (§10): a thin shell over ANY one-shot agent CLI. The agent
    gets the Manju playbook (SKILL.md) plus your task and works through the
    ordinary CLI surface; its actions are logged as actor=ai. No LLM SDK —
    strictly a subprocess. Structured integrations: `manju serve-mcp`."""
    import subprocess

    from .agents import AgentResolutionError, build_command, resolve_agent

    project = _project()
    try:
        template = resolve_agent(
            getattr(project.load_config(), "agent", None), agent
        )
    except AgentResolutionError as exc:
        _fail(str(exc))

    # Round V progressive disclosure: the core protocol skill travels in full
    # (project > user > bundled resolution via core/skills), every OTHER skill
    # only as an index line — the agent pulls full bodies on demand with
    # `manju skills show <id>` instead of hauling the whole library每次.
    from .core.skills import CORE_SKILL_ID, skill_index_text, skill_text

    try:
        core_text: Optional[str] = skill_text(project, CORE_SKILL_ID)
    except KeyError:
        core_text = None
    index = skill_index_text(project, exclude=(CORE_SKILL_ID,))

    # round AA (goal item 8): the auto-injection path IS content served —
    # every `manju auto` run hands the driven agent the core skill's full
    # body, the same "skill_used" signal `manju skills show` records.
    if core_text:
        try:
            append_event(project.root, ACTOR, "skill_used", {"skill": CORE_SKILL_ID, "via": "auto"})
        except Exception:
            pass

    if core_text:
        composed = "按照以下 Manju 操作手册工作:\n\n" + core_text
        if index:
            composed += "\n\n---\n\n" + index
        composed += "\n\n---\n\n任务:" + prompt_text
    else:
        composed = _MINI_PLAYBOOK + "\n\n任务:" + prompt_text

    argv = build_command(template, composed)
    if shutil.which(argv[0]) is None:
        _fail(f"agent CLI '{argv[0]}' not found on PATH — install it or pick "
              "another via --agent/MANJU_AGENT/project.yaml:agent")
    # goal item 49: the FULL prompt used to ride into events.jsonl verbatim —
    # a project truth file that gets `git add`-ed at snapshot and can carry
    # customer info, plot secrets, links, or a stray token/key the user typed
    # into their prompt. Only a sha256 (so the exact prompt is still provable
    # from a log) + a short, non-secret-shaped preview are recorded now.
    import hashlib

    prompt_preview = prompt_text[:80]
    append_event(project.root, ACTOR, "auto", {
        "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
        "prompt_preview": prompt_preview,
        "prompt_len": len(prompt_text),
        "agent": argv[0],
    })
    env = dict(os.environ)
    env["MANJU_ACTOR"] = "ai"  # every action the driven agent takes is logged as ai
    proc = subprocess.run(argv, cwd=project.root, env=env)
    raise typer.Exit(proc.returncode)


# ---------------------------------------------------------------- qc/repair


qc_app = typer.Typer(
    no_args_is_help=False,
    help="三层质检 (§9)。子命令 brief/verdict 是 round V 的视觉判读管道 (§6):Manju "
         "自身不跑视觉模型,而是出题给驱动它的 agent 用眼判读(标准见 "
         "manju skills show visual-qc-review),再把结论回填。",
)
app.add_typer(qc_app, name="qc")


@qc_app.callback(invoke_without_command=True)
def qc_main(ctx: typer.Context,
            deep: bool = typer.Option(False),
            as_json: bool = typer.Option(False, "--json")):
    """Three-layer QC; writes reports/qc.json, qc.md, repair_plan.yaml (§9)."""
    if ctx.invoked_subcommand is not None:
        return
    from .qc.checks import run_qc as _run_qc
    from .qc.report import build_assurance_block, write_reports

    project = _project()
    report = _run_qc(project, project.load_timeline(), deep=deep)

    # DR02 WP4: derive per-shot bound-acceptance assurance (read-only) and thread
    # it into the reports + envelope. It is a SEPARATE axis from the qc gate — it
    # never changes the exit code below. Degrade gracefully: any failure omits the
    # block (a lone warn line off the --json stdout so parsing stays clean).
    assurance = None
    try:
        from .qc.assurance import assurance_for_all

        assurance = assurance_for_all(project, qc_report=report)
    except Exception as exc:
        assurance = None
        if not as_json:
            typer.secho(f"⚠ 验收(assurance)计算跳过:{exc}", fg=typer.colors.YELLOW, err=True)

    paths = write_reports(project, report, assurance=assurance)
    if as_json:
        envelope = report.to_dict()
        if assurance is not None:
            envelope["assurance"] = build_assurance_block(project, assurance)
        _emit(envelope, True)
    else:
        errors = sum(1 for i in report.items if i.level == "error")
        warns = sum(1 for i in report.items if i.level == "warn")
        typer.echo(f"QC: {errors} errors, {warns} warnings → {project.relpath(paths['qc_md'])}")
        typer.secho("qc ok" if report.ok else "qc found errors",
                    fg=typer.colors.GREEN if report.ok else typer.colors.RED)
        _echo_assurance_summary(assurance)
    if not report.ok:
        raise typer.Exit(1)


def _echo_assurance_summary(assurance: Optional[list]) -> None:
    """Compact human summary for ``manju qc`` (DR02 WP4): counts per state, then
    a per-shot line for every rejected/unknown/stale shot with its first reason
    (stale reasons named). Silent when no assurance was computed."""
    if not assurance:
        return
    from collections import Counter

    counts = Counter(a.get("assurance_state") for a in assurance)
    summary = ", ".join(f"{state} {n}" for state, n in sorted(counts.items()))
    typer.echo(f"验收 assurance: {summary}")
    for a in assurance:
        state = a.get("assurance_state")
        if state not in ("rejected", "unknown", "stale"):
            continue
        sid = (a.get("subject") or {}).get("id", "?")
        reasons = a.get("reasons") or []
        first = f" — {reasons[0]}" if reasons else ""
        typer.secho(f"  {sid} · {state}{first}", fg=typer.colors.YELLOW)
        stale = a.get("stale_reasons") or []
        if stale:
            typer.echo(f"    stale 原因: {', '.join(stale)}")


@qc_app.command("brief")
def qc_brief_cmd(
    shots: Optional[str] = typer.Option(
        None, "--shots", help="逗号分隔的镜头 id;省略则出题全部可判读镜头/组合"),
    mode: str = typer.Option(
        "shots", "--mode",
        help="shots(逐镜出题,默认) | consistency(一致性组合出题:角色出场对照表 / "
             "相邻镜头场景对比 / 场景整体看板 — round X 解决跨镜一致性)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """出题给驱动 Manju 的 vision-capable agent (§6, goal item 6;一致性组合见 round X)。

    --mode shots(默认):每镜头的评审帧(mid + 首/尾)+ 上下文(场景/角色 bible 参考图/
    must_show/avoid/连续性锁/台词)。--mode consistency:跨镜 COMPARISON UNIT ——
    每个出场 >1 镜的角色一张对照看板(bible 参考图 + 每个出场镜头一帧)、每对共享
    场景的相邻镜头一张并排对比图、每个场景一张整体看板 —— 一致性是跨镜属性,不该
    逐镜孤立判读。两种模式都只出判读标准指针(visual-qc-review)+ 回填 JSON 契约,
    Manju 自身不判图。"""
    from .qc.agent_review import qc_brief as _qc_brief

    project = _project()
    if mode not in ("shots", "consistency"):
        _fail(f"--mode 必须是 shots|consistency,收到 {mode!r}")
    ids = [s.strip() for s in shots.split(",") if s.strip()] if shots else None
    brief = _qc_brief(project, ids, mode=mode)
    if as_json:
        _emit(brief, True)
        return

    if mode == "consistency":
        typer.secho(f"质检出题(一致性)/ qc brief --mode consistency:"
                    f"{len(brief['units'])} 个组合可判读", fg=typer.colors.CYAN)
        typer.echo(f"判读标准:manju skills show {brief['criteria']['skill']}"
                   f"({brief['criteria']['note']})")
        for u in brief["units"]:
            members = ", ".join(m["shot"] for m in u["members"])
            typer.echo(f"  [{u['kind']}] {u['unit']}  成员:{members}")
            if u.get("image"):
                typer.echo(f"    对比图:{u['image']}")
        for sk in brief["skipped"]:
            typer.secho(f"  跳过 {sk['unit']}:{sk['reason']}", fg=typer.colors.BRIGHT_BLACK)
        typer.echo("→ 判读后用 manju qc verdict --from-file <json>(每条判读带 unit 字段)回填")
        return

    typer.secho(f"质检出题 / qc brief:{len(brief['shots'])} 个镜头可判读", fg=typer.colors.CYAN)
    typer.echo(f"判读标准:manju skills show {brief['criteria']['skill']}"
               f"({brief['criteria']['note']})")
    for s in brief["shots"]:
        typer.echo(f"  {s['shot']}  take={s['take']}")
        frames = "  ".join(f"{k}={v}" for k, v in (s["frames"] or {}).items() if v)
        if frames:
            typer.echo(f"    评审帧:{frames}")
        must = s["context"].get("must_show") or []
        if must:
            typer.echo("    须体现:" + "; ".join(must))
    for sk in brief["skipped"]:
        typer.secho(f"  跳过 {sk['shot']}:{sk['reason']}", fg=typer.colors.BRIGHT_BLACK)
    typer.echo("→ 判读后用 manju qc verdict --from-file <json>(或 - 走 stdin)回填")


@qc_app.command("coverage")
def qc_coverage_cmd(as_json: bool = typer.Option(False, "--json")):
    """AI 判读覆盖率(round X agent XB,user pain #2):每个镜头 / 一致性组合是
    reviewed(判读仍与当前字节匹配)/ stale(判读过期)/ never(从未判读过)。"""
    from .qc.agent_review import qc_coverage as _qc_coverage

    project = _project()
    cov = _qc_coverage(project)
    if as_json:
        _emit(cov, True)
        return
    s = cov["summary"]
    typer.secho(
        f"覆盖率:镜头 {s['shots_reviewed']}/{s['shots_total']} 已判读"
        f"(过期 {s['shots_stale']}, 未判读 {s['shots_never']}) · "
        f"组合 {s['units_reviewed']}/{s['units_total']} 已判读"
        f"(过期 {s['units_stale']}, 未判读 {s['units_never']})",
        fg=typer.colors.CYAN,
    )
    for sid, state in cov["shots"].items():
        if state != "reviewed":
            typer.echo(f"  镜头 {sid}: {state}")
    for uid, info in cov["units"].items():
        if info["state"] != "reviewed":
            typer.echo(f"  组合 {uid} [{info['kind']}]: {info['state']}")


@qc_app.command("verdict")
def qc_verdict_cmd(
    from_file: str = typer.Option(
        ..., "--from-file", help="判读结果 JSON 文件路径;- 表示从 stdin 读取"),
    as_json: bool = typer.Option(False, "--json"),
):
    """回填 agent 的视觉判读结果 (§6):写入 reports/qc_agent.jsonl,并与所判 take 的
    字节内容哈希绑定。下次 manju qc 会把仍匹配当前 take 的判读汇入为 [AI判读] 项
    (blocker→error / issue→warn / fyi→info);take 一旦重生成,旧判读自动过期。"""
    from .qc.agent_review import VerdictError, record_verdicts

    project = _project()
    if from_file == "-":
        text = sys.stdin.read()
    else:
        try:
            text = Path(from_file).read_text(encoding="utf-8")
        except OSError as exc:
            _fail(f"无法读取判读文件:{exc}")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        _fail(f"判读 JSON 解析失败:{exc}")
    try:
        result = record_verdicts(project, payload, actor=ACTOR)
    except VerdictError as exc:
        _fail(str(exc))
    if as_json:
        _emit(result, True)
    else:
        typer.secho(f"已回填 {result['written']} 条 AI 判读 → {result['path']}",
                    fg=typer.colors.GREEN)
        if result["levels"]:
            typer.echo("  级别:" + ", ".join(f"{k}={v}" for k, v in result["levels"].items()))
        typer.echo("→ 跑 manju qc 查看汇入的 [AI判读] 项")


@qc_app.command("tech")
def qc_tech_cmd(
    media: Path = typer.Argument(..., help="the exact source media (project-relative) to profile"),
    edit_fps: Optional[int] = typer.Option(
        None, "--edit-fps",
        help="integer edit-grid fps; turns on the edit-grid drift diagnostic (§5.1)"),
    write: bool = typer.Option(
        False, "--write", help="materialise the deletable reports/technical/<hash>.json"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Honest per-file technical facts (`manju.media-technical-profile/v1`, §5.2-5.6):
    time / picture / colour / audio / container recorded VERBATIM or ``"unknown"`` —
    never guessed — plus structured diagnostics (edit-grid drift vs --edit-fps,
    DAR/SAR geometry mismatch, colour-unknown, VFR suspicion). The report is a
    deletable derived projection, never a build/authorization input."""
    from .media.ffmpeg import MediaError
    from .media.technical_profile import technical_profile, write_profile

    project = _project()
    if not media.exists():
        _fail(f"media not found: {media}", code="no_media")
        return
    try:
        source_ref = project.relpath(media)  # project-relative — never store an abs path
    except (ValueError, ProjectError):
        source_ref = media.name  # media outside the project root
    try:
        doc = technical_profile(media, edit_fps=edit_fps, source_ref=source_ref)
    except MediaError as exc:
        _fail(f"cannot profile media: {exc}", code="unreadable_media")
        return
    if write:
        write_profile(project, doc)
    if as_json:
        _emit(doc, True)
        return
    f = doc["facts"]
    t, p, c = f["time"], f["picture"], f["color"]
    typer.secho(f"technical profile {media.name}  digest={doc['profile_digest'][:23]}…",
                fg=typer.colors.GREEN)
    typer.echo(f"  time    rate={t['r_frame_rate']} mode={t['rate_mode']} "
               f"dur_ms={t['duration_ms']}")
    typer.echo(f"  picture {p['coded_width']}x{p['coded_height']} {p['pix_fmt']} "
               f"{p['bit_depth']}-bit {p['chroma_subsampling']} rot={p['rotation']}")
    typer.echo(f"  color   primaries={c['primaries']} transfer={c['transfer']} "
               f"matrix={c['matrix']} range={c['range']} known={c['color_known']}")
    if f["audio"]:
        a = f["audio"]
        typer.echo(f"  audio   {a['codec_name']} {a['sample_rate']}Hz "
                   f"{a['channel_layout']} {a['bit_depth']}-bit")
    else:
        typer.echo("  audio   (none)")
    for d in doc["diagnostics"]:
        typer.secho(f"  ⚑ {d['code']} [{d['severity']}] {d['detail']}",
                    fg=typer.colors.YELLOW)


@qc_app.command("conformance")
def qc_conformance_cmd(
    profile: str = typer.Option(
        "master", "--profile",
        help="delivery profile id from project.yaml delivery_profiles; its "
             "additive `technical:` block declares the delivery targets (§7.1)"),
    metadata_file: Optional[str] = typer.Option(
        None, "--metadata", help="platform metadata file (never a credential)"),
    write: bool = typer.Option(
        False, "--write",
        help="materialise the deletable reports/conformance/<profile>.json "
             "(never a build/release input)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Declarative delivery conformance (`manju.delivery-conformance/v1`, §7.2):
    per-check PASS / FAIL / UNKNOWN / NOT_APPLICABLE against the profile's
    additive `technical:` targets — container / codec / resolution / PAR /
    frame-rate / colour / audio / loudness / true-peak / captions / checksum /
    package / disclosure / unresolved. UNKNOWN is honest (no stored profile,
    colour genuinely unknown, no measured loudness) — never guessed into PASS.

    There is NO aggregate score (roadmap §7.2 不生成单一总分); the summary is
    counts by status only. The report is a deletable derived projection and is
    NEVER a build or release input — it does not gate readiness this loop."""
    from .build import conformance as _conf
    from .build import delivery as _dm
    from .build import exportstatus as _es

    project = _project()
    config = _es._safe(lambda: project.load_config())
    profiles = _dm._delivery_profiles(config) if config is not None else {}
    prof = dict(profiles.get(profile) or {})
    try:
        manifest = _dm.build_manifest(project, profile, metadata_file=metadata_file)
    except (_dm.DeliveryManifestError, ProjectError) as exc:
        _fail(str(exc))
        return
    doc = _conf.check_delivery_conformance(project, manifest, prof)
    if write:
        dest = _conf.write_conformance_report(project, doc)
        append_event(project.root, ACTOR, "delivery_conformance",
                     {"profile": profile, "path": project.relpath(dest)})
    if as_json:
        _emit(doc, True)
        return
    s = doc["summary"]
    typer.secho(f"交付一致性 / delivery-conformance  profile={profile}  "
                f"digest={doc['conformance_digest'][:23]}…", fg=typer.colors.CYAN)
    typer.echo(f"  PASS={s['PASS']}  FAIL={s['FAIL']}  UNKNOWN={s['UNKNOWN']}  "
               f"NOT_APPLICABLE={s['NOT_APPLICABLE']}  (no single score)")
    _color = {"PASS": typer.colors.GREEN, "FAIL": typer.colors.RED,
              "UNKNOWN": typer.colors.YELLOW, "NOT_APPLICABLE": typer.colors.BRIGHT_BLACK}
    for r in doc["checks"]:
        typer.secho(f"    {r['status']:<15} {r['check']:<20} {r['artifact']}"
                    + (f"  — {r['detail']}" if r['detail'] else ""),
                    fg=_color.get(r["status"], typer.colors.WHITE))


@qc_app.command("captions")
def qc_captions_cmd(
    write: bool = typer.Option(
        False, "--write",
        help="materialise the deletable reports/captions/<digest>."
             "caption-accessibility.json(仅记录 — never a build/check input)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Caption readability / accessibility advisories
    (`manju.caption-accessibility/v1`, roadmap §5.5 — record + advise, NEVER block).

    CJK-aware per-cue facts over the SAME cue truth the SRT/ASS writers render:
    reading speed(East Asian Width W/F 记 2、换行记 0,其余记 1;CPS=加权字符/秒)、
    行宽/行数、时长下限/上限、字幕间隔/重叠、forced 与非 forced 同屏、role 覆盖统计。
    每一行 severity 都是 "advisory" — 阈值只是起点,平台/人来决定;本命令永远退出 0,
    从不改变 qc/check/build 的结论(manual 模式分析人工 captions.srt,SRT 无 role
    字段,role 覆盖如实为空)。"""
    from .qc.captions_access import accessibility_for_project, write_accessibility

    project = _project()
    doc = accessibility_for_project(project)
    if write:
        path = write_accessibility(project, doc)
        if not as_json:
            typer.echo(f"报告 → {project.relpath(path)}")
    if as_json:
        _emit(doc, True)
        return
    s = doc["summary"]
    typer.secho(
        f"caption accessibility: {s['totals']['cues']} cues, "
        f"{s['totals']['advisories']} advisories(仅建议,不阻塞)"
        f"  digest={doc['report_digest'][:23]}…",
        fg=typer.colors.CYAN)
    rs = s["reading_speed"]
    if rs["measured_cues"]:
        typer.echo(f"  阅读速度 cps(加权): min {rs['min']}  median {rs['median']}  "
                   f"max {rs['max']}  超上限 {rs['over_ceiling']}")
    cov = s["role_coverage"]
    if cov["counts"]:
        roles = ", ".join(f"{k}={v}" for k, v in cov["counts"].items())
        typer.echo(f"  角色 roles: {roles}  未标注 {cov['unroled']}")
        if cov["unknown_roles"]:
            typer.secho(f"  未收录 role: {', '.join(cov['unknown_roles'])}"
                        "(仅记录;manju check 会给出结构化 warn)",
                        fg=typer.colors.YELLOW)
    for a in doc["advisories"][:20]:
        typer.echo(f"  ⚑ {a['code']} {a['subject']} — {a['detail']}")
    if len(doc["advisories"]) > 20:
        typer.echo(f"  … 另有 {len(doc['advisories']) - 20} 条(--json 查看全部)")


def _repair_op(project: Project, op: str, shot: Optional[str], take: Optional[str],
               factor: float, ms: int, mode: Optional[str],
               in_ms: Optional[int], out_ms: Optional[int], as_json: bool) -> None:
    """Explicit repair op (round-Q/round-T, §9): builds a NEW take from a source
    take — append-only, the source is never touched."""
    from .media.ffmpeg import MediaError
    from .media.repair_ops import (
        crop_pad_take,
        extend_take,
        retime_take,
        set_inout_take,
        trim_take,
    )

    if not shot:
        _fail(f"--op {op} requires --shot <id>")
    src_take = take
    if src_take is None:
        try:
            src_take = project.load_shot(shot).status.selected_take
        except ProjectError as exc:
            _fail(str(exc))
    if not src_take:
        _fail(f"{shot} has no selected take — pass --take <name> explicitly")

    with _write_lock(project):
        try:
            if op == "retime":
                new = retime_take(project, shot, src_take, factor)
            elif op == "extend":
                if ms <= 0:
                    _fail("--op extend requires --ms > 0")
                new = extend_take(project, shot, src_take, ms, mode=mode or "freeze")
            elif op == "trim":
                if ms <= 0:
                    _fail("--op trim requires --ms > 0")
                new = trim_take(project, shot, src_take, ms)
            elif op == "inout":
                if in_ms is None or out_ms is None:
                    _fail("--op inout requires --in-ms and --out-ms")
                # Virtual is the default (leaves handles for a real cross-dissolve);
                # --mode reencode bakes the region into new bytes (no handles).
                new = set_inout_take(project, shot, src_take, in_ms, out_ms,
                                     mode=mode or "virtual")
            elif op == "croppad":
                new = crop_pad_take(project, shot, src_take, mode=mode or "center_crop")
            else:
                _fail(f"unknown --op {op!r} (choose retime|extend|trim|inout|croppad)")
        except (MediaError, ProjectError) as exc:
            _fail(f"repair failed: {exc}")

        append_event(project.root, ACTOR, "repair",
                     {"shot": shot, "op": op, "source_take": src_take, "new_take": new.name})
    if as_json:
        _emit({"shot": shot, "op": op, "source_take": src_take,
               "new_take": new.name, "params": new.sidecar.params}, True)
    else:
        typer.secho(
            f"repaired {shot}: {op} → new take {new.name} (source {src_take} untouched)",
            fg=typer.colors.GREEN,
        )
        typer.echo(f"select it with: manju select {shot} {new.name}")


def _repair_voice_cli(project: Project, shot: Optional[str], provider: Optional[str],
                      dry_run: bool, as_json: bool) -> None:
    """The voice repair loop (round U, §9/§11): regenerate a shot's voice, realign
    its captions, mark the take repaired — or, with --dry-run, just print the plan.
    The picture is never touched; the loop itself writes no render output (the
    final re-renders naturally through content keys on the next build)."""
    from .media.voicefix import repair_voice

    if not shot:
        _fail("--op voice requires --shot <id>")

    # dry-run is read-only (prints the plan, touches nothing) — lockless,
    # same stance as `build --dry-run` (§9 round W).
    if dry_run:
        result = repair_voice(project, shot, dry_run=True, provider=provider, actor=ACTOR)
    else:
        with _write_lock(project):
            result = repair_voice(project, shot, dry_run=False, provider=provider, actor=ACTOR)

    if as_json:
        _emit(result.to_dict(), True)
        if not result.ok:
            raise typer.Exit(1)
        return

    header = ("配音修复计划(dry-run,未改动任何文件)" if dry_run
              else (f"已修复配音:{shot}" if result.ok else f"配音修复失败:{shot}"))
    typer.secho(header, fg=(typer.colors.CYAN if dry_run else
                            (typer.colors.GREEN if result.ok else typer.colors.RED)))
    for line in result.plan:
        typer.echo(f"  • {line}")
    if not dry_run and result.ok:
        typer.echo(
            f"  新配音:{result.new_take}(source_take={result.old_take or '无'},"
            f"audio_repaired=true;voice_hash={(result.voice_hash or '')[:16]}…);旧配音保留")
        if result.realigned:
            typer.echo(f"  字幕已比例重排 {len(result.realigned)} 行(下次构建生效)")
        typer.echo("  提示:运行 `manju build` 重混 — 成片将随新配音重新渲染")
    for adv in result.advisories:
        typer.secho(f"  ⚠ {adv}", fg=typer.colors.YELLOW)
    if not result.ok:
        if result.failure:
            typer.secho(f"  原因:{result.failure.get('cause')}", fg=typer.colors.RED, err=True)
            typer.secho(f"  建议:{result.failure.get('hint')}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@app.command()
def repair(
    auto: bool = typer.Option(False, "--auto"),
    op: Optional[str] = typer.Option(
        None, "--op", help="explicit repair op: retime|extend|trim|inout|croppad|voice "
                           "(builds a new take; source is never overwritten)"),
    shot: Optional[str] = typer.Option(None, "--shot", help="shot id for --op"),
    take: Optional[str] = typer.Option(
        None, "--take", help="source take for --op (default: the shot's selected take)"),
    factor: float = typer.Option(
        1.0, "--factor", help="retime duration multiplier (0.9 = 10% faster/shorter)"),
    ms: int = typer.Option(0, "--ms", help="extend/trim amount in milliseconds"),
    in_ms: Optional[int] = typer.Option(
        None, "--in-ms", help="set_inout region start in ms (--op inout)"),
    out_ms: Optional[int] = typer.Option(
        None, "--out-ms", help="set_inout region end in ms, exclusive (--op inout)"),
    mode: Optional[str] = typer.Option(
        None, "--mode", help="extend: freeze|pad_black · croppad: center_crop|pad_blur "
                             "· inout: virtual(default)|reencode"),
    provider: Optional[str] = typer.Option(
        None, "--provider", help="--op voice: tts manifest id (default: first configured)"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="--op voice: print the repair plan in 中文, mutate nothing"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Repair the film (§9). Three modes:

    - `--op <retime|extend|trim|inout|croppad>` runs an explicit ffmpeg repair on
      a shot's take, registering the result as a NEW take (append-only lineage):
        manju repair --op retime  --shot S001 --factor 0.9
        manju repair --op extend  --shot S001 --ms 500 --mode freeze
        manju repair --op trim    --shot S001 --ms 300
        manju repair --op inout   --shot S001 --in-ms 500 --out-ms 1500
        manju repair --op inout   --shot S001 --in-ms 500 --out-ms 1500 --mode reencode
        manju repair --op croppad --shot S001 --mode pad_blur
    - `--op voice --shot S001 [--dry-run]` runs the VOICE repair loop (round U):
      keep the picture, regenerate the voice (append-only + repaired_from/audio
      _repaired lineage), realign the shot's captions to the new duration (manual
      cues are left untouched with a 中文 advisory), and let the final re-render
      through content keys. `--dry-run` prints the plan and changes nothing:
        manju repair --op voice --shot S001 --dry-run
        manju repair --op voice --shot S001
    - `--auto` executes auto-safe items from repair_plan.yaml (redo/degrade);
      the rest stay for humans. Repair-plan action is 'edit params and rebuild'.
    """
    from .build.graph import redo_shot
    from .core.yamlio import read_yaml

    project = _project()
    if op is not None:
        if op == "voice":
            _repair_voice_cli(project, shot, provider, dry_run, as_json)
            return
        _repair_op(project, op, shot, take, factor, ms, mode, in_ms, out_ms, as_json)
        return
    plan_path = project.reports_dir / "repair_plan.yaml"
    if not plan_path.exists():
        _fail("no repair_plan.yaml — run `manju qc` first")
    plan = read_yaml(plan_path) or {}
    # The plan writer emits "actions" (qc/report.py); "issues" is accepted for
    # hand-written plans from before the key was pinned. Reading the wrong key
    # made --auto a silent no-op against real plans (round-Q agent finding).
    issues = plan.get("actions") or plan.get("issues") or []
    done, left = 0, 0
    for issue in issues:
        if auto and issue.get("auto_safe") and issue.get("shot") and \
                issue.get("action") in ("redo_new_seed", "degrade_fallback"):
            try:
                # --auto is an explicit human action over a pre-approved plan:
                # the redo proceeds through the §8.3 gate without re-prompting.
                redo_shot(project, issue["shot"], actor=ACTOR, assume_yes=True)
                done += 1
            except Exception as exc:  # a failed repair goes back on the human pile
                typer.secho(f"⚠ {issue['shot']}: repair failed — {exc}", fg=typer.colors.YELLOW)
                left += 1
        else:
            left += 1
    if as_json:
        _emit({"repaired": done, "remaining": left}, True)
    else:
        typer.echo(f"repaired {done}, remaining for human review: {left}")


# ------------------------------------------------------------------ frames


@app.command()
def frames(
    source: str = typer.Argument(..., help="project-relative media path "
                                          "(e.g. media/gen/S001/take_01.mp4)"),
    at_ms: int = typer.Option(0, "--at", help="single-frame timestamp in ms"),
    strip: int = typer.Option(0, "--strip", help="N evenly-spaced scrub thumbnails"),
    width: Optional[int] = typer.Option(
        None, "--width", help="scale to this pixel width (aspect kept)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Preview still frames from any project media (round-T).

    Backs the GUI trim/scrub control and the cover picker; a pure preview that
    never writes the film's cover (that stays `packaging.cover.frame_ms`).
    Frames are cached under `.manju/frames` (disposable, §3).

        manju frames media/gen/S001/take_01.mp4 --at 1500
        manju frames renders/final/final_v1.mp4 --strip 10 --width 160 --json
    """
    from .media.ffmpeg import MediaError
    from .media.frames import extract_frame, frame_strip

    project = _project()
    try:
        if strip > 0:
            paths = frame_strip(project, source, count=strip,
                                width=width if width else 160)
            rels = [project.relpath(p) for p in paths]
            _emit({"source": source, "count": len(rels), "width": width or 160,
                   "strip": rels}, as_json)
            if not as_json:
                for r in rels:
                    typer.echo(r)
        else:
            path = extract_frame(project, source, at_ms, width=width)
            rel = project.relpath(path)
            _emit({"source": source, "at_ms": at_ms, "width": width,
                   "frame": rel}, as_json)
            if not as_json:
                typer.echo(rel)
    except (MediaError, ProjectError) as exc:
        _fail(f"frames failed: {exc}")


# ------------------------------------------------------------------ export


@app.command()
def export(
    jianying: bool = typer.Option(False, "--jianying"),
    capcut: bool = typer.Option(False, "--capcut", help="international CapCut draft (pycapcut)"),
    srt: bool = typer.Option(False, "--srt"),
    ttml: bool = typer.Option(
        False, "--ttml",
        help="IMSC1-flavoured TTML caption sidecar (captions/captions.ttml, "
             "FP §5.5 — same cue truth as --srt)"),
    otio: bool = typer.Option(False, "--otio"),
    edl: bool = typer.Option(
        False, "--edl",
        help="CMX3600 EDL video cut list (exports/edl/, real SMPTE timecode; "
             "V track only — audio is out of scope, see conform-loss)"),
    pullsheet: bool = typer.Option(
        False, "--pullsheet",
        help="AI_IDE_16 §9: CSV + Markdown storyboard pull sheet "
             "(exports/pullsheet/) derived from the timeline + shots"),
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="approve ask_before=final_export when present"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Export drafts/captions from the compiled timeline (§11).

    JianYing is dual-path (decision 8): pyJianYingDraft native draft as the
    primary, the diff-stable skeleton + lint as the secondary; capcut-cli
    lints the result when installed. final.mp4/SRT/OTIO always remain the
    fallback exits (§14)."""
    project = _project()
    # WP5: final_export gate — free, but outward-facing; require --yes when token present
    config = project.load_config()
    if "final_export" in (config.ask_before or []) and not yes:
        _fail(
            "waiting_user: final_export 在 ask_before 中 — 导出是外向制品确认"
            "(非金钱花费)。确认后重试: manju export --yes …",
            code="waiting_user",
        )
    # AI_IDE_16 §9 pull sheet: a derived CSV+MD export that does NOT require a
    # written timeline.json (it loads-or-compiles). Handle it first so it works
    # pre-build, and let it be requested alongside the timeline exporters.
    outputs: dict[str, Path] = {}
    notes: list[str] = []
    if pullsheet:
        from .build.pullsheet import export_pull_sheet

        for kind, path in export_pull_sheet(project).items():
            outputs[f"pullsheet_{kind}"] = path
        notes.append("pull sheet: CSV+MD in exports/pullsheet/ (PDF skipped — "
                     "no headless-Chromium/PDF-table path in this environment)")
    timeline = project.load_timeline()
    if timeline is None:
        if pullsheet and not (jianying or capcut or srt or ttml or otio or edl):
            rel = {k: project.relpath(v) for k, v in outputs.items()}
            append_event(project.root, ACTOR, "export", rel)
            if as_json:
                _emit({"outputs": rel, "notes": notes}, True)
            else:
                for k, v in rel.items():
                    typer.secho(f"{k}: {v}", fg=typer.colors.GREEN)
                for note in notes:
                    typer.secho(f"⚠ {note}", fg=typer.colors.YELLOW)
            return
        _fail("no timeline.json — run `manju build` first")
    if not (jianying or capcut or srt or ttml or otio or edl or pullsheet):
        srt = otio = True
    # Which target we're building, so a mid-export failure names its subject in
    # the structured record (goal 10) — the capcut path below records the same way.
    _target = "timeline"
    try:
        if srt:
            from .exporters.srt_ass import export_captions

            _target = "srt"
            outputs.update(export_captions(project, timeline))
        if ttml:
            from .exporters.ttml import export_ttml

            _target = "ttml"
            outputs["ttml"] = export_ttml(project, timeline)
        if otio:
            from .exporters.otio import export_otio

            _target = "otio"
            outputs["otio"] = export_otio(project, timeline)
        if edl:
            from .exporters.edl import export_edl

            _target = "edl"
            outputs["edl"] = export_edl(project, timeline)
        if jianying:
            from .exporters.jianying import export_jianying
            from .exporters.native_draft import (
                ExporterUnavailable,
                capcut_cli_lint,
                export_jianying_native,
            )

            _target = "jianying"
            outputs["jianying"] = export_jianying(project, timeline)  # skeleton + lint
            try:
                outputs["jianying_native"] = export_jianying_native(project, timeline)
            except ExporterUnavailable as exc:
                notes.append(f"jianying_native skipped: {exc}")
            lint = capcut_cli_lint(outputs["jianying"].parent)
            if lint is None:
                notes.append("capcut-cli not installed — secondary lint skipped (own lint ran)")
            elif lint:
                notes.extend(f"capcut-cli lint: {p}" for p in lint[:10])
    except (OSError, RuntimeError) as exc:
        # MediaError and ProjectError both subclass RuntimeError, so this covers
        # every exporter fault. Record it with cause/evidence/hint before aborting
        # — matches the capcut path so every export exit is debuggable, not a trace.
        _record_failure(project, "export", _target, f"{_target} 导出失败",
                        evidence=" ".join(str(exc).split())[:400],
                        hint="核对 timeline.json 是否完整;或换 --srt/--otio 兜底出口(§14)")
        _fail(f"export failed ({_target}): {exc}")
    if capcut:
        from .exporters.native_draft import ExporterUnavailable, export_capcut_native

        try:
            outputs["capcut"] = export_capcut_native(project, timeline)
        except ExporterUnavailable as exc:
            _record_failure(project, "export", "capcut", "CapCut 原生草稿导出不可用",
                            evidence=" ".join(str(exc).split())[:400],
                            hint="安装 pycapcut;或用 --otio/--srt 兜底出口(§14)")
            _fail(str(exc))
    rel_outputs = {k: project.relpath(v) for k, v in outputs.items()}
    append_event(project.root, ACTOR, "export", rel_outputs)
    if as_json:
        _emit({"outputs": rel_outputs, "notes": notes}, True)
    else:
        for k, v in rel_outputs.items():
            typer.secho(f"{k}: {v}", fg=typer.colors.GREEN)
        for note in notes:
            typer.secho(f"⚠ {note}", fg=typer.colors.YELLOW)


# --------------------------------------------------------------- openclap

openclap_app = typer.Typer(
    no_args_is_help=True,
    help="OpenClap (.clap) 互换适配器 —— 一个隔离的可选出口(§8/§14)。"
         "inspect 只读体检、export 从编译时间线导出、import-plan 只规划不落盘"
         "(从不下载远端媒体、从不写入项目、从不自动选take)。",
)
app.add_typer(openclap_app, name="openclap")


def _openclap_read_or_fail(file: Path, as_json: bool):
    """Read a .clap file, turning a fail-closed parse into the repo's JSON error
    envelope (with diagnostics) on ``--json`` or colored prose otherwise."""
    from .exporters.openclap import read_clap
    from .exporters.openclap.model import ClapReadError

    try:
        return read_clap(file)
    except ClapReadError as exc:
        diags = [d.to_dict() for d in exc.diagnostics]
        code = next((d["code"] for d in diags if d["severity"] == "error"),
                    "clap_parse_error")
        if as_json:
            typer.echo(json.dumps(
                {"error": str(exc), "code": code, "diagnostics": diags},
                ensure_ascii=False, indent=2))
        else:
            typer.secho(f"无法解析 .clap: {exc}", fg=typer.colors.RED, err=True)
            for d in diags:
                typer.secho(f"  [{d['severity']}] {d['code']}: {d['message']}"
                            + (f" ({d['path']})" if d['path'] else ""),
                            fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)


@openclap_app.command("inspect")
def openclap_inspect(
    file: Path = typer.Argument(..., help="path to a .clap file"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Read-only体检:表头/实际计数、meta 摘要、分类直方图、assetUrl 定位符分类、
    未知字段统计与全部诊断。从不写任何东西;硬解析错误以非零码退出。"""
    from .exporters.openclap import inspect_clap

    doc = _openclap_read_or_fail(file, as_json)
    summary = inspect_clap(doc)
    if as_json:
        _emit(summary, True)
        return
    h = summary["header"]
    a = summary["actual_counts"]
    m = summary["meta"]
    typer.secho(f"OpenClap {h['format']} · {file}", fg=typer.colors.CYAN)
    typer.echo(f"  表头声明 workflows={h['declared']['workflows']} "
               f"entities={h['declared']['entities']} scenes={h['declared']['scenes']} "
               f"segments={h['declared']['segments']}")
    typer.echo(f"  实际计数 workflows={a['workflows']} entities={a['entities']} "
               f"scenes={a['scenes']} segments={a['segments']}")
    typer.echo(f"  meta: title={m['title']!r} {m['width']}x{m['height']} "
               f"orientation={m['orientation']} durationInMs={m['duration_in_ms']}")
    if summary["segment_categories"]:
        typer.echo("  分类: " + ", ".join(f"{k}={v}" for k, v in
                                          summary["segment_categories"].items()))
    if summary["locator_histogram"]:
        typer.echo("  定位符: " + ", ".join(f"{k}={v}" for k, v in
                                            summary["locator_histogram"].items()))
    if summary["unknown_categories"]:
        typer.echo("  未知分类: " + ", ".join(summary["unknown_categories"]))
    if summary["unknown_fields"]:
        for section, info in summary["unknown_fields"].items():
            typer.echo(f"  未知字段[{section}]: {info['items_with_unknown_keys']} 项 · "
                       + ", ".join(info["distinct_unknown_keys"]))
    errs = sum(1 for d in summary["diagnostics"] if d["severity"] == "error")
    warns = sum(1 for d in summary["diagnostics"] if d["severity"] == "warning")
    typer.secho(f"  诊断: {errs} errors, {warns} warnings",
                fg=typer.colors.GREEN if errs == 0 else typer.colors.RED)
    for d in summary["diagnostics"]:
        if d["severity"] != "info":
            typer.echo(f"    [{d['severity']}] {d['code']}: {d['message']}")


@openclap_app.command("export")
def openclap_export(
    output: Optional[Path] = typer.Option(
        None, "--output", help="输出路径(默认 exports/openclap/<项目名>.clap,项目内)"),
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="approve ask_before=final_export when present"),
    as_json: bool = typer.Option(False, "--json"),
):
    """从编译好的 timeline 导出 .clap 快照(确定性、原子写、项目内路径)。
    需要先 `manju build` 生成 timeline.json。"""
    from .exporters.openclap import export_openclap

    project = _project()
    # WP5: same final_export gate as `manju export` (outward-facing, free) —
    # a .clap is an exchange artifact for other tools, so it confirms like one.
    if "final_export" in (project.load_config().ask_before or []) and not yes:
        _fail(
            "waiting_user: final_export 在 ask_before 中 — 导出是外向制品确认"
            "(非金钱花费)。确认后重试: manju openclap export --yes …",
            code="waiting_user",
        )
    timeline = project.load_timeline()
    if timeline is None:
        _fail("no timeline.json — run `manju build` first")
    try:
        out = export_openclap(project, timeline, output=output)
    except (OSError, RuntimeError) as exc:
        _record_failure(project, "export", "openclap", "openclap 导出失败",
                        evidence=" ".join(str(exc).split())[:400],
                        hint="核对 timeline.json 是否完整;或换 --srt/--otio 兜底出口(§14)")
        _fail(f"export failed (openclap): {exc}")
    rel = project.relpath(out)
    # Re-read our own output for honest counts (cheap; also proves it parses).
    from .exporters.openclap import read_clap

    counts = read_clap(out).actual_counts
    append_event(project.root, ACTOR, "export", {"openclap": rel})
    if as_json:
        _emit({"output": rel, "counts": counts}, True)
    else:
        typer.secho(f"openclap: {rel}", fg=typer.colors.GREEN)
        typer.echo(f"  entities={counts['entities']} scenes={counts['scenes']} "
                   f"segments={counts['segments']}")


@openclap_app.command("import-plan")
def openclap_import_plan(
    file: Path = typer.Argument(..., help="path to a .clap file"),
    target: Optional[Path] = typer.Option(
        None, "--target", help="existing project to describe against (never written)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """规划一次 .clap 导入(只规划、零写入):从不下载远端媒体、从不写入项目、
    从不自动选take。有 --target 时只描述并列出冲突。"""
    from .core.hashing import hash_file
    from .exporters.openclap import build_import_plan

    doc = _openclap_read_or_fail(file, as_json)
    target_project = None
    if target is not None:
        try:
            target_project = Project(target)
        except ProjectError as exc:
            _fail(str(exc), code="no_project")
    plan = build_import_plan(doc, source_sha256=hash_file(file),
                             target_project=target_project)
    if as_json:
        _emit(plan, True)
        return
    typer.secho(f"import-plan {plan['schema']} · {file}", fg=typer.colors.CYAN)
    typer.echo(f"  source_sha256: {plan['source_sha256']}")
    typer.echo(f"  target: {plan['target_project'] or '(hypothetical fresh project)'}")
    typer.echo(f"  operations: {len(plan['operations'])} · "
               f"unmapped: {len(plan['unmapped'])} · conflicts: {len(plan['conflicts'])}")
    for op in plan["operations"]:
        extra = f" [{op.get('note')}]" if op.get("note") else ""
        typer.echo(f"    {op['op']} -> {op['target']} (from {op.get('from_segment')}){extra}")
    for c in plan["conflicts"]:
        typer.secho(f"    冲突 {c['target']}: {c['reason']}", fg=typer.colors.YELLOW)


# ----------------------------------------------------------------- package


@app.command()
def package(
    force: bool = typer.Option(False, "--force",
                               help="re-cut even if the content key matches"),
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="approve ask_before=final_export when present"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Cut the cover (+ teaser) out of the current final (§13-14).

    Cover: a frame pulled from the final (frame mode) or a rendered card (card
    mode) → exports/packaging/cover.png at project resolution. Teaser (when
    enabled in packaging.yaml): the final sliced [from_ms, +duration_ms],
    re-encoded with the final params → exports/packaging/teaser.mp4. Both are
    idempotent via .key.json sidecars; --force bypasses. Needs a final —
    without one it points you at `manju build`."""
    # WP5: same final_export gate as `manju export` (outward-facing, free)
    _pkg_project = _project()
    if "final_export" in (_pkg_project.load_config().ask_before or []) and not yes:
        _fail(
            "waiting_user: final_export 在 ask_before 中 — 包装导出是外向制品确认"
            "(非金钱花费)。确认后重试: manju package --yes",
            code="waiting_user",
        )
    from pydantic import ValidationError

    from .media.ffmpeg import MediaError
    from .media.packaging import PackagingError, make_package

    project = _project()
    try:
        result = make_package(project, force=force)
    except (PackagingError, MediaError) as exc:  # one-line envelope (FIX-D)
        _record_failure(project, "package", "final", "封面/预告切片失败",
                        evidence=" ".join(str(exc).split())[:800],
                        hint="确认已有 final(manju build);检查 packaging.yaml 与素材",
                        log_path=".manju/logs/render.log")
        _fail(str(exc))
    except ValidationError as exc:  # a malformed packaging.yaml, same envelope
        _record_failure(project, "package", "packaging.yaml", "packaging.yaml 无法解析",
                        evidence=str(exc.errors()[0].get("msg", exc))[:400],
                        hint="修正 packaging.yaml 的字段(见 manju new 生成的注释模板)")
        _fail(f"packaging.yaml is invalid: {exc.errors()[0].get('msg', exc)}")
    append_event(project.root, ACTOR, "package",
                 {k: result[k] for k in ("cover", "teaser", "skipped")})
    if as_json:
        _emit(result, True)
    else:
        typer.secho(f"封面: {result['cover']}", fg=typer.colors.GREEN)
        if result["teaser"]:
            typer.secho(f"预告: {result['teaser']}", fg=typer.colors.GREEN)
        if result["skipped"]:
            typer.secho(f"⚠ 已跳过(内容键未变): {', '.join(result['skipped'])} "
                        "— 用 --force 强制重切", fg=typer.colors.YELLOW)
        for warning in result.get("warnings", []):
            typer.secho(f"⚠ {warning}", fg=typer.colors.YELLOW)


# ----------------------------------------------------------------- exports


_FRESHNESS_COLOR = {
    "up_to_date": typer.colors.GREEN,
    "stale": typer.colors.YELLOW,
    "missing": typer.colors.BRIGHT_BLACK,
    "problematic": typer.colors.RED,
    "needs_manual": typer.colors.CYAN,
    "verified": typer.colors.GREEN,
}


@app.command()
def exports(
    as_json: bool = typer.Option(False, "--json"),
    baseline: bool = typer.Option(
        False, "--baseline",
        help="AI_IDE_07C: show the current approved release baseline (VALID/"
             "NO_BASELINE/DAMAGED/CORRUPT) and its exact byte binding"),
    approve_baseline: bool = typer.Option(
        False, "--approve-baseline",
        help="AI_IDE_07C: approve a final's exact bytes as the current release "
             "baseline (human action — appends to the verification log; never spends)"),
    final: Optional[str] = typer.Option(
        None, "--final", help="which final to approve (default: current newest)"),
    reason: str = typer.Option(
        "", "--reason", help="why this is the release baseline (recorded in the event)"),
    accept_known_risk: bool = typer.Option(
        False, "--accept-known-risk",
        help="approve despite blockers (human-only; records the blockers + reason)"),
    manifest: bool = typer.Option(
        False, "--manifest",
        help="AI_IDE_13C: derive the manju.delivery-manifest/v1 for --profile "
             "(read-only; add --output to materialize atomically into reports/)"),
    bundle: bool = typer.Option(
        False, "--bundle",
        help="AI_IDE_13C: write a delivery bundle ZIP (manifest files + "
             "SHA256SUMS, atomic) for --profile — distinct from `manju pack`"),
    profile: str = typer.Option(
        "master", "--profile",
        help="AI_IDE_13C: delivery profile id from project.yaml delivery_profiles "
             "(default: master)"),
    metadata_file: Optional[str] = typer.Option(
        None, "--metadata",
        help="AI_IDE_13C: user-provided platform metadata file (never a credential)"),
    output: Optional[Path] = typer.Option(
        None, "--output",
        help="AI_IDE_13C: where to write the manifest/bundle (project-relative)"),
):
    """导出中心 Export center — every finished-output's freshness at a glance.

    One honest table over the nine deliverables (成片/预览版/SRT/ASS/OTIO/剪映
    草稿/CapCut 草稿/封面/预告): 上新 / 待更新 / 缺失 / 有问题 / 待人工确认 /
    已人工确认, each with the one-line evidence behind the verdict. Read-only —
    never spends, never mutates. Reads the SAME engine (build/exportstatus) the
    GUI /exports page renders, so the two can never disagree. `--json` for agents.

    AI_IDE_07C: `--approve-baseline` blesses the exact final bytes as the release
    baseline (a narrow human-verification event, no new store); `--baseline`
    shows it; `--json` embeds the composed `release_assessment` (blockers,
    readiness, regression review, next safe actions)."""
    from .build.exportstatus import deliverables_data

    project = _project()

    if approve_baseline:
        from .build import baseline as _bl

        try:
            res = _bl.approve_baseline(
                project, final, reason=reason, actor_kind=ACTOR,
                accept_known_risk=accept_known_risk)
        except _bl.BaselineError as exc:
            _fail(str(exc))
        ev = res["event"]
        append_event(project.root, ACTOR, "approve_baseline",
                     {"artifact": ev["artifact"]["path"], "event_id": ev["event_id"]})
        if as_json:
            _emit(res, True)
            return
        typer.secho("已批准发布基线 / baseline approved", fg=typer.colors.GREEN)
        typer.echo(f"  artifact  {ev['artifact']['path']}")
        typer.echo(f"  sha256    {ev['artifact']['sha256']}")
        typer.echo(f"  final_key {ev['artifact']['final_key']}")
        if res.get("risk_accepted_blockers"):
            typer.secho("  ⚠ 已接受风险 known blockers: "
                        + ", ".join(res["risk_accepted_blockers"]), fg=typer.colors.YELLOW)
        return

    if baseline:
        from .build import baseline as _bl

        info = _bl.current_baseline(project)
        if as_json:
            _emit(info, True)
            return
        color = (typer.colors.GREEN if info["status"] == "VALID"
                 else typer.colors.BRIGHT_BLACK if info["status"] == "NO_BASELINE"
                 else typer.colors.RED)
        typer.secho(f"发布基线 / release baseline: {info['status']}", fg=color)
        if info.get("artifact"):
            typer.echo(f"  artifact  {info['artifact'].get('path')}")
            typer.echo(f"  sha256    {info['artifact'].get('sha256')}")
        if info.get("damage"):
            typer.secho(f"  ⚠ {info['damage']}", fg=typer.colors.RED)
        if info["status"] == "NO_BASELINE":
            typer.secho("  （尚无发布基线;manju exports --approve-baseline 设定）",
                        fg=typer.colors.BRIGHT_BLACK)
        return

    if manifest or bundle:
        # AI_IDE_13C: the derived delivery manifest / bundle. Read-only pure
        # derivation over the SAME deliverables engine + 07C release assessment —
        # never an export input; --output/--bundle write atomically, never delete.
        from .build import delivery as _dm

        try:
            man = _dm.build_manifest(project, profile, metadata_file=metadata_file)
            if bundle:
                out, man = _dm.write_bundle(project, man, output=output)
                append_event(project.root, ACTOR, "delivery_bundle",
                             {"profile": profile, "bundle": project.relpath(out)})
                if as_json:
                    _emit({"bundle": project.relpath(out), "manifest": man}, True)
                    return
                typer.secho(f"交付包 / delivery bundle: {project.relpath(out)}",
                            fg=typer.colors.GREEN)
                typer.echo(f"  entries={len(_dm._bundle_members(project, man))}  "
                           f"checksums={man['checksums']['path']}")
                return
            if output is not None:
                dest = _dm.materialize_manifest(project, man, output=output)
                append_event(project.root, ACTOR, "delivery_manifest",
                             {"profile": profile, "path": project.relpath(dest)})
            if as_json:
                _emit(man, True)
                return
            ds = man["release"]["delivery_state"]
            typer.secho(f"交付清单 / delivery-manifest  profile={profile}  "
                        f"variant={man['variant']['kind']}", fg=typer.colors.CYAN)
            typer.echo(f"  manifest_id  {man['manifest_id']}")
            typer.echo(f"  technical_ready={ds['technical_ready']}  "
                       f"editor_approved={ds['editor_approved']}  "
                       f"publish_handoff_ready={ds['publish_handoff_ready']}  "
                       f"published={ds['published']}")
            for a in man["artifacts"]:
                typer.echo(f"    {a['role']:<16} {a['state']:<20} {a['path'] or '—'}")
            for d in man["diagnostics"]:
                typer.secho(f"    ⚠ {d['code']} [{d['severity']}] {d['detail']}",
                            fg=typer.colors.YELLOW)
            return
        except (_dm.DeliveryManifestError, ProjectError) as exc:
            _fail(str(exc))

    data = deliverables_data(project)
    if as_json:
        _emit(data, True)
        return

    typer.secho("导出中心 / exports", fg=typer.colors.CYAN)
    for row in data["deliverables"]:
        color = _FRESHNESS_COLOR.get(row["freshness"], typer.colors.WHITE)
        ver = f" {row['version']}" if row["version"] else ""
        verified = ""
        if row["freshness"] == "verified" and row["verified_by"]:
            verified = f"  [{row['verified_by']} @ {str(row['verified_at'] or '')[:19]}]"
        typer.echo(
            f"  {row['label']:<18}"
            + typer.style(f"{row['freshness_zh']:<5}", fg=color)
            + f"{ver}{verified}"
        )
        typer.secho(f"      └ {row['basis']}", fg=typer.colors.BRIGHT_BLACK)
        if row["path"]:
            typer.secho(f"        {row['path']}", fg=typer.colors.BRIGHT_BLACK)
    counts = "  ".join(f"{k}:{v}" for k, v in sorted(data["counts"].items()))
    typer.secho(f"合计 / by state:  {counts}", fg=typer.colors.BRIGHT_BLACK)

    # AI_IDE_07C: the composed release verdict over the same evidence.
    ra = data.get("release_assessment") or {}
    ready = ra.get("ready")
    blockers = ra.get("blockers") or []
    base_status = (ra.get("baseline") or {}).get("status")
    verdict = "READY 可发布" if ready else "NOT READY 未就绪"
    typer.secho(f"发布评估 / release:  {verdict}  "
                f"(baseline={base_status}, blockers={len(blockers)})",
                fg=typer.colors.GREEN if ready else typer.colors.YELLOW)
    for b in blockers:
        typer.secho(f"    ✗ {b['code']}  [{b['scope']}]  {b['detail']}", fg=typer.colors.RED)
    for act in (ra.get("next_actions") or []):
        typer.secho(f"    → {act['command']}  ({act['reason_code']}, "
                    f"fix={act['fix_owner']}, auto={act['safe_to_auto_run']})",
                    fg=typer.colors.BRIGHT_BLACK)
    typer.secho("（生成/更新与标记已人工确认见 manju gui → 导出中心）",
                fg=typer.colors.BRIGHT_BLACK)


# ----------------------------------------------------------------- explain


@app.command()
def explain(
    as_json: bool = typer.Option(False, "--json"),
    cost: bool = typer.Option(
        False, "--cost",
        help="WP5: add per-shot est_cost + total (same estimators as build --dry-run)"),
    graph: bool = typer.Option(
        False, "--graph",
        help="DR03B: append explicit-DAG diagnostics — the read-only derived "
             "phase/shot/render/export dependency VIEW (blocking, root cause, cycles)"),
):
    """Why will the next build do what it will do? Read-only: per-shot
    picture/voice states with hash evidence, timeline fingerprint diff, and
    final/proxy content-key verdicts. Never mutates, never spends.

    ``--graph`` (DR03B) appends the ``manju.graph-diagnostics/v1`` document: a
    read-only derivation of Manju's actually-modeled dependencies (every indexed
    shot's generation → compile → render/export, voice advisory). It is a
    diagnostic VIEW, never a scheduling truth source."""
    project = _project()
    from .build.explain import explain as _explain

    info = _explain(project, with_cost=cost)
    if graph:
        from .build.graphdiag import diagnose_project

        info["graph"] = diagnose_project(project)
    if as_json:
        _emit(info, True)
        return
    for entry in info["shots"]:
        video = entry["video"]
        line = f"{entry['shot']}  画面={video['state']}"
        if video.get("selected_take"):
            line += f"({video['selected_take']})"
        if video.get("why"):
            line += f" — {video['why']}"
        if "voice" in entry:
            line += f"  配音={entry['voice']['state']}"
            if entry["voice"].get("why"):
                line += f" — {entry['voice']['why']}"
        if cost and "est_cost" in entry:
            line += f"  ≈{entry['est_cost']}"
        typer.echo(line)
    tl = info["timeline"]
    typer.echo(f"时间线  mode={tl['mode']} captions={tl['captions_mode']} → {tl['verdict']}")
    renders = info["renders"]
    for target in ("final", "proxy"):
        if target in renders:
            r = renders[target]
            typer.echo(f"{target:5}  {r.get('latest') or '—'} → {r['verdict']}")
    if cost and "cost" in info:
        c = info["cost"]
        typer.echo(f"成本合计  {c.get('total')} {c.get('currency') or ''}  ({c.get('note') or ''})")
    if graph and "graph" in info:
        gd = info["graph"]
        s = gd["summary"]
        typer.echo(
            f"依赖图 graph  节点={s['node_count']} 边={s['edge_count']}  "
            f"ready={s['ready']} blocked={s['blocked']} pending={s['pending']} "
            f"waiting={s['waiting']} failed={s['failed']} cycles={s['cycles']}  "
            f"(只读派生视图,非调度真相)"
        )
        for iss in gd["issues"]:
            typer.echo(f"  ! {iss['code']}  {iss['node_id']}")


# ------------------------------------------------------------------ locale


@app.command()
def locale(
    action: str = typer.Argument(..., help="add | status"),
    lang: Optional[str] = typer.Argument(None, help="locale id e.g. en"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Multilingual locale overlays (WP4). Locale text never enters picture
    hashes — video segments are shared; only voice/captions differ."""
    from .core.container import ProjectError
    from .core.locale import add_locale, list_locales, locale_status

    project = _project()
    action = (action or "").strip().lower()
    try:
        if action == "add":
            if not lang:
                _fail("locale add 需要语言 id: manju locale add en")
            result = add_locale(project, lang)
            if as_json:
                _emit(result, True)
            else:
                typer.secho(
                    f"locale {result['lang']}: {result['path']}  "
                    f"(+{len(result['added'])} lines, total={result['total']})",
                    fg=typer.colors.GREEN,
                )
        elif action == "status":
            info = locale_status(project, lang)
            if as_json:
                _emit(info, True)
            else:
                if not info.get("locales"):
                    typer.echo("no locales — manju locale add en")
                    return
                for lg, body in info["locales"].items():
                    counts = body.get("counts") or {}
                    typer.echo(
                        f"{lg}: ok={counts.get('ok', 0)}  "
                        f"missing={counts.get('missing', 0)}  "
                        f"翻译过期={counts.get('翻译过期', 0)}"
                    )
                    for row in body.get("lines") or []:
                        vs = (body.get("voice") or {}).get(row["shot"], "")
                        if row["state"] != "ok" or vs not in ("", "fresh", "not_needed"):
                            typer.echo(
                                f"  {row['shot']}: 译={row['state']}  配音={vs or '—'}"
                            )
                    caps = body.get("captions") or {}
                    fin = body.get("final") or {}
                    typer.echo(
                        f"  captions: {caps.get('freshness')}  "
                        f"{caps.get('path') or '—'}"
                    )
                    typer.echo(
                        f"  final: {fin.get('freshness')}  "
                        f"{fin.get('path') or '—'}"
                    )
        else:
            _fail(f"locale: unknown action {action!r} (add|status)")
    except ProjectError as exc:
        _fail(str(exc))


# ---------------------------------------------------------------- roundtrip


@app.command()
def roundtrip(
    edited: Path = typer.Argument(..., help="edited skeleton draft_content.json or OTIO"),
    apply: bool = typer.Option(False, "--apply", help="apply accepted rows"),
    rows: Optional[str] = typer.Option(
        None, "--rows", help="1-based row indices e.g. 1,3-5"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Flow external editor edits back as reviewable truth changes (WP6).

    Carriers: JianYing diff-stable skeleton + OTIO only. Plan by default;
    ``--apply`` under one build_lock."""
    from .build.roundtrip import apply_roundtrip, plan_roundtrip
    from .core.container import ProjectError

    project = _project()
    try:
        plan = plan_roundtrip(project, edited)
    except ProjectError as exc:
        _fail(str(exc))
        return
    if apply:
        sel = None
        if rows:
            sel = []
            for part in rows.split(","):
                part = part.strip()
                if "-" in part:
                    a, b = part.split("-", 1)
                    sel.extend(range(int(a) - 1, int(b)))
                else:
                    sel.append(int(part) - 1)
        result = apply_roundtrip(project, plan, rows=sel, actor=ACTOR)
        if as_json:
            _emit({"plan": plan, "apply": result}, True)
        else:
            typer.secho(
                f"roundtrip apply: {len(result['applied'])} 条, "
                f"跳过 {len(result['skipped'])}, batch={result['batch']}",
                fg=typer.colors.GREEN,
            )
        return
    if as_json:
        _emit(plan, True)
    else:
        typer.echo(f"roundtrip plan  kind={plan.get('kind')}  "
                   f"truth_moved={plan.get('truth_moved')}")
        typer.echo(f"  {plan.get('carrier_note')}")
        for i, r in enumerate(plan.get("rows") or [], 1):
            typer.echo(
                f"  {i}. [{r.get('state')}] {r.get('class')} → {r.get('target')}  "
                f"action={r.get('action')}"
            )


# -------------------------------------------------------------- shot-package


def _print_shot_package_plan(plan) -> None:
    s = plan["summary"]
    typer.secho(
        f"shot-import-plan  digest={plan['package_digest'][:19]}…  "
        f"safe_to_apply={plan['safe_to_apply']}", fg=typer.colors.CYAN)
    typer.echo(f"  create={s['create']}  conflicts={s['conflicts']}  "
               f"unresolved_refs={s['unresolved_refs']}  (update:{s['update']})")
    for op in plan["operations"]:
        if op["kind"] == "conflict":
            typer.secho(f"  ✗ conflict     {op['target_path']} — {op.get('reason', '')}",
                        fg=typer.colors.YELLOW)
        elif op["kind"] == "update_index":
            typer.echo(f"  · update_index {op['target_path']}  +{op['fields'].get('appends')}")
        else:
            typer.echo(f"  + create       {op['target_path']}  (draft {op['draft_id']})")
            for u in op.get("unresolved_refs") or []:
                typer.secho(f"      unresolved {u['field']}={u['ref']} — add it to the bible",
                            fg=typer.colors.YELLOW)
    for w in plan.get("warnings") or []:
        typer.secho(f"  ⚠ {w}", fg=typer.colors.BRIGHT_BLACK)


@app.command("shot-package")
def shot_package(
    file: Path = typer.Argument(..., help="external ShotDraftPackage v1 YAML "
                                          "(schema manju.shot-draft-package/v1)"),
    apply: bool = typer.Option(False, "--apply",
                               help="apply the plan (create NEW shots + append index); "
                                    "default = inspect/plan only, zero writes"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Validate + inspect an external ShotDraftPackage, or apply it under controlled write.

    The package is a PROPOSAL, never truth: ``creative_suggestions`` stay SOFT
    (they never become quality.must_show/avoid, continuity locks, routing, or a
    locked duration), and an op targeting an EXISTING shot id is a CONFLICT
    (change it via `manju propose`, never a package overwrite). Default =
    inspect (ZERO writes). ``--apply`` creates the NEW shots + updates
    ``shots/index.yaml`` under one build_lock, CAS-guarded against the reviewed
    plan, with a compensating rollback and a post-apply `manju check`; the event
    it records carries only ids + digest (no prompts, no secrets)."""
    from .build.shotpackage import (
        ShotPackageError,
        apply_shot_import_plan,
        build_shot_import_plan,
        load_package,
    )

    project = _project()
    try:
        package = load_package(file)
        plan = build_shot_import_plan(project, package)
    except ShotPackageError as exc:
        _fail(" ".join(str(exc).split())[:600], code="shot_package_invalid")
        return

    if not apply:
        if as_json:
            _emit(plan, True)
        else:
            _print_shot_package_plan(plan)
            typer.secho("(inspect — 加 --apply 才会创建镜头;未改动任何文件)",
                        fg=typer.colors.BRIGHT_BLACK)
        return

    # apply acquires its OWN build_lock (like `roundtrip`) — do NOT wrap in _write_lock.
    result = apply_shot_import_plan(project, package, actor=ACTOR, plan=plan)
    if as_json:
        _emit({"plan": plan, "apply": result}, True)
    elif result["ok"]:
        typer.secho(
            f"shot-package apply: created {', '.join(result['created']) or '(none)'} "
            f"· digest {result['package_digest'][:19]}…", fg=typer.colors.GREEN)
    else:
        _print_shot_package_plan(plan)
        detail = "; ".join(result.get("reasons") or result.get("new_errors")
                           or [result.get("error", "")])
        typer.secho(f"apply refused ({result['code']}): {detail}"[:600], fg=typer.colors.RED)
    if not result["ok"]:
        raise typer.Exit(1)


@app.command(name="pull-sheet")
def pull_sheet(
    file: Path = typer.Argument(..., help="an edited storyboard pull sheet "
                                          "(.csv or .md) exported by `manju export --pullsheet`"),
    apply: bool = typer.Option(False, "--apply",
                               help="apply the reviewed plan under CAS; "
                                    "default = inspect/plan only, zero writes"),
    as_json: bool = typer.Option(False, "--json"),
):
    """AI_IDE_16 §9 — round-trip an edited storyboard pull sheet.

    The import DIFFS the sheet against current truth and maps it onto the DR03A
    ShotDraftPackage machinery: a NEW row creates a shot, an EDITED row is a
    checked-write CAS proposal. Default = inspect (ZERO writes). ``--apply``
    applies the reviewed plan; a source that moved since inspect fails the CAS,
    and ``selected_take`` / media / locks are NEVER overwritten. ``transition``,
    ``refs``, ``frame_refs`` and ``status`` are export-only (never written back)."""
    from .build.pullsheet import (
        PullSheetError,
        apply_pull_sheet_import,
        plan_pull_sheet_import,
    )

    project = _project()
    try:
        plan = plan_pull_sheet_import(project, file)
    except PullSheetError as exc:
        _fail(" ".join(str(exc).split())[:600], code="pull_sheet_invalid")
        return

    if not apply:
        if as_json:
            _emit({k: v for k, v in plan.items() if k != "_package"}, True)
        else:
            s = plan["summary"]
            typer.secho(
                f"pull-sheet plan: {s['create']} create · {s['update']} update · "
                f"{s['unchanged']} unchanged (inspect — 加 --apply 才会写入)",
                fg=typer.colors.BRIGHT_BLACK)
        return

    result = apply_pull_sheet_import(project, plan, actor=ACTOR)
    if as_json:
        _emit({"apply": result}, True)
    elif result["ok"]:
        typer.secho(
            f"pull-sheet apply: updated {', '.join(result['applied']) or '(none)'}"
            f" · created {', '.join(result['created']) or '(none)'}",
            fg=typer.colors.GREEN)
    else:
        typer.secho(f"apply refused: {result['refused']}"[:600], fg=typer.colors.RED)
    if not result["ok"]:
        raise typer.Exit(1)


# ------------------------------------------------------------------- impact


@app.command()
def impact(
    shot_id: str = typer.Argument(..., metavar="SHOT"),
    field: Optional[str] = typer.Option(
        None, "--field", help="dotted path to edit hypothetically, e.g. dialogue.text"),
    value: Optional[str] = typer.Option(
        None, "--value", help="new value for --field (hypothetical; nothing written)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """If I change this shot, what happens? Read-only interconnection report.

    With ``--field`` + ``--value``: hypothetical impact of that edit (voice/
    video staleness, caption cues, timeline recompile, final re-render,
    export deliverables, catch-up cost). Without: current staleness impact.
    Never mutates, never spends, no lock."""
    from .build.impact import impact_report, impact_summary_zh
    from .core.container import ProjectError

    project = _project()
    try:
        report = impact_report(project, shot_id, field=field, new_value=value)
    except (ProjectError, ValueError, KeyError) as exc:
        _fail(" ".join(str(exc).split())[:500], code="impact_error")
        return
    except Exception as exc:  # pydantic ValidationError etc.
        _fail(" ".join(str(exc).split())[:500], code="impact_error")
        return
    if as_json:
        _emit(report, True)
        return
    typer.echo(impact_summary_zh(report))
    v, vo = report["video"], report["voice"]
    typer.echo(
        f"  画面: {v['state']} → {v['would_become']}"
        + (f"  fields={','.join(v['changed_fields'])}" if v.get("changed_fields") else "")
        + (f"  take={v['selected_take']}" if v.get("selected_take") else "")
    )
    typer.echo(
        f"  配音: {vo['state']} → {vo['would_become']}"
        + ("  (manual)" if vo.get("manual") else "")
        + (f"  take={vo['newest_take']}" if vo.get("newest_take") else "")
    )
    caps = report["captions"]
    n = len(caps.get("cues") or [])
    typer.echo(f"  字幕: {n} 条  mode={caps.get('mode')}")
    if caps.get("manual_note"):
        typer.secho(f"    {caps['manual_note']}", fg=typer.colors.YELLOW)
    for c in (caps.get("cues") or [])[:6]:
        typer.echo(f"    [{c['index']}] {c['start_ms']}-{c['end_ms']}ms  {c['text'][:40]}")
    typer.echo(f"  时间线: {report['timeline'].get('verdict')}")
    typer.echo(
        f"  成片: final={report['renders'].get('final')}  "
        f"proxy={report['renders'].get('proxy')}"
    )
    stale_ex = (report.get("exports") or {}).get("stale_after") or []
    if stale_ex:
        typer.echo(f"  导出将待更新: {', '.join(stale_ex)}")
    cost = report.get("cost") or {}
    typer.echo(
        f"  成本: video={cost.get('regen_video')}  voice={cost.get('regen_voice')} "
        f"{cost.get('currency') or ''}"
        + (f"  ({cost['note']})" if cost.get("note") else "")
    )


# ------------------------------------------------------------------- prompt


def _print_prompt_checks(findings: list, *, indent: str = "  ") -> None:
    """Human render of prompt-check findings (shared by `prompt` and
    `prompt --check`). Each finding on its own coloured line; a split proposal
    prints its sub-shot texts + durations (nothing auto-applies)."""
    colour = {"warning": typer.colors.YELLOW, "advisory": typer.colors.BRIGHT_BLACK}
    for f in findings:
        head = f"{indent}[{f['level']}] {f['code']}: {f['message']}"
        typer.secho(head, fg=colour.get(f["level"]))
        if f.get("suggestion"):
            typer.echo(f"{indent}  建议 suggestion: {f['suggestion']}")
        split = f.get("split")
        if split:
            typer.echo(f"{indent}  拆分建议 split ({split['reason']}):")
            for sub in split["sub_shots"]:
                text = sub["text"] or "(沿用原动作 / same action)"
                typer.echo(f"{indent}    {sub['index']}) {text}  ≈{sub['duration_ms']}ms")


@app.command()
def prompt(
    shot_id: Optional[str] = typer.Argument(None, metavar="SHOT"),
    check: bool = typer.Option(
        False, "--check",
        help="lint EVERY shot's action/prompt text; exit non-zero on warnings"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Prompt workbench (goal 7/8) — read-only.

    `manju prompt S001` shows the exact prompts the build will send (image /
    video / director / negative), the resolved references with tier lineage, the
    routed provider and why, the pre-flight cost, and the single-action checks.
    `--json` emits the whole bundle. `manju prompt --check` runs the single-action
    checks over every shot and exits non-zero when any warning is found (a
    lint gate; nothing is spent, nothing is mutated)."""
    project = _project()

    if check:
        from .qc.prompt_checks import check_all, has_blocking

        findings = check_all(project)
        if as_json:
            _emit({"findings": findings, "ok": not has_blocking(findings)}, True)
        else:
            if not findings:
                typer.secho("prompt check: 没有发现问题 (all shots clean)",
                            fg=typer.colors.GREEN)
            else:
                by_shot: dict[str, list] = {}
                for f in findings:
                    by_shot.setdefault(f["shot"], []).append(f)
                for sid, items in by_shot.items():
                    typer.secho(f"{sid}", fg=typer.colors.CYAN)
                    _print_prompt_checks(items)
                warns = sum(1 for f in findings if f["level"] == "warning")
                typer.secho(f"prompt check: {warns} 处警告 / {len(findings)} 项发现",
                            fg=typer.colors.YELLOW if warns else typer.colors.GREEN)
        if has_blocking(findings):
            raise typer.Exit(1)
        return

    if shot_id is None:
        _fail("prompt: 要看哪个镜头的提示词?给一个镜头 id(`manju prompt S001`),"
              "或用 --check 一次性 lint 所有镜头。")
    from .build.promptlab import shot_prompt_bundle

    try:
        bundle = shot_prompt_bundle(project, shot_id)
    except ProjectError as exc:
        _fail(str(exc))
    if as_json:
        _emit(bundle, True)
        return

    typer.secho(f"镜头 {bundle['shot']}  spec_hash={bundle['spec_hash'][:16]}…",
                fg=typer.colors.CYAN)
    for label, key in (("图像 image", "image_prompt"), ("视频 video", "video_prompt"),
                       ("导演 director", "director_prompt"), ("负向 negative", "negative_prompt")):
        text = bundle[key]
        typer.secho(f"—— {label} prompt ——", fg=typer.colors.BRIGHT_BLACK)
        typer.echo(text if text else "(空 / empty)")
    refs = bundle["references"]
    typer.secho("—— 参考 references ——", fg=typer.colors.BRIGHT_BLACK)
    if refs["items"]:
        for it in refs["items"]:
            flag = "" if it["exists"] else " (缺失 missing)"
            loc = it["ref"] if it["is_url"] else (it["path"] or it["ref"])
            typer.echo(f"  [{it['kind']}] {loc}  ← tier {it['tier']}{flag}")
    else:
        typer.echo("  (无 / none)")
    prov = bundle["provider"]
    typer.secho("—— 供应商 provider ——", fg=typer.colors.BRIGHT_BLACK)
    typer.echo(f"  选中 chosen: {prov.get('label')}  (why={prov.get('why')})")
    typer.echo(f"  顺序 order: {' → '.join(prov.get('order') or []) or '(none)'}")
    typer.echo(f"  回退链 fallback: {' → '.join(prov.get('fallback_chain') or []) or '(none)'}")
    if prov.get("routing_error"):
        typer.secho(f"  ⚠ routing.yaml: {prov['routing_error']}", fg=typer.colors.YELLOW)
    cost = bundle["cost"]
    typer.secho("—— 预估 cost ——", fg=typer.colors.BRIGHT_BLACK)
    typer.echo(f"  ≈ {cost['estimated_cost']} {cost['currency'] or ''} "
               f"({cost['duration_ms']}ms)".rstrip())
    typer.secho("—— 检查 checks ——", fg=typer.colors.BRIGHT_BLACK)
    if bundle["checks"]:
        _print_prompt_checks(bundle["checks"])
    else:
        typer.secho("  ✓ 单动作检查通过 (single-action checks clean)", fg=typer.colors.GREEN)


# ------------------------------------------------------------------- voice


@app.command()
def voice(
    shot_id: Optional[str] = typer.Argument(None),
    shots: Optional[str] = typer.Option(
        None, "--shots", help="batch: comma-separated shot ids (S001,S003)"),
    all_shots: bool = typer.Option(
        False, "--all", help="batch: voice every dialogued shot that is MISSING"),
    missing: bool = typer.Option(
        False, "--missing", help="batch: voice every shot with dialogue but no voice take"),
    provider: Optional[str] = typer.Option(None, help="tts manifest id (default: first configured)"),
    preview: bool = typer.Option(
        False, "--preview",
        help="试听: synthesize a disposable sample into .manju/webpreview/tts/ "
             "(never a take; cache-keyed by text+voice — WP2/R19)"),
    text: Optional[str] = typer.Option(
        None, "--text", help="--preview: override the dialogue text for this sample"),
    lang: Optional[str] = typer.Option(
        None, "--lang",
        help="WP4 locale overlay: synthesize into media/gen/<shot>/locales/<lang>/ "
             "using locales/<lang>/lines.yaml text (picture pipeline shared)"),
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="approve ask_before-gated spend (§8.3)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Synthesize a NEW voice take (M3, append-only — the newest take wins on the
    next build). Use this to redo a stale voice (§4.3: builds flag stale voices
    but never redo them on their own).

    One shot: `manju voice S002`. Batch: `--missing` (every shot needing voice),
    `--shots S001,S003` (explicit — the escape hatch for re-voicing a stale
    line), `--all` (every dialogued shot, but only the MISSING ones run; stale
    voices stay advisory-only, §4.3). One build lock + one aggregated spend gate.

    `--preview` (WP2): 试听 — write a throwaway sample under
    ``.manju/webpreview/tts/``; second call is a cache hit. Never creates a
    file under ``media/gen/``.

    `--lang en` (WP4): voice the locale overlay text into a per-locale take
    directory; base project picture/takes stay untouched.

    A priced TTS synthesis stops as waiting_user unless --yes — the same §8.3
    ask_before gate build and redo enforce (R7 spend-gate hole closure)."""
    from .build.graph import BuildError, WaitingUser, spend_gate, voice_batch
    from .providers.tts import TtsUnavailable, get_tts_provider

    project = _project()

    if preview:
        if shot_id is None:
            _fail("voice --preview: 要试听哪个镜头?给一个镜头 id")
        from .media.ttspreview import PreviewUnavailable, preview_voice

        try:
            info = preview_voice(
                project, shot_id, text=text, provider=provider, assume_yes=yes,
            )
        except WaitingUser as exc:
            _fail(str(exc), code="waiting_user")
            return
        except PreviewUnavailable as exc:
            _fail(str(exc), code="tts_unavailable")
            return
        if as_json:
            _emit({"preview": info["preview"], "cached": info["cached"],
                   "provider": info.get("provider")}, True)
        else:
            tag = "缓存命中" if info["cached"] else "新合成"
            typer.secho(f"{shot_id}: 试听 {info['preview']} ({tag})",
                        fg=typer.colors.GREEN)
        return

    batch_flags = [bool(shots), all_shots, missing]
    if any(batch_flags):
        if shot_id is not None:
            _fail("voice: a positional shot id is mutually exclusive with --shots/--all/--missing")
        if sum(batch_flags) > 1:
            _fail("voice: --shots/--all/--missing are mutually exclusive")
        if lang:
            # WP4: locale batch = plan_locale_voice + synthesize_locale_voices
            from .build.graph import WaitingUser as _WU, spend_gate as _sg
            from .build.locale_build import plan_locale_voice, synthesize_locale_voices

            plan = plan_locale_voice(project, lang, gen="missing")
            if shots:
                want = {s.strip() for s in shots.split(",") if s.strip()}
                plan = [p for p in plan if p["shot"] in want]
            if not plan:
                if as_json:
                    _emit({"lang": lang, "ran": [], "note": "nothing missing"}, True)
                else:
                    typer.echo(f"locale {lang}: 没有缺失的配音")
                return
            total = sum(float(p.get("estimated_cost") or 0) for p in plan)
            cur = next((p.get("currency") for p in plan if p.get("currency")), "CNY")
            try:
                if total > 0:
                    _sg(project, total, cur, assume_yes=yes,
                        hint=f"确认后重试: manju voice --missing --lang {lang} --yes")
                gen = synthesize_locale_voices(
                    project, plan, lang=lang, actor=ACTOR,
                )
            except _WU as exc:
                _fail(str(exc))
                return
            except Exception as exc:
                _fail(" ".join(str(exc).split())[:500])
                return
            if as_json:
                _emit({"lang": lang, "ran": gen, "plan": plan}, True)
            else:
                typer.secho(
                    f"locale {lang}: 合成 {len(gen)} 条 — {', '.join(gen) or '(none)'}",
                    fg=typer.colors.GREEN,
                )
            return
        shot_ids = [s.strip() for s in shots.split(",") if s.strip()] if shots else None
        try:
            result = voice_batch(project, shots=shot_ids, all_shots=all_shots,
                                 missing=missing, provider=provider,
                                 actor=ACTOR, assume_yes=yes)
        except (BuildError, WaitingUser) as exc:
            _fail(str(exc))
        if as_json:
            _emit(result.to_dict(), True)
        else:
            _print_batch_result(result, "voice")
        return

    if shot_id is None:
        _fail("voice: 要给哪个镜头配音?给一个镜头 id(`manju voice S002`),"
              "或用批量选择器 (--missing / --all / --shots S001,S003)。")
    shot = project.load_shot(shot_id)
    if lang:
        from .core.locale import load_lines, overlay_shot_for_voice

        lines = load_lines(project, lang)
        entry = lines.get(shot_id) or {}
        if not str(entry.get("text") or "").strip():
            _fail(f"{shot_id}: locale {lang} 无译文 — 先填 "
                  f"locales/{lang}/lines.yaml 的 text,或 manju locale status {lang}")
        shot = overlay_shot_for_voice(project, shot, lang)
    if not shot.dialogue.text:
        _fail(f"{shot_id} has no dialogue.text to voice — 该镜头没有台词,配音无从下手。"
              f"在 shots/{shot_id}.yaml 里写 dialogue.text,再运行 `manju voice {shot_id}`。")
    try:
        tts = get_tts_provider(provider)
        manifest = getattr(tts, "manifest", None)
        cost = getattr(manifest, "cost", None) if manifest is not None else None
        if cost is not None:
            spend_gate(project, cost.per_call, cost.currency, assume_yes=yes,
                       hint=f"确认后重试:manju voice {shot_id} --yes"
                            + (f" --lang {lang}" if lang else ""))
        # WP4: inject lang into register_voice_take without forking providers
        if lang:
            _orig_reg = project.register_voice_take

            def _reg_lang(sid, media_file, sidecar, **kw):
                kw.setdefault("lang", lang)
                return _orig_reg(sid, media_file, sidecar, **kw)

            project.register_voice_take = _reg_lang  # type: ignore[method-assign]
            try:
                media = tts.synthesize(project, shot, project.load_bible())
            finally:
                project.register_voice_take = _orig_reg  # type: ignore[method-assign]
        else:
            media = tts.synthesize(project, shot, project.load_bible())
    except (TtsUnavailable, WaitingUser) as exc:
        _fail(str(exc))
    append_event(project.root, ACTOR, "voice",
                 {"shot": shot_id, "take": media.stem, "provider": tts.id})
    if as_json:
        _emit({"shot": shot_id, "take": media.stem,
               "media": project.relpath(media)}, True)
    else:
        typer.secho(f"{shot_id}: 新配音 {media.stem} ({tts.id})", fg=typer.colors.GREEN)


# ------------------------------------------------------------------- align


@app.command()
def align(
    shot_id: Optional[str] = typer.Argument(
        None, metavar="SHOT",
        help="single-shot: align this shot's newest voice take"),
    take: Optional[str] = typer.Option(
        None, "--take", help="voice take stem (default: newest)"),
    from_srt: Optional[Path] = typer.Option(
        None, "--from-srt", help="cue timings from a hand-made SRT"),
    asr: Optional[str] = typer.Option(
        None, "--asr",
        help="single-shot only: ASR provider id (spend-gated). "
             "Multi-shot: use manju transcribe first, then --from-srt"),
    media: Optional[Path] = typer.Option(
        None, "--media", help="multi-shot: long VO file to split"),
    shots: Optional[str] = typer.Option(
        None, "--shots", help="multi-shot: S001-S012 or S001,S002,S003"),
    apply: bool = typer.Option(
        False, "--apply", help="multi-shot: apply the plan (default is plan-only)"),
    rows: Optional[str] = typer.Option(
        None, "--rows", help="multi-shot apply: 1-based row indices e.g. 1,3-5"),
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="approve ASR spend gate (§8.3)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Align imported human voiceover to script (WP3).

    Single shot: ``manju align S001`` writes ``<take>.timing.json`` (regenerable)
    via free text-anchoring; ``--from-srt`` / ``--asr`` for real timings.

    Multi-shot: ``manju align --media vo.wav --shots S001-S012 [--from-srt x.srt]``
    plans windows; ``--apply`` slices + registers MANUAL voice takes + batch
    record under reports/ingest_batches/.

    Multi-shot ``--asr`` is intentionally NOT inlined (reproducibility):
    run ``manju transcribe`` first, then pass the SRT via ``--from-srt``.
    Single-shot ``manju align S001 --asr`` still runs ASR directly."""
    from .core.container import ProjectError
    from .media.align import align_shot, apply_multi_shot, plan_multi_shot

    project = _project()

    def _expand_shots(spec: str) -> list[str]:
        out: list[str] = []
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            m = re.match(r"([A-Za-z]*)(\d+)\s*-\s*([A-Za-z]*)(\d+)$", part)
            if m:
                pre, a, pre2, b = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
                prefix = pre or pre2 or "S"
                width = max(len(m.group(2)), len(m.group(4)))
                for n in range(min(a, b), max(a, b) + 1):
                    out.append(f"{prefix}{n:0{width}d}")
            else:
                out.append(part)
        return out

    if media is not None:
        if not shots:
            _fail("align --media 需要 --shots S001-S012")
        ids = _expand_shots(shots)
        try:
            plan = plan_multi_shot(
                project, media, ids,
                from_srt=from_srt,
                asr=asr if asr is not None else None,
                assume_yes=yes,
            )
        except ProjectError as exc:
            _fail(str(exc))
            return
        if apply:
            sel = None
            if rows:
                sel = []
                for part in rows.split(","):
                    part = part.strip()
                    if "-" in part:
                        a, b = part.split("-", 1)
                        sel.extend(range(int(a) - 1, int(b)))
                    else:
                        sel.append(int(part) - 1)
            try:
                result = apply_multi_shot(project, plan, rows=sel, actor=ACTOR)
            except Exception as exc:
                _fail(" ".join(str(exc).split())[:500])
                return
            if as_json:
                _emit({"plan": plan, "apply": result}, True)
            else:
                typer.secho(
                    f"align apply: {len(result['applied'])} 条, "
                    f"跳过 {len(result['skipped'])}, batch={result['batch']}",
                    fg=typer.colors.GREEN,
                )
            return
        if as_json:
            _emit(plan, True)
        else:
            typer.echo(f"align plan  media={plan.get('media')}  "
                       f"duration={plan.get('duration_ms')}ms  source={plan.get('source')}")
            for i, r in enumerate(plan.get("rows") or [], 1):
                typer.echo(
                    f"  {i}. {r['shot']}  [{r.get('start_ms')}-{r.get('end_ms')}]ms  "
                    f"conf={r.get('confidence')}  state={r.get('state')}  "
                    f"{(r.get('matched_text') or '')[:30]}"
                )
            if plan.get("unmatched"):
                typer.secho(f"  unmatched: {', '.join(plan['unmatched'])}",
                            fg=typer.colors.YELLOW)
        return

    if shot_id is None:
        _fail("align: 给一个镜头 id,或用 --media + --shots 做多镜头切分")
    asr_flag = asr
    # bare `--asr` without value may come through as empty string from typer
    if asr is not None and asr == "":
        asr_flag = "default"
    try:
        report = align_shot(
            project, shot_id, take=take, from_srt=from_srt,
            asr=asr_flag, assume_yes=yes,
        )
    except ProjectError as exc:
        _fail(str(exc))
        return
    if as_json:
        _emit(report, True)
    else:
        typer.secho(
            f"{report['shot']}: timing → {report['timing']}  "
            f"({report['cues']} cues, source={report['source']})",
            fg=typer.colors.GREEN,
        )
        for a in report.get("advisories") or []:
            typer.secho(f"  ⚠ {a}", fg=typer.colors.YELLOW)


# --------------------------------------------------------------- masters


@app.command()
def masters(
    profile: str = typer.Option(
        "master", "--profile",
        help="AI_IDE_18 WP7: delivery profile id (project.yaml delivery_profiles). "
             "Its loudness_target_lufs / true_peak_target_dbtp drive the "
             "normalised master — targets are the PROFILE's, never hardcoded"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Render the professional audio masters (AI_IDE_18 WP7): DIALOGUE / MUSIC /
    SFX stems, FULL MIX and the international M&E master — all REAL ffmpeg renders
    off the current timeline's four audio buses, with measured EBU R128 loudness.
    They fill the 13C DeliveryManifest audio roles through the export centre."""
    from .build import delivery as _delivery
    from .build import exportstatus as _es
    from .media import masters as _masters

    project = _project()
    ctx = _es._gather(project)
    if ctx.timeline is None:
        _fail("没有可渲染的时间线(先 manju build 或提供 manual timeline.json)")
        return
    config = _es._safe(lambda: project.load_config())
    profiles = _delivery._delivery_profiles(config) if config is not None else {}
    prof = dict(profiles.get(profile) or {})
    target = prof.get("loudness_target_lufs")
    tp = prof.get("true_peak_target_dbtp")
    try:
        index = _masters.render_masters(
            project, ctx.timeline,
            loudness_target_lufs=float(target) if target is not None else None,
            true_peak_target_dbtp=float(tp) if tp is not None else None)
    except Exception as exc:
        _fail(" ".join(str(exc).split())[:500])
        return
    if as_json:
        _emit(index, True)
    else:
        typer.secho(f"audio masters → exports/masters/ (profile={profile})",
                    fg=typer.colors.GREEN)
        for a in index["artifacts"]:
            loud = a["loudness"]
            typer.echo(f"  {a['role']:16} {a['path']}  "
                       f"I={loud['integrated_lufs']} LUFS  TP={loud['true_peak_dbtp']} dBTP")
        if index.get("loudnorm_master"):
            ln = index["loudnorm_master"]
            typer.echo(f"  normalised → {ln['path']} (target {ln['target_lufs']} LUFS)")


# -------------------------------------------------------------- transcribe


@app.command()
def transcribe(
    media: Path,
    provider: Optional[str] = typer.Option(None, help="asr manifest id (default: first configured)"),
    from_srt: Optional[Path] = typer.Option(None, "--from-srt", help="manual input: normalize your own SRT"),
    text: Optional[str] = typer.Option(None, "--text", help="manual input: plain transcript, auto-timed over the media"),
    out: Optional[Path] = typer.Option(None, help="output SRT (default captions/transcripts/<name>.srt)"),
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="approve ask_before-gated spend (§8.3)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Transcribe imported real footage into an SRT (M4 ASR slot, on demand).

    Three equal on-ramps: a configured cloud ASR manifest (§8.6, type: asr),
    --from-srt with human-made subtitles, or --text with the raw transcript
    (distributed over the media duration). The scripted-drama main line never
    needs this — its captions come from dialogue + TTS alignment.

    ``media`` must resolve INSIDE the project (§8.2 containment, R goal 52):
    an absolute or ``../``-escaping path is refused — cloud ASR uploads the
    file's bytes to an external provider, so a path outside the project could
    otherwise exfiltrate local files the project never claimed. Bring outside
    footage in first with `manju import <file>`.

    A priced cloud ASR call stops as waiting_user unless --yes — the same
    §8.3 ask_before gate build/redo/voice enforce (goal 52 spend-gate hole
    closure); the manual --from-srt/--text on-ramps are always free."""
    from .build.graph import WaitingUser, spend_gate
    from .providers.asr import (
        AsrUnavailable,
        distribute_text,
        get_asr_provider,
        parse_srt,
        segments_to_srt,
    )

    project = _project()
    try:
        media_abs = project.resolve(media)
    except ProjectError:
        _fail(f"{media}: 路径在项目外(§8.2 containment)— transcribe 只接受项目内素材,"
              f"云 ASR 会把文件内容上传给外部 provider。先 `manju import {media}` "
              "把素材带进项目(media/imports/),再对项目内路径跑 transcribe。")
    if not media_abs.exists():
        _fail(f"media not found: {media}")
    if from_srt and text:
        _fail("pick one input: --from-srt or --text (or neither, for cloud ASR)")

    if from_srt:
        if not from_srt.exists():
            _fail(f"not found: {from_srt}")
        segments = parse_srt(from_srt.read_text(encoding="utf-8"))
        source = "manual_srt"
    elif text is not None:
        from .media.probe import probe_duration_ms

        duration = probe_duration_ms(media_abs)
        if not duration:
            _fail(f"cannot probe media duration for --text timing: {media}")
        segments = distribute_text(text, duration)
        source = "manual_text"
    else:
        try:
            asr = get_asr_provider(provider)
        except AsrUnavailable as exc:
            _fail(str(exc))
        cost = asr.manifest.cost
        try:
            spend_gate(project, cost.per_call, cost.currency, assume_yes=yes,
                      hint=f"确认后重试:manju transcribe {media} --yes")
        except WaitingUser as exc:
            _fail(str(exc))
        segments = asr.transcribe(media_abs)
        source = asr.id

    if not segments:
        _fail("no transcript segments produced")
    out = out or project.captions_dir / "transcripts" / (media_abs.stem + ".srt")
    from .core.yamlio import atomic_write_text

    atomic_write_text(out, segments_to_srt(segments))
    rel = project.relpath(out) if out.is_relative_to(project.root) else str(out)
    append_event(project.root, ACTOR, "transcribe",
                 {"media": str(media), "source": source, "segments": len(segments),
                  "out": rel})
    if as_json:
        _emit({"out": rel, "source": source, "segments": len(segments)}, True)
    else:
        typer.secho(f"{rel}: {len(segments)} segments (source: {source})",
                    fg=typer.colors.GREEN)


# ------------------------------------------------------------------- board

# `board` is a command GROUP: the bare `manju board` (and `--serve`) keep the
# review workbench (§1-⑦), while `board scene` / `board keyframes` add the
# multi-image storyboard + keyframe scaffolding of round-U (goal item 12).
board_app = typer.Typer(
    invoke_without_command=True,
    help="Review workbench + multi-image storyboards (round-U goal item 12).",
)
app.add_typer(board_app, name="board")


@board_app.callback(invoke_without_command=True)
def board(
    ctx: typer.Context,
    serve: bool = typer.Option(False, "--serve/--no-serve",
                               help="serve an actionable local workspace instead of writing board.html"),
    port: int = typer.Option(8787, "--port", help="serve port (--serve)"),
    host: str = typer.Option("127.0.0.1", "--host", help="serve host (--serve; localhost only)"),
    open_browser: bool = typer.Option(True, "--open/--no-open",
                                      help="open the board in a browser after binding (--serve)"),
):
    """Review board — the director's workbench (§1-⑦).

    Bare ``manju board`` writes a static, self-contained ``board.html``; with
    ``--serve`` it becomes a live, ACTIONABLE workspace on localhost (select
    takes, redo/rollback, build/qc/package/snapshot — a thin veneer over the same
    core the CLI calls, unlock/gc/pack stay off this surface, like MCP §11).
    Subcommands compose storyboards: ``board scene`` / ``board keyframes``."""
    if ctx.invoked_subcommand is not None:
        return  # dispatch to `scene` / `keyframes`
    project = _project()
    if not serve:
        from .board.board import generate_board

        path = generate_board(project)
        typer.secho(f"board: {project.relpath(path)}", fg=typer.colors.GREEN)
        return

    import webbrowser

    from .board.server import make_server

    server = make_server(project, host=host, port=port)
    url = f"http://{host}:{server.server_address[1]}/"
    typer.secho(f"manju board 工作台: {url}  (Ctrl-C 退出)", fg=typer.colors.GREEN)
    if open_browser:
        try:  # best-effort; headless / no-browser must never crash the server
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("stopping…")
    finally:
        server.server_close()


@board_app.command("scene")
def board_scene(
    scene: str = typer.Argument(..., help="scene id (bible/scenes.yaml) to storyboard"),
    grid: int = typer.Option(4, "--grid", help="4 (2×2, 4-panel) or 9 (3×3, 9-panel)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Compose a multi-panel storyboard of a scene's shots (goal item 12).

    Each panel is a shot's best frame — a declared reference image, else a
    mid-frame of its newest take — captioned with the shot id, tiled 2×2 or 3×3.
    Cached under .manju/frames (content-addressed by the input frames + grid),
    so an unchanged scene returns the same file for free."""
    project = _project()
    if grid not in (4, 9):
        _fail("--grid must be 4 or 9")
    from .media.boards import scene_board
    from .media.ffmpeg import MediaError

    try:
        out = scene_board(project, scene, grid=grid)
    except MediaError as exc:
        _record_failure(project, "board", scene, "storyboard 合成失败",
                        evidence=" ".join(str(exc).split())[:600])
        _fail(str(exc))
    rel = project.relpath(out)
    append_event(project.root, ACTOR, "board_scene", {"scene": scene, "grid": grid, "output": rel})
    if as_json:
        _emit({"scene": scene, "grid": grid, "board": rel, "ok": True}, True)
    else:
        typer.secho(f"board: {rel}", fg=typer.colors.GREEN)


@board_app.command("keyframes")
def board_keyframes(
    shot: str = typer.Argument(..., help="shot id whose action to break into keyframes"),
    n: int = typer.Option(4, "--n", help="number of keyframe beats to suggest"),
    scaffold: bool = typer.Option(
        False, "--scaffold",
        help="WRITE the suggested keyframes into the shot spec (else just print)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Break a shot's action into a keyframe sequence (goal item 12, part D).

    Splits the shot's action (deterministically, at Chinese/English clause
    boundaries) into ``--n`` beats mapped to start / mid… / end keyframes.
    Without ``--scaffold`` it only PRINTS the suggestion (中文); with
    ``--scaffold`` it writes them into ``shots/<id>.yaml`` via the normal spec
    write path, respecting locks (a sealed ``keyframes`` field is refused)."""
    project = _project()
    try:
        sh = project.load_shot(shot)
    except ProjectError as exc:
        _fail(str(exc))
    from .media.boards import (
        KeyframeScaffoldError,
        beats_to_keyframes,
        breakdown_action,
        scaffold_keyframes,
    )

    text = (sh.action.main or "").strip() or (sh.dialogue.text or "").strip()
    beats = breakdown_action(text, n)

    if not scaffold:
        kfs = beats_to_keyframes(beats)
        if as_json:
            _emit({"shot": shot, "n": len(beats), "keyframes": kfs, "scaffolded": False}, True)
        else:
            typer.secho(
                f"镜头 {shot} 动作拆解建议(共 {len(beats)} 拍;加 --scaffold 写入 keyframes):",
                fg=typer.colors.CYAN,
            )
            for i, kf in enumerate(kfs, 1):
                typer.echo(f"  {i}. [{kf['position']}] {kf['prompt']}")
        return

    try:
        kfs = scaffold_keyframes(project, shot, beats)
    except KeyframeScaffoldError as exc:
        _fail(str(exc))
    append_event(project.root, ACTOR, "board_keyframes",
                 {"shot": shot, "n": len(beats), "scaffolded": True})
    if as_json:
        _emit({"shot": shot, "n": len(beats), "keyframes": kfs, "scaffolded": True}, True)
    else:
        typer.secho(f"{shot}: 已写入 {len(kfs)} 个关键帧到 keyframes(可 manju check 复核)",
                    fg=typer.colors.GREEN)


@app.command()
def gui(
    host: str = typer.Option("127.0.0.1", help="bind address (non-local hosts print a warning)"),
    port: int = typer.Option(8321, help="port (0 = pick a free one)"),
    open_browser: bool = typer.Option(True, "--open/--no-open",
                                      help="open the page in the default browser"),
    readonly: bool = typer.Option(False, "--readonly",
                                  help="review-only: every mutating request is refused"),
    workspace: Optional[Path] = typer.Option(
        None, "--workspace",
        help="serve every *.manju project under this directory (switchable)"),
):
    """Local web workbench (§1-⑦ revisited) — a client of the SAME engine core
    as the CLI/MCP: truth stays in text files, mutations are serialized jobs,
    and dangerous ops (unlock, gc --hard) are absent, exactly as on MCP.

    Round X (agent XE, user pain #6/#8): OUTSIDE a project (and without
    ``--workspace``) this no longer fails — it serves a WORKSPACE PICKER
    instead (recents list with per-project status, 按路径打开/新建项目 forms).
    Opening a project from the picker rebinds this SAME server to it."""
    from .gui.server import create_server, discover_workspace

    projects: Optional[dict] = None
    project: Optional[Project] = None
    if workspace is not None:
        projects = discover_workspace(workspace)
        if not projects:
            _fail(f"no manju projects found under {workspace}")
        project = next(iter(projects.values()))
        typer.echo("workspace: " + ", ".join(
            f"{slug} ({p.root.name})" for slug, p in projects.items()))
    else:
        try:
            project = Project.find(Path.cwd())
            _touch_recents_once(project)
        except ProjectError:
            project = None
            typer.secho(
                "未在项目目录下 (not inside a project) — 提供工作区选择器 "
                "(serving the workspace picker):从最近项目中选,或按路径/新建打开。",
                fg=typer.colors.YELLOW)
    if host not in ("127.0.0.1", "localhost", "::1"):
        typer.secho(f"⚠ binding non-local host {host} — the GUI has no auth beyond "
                    "its CSRF token; only do this on a trusted network"
                    + ("" if readonly else " (consider --readonly for review-only sharing)"),
                    fg=typer.colors.YELLOW)
    try:
        server = create_server(project, host=host, port=port, actor=ACTOR,
                               readonly=readonly, workspace=projects)
    except OSError as exc:
        _fail(f"cannot bind {host}:{port} — {exc} (try --port 0 for a free port)")
    typer.secho(f"manju gui → {server.url}  (Ctrl-C to stop)", fg=typer.colors.GREEN)
    if open_browser:
        import threading
        import webbrowser

        threading.Timer(0.4, lambda: webbrowser.open(server.url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("\nbye")
    finally:
        server.close()


# -------------------------------------------------------------- pack/unpack

PACK_EXCLUDE = (".manju/", ".git/")
# Rebuildable render caches (§3): a `manju build` reproduces them from truth, so
# they are excluded by default — they can dwarf the truth+imports payload. --full
# keeps them.
PACK_CACHE = ("renders/segments/", "renders/proxy/")

# ------------------------------------------------------------- archive fixity
# FP roadmap §7.8 (narrow): `manju pack` records a content-fixity manifest as
# ONE extra zip member so `unpack` (and `manju fixity`) can prove the restored
# bytes are exactly what was packed. The manifest is a SELF-DESCRIBING document
# member — a PLAIN "manju-fixity.1" format string, deliberately NOT a
# CONTRACTS manju.*/vN registered schema (it never leaves the zip). Each file's
# ``sha256`` is the BARE 64-hex digest (the field name already names the algo).
# Additive by construction: an older .manjupkg with no manifest unpacks exactly
# as before.
FIXITY_MANIFEST_NAME = "MANJU_FIXITY.json"
FIXITY_FORMAT = "manju-fixity.1"


def _fixity_manifest_bytes(files_map: dict[str, dict]) -> bytes:
    """Serialize the in-zip fixity manifest. Deterministic for an unchanged
    tree: sorted keys, no wall-clock timestamps in the content (the manju
    version is the only non-tree input, and it is stable within a release)."""
    from .core.toolchain import _manju_version

    manifest = {
        "format": FIXITY_FORMAT,
        "manju_version": _manju_version(),
        "files": files_map,
        "totals": {
            "count": len(files_map),
            "bytes": sum(f["bytes"] for f in files_map.values()),
        },
    }
    return (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _fixity_zipinfo() -> zipfile.ZipInfo:
    """ZipInfo for the manifest member. A FIXED 1980-01-01 stamp (zip's minimum)
    — the manifest is synthesized at pack time and has no source mtime, so a
    wall-clock stamp (writestr's default) would inject needless nondeterminism.
    (The other members keep their real file mtimes, as the writer always has.)"""
    info = zipfile.ZipInfo(FIXITY_MANIFEST_NAME, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _parse_fixity_manifest(raw: bytes) -> Optional[dict]:
    """Parse + validate a manifest member. Returns None when it is not a
    recognizable ``manju-fixity.1`` document (unparseable, or a foreign/newer
    format) — callers treat that as 'cannot verify', never as a failure of the
    payload itself."""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(doc, dict) or doc.get("format") != FIXITY_FORMAT:
        return None
    if not isinstance(doc.get("files"), dict):
        return None
    return doc


def _verify_fixity_rows(manifest: dict, present: dict[str, tuple[str, int]]) -> dict:
    """Compare a parsed manifest against the ACTUAL members. ``present`` maps
    relpath -> (bare-hex sha256, size) for every real member (the manifest
    member itself already excluded by the caller). Structured rows:
    mismatched / missing / extra, plus overall ``ok`` and a checked count."""
    declared: dict = manifest.get("files") or {}
    mismatched: list[dict] = []
    missing: list[dict] = []
    extra: list[dict] = []
    for rel in sorted(declared):
        spec = declared[rel] or {}
        want_sha = str(spec.get("sha256") or "")
        want_bytes = spec.get("bytes")
        got = present.get(rel)
        if got is None:
            missing.append({"path": rel, "expected_sha256": want_sha,
                            "expected_bytes": want_bytes})
            continue
        got_sha, got_bytes = got
        if got_sha != want_sha or got_bytes != want_bytes:
            mismatched.append({"path": rel, "expected_sha256": want_sha,
                               "actual_sha256": got_sha,
                               "expected_bytes": want_bytes,
                               "actual_bytes": got_bytes})
    for rel in sorted(present):
        if rel not in declared:
            got_sha, got_bytes = present[rel]
            extra.append({"path": rel, "actual_sha256": got_sha,
                          "actual_bytes": got_bytes})
    return {
        "ok": not (mismatched or missing or extra),
        "format": manifest.get("format"),
        "manju_version": manifest.get("manju_version"),
        "checked": len(declared),
        "mismatched": mismatched,
        "missing": missing,
        "extra": extra,
    }


def _present_from_dir(root: Path) -> dict[str, tuple[str, int]]:
    """(bare-hex sha256, size) for every extracted file under ``root``, minus
    the manifest itself and the ``.manju`` runtime dir (created post-extract)."""
    present: dict[str, tuple[str, int]] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel == FIXITY_MANIFEST_NAME or rel == ".manju" or rel.startswith(".manju/"):
            continue
        present[rel] = (hash_file(p).removeprefix(HASH_PREFIX), p.stat().st_size)
    return present


def _present_from_zip_stream(zf: zipfile.ZipFile) -> dict[str, tuple[str, int]]:
    """(bare-hex sha256, size) for every member, computed by STREAMING each
    member (bounded memory — never materialize a whole member), for the
    verify-without-extract path. Directory entries and the manifest are
    skipped."""
    import hashlib

    present: dict[str, tuple[str, int]] = {}
    for info in zf.infolist():
        nm = info.filename
        if nm == FIXITY_MANIFEST_NAME or nm.endswith("/"):
            continue
        h = hashlib.sha256()
        size = 0
        with zf.open(info) as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                size += len(chunk)
                h.update(chunk)
        present[nm] = (h.hexdigest(), size)
    return present


def _remove_restored_dest(dest: Path) -> bool:
    """Remove a freshly-created restore dir after a fixity FAILURE, so a corrupt
    restore can never masquerade as a good one. Guarded: `unpack` refuses when
    ``dest`` pre-exists, so ``dest`` here is always the tree THIS invocation just
    extracted. Best-effort; returns True iff ``dest`` is gone afterward."""
    try:
        if dest.is_dir():
            shutil.rmtree(dest)
    except OSError:
        pass
    return not dest.exists()


def _print_fixity_rows(result: dict) -> None:
    for row in result.get("mismatched") or []:
        typer.secho(f"  ✗ mismatch  {row['path']}", fg=typer.colors.RED)
    for row in result.get("missing") or []:
        typer.secho(f"  ✗ missing   {row['path']}", fg=typer.colors.RED)
    for row in result.get("extra") or []:
        typer.secho(f"  ✗ extra     {row['path']}", fg=typer.colors.RED)


@app.command()
def pack(out: Optional[Path] = typer.Option(None),
         full: bool = typer.Option(False, "--full",
                                   help="include the rebuildable render caches too"),
         as_json: bool = typer.Option(False, "--json")):
    """Archive the project into a single .manjupkg (zip) for backup/migration.

    FIX-E: the original project directory name rides in the zip comment, so
    the archive file can be renamed freely without losing the identity.
    The segment/proxy caches are rebuildable (§3) and excluded by default —
    they can dwarf the truth+imports payload; --full keeps them.

    goal item 14: a symlink is NEVER followed — ``path.is_file()`` (used
    below) is true for a symlink to a file too, and a naive ``zf.write``
    would read the TARGET's bytes and store them under the safe-looking
    in-archive name, smuggling an outside-project file into the package.
    Symlinks are skipped with a warning instead (the zip writer here has no
    portable way to store a real symlink entry)."""
    project = _project()
    out = out or project.root.parent / (project.root.stem + ".manjupkg")
    excluded_cache = 0
    skipped_symlinks: list[str] = []
    files_packed = 0
    fixity_files: dict[str, dict] = {}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.comment = json.dumps(
            {"manjupkg": 1, "name": project.root.name}, ensure_ascii=False
        ).encode("utf-8")
        for path in sorted(project.root.rglob("*")):
            if path.is_symlink():
                if path.is_file():  # only files matter for the pack payload
                    skipped_symlinks.append(path.relative_to(project.root).as_posix())
                continue
            if not path.is_file():
                continue
            rel = path.relative_to(project.root).as_posix()
            # never let a stray on-disk MANJU_FIXITY.json ride as a payload
            # member — it would collide with the manifest we write below and
            # break self-exclusion (normal project trees never contain one).
            if rel == FIXITY_MANIFEST_NAME:
                continue
            if any(rel.startswith(prefix) for prefix in PACK_EXCLUDE):
                continue
            if not full and any(rel.startswith(prefix) for prefix in PACK_CACHE):
                excluded_cache += path.stat().st_size
                continue
            zf.write(path, rel)
            files_packed += 1
            fixity_files[rel] = {
                "sha256": hash_file(path).removeprefix(HASH_PREFIX),
                "bytes": path.stat().st_size,
            }
        # LAST member: the in-zip fixity manifest, recording every OTHER member.
        zf.writestr(_fixity_zipinfo(), _fixity_manifest_bytes(fixity_files))
    if as_json:
        _emit({
            "packed": str(out),
            "name": project.root.name,
            "files": files_packed,
            "full": full,
            "excluded_cache_bytes": excluded_cache,
            "fixity": FIXITY_FORMAT,
        }, True)
        return
    typer.secho(f"packed → {out}", fg=typer.colors.GREEN)
    typer.secho(
        f"  ✓ 写入完整性清单 {FIXITY_MANIFEST_NAME} "
        f"({len(fixity_files)} files, sha256+size;unpack 时自动校验)",
        fg=typer.colors.BRIGHT_BLACK,
    )
    if excluded_cache:
        typer.echo(f"  跳过可重建缓存 {excluded_cache / 1e6:.1f} MB "
                   "(segments/proxy — 重建即回;--full 保留)")
    if skipped_symlinks:
        typer.secho(
            f"  ⚠ 跳过 {len(skipped_symlinks)} 个符号链接(symlink 可能指向项目外文件,"
            f"打包时永不跟随):{', '.join(skipped_symlinks[:5])}"
            + (" …" if len(skipped_symlinks) > 5 else ""),
            fg=typer.colors.YELLOW,
        )


def _sanitize_archive_stem(stem: str) -> str:
    """A restore-directory name derived from the ARCHIVE'S OWN FILENAME
    (goal item 20) — never from the zip comment, which is archive-controlled
    content an untrusted .manjupkg fully owns. ``Path.stem`` is always a
    single path component (it cannot itself contain ``/`` on any OS), so the
    only remaining risk is a degenerate value like ``""`` / ``"."`` / ``".."``
    — guarded here with a safe fallback."""
    stem = stem.strip()
    if not stem or set(stem) <= {"."}:
        return "restored"
    return stem


# FP security loop (roadmap §8.9): unpack decompression preflight. A project
# restore is legitimately huge (media), so absolute size caps would false-refuse
# real projects and compression-ratio heuristics false-positive on digital-
# silence WAVs (~1000:1 legally) — the honest guards are a member-count sanity
# cap and DECLARED-total-vs-free-disk (the actual harm is fill-the-disk DoS).
UNPACK_MAX_MEMBERS = 100_000
UNPACK_FREE_DISK_MARGIN_BYTES = 64 * 1024 * 1024


@app.command("support-bundle")
def support_bundle_cmd(
    out: Optional[Path] = typer.Option(None, "--out",
                                       help="bundle zip 输出路径(默认 support-bundle.zip)"),
    events_tail: int = typer.Option(200, "--events-tail",
                                    help="包含 events.jsonl 末尾多少行(脱敏后)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Build a REDACTED diagnostic support bundle (roadmap §8.5).

    Default-deny collectors only: environment versions, project shape COUNTS,
    a redacted events/failures tail, provider-manifest digests (bytes hashed,
    never parsed). Never media bytes, never bible/shot/timeline content, never
    absolute private paths — a pre-write self-scan REFUSES to produce a bundle
    containing any secret/path marker."""
    from .core.supportbundle import BundleError, build_support_bundle

    project = _project()
    dest = out if out is not None else Path.cwd() / "support-bundle.zip"
    try:
        summary = build_support_bundle(project, dest,
                                       include_events_tail=events_tail)
    except BundleError as exc:
        _fail(str(exc), code="bundle_refused")
        return
    if as_json:
        _emit(summary, True)
        return
    typer.secho(f"support bundle → {dest.name}", fg=typer.colors.GREEN)
    typer.echo(f"  members: {len(summary.get('members') or [])}  "
               f"redaction: {summary.get('redaction')}  "
               f"self-scan ok: {summary.get('self_scan', {}).get('ok')}")


@app.command()
def unpack(archive: Path, dest: Optional[Path] = typer.Option(
        None, "--dest", help="override the restored directory (default: from the archive filename)"),
        as_json: bool = typer.Option(False, "--json")):
    """Restore a .manjupkg.

    goal item 20: the default restore directory comes from the ARCHIVE's OWN
    FILENAME stem (sanitized), never from the zip comment — a comment is
    archive-controlled content, and trusting it for a path let a malicious
    .manjupkg pick where it lands (e.g. a comment name like ``../../etc`` or
    an absolute path) whenever the user omitted --dest. The zip-comment name
    is shown for information only when it differs."""
    if not archive.exists():
        _fail(f"not found: {archive}")
    original_name: str | None = None
    with zipfile.ZipFile(archive) as zf:
        try:
            meta = json.loads(zf.comment.decode("utf-8")) if zf.comment else {}
            if isinstance(meta, dict) and meta.get("manjupkg"):
                original_name = str(meta.get("name") or "") or None
        except (json.JSONDecodeError, UnicodeDecodeError):
            original_name = None
        default_name = _sanitize_archive_stem(archive.stem) + ".manju"
        if dest is None:
            dest = Path.cwd() / default_name
            if original_name and original_name != default_name:
                typer.secho(
                    f"  注:压缩包内记录的原项目名为 '{original_name}',但为避免信任 "
                    f"zip comment 里的路径,恢复目录名取自压缩包文件名 → {default_name}"
                    "(如需原名,--dest 显式指定)",
                    fg=typer.colors.BRIGHT_BLACK,
                )
        if dest.exists():
            _fail(f"destination exists, refusing to overwrite: {dest}")
        # Round Y (review #20 hardening): validate every member BEFORE extraction.
        # stdlib extractall already neutralises `..`/absolute paths in member
        # NAMES, but it does NOT stop a SYMLINK member (a zip can carry one) from
        # being recreated and letting a later member write through it to outside
        # the destination. Reject symlink members and any traversal/absolute name
        # up front — an archive that carries them is not a normal `.manjupkg`.
        import stat as _stat

        for info in zf.infolist():
            nm = info.filename
            if nm.startswith("/") or nm.startswith("\\") or ".." in Path(nm).parts:
                _fail(f"拒绝解包:压缩包成员路径越界或为绝对路径 → {nm!r}"
                      "(不是正常的 .manjupkg,可能是恶意压缩包)")
            mode = info.external_attr >> 16
            if mode and _stat.S_ISLNK(mode):
                _fail(f"拒绝解包:压缩包含符号链接成员 → {nm!r}"
                      "(symlink 可越出解包目录,正常 .manjupkg 不含符号链接)")
        # FP security loop: decompression preflight BEFORE extractall — a zip
        # bomb must refuse structurally, not fill the disk. Declared sizes are
        # summed (a lie-small header still cannot exceed what zipfile inflates
        # per its declared size on extract) and compared against the ACTUAL
        # free space at the destination's filesystem, with a safety margin.
        infos = zf.infolist()
        if len(infos) > UNPACK_MAX_MEMBERS:
            _fail(f"拒绝解包:成员数 {len(infos)} 超出上限 {UNPACK_MAX_MEMBERS}"
                  "(zip bomb 防护)")
        declared_total = sum(i.file_size for i in infos)
        probe_dir = dest.parent
        while not probe_dir.exists() and probe_dir != probe_dir.parent:
            probe_dir = probe_dir.parent
        free = shutil.disk_usage(probe_dir).free
        if declared_total + UNPACK_FREE_DISK_MARGIN_BYTES > free:
            _fail(f"拒绝解包:压缩包声明解压总量 {declared_total} 字节,目标磁盘可用 "
                  f"{free} 字节(含 {UNPACK_FREE_DISK_MARGIN_BYTES} 安全余量)— "
                  "空间不足或为 zip bomb,先释放磁盘空间或检查压缩包")
        names = zf.namelist()
        fixity_raw = (
            zf.read(FIXITY_MANIFEST_NAME) if FIXITY_MANIFEST_NAME in names else None
        )
        zf.extractall(dest)
    # ---- fixity verify-after-extract (only when a manifest is present) ------
    # Absent manifest ⇒ today's behavior byte-for-byte + a one-line advisory
    # (exit 0). Present + FAIL ⇒ structured rows, the just-created destination
    # is REMOVED (a corrupt restore must not look like a good one), nonzero exit.
    fixity_json: dict = {"present": False}
    fixity_note: Optional[str] = None
    if fixity_raw is None:
        fixity_note = (
            f"注:此包不含 {FIXITY_MANIFEST_NAME}(旧包或非 manju pack 生成)— "
            "已按原样解包,未做完整性校验"
        )
    else:
        manifest = _parse_fixity_manifest(fixity_raw)
        if manifest is None:
            # present but unparseable/unsupported: cannot verify, but that is not
            # itself evidence the payload is bad — advisory, keep the restore.
            fixity_json = {"present": True, "verified": False, "reason": "unparseable"}
            fixity_note = (
                f"注:{FIXITY_MANIFEST_NAME} 无法解析/非受支持格式 — 跳过完整性校验"
            )
        else:
            result = _verify_fixity_rows(manifest, _present_from_dir(dest))
            if not result["ok"]:
                removed = _remove_restored_dest(dest)
                if as_json:
                    _emit({
                        "ok": False,
                        "unpacked": None,
                        "archive": str(archive),
                        "dest_removed": removed,
                        "fixity": {"present": True, **result},
                    }, True)
                else:
                    typer.secho(
                        f"✗ 解包完整性校验失败 fixity(mismatch {len(result['mismatched'])}"
                        f" · missing {len(result['missing'])} · extra {len(result['extra'])})",
                        fg=typer.colors.RED,
                    )
                    _print_fixity_rows(result)
                    typer.secho(
                        f"  已删除恢复目录 {dest}(损坏的恢复不能伪装成成功)"
                        if removed else
                        f"  ⚠ 尝试删除 {dest} 未完全成功,请手动清理",
                        fg=typer.colors.RED,
                    )
                raise typer.Exit(1)
            fixity_json = {"present": True, **result}
            # verified OK — drop the transport-only manifest so the restored tree
            # equals the original project (and a re-pack won't double-write it).
            try:
                (dest / FIXITY_MANIFEST_NAME).unlink(missing_ok=True)
            except OSError:
                pass
    (dest / ".manju").mkdir(exist_ok=True)
    restored_files = sum(1 for n in names if n != FIXITY_MANIFEST_NAME)
    if as_json:
        _emit({
            "unpacked": str(dest),
            "archive": str(archive),
            "name": original_name,
            "files": restored_files,
            "fixity": fixity_json,
        }, True)
        return
    typer.secho(f"unpacked → {dest}", fg=typer.colors.GREEN)
    if fixity_json.get("present") and fixity_json.get("ok"):
        typer.secho(
            f"  ✓ 完整性校验通过 fixity({fixity_json['checked']} files, sha256+size)",
            fg=typer.colors.GREEN,
        )
    if fixity_note:
        typer.secho("  " + fixity_note, fg=typer.colors.BRIGHT_BLACK)


@app.command()
def fixity(archive: Path,
           as_json: bool = typer.Option(False, "--json")):
    """Verify a .manjupkg's in-zip MANJU_FIXITY.json WITHOUT extracting it.

    Streams every member (bounded memory — never inflates a whole member),
    recomputes sha256+size and compares against the manifest recorded at pack
    time: structured rows for mismatched / missing / extra members, an overall
    ``ok``, and a matching exit code. READ-ONLY — nothing is written to disk.

    A pack that carries no manifest (packed before fixity, or not produced by
    ``manju pack``) cannot be verified — reported ``present: false`` with a
    nonzero exit (an unverifiable pack is not a verified one). The member-count
    sanity cap mirrors the unpack preflight; the free-disk cap does not apply
    here because nothing is extracted."""
    if not archive.exists():
        _fail(f"not found: {archive}")
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > UNPACK_MAX_MEMBERS:
            _fail(f"拒绝校验:成员数 {len(infos)} 超出上限 {UNPACK_MAX_MEMBERS}"
                  "(zip bomb 防护)")
        names = [i.filename for i in infos]
        if FIXITY_MANIFEST_NAME not in names:
            if as_json:
                _emit({"ok": False, "present": False, "archive": str(archive),
                       "reason": "no_manifest"}, True)
            else:
                typer.secho(
                    f"fixity: {archive.name} 不含 {FIXITY_MANIFEST_NAME}"
                    "(旧包或非 manju pack 生成)— 无法校验完整性",
                    fg=typer.colors.YELLOW)
            raise typer.Exit(1)
        manifest = _parse_fixity_manifest(zf.read(FIXITY_MANIFEST_NAME))
        if manifest is None:
            if as_json:
                _emit({"ok": False, "present": True, "archive": str(archive),
                       "reason": "unparseable_manifest"}, True)
            else:
                typer.secho(
                    f"fixity: {archive.name} 的 {FIXITY_MANIFEST_NAME} "
                    "无法解析/非受支持格式", fg=typer.colors.RED)
            raise typer.Exit(1)
        result = _verify_fixity_rows(manifest, _present_from_zip_stream(zf))
    result = {"present": True, "archive": str(archive), **result}
    if as_json:
        _emit(result, True)
    elif result["ok"]:
        typer.secho(
            f"fixity ✓ {archive.name}: {result['checked']} files verified "
            "(sha256+size)", fg=typer.colors.GREEN)
    else:
        typer.secho(
            f"fixity ✗ {archive.name}: FAILED(mismatch {len(result['mismatched'])}"
            f" · missing {len(result['missing'])} · extra {len(result['extra'])})",
            fg=typer.colors.RED)
        _print_fixity_rows(result)
    if not result["ok"]:
        raise typer.Exit(1)


# ----------------------------------------------------------------- relink


@app.command()
def relink(
    mode: str = typer.Argument(..., help="report | plan | apply"),
    root: Optional[list[Path]] = typer.Option(
        None, "--root", help="plan: 搜索候选文件的目录(可重复给多个)"),
    out: Optional[Path] = typer.Option(
        None, "--out", help="plan: 计划 JSON 写入路径(省略则只打印 — 保持零写入)"),
    plan_file: Optional[Path] = typer.Option(
        None, "--plan", help="apply: relink 计划文件(`manju relink plan --out` 生成)"),
    allow_unverified: bool = typer.Option(
        False, "--allow-unverified",
        help="apply: 允许 name-only ADVISORY 行落盘(逐行重新哈希并记录为 unverified)"),
    max_files: int = typer.Option(
        None, "--max-files", help="plan: 扫描文件数上限(默认 20000;超限 ⇒ 结构化 partial-scan)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Missing-media report + hash-verified relink (FP roadmap §6.3).

    ``report``: ZERO-WRITE — every take sidecar whose media file is gone
    (the container's ``media_path=None`` seam), every timeline clip source
    that does not resolve, every bible ref pin naming an absent file; rows
    carry a recorded content hash ONLY where attempt evidence recorded one.

    ``plan``: ZERO-WRITE, bounded scan of ``--root`` dirs — hash-first for
    recorded-hash items (a byte-identical file is found under any name);
    hashless items get name-only ADVISORY candidates.

    ``apply``: CAS — every candidate re-hashed at apply time; bytes restored
    to the ORIGINAL recorded path under media/gen/**|media/refs/** only
    (imports/ is ingest-only; truth files are NEVER rewritten); tampered/
    vanished candidates refuse per-row; unverified rows need
    ``--allow-unverified`` and are recorded as unverified restores."""
    from .media.relink import (
        DEFAULT_MAX_FILES,
        RelinkError,
        apply_relink,
        missing_media_report,
        relink_plan,
    )

    if mode not in ("report", "plan", "apply"):
        _fail(f"未知模式: {mode!r} — 用 report | plan | apply", code="bad_mode")
    project = _project()

    if mode == "report":
        report = missing_media_report(project)
        if as_json:
            _emit(report, True)
            return
        s = report["summary"]
        color = typer.colors.GREEN if s["total"] == 0 else typer.colors.YELLOW
        typer.secho(
            f"缺失媒体 / missing media: {s['total']}  "
            f"(take {s['by_kind']['take']} · timeline {s['by_kind']['timeline_source']}"
            f" · ref {s['by_kind']['ref']};可哈希校验恢复 {s['hash_relink_available']})",
            fg=color)
        for row in report["rows"]:
            tag = "✓hash" if row["hash_relink_available"] else "advisory-only"
            typer.echo(f"  [{row['kind']}] {row['id']} → "
                       f"{row['last_known_relpath'] or '(无记录路径)'}  ({tag})")
        if s["total"]:
            typer.secho("  下一步: manju relink plan --root <备份/外置盘目录> --out plan.json",
                        fg=typer.colors.BRIGHT_BLACK)
        return

    if mode == "plan":
        if not root:
            _fail("plan 需要至少一个 --root <目录>(候选文件搜索范围)",
                  code="missing_root")
        plan = relink_plan(project, list(root),
                           max_files=max_files or DEFAULT_MAX_FILES)
        if out is not None:
            out.write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        if as_json:
            _emit(plan, True)
        else:
            s = plan["summary"]
            typer.secho(
                f"relink 计划: 缺失 {s['missing']} · 可恢复 {s['relink']} "
                f"(已验证 {s['verified']} · advisory {s['advisory']}) · 跳过 {s['skip']}",
                fg=typer.colors.CYAN)
            if not plan["scan"]["complete"]:
                typer.secho(f"  ⚠ partial scan — {'; '.join(plan['scan']['notes'])}",
                            fg=typer.colors.YELLOW)
            for r in plan["rows"]:
                if r["action"] == "relink":
                    mark = "✓" if r["verified"] else "?"
                    typer.echo(f"  {mark} {r['missing']['id']} ← {r['candidate']['path']}"
                               f"  ({r['verification']})")
            if out is not None:
                typer.secho(f"  计划已写入 {out} — apply: manju relink apply --plan {out}",
                            fg=typer.colors.BRIGHT_BLACK)
        return

    # ---- apply
    if plan_file is None:
        _fail("apply 需要 --plan <计划文件>(先 manju relink plan --out 生成)",
              code="missing_plan")
    if not plan_file.exists():
        _fail(f"计划文件不存在: {plan_file}", code="plan_not_found")
    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"计划文件不可读/不是 JSON: {plan_file} — {exc}", code="plan_unreadable")
        return  # unreachable; keeps the type checker happy
    with _write_lock(project):
        try:
            result = apply_relink(project, plan,
                                  allow_unverified=allow_unverified, actor=ACTOR)
        except RelinkError as exc:
            _fail(str(exc), code="bad_plan")
            return
    if as_json:
        _emit(result, True)
    else:
        s = result["summary"]
        color = typer.colors.GREEN if result["ok"] else typer.colors.RED
        typer.secho(
            f"relink apply: 恢复 {s['restored']} · unverified {s['restored_unverified']}"
            f" · 拒绝 {s['refused']} · 跳过 {s['skipped']}", fg=color)
        for r in result["rows"]:
            if r["status"] in ("restored", "restored_unverified"):
                typer.secho(f"  ✓ {r['target']}  ({r['status']})", fg=typer.colors.GREEN)
            elif r["status"] == "refused":
                typer.secho(f"  ✗ {r['id'] or r['target']}  拒绝: {r['reason']}",
                            fg=typer.colors.RED)
    if not result["ok"]:
        raise typer.Exit(1)


# ----------------------------------------------------------------- perf


@app.command()
def perf(
    run_id: Optional[str] = typer.Argument(
        None, help="build run id(见 `manju build --json`);缺省取最近一次运行/latest"),
    as_json: bool = typer.Option(False, "--json"),
):
    """运行性能报告 — the run performance report (§8.6, a DERIVED view).

    A read-only lens over the SAME events.jsonl attempt evidence the RunManifest
    projects — it adds NO instrumentation and the engine never reads it back
    (§8.7 原则:性能提示不是调度事实). Surfaces per-stage duration, cache hit rate
    (derived from the recorded SKIPPED_CACHE_HIT state), provider-vs-local time
    split, recorded cost totals, slowest stages, and error categories. A §8.6
    metric with no recorded source (QC time, disk usage) is listed honestly under
    `unavailable`, never estimated. No RUN_ID ⇒ the latest run on the stream;
    `--json` prints the full report (plain CLI JSON, not a manju.*/vN schema)."""
    from .qc.runperf import run_performance

    report = run_performance(_project(), run_id)
    if as_json:
        _emit(report, True)
        return

    if not report["found"]:
        which = f"运行/run {report['run_id']}" if report["run_id"] else "任何运行/any run"
        typer.secho(f"性能报告 / perf:未找到 {which}(terminal_status="
                    f"{report['terminal_status']})。", fg=typer.colors.YELLOW)
        return

    typer.secho(f"性能报告 / performance  run {report['run_id']}  "
                f"[{report['terminal_status']}]", fg=typer.colors.CYAN)
    wall = report["wall"]
    if wall["ms"] is not None:
        typer.echo(f"  墙钟 / wall        {wall['ms'] / 1000:g}s  ({wall['source']})")
    typer.echo(f"  尝试 / attempts    {report['attempt_count']}"
               + (f"  (malformed lines: {report['malformed_lines']})"
                  if report["malformed_lines"] else ""))

    if report["stages"]:
        typer.secho("按阶段 / by stage (slowest first):", fg=typer.colors.BRIGHT_BLACK)
        for s in report["stages"]:
            counts = ", ".join(f"{st}×{n}" for st, n in s["status_counts"].items())
            typer.echo(f"  {s['stage']:<10} {s['total_ms'] / 1000:>8.3g}s  "
                       f"×{s['attempts']}  [{counts}]")

    ts = report["time_split"]
    typer.echo(f"  供应商 / provider  {ts['provider_ms'] / 1000:g}s"
               f"   本地 / local  {ts['local_ms'] / 1000:g}s"
               f"   渲染 / render  {ts['render_ms'] / 1000:g}s")

    cache = report["cache"]
    if cache["eligible"]:
        rate = f"{cache['rate'] * 100:.1f}%" if cache["rate"] is not None else "—"
        typer.echo(f"  缓存命中 / cache   {cache['hits']}/{cache['eligible']}  ({rate})")

    if report["cost"]["totals"]:
        parts = ", ".join(f"{c['amount']:g} {c['currency']}"
                          for c in report["cost"]["totals"])
        typer.echo(f"  花费 / cost        {parts}")

    if report["errors"]["count"]:
        cats = ", ".join(f"{c['category']}×{c['count']}"
                         for c in report["errors"]["by_category"])
        typer.secho(f"  错误 / errors      {report['errors']['count']}  ({cats})",
                    fg=typer.colors.YELLOW)

    # honest gap list — every §8.6 metric with no recorded source, named.
    if report["unavailable"]:
        typer.secho("不可得 / unavailable (no recorded source — never estimated):",
                    fg=typer.colors.BRIGHT_BLACK)
        for u in report["unavailable"]:
            typer.echo(f"  {u['metric']}: {u['missing_source']}")


# ------------------------------------------------------------- appearances


@app.command()
def appearances(as_json: bool = typer.Option(False, "--json")):
    """Cross-reference bible ids against the shots that use them (goal 11).

    Read-only: for every character/scene/prop referenced by a shot, list the
    shots it appears in (index order); plus orphans (bible entries no shot
    uses) and missing refs (used in a shot, absent from the bible). Does not
    duplicate `manju check` — a missing scene/character is already a check
    error and is only echoed here for context."""
    from .core.appearances import appearances as _appearances

    info = _appearances(_project())
    if as_json:
        _emit(info, True)
        return

    def _line(rid: str, entry: dict) -> str:
        label = f"{rid}"
        if entry.get("name"):
            label += f"  {entry['name']}"
        shots = entry["shots"]
        return f"  {label}  → {', '.join(shots)}  ({len(shots)})"

    typer.secho(f"出场表 / appearances  (共 {info['shots_total']} 镜)", fg=typer.colors.CYAN)
    for kind, title in (("characters", "角色 / characters"),
                        ("scenes", "场景 / scenes"),
                        ("props", "道具 / props")):
        entries = info.get(kind) or {}
        typer.secho(title, fg=typer.colors.BRIGHT_BLACK)
        if not entries:
            typer.echo("  —")
        for rid, entry in entries.items():
            typer.echo(_line(rid, entry))
    orphans = info.get("orphans") or {}
    orphan_bits = [f"{k}: {', '.join(v)}" for k, v in orphans.items() if v]
    if orphan_bits:
        typer.secho("未被引用 / orphans:  " + " · ".join(orphan_bits), fg=typer.colors.YELLOW)
    missing = info.get("missing") or {}
    missing_flat = [(k, m) for k, ms in missing.items() for m in ms]
    if missing_flat:
        typer.secho("缺失引用 / missing (镜头引用但 bible 无此条):", fg=typer.colors.RED)
        for kind, m in missing_flat:
            hint = "  (manju check 也会报)" if kind in ("characters", "scenes") else ""
            typer.echo(f"  {kind[:-1] if kind.endswith('s') else kind} {m['id']} ← "
                       f"{', '.join(m['shots'])}{hint}")


# -------------------------------------------------------------- toolchain


@app.command()
def toolchain(
    write: bool = typer.Option(False, "--write",
                               help="落盘 reports/toolchain/<digest>.json(派生、可删除的证据投影)"),
    diff: Optional[Path] = typer.Option(None, "--diff",
                                        help="与一份旧清单 JSON 比较,输出结构化漂移行"),
    as_json: bool = typer.Option(False, "--json"),
):
    """工具链清单(roadmap §7.7):record-only reproducibility evidence.

    Captures THIS machine's toolchain facts — manju/python/OS versions, the
    ffmpeg/ffprobe -version first lines verbatim, key dep versions, the burn
    font (basename + content hash), optional tools, locale — as a
    manju.toolchain-manifest/v1 document. Derived + deletable: nothing reads
    it back; it never enters content keys, caching, or authorization (that
    future step is declared in core/toolchain.py's docstring). No hostname,
    username, or absolute path ever enters the document. --diff OLD.json
    prints the changed-fact rows; drift is evidence, never an error (exit 0).
    Needs a project only for --write."""
    from .core.toolchain import toolchain_drift, toolchain_manifest, write_toolchain_manifest

    doc = toolchain_manifest()
    payload: dict = {"manifest": doc}
    if diff is not None:
        try:
            old = json.loads(Path(diff).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _fail(f"无法读取旧清单 / cannot read old manifest {diff}: {exc}",
                  code="toolchain_diff_unreadable")
        try:
            payload["drift"] = toolchain_drift(old, doc)
        except ValueError as exc:
            _fail(f"旧清单不是工具链清单 / not a toolchain manifest: {exc}",
                  code="toolchain_diff_unreadable")
    if write:
        project = _project()
        path = write_toolchain_manifest(project, doc)
        payload["written"] = path.relative_to(project.root).as_posix()
    if as_json:
        _emit(payload, True)
        return

    facts = doc["facts"]
    typer.secho(f"工具链清单 / toolchain manifest  digest {doc['manifest_digest']}",
                fg=typer.colors.CYAN)
    typer.echo(f"  manju {facts['manju']['version']} · "
               f"python {facts['python']['version']} "
               f"{facts['python']['implementation']} {facts['python']['arch']} · "
               f"{facts['os']['system']} {facts['os']['release']} {facts['os']['machine']}")
    for tool, line in facts["tools"].items():
        color = typer.colors.RED if line == "missing" else None
        typer.secho(f"  {tool}: {line}", fg=color)
    opt = " · ".join(f"{name} {'✓' if present else '✗'}"
                     for name, present in facts["optional_tools"].items())
    typer.secho(f"  可选 optional: {opt}", fg=typer.colors.BRIGHT_BLACK)
    for role, entry in facts["fonts"].items():
        desc = "unknown" if entry == "unknown" else f"{entry['basename']}  {entry['sha256']}"
        typer.secho(f"  字体 font [{role}]: {desc}", fg=typer.colors.BRIGHT_BLACK)
    deps = " · ".join(f"{k} {v}" for k, v in facts["deps"].items())
    typer.secho(f"  deps: {deps}", fg=typer.colors.BRIGHT_BLACK)
    typer.secho(f"  locale: fs-encoding {facts['locale']['filesystem_encoding']} · "
                f"LANG {'set' if facts['locale']['lang_set'] else 'unset'}",
                fg=typer.colors.BRIGHT_BLACK)
    if "drift" in payload:
        rows = payload["drift"]
        if not rows:
            typer.secho("漂移 / drift: 无 (toolchain unchanged)", fg=typer.colors.GREEN)
        else:
            typer.secho(f"漂移 / drift: {len(rows)} 项 (evidence, not an error)",
                        fg=typer.colors.YELLOW)
            for row in rows:
                typer.echo(f"  {row['fact']}: {row['old']!r} → {row['new']!r}")
    if "written" in payload:
        typer.secho(f"已写入 / written: {payload['written']}", fg=typer.colors.GREEN)


# ----------------------------------------------------------- help-workflow


@app.command("help-workflow")
def help_workflow(
    name: Optional[str] = typer.Argument(
        None, help="workflow id(如 qc-repair);缺省列出全部工作流"),
    as_json: bool = typer.Option(False, "--json"),
):
    """我想做 X,该按什么顺序敲哪些命令?Task-oriented navigation (roadmap §8.3).

    Read-only and project-free: renders the curated WORKFLOWS table from
    cli_workflows.py — a cli-side dict, NOT a new fact source, and its --json
    is plain CLI JSON (no manju.*/vN schema). Every command a step names must
    exist in this app, test-enforced against the live typer registry
    (tests/test_fp_workflows.py — the same mechanism that keeps the README
    command table honest). No name ⇒ list workflows (title + when); with a
    name ⇒ the steps table with a one-line why per command."""
    from .cli_workflows import (WORKFLOWS, detail_payload, list_payload,
                                render_detail, render_list)

    if name is None:
        if as_json:
            _emit(list_payload(), True)
            return
        typer.secho("工作流 / workflows — manju help-workflow <name> 看步骤",
                    fg=typer.colors.CYAN)
        for line in render_list():
            typer.echo(line)
        return
    if name not in WORKFLOWS:
        valid = sorted(WORKFLOWS)
        if as_json:
            typer.echo(json.dumps(
                {"error": f"unknown workflow: {name}", "code": "unknown_workflow",
                 "valid_workflows": valid}, ensure_ascii=False))
        else:
            typer.secho(f"未知工作流 / unknown workflow: {name}",
                        fg=typer.colors.RED, err=True)
            typer.echo("可用 / valid: " + ", ".join(valid))
        raise typer.Exit(1)
    if as_json:
        _emit(detail_payload(name), True)
        return
    for line in render_detail(name):
        typer.echo(line)


# ----------------------------------------------------------------- assets

assets_app = typer.Typer(no_args_is_help=False,
                         help="Asset matrix (goal 5): the read model over bible/*.yaml — "
                              "角色/场景/道具/配音/风格,含别名·关系·参考图·出场。")
app.add_typer(assets_app, name="assets")

_ASSET_KIND_TITLES = (
    ("character", "角色 / characters"),
    ("scene", "场景 / scenes"),
    ("prop", "道具 / props"),
    ("voice", "配音 / voices"),
    ("style", "风格 / styles"),
)


def _asset_row_lines(row: dict) -> list[str]:
    """The extra attribute lines under an asset row (only non-empty ones)."""
    lines: list[str] = []
    if row.get("aliases"):
        lines.append(f"      别名 aliases: {', '.join(row['aliases'])}")
    refs = row.get("refs") or {}
    ref_bits = []
    if refs.get("images"):
        ref_bits.append(f"图×{len(refs['images'])}")
    if refs.get("videos"):
        ref_bits.append(f"视频×{len(refs['videos'])}")
    if ref_bits:
        lines.append(f"      参考 refs: {', '.join(ref_bits)}")
    if row.get("relations"):
        rel = "; ".join(
            f"{verb}→{v if isinstance(v, str) else ', '.join(v)}"
            for verb, v in row["relations"].items()
        )
        lines.append(f"      关系 relations: {rel}")
    if row.get("default_position"):
        lines.append(f"      默认位置 default_position: {row['default_position']}")
    if row.get("locked_fields"):
        lines.append(f"      标注锁定 locked_fields: {', '.join(row['locked_fields'])}")
    return lines


@assets_app.callback(invoke_without_command=True)
def assets_main(ctx: typer.Context, as_json: bool = typer.Option(False, "--json")):
    """Asset matrix table (中文表头). `manju assets show <id>` for one asset's detail."""
    if ctx.invoked_subcommand is not None:
        return
    from .core.assets import asset_matrix

    matrix = asset_matrix(_project())
    if as_json:
        _emit(matrix, True)
        return
    typer.secho(f"资产矩阵 / asset matrix  (共 {matrix['shots_total']} 镜)",
                fg=typer.colors.CYAN)
    for kind, title in _ASSET_KIND_TITLES:
        rows = matrix["kinds"].get(kind) or []
        typer.secho(f"{title}  ({len(rows)})", fg=typer.colors.BRIGHT_BLACK)
        if not rows:
            typer.echo("  —")
            continue
        for row in rows:
            name = f"  {row['name']}" if row.get("name") else ""
            shots = row.get("appearances") or []
            tail = f"  → 出场 {', '.join(shots)} ({len(shots)})" if shots else ""
            typer.echo(f"  {row['id']}{name}{tail}")
            for line in _asset_row_lines(row):
                typer.secho(line, fg=typer.colors.BRIGHT_BLACK)


@assets_app.command("show")
def assets_show(asset_id: str, as_json: bool = typer.Option(False, "--json")):
    """Detail for one asset by id — aliases, relations, refs, and its appearances."""
    from .core.assets import asset_matrix, find_asset

    row = find_asset(asset_matrix(_project()), asset_id)
    if row is None:
        _fail(f"资产矩阵中没有 id 为 '{asset_id}' 的条目(bible/*.yaml 未登记?)")
    if as_json:
        _emit(row, True)
        return
    typer.secho(f"{row['id']}  ({row['kind']})"
                + (f"  {row['name']}" if row.get("name") else ""), fg=typer.colors.CYAN)
    if row.get("description"):
        typer.echo(f"  描述 description: {row['description']}")
    for line in _asset_row_lines(row):
        typer.echo(line.strip())
    shots = row.get("appearances") or []
    typer.secho("出场 / appearances: " + (", ".join(shots) if shots else "—"),
                fg=typer.colors.BRIGHT_BLACK)


# ---------------------------------------------------------------- mentions


@app.command()
def mentions(
    shot_id: Optional[str] = typer.Argument(None, help="限定单个镜头;省略=全部镜头(--apply 只处理镜头,不动 story 文本)"),
    check: bool = typer.Option(False, "--check", help="只报告已解析/未解析的 @提及(默认行为)"),
    apply: bool = typer.Option(False, "--apply", help="把已解析的角色/场景 @提及写入镜头登记字段"),
    as_json: bool = typer.Option(False, "--json"),
):
    """@提及系统(goal 6):解析 shot 自由文本与 story/*.md 中的 `@id-或-别名`。

    设计立场:@提及是登记辅助,不是隐藏的运行时绑定 —— 构建从不读取 @提及。
    `--check`(默认)只报告;`--apply` 通过正常写入路径把已解析的角色/场景提及
    写入 shot.characters / shot.scene(锁定字段绝不自动写,改为建议走 proposals/)。
    """
    project = _project()
    from .core.assets import asset_matrix
    from .core.mentions import apply_mentions, mention_report

    matrix = asset_matrix(project)

    if apply:
        results = apply_mentions(project, matrix, shot_id)
        changed = [r for r in results if r.get("changed")]
        for r in changed:
            append_event(project.root, ACTOR, "mentions_apply",
                         {"shot": r["shot"], "added_characters": r.get("added_characters"),
                          "set_scene": r.get("set_scene")})
        if as_json:
            _emit({"applied": results}, True)
            return
        if not changed:
            typer.secho("没有可写入的角色/场景 @提及(或全部已登记/被锁定)。", fg=typer.colors.YELLOW)
        for r in results:
            if r.get("error"):
                typer.secho(f"{r['shot']}: 出错 {r['error']}", fg=typer.colors.RED)
                continue
            if r.get("changed"):
                bits = []
                if r.get("added_characters"):
                    bits.append(f"+角色 {', '.join(r['added_characters'])}")
                if r.get("set_scene"):
                    bits.append(f"场景={r['set_scene']}")
                typer.secho(f"{r['shot']}: {'; '.join(bits)}", fg=typer.colors.GREEN)
            for sk in r.get("skipped") or []:
                typer.secho(f"  跳过 @{sk['mention']} ({sk['reason']})", fg=typer.colors.YELLOW)
        return

    # default / --check: report only
    report = mention_report(project, matrix, shot_id)
    if as_json:
        _emit(report, True)
        return
    typer.secho("@ 提及报告 / mentions  (--apply 写入镜头登记字段)", fg=typer.colors.CYAN)
    for entry in report["shots"]:
        if not entry["resolved"] and not entry["unresolved"]:
            continue
        typer.secho(entry["shot"], fg=typer.colors.BRIGHT_BLACK)
        for r in entry["resolved"]:
            reg = r["registered"]
            mark = "" if reg is None else ("已登记" if reg else "未登记 → --apply")
            typer.echo(f"  @{r['raw']} → {r['kind']}:{r['asset']}  {mark}")
        for u in entry["unresolved"]:
            near = f"  (最相近:{', '.join(u['nearest'])})" if u.get("nearest") else ""
            typer.secho(f"  @{u['raw']} → 未解析{near}", fg=typer.colors.YELLOW)
    if report["story"]:
        typer.secho("story 文本(只报告,不写入):", fg=typer.colors.BRIGHT_BLACK)
        for s in report["story"]:
            resolved = ", ".join(f"@{r['raw']}→{r['kind']}:{r['asset']}" for r in s["resolved"])
            unresolved = ", ".join(f"@{u['raw']}" for u in s["unresolved"])
            typer.echo(f"  {s['file']}: {resolved}"
                       + (f"  未解析: {unresolved}" if unresolved else ""))

# -------------------------------------------------------------------- refs


def _shot_manifest(project, shot):
    """The ProviderManifest that WOULD deliver this shot's refs — the explicit
    provider, else the first provider in its fallback chain that carries one.
    ``None`` when the shot routes only to built-in local providers (no budget)."""
    try:
        from .providers.registry import fallback_chain, get_manifest

        order = ([shot.generation.provider] if shot.generation.provider else [])
        order += [n for n in fallback_chain(shot) if n not in order]
        for name in order:
            manifest = get_manifest(name)
            if manifest is not None:
                return manifest
    except Exception:
        return None
    return None


# `refs` is a command GROUP (round-AA goal item 3): the bare `manju refs`
# (and `--orphans`) is the project-wide media/refs OWNERSHIP report — who
# uses which file, which are orphans, which bible pointers are dangling.
# `refs shot <id>` keeps the pre-existing per-shot resolution/budget/
# cleanliness inspection (goal items 9 & 10) verbatim, just moved under the
# group (mirrors `assets` / `assets show`'s bare-report-vs-named-subcommand
# split). `refs assign` is the new mutator that fixes an orphan/ownership
# gap by renaming the file / setting a bible field — never a parallel index.
refs_app = typer.Typer(
    invoke_without_command=True,
    help="media/refs 归属追溯(goal item 3)+ 单镜头参考解析/预算/洁净度(goal items 9-10)。",
)
app.add_typer(refs_app, name="refs")


@refs_app.callback(invoke_without_command=True)
def refs_main(
    ctx: typer.Context,
    orphans: bool = typer.Option(False, "--orphans", help="只显示孤儿(未关联)参考文件"),
    as_json: bool = typer.Option(False, "--json"),
):
    """media/refs 归属报告(goal item 3,只读):每个文件的角色(shot_ref /
    character_ref / scene_ref / prop_ref / unknown,来自文件名约定)、归属
    (镜头 id,或 bible 资产地址如 characters:hero)、是否被 bible ref_image 钉住、
    是否孤儿(无归属也未被钉住);外加 bible 指向不存在文件的缺失指针列表。

    子命令:``refs shot <id>`` 单镜头参考解析/预算/洁净度(goal items 9-10);
    ``refs assign <relpath> --shot/--character/--scene/--prop`` 关联一个孤儿。"""
    if ctx.invoked_subcommand is not None:
        return  # dispatch to `shot` / `assign`
    from .core.refs import refs_report

    project = _project()
    report = refs_report(project)
    rows = [r for r in report["files"] if r["orphan"]] if orphans else report["files"]

    if as_json:
        _emit({**report, "files": rows}, True)
        return

    header = f"参考资产归属 / refs — 共 {report['total']} 个文件"
    if report["orphan_count"]:
        header += f",孤儿 {report['orphan_count']} 个"
    typer.secho(header, fg=typer.colors.CYAN)
    if not rows:
        typer.echo("  (无孤儿)" if orphans else "  media/refs 为空")
    for r in rows:
        owners = ", ".join(r["owners"]) if r["owners"] else "—"
        pinned = "钉住" if r["bible_pinned"] else "  "
        line = f"  [{r['kind']:>5}] {r['role']:<13} {r['file']}  归属: {owners}  {pinned}"
        typer.secho(line, fg=typer.colors.YELLOW if r["orphan"] else None)
    if not orphans and report["missing"]:
        typer.secho("缺失指针 / bible 指向不存在的文件:", fg=typer.colors.RED)
        for m in report["missing"]:
            typer.echo(f"  bible/{m['bible_file']}.yaml:{m['asset_id']}.{m['field']} → {m['value']}")


@refs_app.command("shot")
def refs_shot(shot_id: str = typer.Argument(..., help="the shot to inspect"),
             as_json: bool = typer.Option(False, "--json")):
    """Resolve a shot's reference inputs and report resolution + budget +
    cleanliness (goal items 9 & 10). Read-only, spends nothing.

    - RESOLUTION: which refs resolved and from which tier (params/shot/bible/
      media-refs), with their role;
    - BUDGET: against the delivering provider's manifest limits — what would be
      delivered and what omitted (each omission's consequence in 中文); with no
      budget configured it says delivery is byte-identical to today;
    - CLEANLINESS: local ffmpeg heuristics (busy background / lighting conflict /
      unclear scale) plus honest needs_vision advisories for the checks a vision
      model must make."""
    from .providers.refbudget import allocate, classify_role
    from .providers.refs import resolve_refs
    from .qc.ref_checks import check_refs

    project = _project()
    try:
        shot = project.load_shot(shot_id)
    except Exception as exc:
        _fail(f"无法加载镜头 {shot_id}:{exc}")
    bible = project.load_bible()
    refset = resolve_refs(project, shot, bible)
    manifest = _shot_manifest(project, shot)
    limits = manifest.limits if manifest is not None else None
    budget = allocate(refset, limits, shot, bible=bible)
    findings = check_refs(project, shot, refset, bible=bible)

    def _ref_row(item) -> dict:
        return {"ref": item.ref, "tier": item.tier,
                "role": classify_role(item, shot, bible),
                "exists": item.exists, "is_url": item.is_url}

    if as_json:
        _emit({
            "shot": shot_id,
            "provider": manifest.id if manifest is not None else None,
            "resolution": {
                "images": [_ref_row(it) for it in refset.image_items()],
                "videos": [_ref_row(it) for it in refset.video_items()],
                "primary_image": refset.primary_image_source,
            },
            "budget": {"active": budget.active, **budget.to_lineage()},
            "cleanliness": [f.to_dict() for f in findings],
        }, True)
        return

    typer.secho(f"参考 / refs — 镜头 {shot_id}"
                + (f"  (provider: {manifest.id})" if manifest is not None else ""),
                fg=typer.colors.CYAN)

    typer.secho("解析 / resolution:", fg=typer.colors.BRIGHT_BLACK)
    for kind, items in (("图像 images", refset.image_items()),
                        ("视频 videos", refset.video_items())):
        typer.echo(f"  {kind}:")
        if not items:
            typer.echo("    —")
        for it in items:
            mark = "✓" if it.exists else ("URL" if it.is_url else "缺失")
            role = classify_role(it, shot, bible)
            typer.echo(f"    [{it.tier}] {it.ref}  {role}  {mark}")

    typer.secho("预算 / budget:", fg=typer.colors.BRIGHT_BLACK)
    for line in budget.human_lines():
        typer.echo("  " + line)

    typer.secho("洁净度 / cleanliness:", fg=typer.colors.BRIGHT_BLACK)
    if not findings:
        typer.echo("  无发现 / clean")
    _level_color = {"error": typer.colors.RED, "warn": typer.colors.YELLOW,
                    "needs_vision": typer.colors.MAGENTA, "info": typer.colors.BLUE}
    for f in findings:
        typer.secho(f"  [{f.level}] {f.code}: {f.message}",
                    fg=_level_color.get(f.level, None))
        if f.hint:
            typer.echo(f"      → {f.hint}")


@refs_app.command("assign")
def refs_assign(
    relpath: str = typer.Argument(..., help="media/refs 下的文件相对路径"),
    shot: Optional[str] = typer.Option(None, "--shot", help="归属到某个镜头(重命名为 {shot}_ref)"),
    character: Optional[str] = typer.Option(
        None, "--character", help="归属到某个 bible 角色(重命名为 {id}_ref 并写 ref_image)"),
    scene: Optional[str] = typer.Option(
        None, "--scene", help="归属到某个 bible 场景(重命名为 {id}_ref 并写 ref_image)"),
    prop: Optional[str] = typer.Option(
        None, "--prop", help="归属到某个 bible 道具(重命名为 {id}_ref 并写 ref_image)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """把一个 media/refs 文件关联到唯一归属(goal item 3)——修正的是真相本身
    (重命名文件 / 写 bible ref_image 字段),不是一份会漂移的并行索引。

    --shot / --character / --scene / --prop 四选一,精确一个。文件已被 bible
    钉住时,重命名会同步更新那个指针,永不留下悬空引用。"""
    from .core.refs import RefsError, assign_ref

    project = _project()
    with _write_lock(project):
        try:
            result = assign_ref(project, relpath, shot=shot, character=character,
                                scene=scene, prop=prop, actor=ACTOR)
        except RefsError as exc:
            _fail(str(exc), code="refs_assign_invalid")
            raise  # unreachable

    if as_json:
        _emit(result, True)
        return
    if result["renamed"]:
        typer.secho(f"refs: {result['old']} → {result['new']}  归属: {result['address']}",
                    fg=typer.colors.GREEN)
    else:
        typer.secho(f"refs: {result['new']} 已归属 {result['address']}(文件名未变)",
                    fg=typer.colors.GREEN)
    if result["repointed"]:
        typer.echo("  已同步更新的 bible 指针: " + ", ".join(result["repointed"]))


# ------------------------------------------------------------------ tasks


def _task_status(run: dict) -> str:
    """Ledger status → display status. content_rejected surfaces as its own
    'moderation-rejected' bucket (§8.4); everything else passes through."""
    if run.get("status") == "failed" and run.get("failure_kind") == "content_rejected":
        return "moderation-rejected"
    return str(run.get("status") or "")


def _reason_tail(text: str | None, limit: int = 90) -> str:
    """The tail of an error / rejection reason, collapsed to one line."""
    if not text:
        return ""
    one = " ".join(str(text).split())
    return one if len(one) <= limit else "…" + one[-limit:]


def _unresolved_submissions(project, state) -> list[dict]:
    """DR06 — the `manju tasks --json` unresolved_submissions section (ruling 11).

    One row per submission still needing resolution (DISPATCHING / ADMITTED-
    without-terminal / OUTCOME_UNKNOWN), each carrying the honest recovery
    contract: ``automatic_resubmit: false`` always, the disposition, the offered
    actions (attach/abandon only — reconcile is SKIPPED_WITH_EVIDENCE), and a
    ``evidence_chain_ok`` flag (a corrupt per-submission chain fail-closes only
    that shot+provider). NO 'retry unknown' anywhere."""
    from .build.attempts import read_submission_events
    from .providers import submission as S

    out: list[dict] = []
    for row in state.unresolved_submissions():
        sid = row["submission_id"]
        events, _ = read_submission_events(project, sid)
        ok, broken_at = S.verify_chain(events)
        st = S.normalize_state(row.get("state"))
        disposition = (S.OUTCOME_UNKNOWN_DISPOSITION
                       if st in S.SIDE_EFFECT_AMBIGUOUS else st)
        out.append({
            "submission_id": sid,
            "shot": row.get("shot"),
            "provider": row.get("provider"),
            "request_digest": row.get("request_digest"),
            "state": st,
            "remote_job_id": row.get("remote_job_id"),
            "updated": row.get("updated_ts"),
            "events": len(events),
            "disposition": disposition,
            "automatic_resubmit": False,
            "possible_remote_side_effect": st in S.SIDE_EFFECT_AMBIGUOUS,
            "evidence_chain_ok": ok,
            "evidence_chain_broken_at": broken_at,
            "actions": ["attach_remote_job", "abandon_with_duplicate_risk"],
        })
    return out


def _load_submission(state, submission_id: str) -> dict:
    row = state.get_submission(submission_id)
    if row is None or row.get("state") is None:
        _fail(f"没有 submission {submission_id}(见 `manju tasks --json` 的 "
              f"unresolved_submissions)")
    return row


def _emit_recovery_transition(project, state, row, to_state, *, reason_code,
                              remote_job_id=None, detail=None):
    """Emit ONE recovery transition event (attach/abandon) onto the submission's
    hash chain and update the SQLite projection. Enforces transition legality via
    the pure state machine; the chain tail digest links the new event."""
    from .build.attempts import append_submission_event, read_submission_events
    from .providers import submission as S

    from_state = S.normalize_state(row.get("state"))
    S.assert_transition(from_state, to_state)  # IllegalTransition -> caught by caller
    events, _ = read_submission_events(project, row["submission_id"])
    prev = S.submission_event_digest(events[-1]) if events else None
    append_submission_event(
        project, submission_id=row["submission_id"],
        request_digest=row.get("request_digest"), from_state=from_state,
        to_state=to_state, provider_id=row.get("provider"), shot=row.get("shot"),
        remote_job_id=remote_job_id, reason_code=reason_code, prev_event_digest=prev,
        detail=detail, actor=ACTOR)
    state.set_submission_state(row["submission_id"], to_state, remote_job_id=remote_job_id)


tasks_app = typer.Typer(
    no_args_is_help=False,
    help="任务 / tasks — the run-ledger JOB/QUEUE view (§8.3, goal 19). "
         "`manju tasks` alone lists recent runs; `cancel`/`retry` act on one row.",
)
app.add_typer(tasks_app, name="tasks")


@tasks_app.callback(invoke_without_command=True)
def tasks(ctx: typer.Context,
          n: int = typer.Option(20, "-n", help="how many recent ledger rows to show"),
          as_json: bool = typer.Option(False, "--json")):
    """Recent generation jobs from the run ledger — the JOB/QUEUE view (§8.3, goal 19).

    Read-only view of the disposable SQLite ledger: recent runs with provider,
    shot, status (succeeded/failed/moderation-rejected), cost and error tail,
    plus still-in-flight cloud jobs (submitted/polling). Footer aggregates spend
    by provider and the project total (the same numbers `manju status` shows).

    For the MONEY view — per-shot/per-provider breakdowns and estimate-vs-actual
    delta — use `manju spend`; it reads the same ledger through the same query
    helpers, so the two never disagree on the numbers.

    `manju tasks cancel <id>` / `manju tasks retry <id>` act on one row — see
    their own --help for what "cancel" honestly means from the CLI (goal:
    job cancellation, round X)."""
    if ctx.invoked_subcommand is not None:
        return
    project = _project()
    from .runtime.state import RuntimeState

    tasks_out: list[dict] = []
    pending_out: list[dict] = []
    by_provider: list[dict] = []
    total = 0.0
    currency: Optional[str] = None
    available = True
    note = ""
    runs: list[dict] = []
    pending: list[dict] = []
    unresolved: list[dict] = []
    try:
        with RuntimeState(project.root) as state:
            runs = state.run_log(n)
            pending = state.pending_jobs()
            total, currency = state.total_cost()
            by_provider = state.cost_by_provider()
            # DR06 (ruling 11): the unresolved-submission section — in-flight and
            # ambiguous paid submissions with their honest recovery contract.
            unresolved = _unresolved_submissions(project, state)
    except Exception as exc:  # §3: the ledger is disposable — degrade, never crash
        available = False
        note = f"run ledger unavailable ({exc}); try `manju rebuild-index`"

    if available:
        for r in runs:
            tasks_out.append({
                "id": r.get("id"),
                "shot": r.get("shot"),
                "provider": r.get("provider"),
                "status": _task_status(r),
                "cost": float(r.get("cost") or 0.0),
                "currency": r.get("currency"),
                "created": r.get("ts"),
                "updated": r.get("ts"),
                "take": r.get("take"),
                "remote_job_id": r.get("remote_job_id"),
                "reason": _reason_tail(r.get("error")),
                # goal 10: link the ledger row to its full failure record — the
                # one-line reason here matches reports/failures.jsonl exactly.
                "failure_id": r.get("failure_id"),
                # DR03C: the stage_attempt id in events.jsonl that produced this
                # take (null for legacy rows / rebuilt-from-sidecar rows).
                "attempt_id": r.get("attempt_id"),
            })
        for j in pending:
            pending_out.append({
                "shot": j.get("shot"),
                "provider": j.get("provider"),
                "status": "polling",
                "remote_job_id": j.get("remote_job_id"),
                "created": j.get("submitted_at"),
                "updated": j.get("updated_at"),
            })

    payload = {
        "tasks": tasks_out,
        "pending": pending_out,
        "unresolved_submissions": unresolved,
        "spend": {"by_provider": by_provider, "total": float(total), "currency": currency},
        "shown": len(tasks_out),
    }
    if as_json:
        if not available:
            payload["note"] = note
        _emit(payload, True)
        return

    if not available:
        typer.secho(note, fg=typer.colors.YELLOW)
        return
    typer.secho(f"任务 / tasks  (最近 {len(tasks_out)})", fg=typer.colors.CYAN)
    if not tasks_out:
        typer.echo("  —(登记账本为空;云生成/redo 后才有记录)")
    _status_color = {
        "succeeded": typer.colors.GREEN,
        "failed": typer.colors.RED,
        "moderation-rejected": typer.colors.MAGENTA,
    }
    for t in tasks_out:
        cost = f"{t['cost']:g} {t['currency']}" if t["cost"] else "—"
        head = (f"  #{t['id']}  {t['shot'] or '—':<6}  {t['provider'] or '—':<14}  ")
        typer.echo(head, nl=False)
        typer.secho(f"{t['status']:<20}", fg=_status_color.get(t["status"], typer.colors.WHITE), nl=False)
        typer.echo(f"  {cost}  {t['created'] or ''}"
                   + (f"  {t['take']}" if t["take"] else ""))
        if t["reason"]:
            typer.secho(f"        ↳ {t['reason']}", fg=typer.colors.BRIGHT_BLACK)
    if pending_out:
        typer.secho("进行中 / in-flight (submit 后仍在续轮询):", fg=typer.colors.BRIGHT_BLACK)
        for p in pending_out:
            typer.echo(f"  {p['remote_job_id']}  {p['shot'] or '—'}  {p['provider'] or '—'}  "
                       f"polling  {p['created'] or ''}")
    if unresolved:
        typer.secho("未决提交 / unresolved submissions (结果未知,绝不自动重提):",
                    fg=typer.colors.YELLOW)
        for u in unresolved:
            corrupt = "" if u["evidence_chain_ok"] else "  ⚠证据链损坏/CORRUPT"
            typer.echo(f"  {u['submission_id']}  {u['shot'] or '—'}  "
                       f"{u['provider'] or '—'}  {u['state']}  "
                       f"job={u['remote_job_id'] or '—'}{corrupt}")
            typer.secho(
                f"        ↳ automatic_resubmit: false — 恢复: "
                f"`manju tasks attach-remote-job {u['submission_id']} <remote_job_id>` "
                f"或 `manju tasks abandon {u['submission_id']} --reason ...`",
                fg=typer.colors.BRIGHT_BLACK)
    typer.secho("花费 / spend by provider:", fg=typer.colors.BRIGHT_BLACK)
    for row in by_provider:
        cur = row["currency"] or "?"
        typer.echo(f"  {row['provider'] or '—':<14}  {row['cost']:g} {cur}  ({row['runs']} runs)")
    total_cur = currency or "(混合/mixed)"
    typer.secho(f"  合计 / total  {float(total):g} {total_cur}", fg=typer.colors.CYAN)


@tasks_app.command("cancel")
def tasks_cancel(
    job_id: str = typer.Argument(..., help="run-ledger row id (see `manju tasks`)"),
):
    """Cancel a task — HONEST scope note (goal: job cancellation, round X).

    The GUI (`manju gui`) has a LIVE in-process job registry (gui/jobs.py
    JobRunner): a running build/redo/voice job there can be cooperatively
    canceled from the same server process that is running it, because the
    cancel request and the running job share memory (a threading.Event) —
    see the GUI queue panel's 取消 button.

    The CLI has NO such registry. `manju build` / `manju redo` / `manju voice`
    run SYNCHRONOUSLY in the terminal that invoked them; this ledger
    (`manju tasks`) records already-submitted/finished attempts, not a live
    queue a second CLI invocation could reach into. So this command refuses
    to pretend — it can never actually stop a build running in another
    terminal. It always exits non-zero with the honest alternative."""
    typer.secho(
        "manju tasks cancel: CLI 任务没有可取消的进行中队列(GUI 才有 —— `manju gui`)。\n"
        "  · 若某个 manju build/redo/voice 正在别的终端跑,请在那个终端按 Ctrl-C\n"
        "    终止它 —— 构建会正常收尾(build_lock 释放,已生成分段作为内容寻址\n"
        "    缓存保留,可复用,不会回滚)。\n"
        "  · 若你想停掉一条已提交、还在轮询的云端任务(`manju tasks` 的\n"
        "    进行中/pending 一栏),目前只能等它在下一次构建/轮询时收尾;远程\n"
        "    任务可能已经在计费,job id 已记录(§8.1),不会被重复提交。\n"
        "  · 交互式取消(真正生效)见 `manju gui` 的任务面板。",
        fg=typer.colors.YELLOW)
    raise typer.Exit(1)


@tasks_app.command("retry")
def tasks_retry(
    run_id: int = typer.Argument(..., help="run-ledger row id to retry (see `manju tasks`)"),
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="approve ask_before-gated spend (§8.3)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Retry a FAILED run-ledger row — a direct, synchronous re-run of the
    same shot/provider recipe (goal: honest job retry).

    Unlike the GUI's retry (a fresh JobRunner job with `retry_of` lineage,
    re-enqueued behind whatever else is queued), the CLI has no live job
    registry to re-enqueue into: this runs the SAME §8.3-gated `redo_shot`
    any `manju redo` uses, right now, sourced from the failed row's recorded
    shot/provider/seed instead of typed-out flags — a fresh ledger row lands
    on success, exactly like typing `manju redo <shot>` again would.

    Refuses (one line, never a traceback) a row that is not `failed`, or
    whose shot no longer exists — never silently retries something that can
    no longer validate."""
    project = _project()
    from .runtime.state import RuntimeState

    with RuntimeState(project.root) as state:
        row = state.get_run(run_id)
    if row is None:
        _fail(f"没有 id 为 {run_id} 的任务记录(见 `manju tasks`)")
    if row.get("status") != "failed":
        _fail(f"只能重试已失败的任务(#{run_id} 当前状态: {row.get('status')})")
    shot_id = row.get("shot")
    if not shot_id:
        _fail(f"#{run_id} 没有记录 shot,无法重试")
    if not project.shot_path(shot_id).exists():
        _fail(f"#{run_id} 对应的镜头 {shot_id} 已不存在,无法重试")
    provider = row.get("provider") or None
    try:
        params = json.loads(row.get("params") or "{}") or {}
    except (json.JSONDecodeError, TypeError):
        params = {}
    seed = params.get("seed") if isinstance(params, dict) else None

    from .build.graph import BuildError, WaitingUser, redo_shot

    try:
        takes = redo_shot(project, shot_id, provider=provider,
                          seed=int(seed) if seed is not None else None,
                          actor=ACTOR, assume_yes=yes)
    except (BuildError, WaitingUser) as exc:
        _fail(str(exc))
    payload = {"ok": True, "retry_of": run_id, "shot": shot_id, "takes": takes}
    _emit(payload, as_json)
    if not as_json:
        typer.secho(f"已重试 #{run_id} → {shot_id}: {', '.join(takes)}",
                    fg=typer.colors.GREEN)


@tasks_app.command("attach-remote-job")
def tasks_attach_remote_job(
    submission_id: str = typer.Argument(..., help="unresolved submission id (see `manju tasks --json`)"),
    remote_job_id: str = typer.Argument(..., help="the remote job id you confirmed out-of-band"),
    expected_state: Optional[str] = typer.Option(
        None, "--expected-state", help="guard: only act if the submission is in this state"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Attach a remote job id to an UNKNOWN/DISPATCHING submission (DR06 §11.5).

    The HONEST recovery for an ambiguous outcome once YOU have confirmed, in the
    provider's console, that the remote job DID run: it appends an ADMITTED
    evidence event under the SAME submission_id (never a new submission), updates
    the projection, and hands the job to the normal poll path on the next build —
    it NEVER claims verified ownership itself (you asserted it) and NEVER
    resubmits. The provider is taken from the submission row (you cannot attach a
    job from a different provider)."""
    from .providers import submission as S
    from .runtime.state import RuntimeState

    project = _project()
    with RuntimeState(project.root) as state:
        row = _load_submission(state, submission_id)
        st = S.normalize_state(row.get("state"))
        if expected_state and st != expected_state:
            _fail(f"submission {submission_id} 当前状态 {st} ≠ 期望 {expected_state}")
        # P0 WP3: the RECOVERY_EVIDENCE_CORRUPT sentinel is attachable too — a
        # corrupt-evidence submission resolves ONLY via attach/abandon.
        if st not in (S.OUTCOME_UNKNOWN, S.DISPATCHING, S.RECOVERY_EVIDENCE_CORRUPT):
            _fail(f"只能给 UNKNOWN/DISPATCHING/EVIDENCE_CORRUPT 的 submission 关联"
                  f"远程任务(当前 {st});已 ADMITTED/terminal 的无需关联")
        try:
            _emit_recovery_transition(
                project, state, row, S.ADMITTED, reason_code="attach_remote_job",
                remote_job_id=remote_job_id,
                detail={"attached_by": ACTOR, "claimed_verified_ownership": False})
        except S.IllegalTransition as exc:
            _fail(str(exc))
    payload = {"ok": True, "submission_id": submission_id, "state": S.ADMITTED,
               "remote_job_id": remote_job_id, "note": "poll-only on next build; "
               "no resubmit; ownership asserted by operator, not verified"}
    _emit(payload, as_json)
    if not as_json:
        typer.secho(f"已关联 {submission_id} → 远程任务 {remote_job_id}(ADMITTED);"
                    f"下次构建将仅轮询,不会重提。", fg=typer.colors.GREEN)


@tasks_app.command("abandon")
def tasks_abandon(
    submission_id: str = typer.Argument(..., help="unresolved submission id (see `manju tasks --json`)"),
    reason: str = typer.Option(..., "--reason", help="why (recorded on the evidence event)"),
    expected_state: Optional[str] = typer.Option(
        None, "--expected-state", help="guard: only act if the submission is in this state"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Abandon an unresolved submission (DR06 §11.6) → ABANDONED_BY_USER.

    Accepts the duplicate-risk EXPLICITLY (the remote side may have run and may
    bill): records an ABANDONED_BY_USER evidence event carrying your reason and
    the risk acceptance, and stops the submission blocking the shot+provider. The
    history is preserved (append-only); a NEW submission_id is only ever minted by
    the next explicit redo/build — abandon itself never resubmits."""
    from .providers import submission as S
    from .runtime.state import RuntimeState

    project = _project()
    with RuntimeState(project.root) as state:
        row = _load_submission(state, submission_id)
        st = S.normalize_state(row.get("state"))
        if expected_state and st != expected_state:
            _fail(f"submission {submission_id} 当前状态 {st} ≠ 期望 {expected_state}")
        if st in S.TERMINAL_STATES:
            _fail(f"submission {submission_id} 已是终态 {st},无需 abandon")
        try:
            _emit_recovery_transition(
                project, state, row, S.ABANDONED_BY_USER, reason_code="abandoned_by_user",
                detail={"reason": S.redact_reason(reason), "abandoned_by": ACTOR,
                        "duplicate_risk_accepted": True})
        except S.IllegalTransition as exc:
            _fail(str(exc))
    payload = {"ok": True, "submission_id": submission_id, "state": S.ABANDONED_BY_USER,
               "reason": reason, "duplicate_risk_accepted": True}
    _emit(payload, as_json)
    if not as_json:
        typer.secho(f"已放弃 {submission_id}(ABANDONED_BY_USER,接受重复风险);"
                    f"历史保留,后续 redo/build 才会生成新的 submission。",
                    fg=typer.colors.YELLOW)


@tasks_app.command("manifest")
def tasks_manifest(
    run_id: str = typer.Argument(..., help="build run id (see `manju build --json`)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Re-materialize + print a run's RunManifest (DR03C).

    The RunManifest (`reports/runs/<run_id>/run.json`) is a DERIVED, deletable
    projection of the run's attempt evidence in events.jsonl — it is NEVER read
    by build/resume/cache/rebuild-index, so deleting it costs nothing. This is
    the user path to rebuild one: it re-derives the manifest from the single
    events.jsonl attempt stream and prints its path (and, with `--json`, the
    full document). A run id with no attempt events yields an empty-but-valid
    manifest — the projection never invents facts from files on disk."""
    project = _project()
    from .build.attempts import materialize_run_manifest, read_attempts

    _records, _malformed = read_attempts(project, run_id)
    path = materialize_run_manifest(project, run_id)
    rel = project.relpath(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if as_json:
        _emit({"run_id": run_id, "path": rel, "manifest": data}, True)
        return
    typer.secho(f"RunManifest → {rel}", fg=typer.colors.CYAN)
    typer.echo(f"  terminal_status : {data.get('terminal_status')}")
    typer.echo(f"  attempts        : {data.get('attempt_count')}"
               + (f"  (malformed lines: {_malformed})" if _malformed else ""))
    stages = data.get("stages") or {}
    for stage, states in stages.items():
        rollup = ", ".join(f"{st}×{n}" for st, n in states.items())
        typer.echo(f"    {stage:<10} {rollup}")
    costs = data.get("costs") or []
    if costs:
        typer.echo("  costs           : "
                   + ", ".join(f"{c['amount']:g} {c['currency']}" for c in costs))
    finals = [o.get("path") for o in (data.get("final_output_refs") or [])]
    if finals:
        typer.echo(f"  final outputs   : {', '.join(str(f) for f in finals)}")
    if data.get("qc_report_refs"):
        typer.echo(f"  qc reports      : {', '.join(data['qc_report_refs'])}")
    if data.get("failures"):
        typer.secho(f"  failures        : {len(data['failures'])}", fg=typer.colors.YELLOW)
    typer.echo(f"  evidence_digest : {data.get('evidence_digest')}")


# ------------------------------------------------------------------ spend


@app.command()
def spend(as_json: bool = typer.Option(False, "--json")):
    """Accumulated spend — the MONEY view (§8.3 事后逐笔记账, made visible).

    Where the money went: per-provider and per-shot breakdowns, the recent runs,
    and (once dry-run estimates are persisted) estimate-vs-actual delta. Reads the
    same disposable run ledger `manju tasks` does, through the same query helpers
    (runtime/state.py cost_by_provider/cost_by_shot); degrades to the on-disk take
    sidecars when the ledger is gone (§3). `tasks` is the JOB/QUEUE view; this is
    the money lens over the same truth."""
    from .build.spend import spend_report

    report = spend_report(_project())
    if as_json:
        _emit(report, True)
        return

    typer.secho(f"花费 / spend  (来源/source: {report['source']})", fg=typer.colors.CYAN)
    # goal 79: never print a currency-mislabeled total — with more than one
    # currency in play, show every currency's own total instead of merging.
    by_currency = report.get("by_currency") or []
    if len(by_currency) > 1:
        parts = " + ".join(f"{c['cost']:g} {c['currency'] or '?'}" for c in by_currency)
        typer.secho(f"  合计 / total  {parts}(混合币种,按币种分列/mixed currencies)",
                    fg=typer.colors.CYAN)
    else:
        cur = report["currency"] or ""
        typer.secho(f"  合计 / total  {report['total']:g} {cur}", fg=typer.colors.CYAN)
    if report["estimated_total"] is not None:
        delta = report["delta"] or 0.0
        sign = "+" if delta >= 0 else ""
        coverage = f"  ({report['delta_coverage']})" if report.get("delta_coverage") else ""
        typer.echo(f"  预估 / estimated  {report['estimated_total']:g}"
                   f"   δ(实际−预估) {sign}{delta:g}{coverage}")
    if report["budget_limit"] is not None:
        typer.echo(f"  预算 / budget.limit  {report['budget_limit']:g}")

    if report["by_provider"]:
        typer.secho("按供应商 / by provider:", fg=typer.colors.BRIGHT_BLACK)
        for row in report["by_provider"]:
            typer.echo(f"  {row['provider'] or '—':<14}  {row['cost']:g}  ({row['runs']} runs)")
    if report["by_shot"]:
        typer.secho("按镜头 / by shot:", fg=typer.colors.BRIGHT_BLACK)
        for row in report["by_shot"]:
            typer.echo(f"  {row['shot'] or '—':<8}  {row['cost']:g}  ({row['runs']} runs)")
    if report["recent"]:
        typer.secho("最近 / recent:", fg=typer.colors.BRIGHT_BLACK)
        for r in report["recent"]:
            est = f"  (est {r['estimated_cost']:g})" if r["estimated_cost"] is not None else ""
            rc = r["currency"] or ""
            typer.echo(f"  {r['ts'] or '':<20}  {r['shot'] or '—':<6}  "
                       f"{r['provider'] or '—':<14}  {r['status']:<10}  "
                       f"{float(r['cost'] or 0.0):g} {rc}{est}")
    typer.secho("（任务/队列视图见 manju tasks）", fg=typer.colors.BRIGHT_BLACK)


# --------------------------------------------------------------- failures


@app.command()
def failures(n: int = typer.Option(10, "-n", help="how many recent failures to show"),
             as_json: bool = typer.Option(False, "--json")):
    """Recent failures, newest first — make EVERY failure debuggable (goal 10).

    Rendered rustc-style: each record names the STEP and SUBJECT, the one-line
    CAUSE, a short verbatim EVIDENCE slice (ffmpeg stderr + argv head / HTTP
    status + body head / the reason a poll returned), one actionable HINT, and
    where the fuller LOG lives. Degradations (a fallback fired, QC gated, a card
    skipped) show in the same shape marked ℹ — so "为什么这个镜头变成了字幕卡?"
    is answerable here. Reads the append-only reports/failures.jsonl; the JOB
    view with cost is `manju tasks`, the money lens is `manju spend`."""
    project = _project()
    from .core.failures import read_failures

    recs = read_failures(project, n)
    if as_json:
        _emit({"failures": recs, "shown": len(recs)}, True)
        return
    if not recs:
        typer.echo("暂无失败记录 — reports/failures.jsonl 为空(一切顺利,或还没构建过)")
        return
    typer.secho(f"失败 / failures  (最近 {len(recs)},新→旧)", fg=typer.colors.CYAN)
    for r in recs:
        is_err = r.get("level") != "info"
        sym = "✗" if is_err else "ℹ"
        head_color = typer.colors.RED if is_err else typer.colors.YELLOW
        typer.secho(f"{sym} {r.get('step', '?')} · {r.get('subject', '?')}",
                    fg=head_color, bold=True, nl=False)
        typer.secho(f"   {r.get('ts', '')}  #{r.get('id', '')}",
                    fg=typer.colors.BRIGHT_BLACK)
        typer.echo(f"   {r.get('cause', '')}")
        evidence = r.get("evidence") or ""
        for line in evidence.splitlines():
            typer.secho(f"     │ {line}", fg=typer.colors.BRIGHT_BLACK)
        if r.get("hint"):
            typer.secho(f"   help: {r['hint']}", fg=typer.colors.CYAN)
        if r.get("log_path"):
            typer.secho(f"   log:  {r['log_path']}", fg=typer.colors.BRIGHT_BLACK)


# ------------------------------------------------------------------ evaluate


@app.command()
def evaluate(as_json: bool = typer.Option(False, "--json")):
    """技能/工作流的诚实用量评估 (round AA, goal item 8)。

    只读 events.jsonl + reports/qc_agent.jsonl:技能实际被读取的次数、
    redo/repair 的镜头级返工热点、AI QC 判读的 blocker/issue/fyi 分布——
    从不编造生产率或质量归因数字(见结尾的『不能证明』段落)。只读,不加锁。
    """
    from .core.evaluate import evaluate as _evaluate

    project = _project()
    report = _evaluate(project)
    if as_json:
        _emit(report, True)
        return

    typer.secho(f"评估报告 / evaluate  ({report['project'] or '?'})",
                fg=typer.colors.CYAN, bold=True)
    typer.echo(f"日志事件共 {report['events_total']} 条")

    sk = report["skills"]
    typer.secho(f"\n技能使用 / skills  ({sk['used_total']}/{sk['installed_total']} 用过)",
                fg=typer.colors.CYAN)
    used_rows = [r for r in sk["usage"] if r["count"] > 0]
    if not used_rows:
        typer.secho("  （还没有任何 skill_used 记录）", fg=typer.colors.BRIGHT_BLACK)
    for row in used_rows:
        via = ", ".join(f"{k}={v}" for k, v in row["by_via"].items())
        low = "  [low-N]" if row["low_n"] else ""
        typer.echo(f"  {row['id']:<24} {row['count']:>4} 次  最近 {row['last_used'] or '—'}"
                    f"  via: {via}{low}")
    if sk["never_used"]:
        typer.secho("  从未用过: " + ", ".join(sk["never_used"]), fg=typer.colors.BRIGHT_BLACK)

    wf = report["workflow"]
    typer.secho("\n返工热点 / rework hotspots", fg=typer.colors.CYAN)
    typer.echo(f"  redo 共 {wf['redo']['total']} 次")
    for h in wf["redo"]["hotspots"]:
        low = "  [low-N]" if h["low_n"] else ""
        typer.echo(f"    {h['shot']:<10} {h['count']:>4} 次{low}")
    typer.echo(f"  repair 共 {wf['repair']['total']} 次")
    for h in wf["repair"]["hotspots"]:
        low = "  [low-N]" if h["low_n"] else ""
        typer.echo(f"    {h['shot']:<10} {h['count']:>4} 次{low}")
    fn = wf["funnel"]
    if fn["never_scaffolded"]:
        typer.secho("  从未脚手架的阶段: " + ", ".join(fn["never_scaffolded"]),
                    fg=typer.colors.BRIGHT_BLACK)

    qc = report["qc"]
    typer.secho(f"\nQC 结果 / qc verdicts  (共 {qc['verdicts_total']} 条判读)",
                fg=typer.colors.CYAN)
    lv = qc["by_level"]
    typer.echo(f"  blocker={lv['blocker']}  issue={lv['issue']}  fyi={lv['fyi']}")
    if qc["malformed_lines"]:
        typer.secho(f"  {qc['malformed_lines']} 行无法解析,已跳过", fg=typer.colors.YELLOW)

    honesty = report["honesty"]
    typer.secho(f"\n提示 / honesty — {honesty['summary']}", fg=typer.colors.YELLOW, bold=True)
    for line in honesty["cannot_claim"]:
        typer.secho(f"  · {line}", fg=typer.colors.YELLOW)


# ------------------------------------------------------------------- misc


def _event_line(e: dict) -> str:
    return (f"{e.get('ts','?')}  [{e.get('actor','?')}]  {e.get('action','?')}  "
            f"{json.dumps(e.get('detail', {}), ensure_ascii=False)}")


@app.command()
def events(n: int = typer.Option(20, "-n"), as_json: bool = typer.Option(False, "--json"),
           follow: bool = typer.Option(False, "--follow", "-f",
                                       help="live-tail the log (§10 co-presence)")):
    """Tail the collaboration log (who did what, when — §10).

    --follow keeps the terminal open and streams each new record as it lands —
    a second terminal's live window on the AI (or human) working next door.
    """
    project = _project()
    entries = tail_events(project.root, n)
    if as_json and not follow:
        _emit(entries, True)
        return
    for e in entries:
        typer.echo(_event_line(e))
    if follow:
        from .core.events import follow_events

        try:
            for e in follow_events(project.root):
                typer.echo(json.dumps(e, ensure_ascii=False) if as_json else _event_line(e))
        except KeyboardInterrupt:
            pass


@app.command()
def watch(interval: float = typer.Option(0.8, help="poll interval seconds"),
          once: bool = typer.Option(False, "--once", help="one check, then exit")):
    """Dev loop (§10): re-run `manju check` whenever truth changes — edit YAML
    in your editor, watch findings land here (watchexec/jest-watch pattern).

    Strictly read-only: it only stats files and runs the read-only check, so it
    never contends with `manju board --serve` or a running build.
    """
    from .build.watchloop import watch_ticks

    project = _project()
    typer.secho(f"watching {project.root.name} (Ctrl-C to stop)", fg=typer.colors.CYAN)
    exit_code = 0
    try:
        for tick in watch_ticks(project, interval_s=interval,
                                max_ticks=1 if once else None):
            mark = "✓" if tick.check_ok else ("?" if tick.check_ok is None else "✗")
            color = (typer.colors.GREEN if tick.check_ok
                     else typer.colors.YELLOW if tick.check_ok is None
                     else typer.colors.RED)
            typer.secho(f"{tick.ts}  {mark} check "
                        f"{'ok' if tick.check_ok else f'{len(tick.errors)} errors'}",
                        fg=color)
            for err in tick.errors[:8]:
                typer.echo(f"    ✗ {err}")
            exit_code = 0 if tick.check_ok else 1
    except KeyboardInterrupt:
        typer.echo("\nbye")
    raise typer.Exit(exit_code if once else 0)


# ------------------------------------------------- history/snapshot/rollback


@app.command()
def history(n: int = typer.Option(30, "-n"), as_json: bool = typer.Option(False, "--json")):
    """The merged change feed (P2 §10): events.jsonl (who did what) interleaved
    with the project's git log (committed disk state), oldest→newest. Read-only
    — the compare/rollback entry point."""
    from .core.history import history as _history

    rows = _history(_project(), n=n)
    if as_json:
        _emit(rows, True)
    else:
        for r in rows:
            tag = r["actor"] if r["source"] == "event" else f"git {r['detail'].get('sha', '')}"
            typer.echo(f"{r['ts']}  [{tag}]  {r['text']}")


@app.command()
def snapshot(label: str = typer.Argument("", help="checkpoint label")):
    """Labeled git checkpoint of the truth text (git is the patch engine, §3).
    `manju rollback file --to <sha>` returns to it. A clean tree is a no-op."""
    from .core.history import HistoryError, snapshot as _snapshot

    project = _project()
    try:
        with _write_lock(project):
            result = _snapshot(project, label)
    except HistoryError as exc:
        _fail(str(exc))
    if result["clean"]:
        typer.secho(f"nothing to snapshot — tree already checkpointed at {result['sha']}",
                    fg=typer.colors.YELLOW)
    else:
        typer.secho(f"snapshot {result['sha']}"
                    + (f"  ({result['label']})" if result["label"] else ""),
                    fg=typer.colors.GREEN)


@app.command()
def rollback(
    what: str = typer.Argument(..., help="'shot' or 'file'"),
    target: str = typer.Argument(..., help="shot id, or a project-relative truth-text path"),
    to: str = typer.Option("HEAD", "--to", help="git ref for file rollback (default HEAD)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Roll back one scoped thing; history only ever grows (P2).

    `rollback shot S002` re-selects the previously selected take (the newer
    take stays on disk for compare). `rollback file timeline/rules.yaml
    [--to <sha>]` restores ONE truth-text file from git — media, renders and
    exports are never touched. Every rollback is itself an event."""
    from .core.history import HistoryError, rollback_file, rollback_shot

    project = _project()
    try:
        with _write_lock(project):
            if what == "shot":
                result = rollback_shot(project, target)
                human = f"{result['shot']}: back to {result['take']} (was {result['was'] or '—'})"
            elif what == "file":
                result = rollback_file(project, target, ref=to)
                findings = run_check(project)
                result["check_errors"] = len(findings.errors)
                human = f"restored {result['path']} from {result['ref']}"
                if findings.errors:
                    human += f"  (⚠ check now reports {len(findings.errors)} error(s))"
            else:
                _fail("rollback what? use: rollback shot <id> | rollback file <path> [--to <ref>]")
    except HistoryError as exc:
        _fail(str(exc))
    if as_json:
        _emit(result, True)
    else:
        typer.secho(human, fg=typer.colors.GREEN)


@app.command()
def compare(
    a: Optional[str] = typer.Argument(None, help="older final name (e.g. final_v2)"),
    b: Optional[str] = typer.Argument(None, help="newer final name (e.g. final_v3)"),
    show_unchanged: bool = typer.Option(
        False, "--all", help="also list shots that did not change"),
    as_json: bool = typer.Option(False, "--json"),
    against_baseline: bool = typer.Option(
        False, "--against-baseline",
        help="AI_IDE_07C: diff the candidate final against the approved release "
             "baseline (same diff engine; baseline is always the reference side)"),
    candidate: Optional[str] = typer.Option(
        None, "--candidate", help="which final is the candidate (default: current newest)"),
):
    """Diff two finals — what changed between final_vA and final_vB (goal 11).

    Duration/resolution/fps deltas over a per-shot change map (same/different
    take with each take's provider, added/removed/moved/duration-changed),
    caption cue diff, audio-track diff and packaging diff — each with a
    root-cause line correlated from events.jsonl. Reads the per-final timeline
    snapshots persisted at render time; finals rendered before snapshots
    existed degrade to a key-only diff. Default: the latest two finals.

    AI_IDE_07C: `--against-baseline` diffs the candidate against the approved
    release baseline through the SAME engine, adding a `review_status`
    (UNCHANGED / CHANGED_REQUIRES_REVIEW); it never auto-approves or rolls back."""
    from .build.compare import CompareError, compare_finals

    if against_baseline:
        from .build import baseline as _bl

        try:
            diff = _bl.compare_against_baseline(_project(), candidate)
        except (_bl.BaselineError, CompareError) as exc:
            _fail(str(exc))
        if as_json:
            _emit(diff, True)
            return
        typer.secho(f"发布基线比较 / against baseline: {diff['review_status']}",
                    fg=typer.colors.CYAN)
        typer.echo(f"  baseline  {diff['baseline']['status']}"
                   + (f"  {diff['baseline'].get('artifact_sha256')}"
                      if diff['baseline'].get('artifact_sha256') else ""))
        if diff.get("note"):
            typer.secho(f"  {diff['note']}", fg=typer.colors.YELLOW)
            return
        changed = [c for c in diff.get("changes", []) if c["change"] != "unchanged"]
        typer.secho(f"  变更 changes: {len(changed)}",
                    fg=typer.colors.MAGENTA if changed else typer.colors.GREEN)
        for c in changed:
            typer.echo(f"    {c['shot']:<16}  {c['change']}")
        return

    try:
        diff = compare_finals(_project(), a, b)
    except CompareError as exc:
        _fail(str(exc))
    if as_json:
        _emit(diff, True)
        return

    ha, hb = diff["a"], diff["b"]
    typer.secho(f"比较 compare  {ha['name']} → {hb['name']}", fg=typer.colors.CYAN)
    s = diff["summary"]
    if s.get("duration_delta_ms") is not None:
        d = s["duration_delta_ms"]
        sign = "+" if d >= 0 else ""
        typer.echo(f"  时长 duration  {s['duration_a_ms']}ms → {s['duration_b_ms']}ms"
                   f"  ({sign}{d}ms)")
    typer.echo("  分辨率 resolution  "
               + (f"{s['resolution_a']} → {s['resolution_b']}" if s["resolution_changed"]
                  else f"{s.get('resolution_a') or '—'} (unchanged)"))
    typer.echo("  帧率 fps  "
               + (f"{s['fps_a']} → {s['fps_b']}" if s["fps_changed"]
                  else f"{s.get('fps_a') or '—'} (unchanged)"))

    if diff["degraded"]:
        typer.secho(f"  ⚠ {diff['note']}", fg=typer.colors.YELLOW)
        return

    if diff["identical"]:
        typer.secho("  内容键一致 content keys match — identical renders",
                    fg=typer.colors.GREEN)

    def _provider_arrow(entry_a, entry_b):
        pa = (entry_a or {}).get("provider")
        pb = (entry_b or {}).get("provider")
        return f"  ({pa or '?'}→{pb or '?'})" if pa != pb else ""

    changed = [c for c in diff["changes"] if c["change"] != "unchanged"]
    typer.secho(f"变更 changes ({len(changed)}):", fg=typer.colors.MAGENTA
                if changed else typer.colors.BRIGHT_BLACK)
    for c in changed:
        line = f"  {c['shot']:<16}  {c['change']}"
        if c["change"] in ("take_changed", "duration_changed", "moved", "added", "removed"):
            av, bv = c.get("a") or {}, c.get("b") or {}
            if c["change"] == "take_changed":
                line += (f"  {av.get('take')}→{bv.get('take')}"
                         + _provider_arrow(av, bv))
            elif c["change"] == "duration_changed":
                line += f"  {av.get('duration_ms')}ms→{bv.get('duration_ms')}ms"
            elif c["change"] == "moved":
                line += f"  #{av.get('order')}→#{bv.get('order')}"
            elif c["change"] == "added":
                line += f"  {bv.get('take')} ({bv.get('provider') or '?'})"
            elif c["change"] == "removed":
                line += f"  {av.get('take')} ({av.get('provider') or '?'})"
        elif c["change"] == "captions_changed":
            line += f"  {c['a']['count']}→{c['b']['count']} cues"
        if c.get("why"):
            line += f"  — {c['why']}"
        typer.echo(line)
    if not changed:
        typer.echo("  —（无差异 no differences)")

    if show_unchanged:
        unchanged = [c["shot"] for c in diff["changes"] if c["change"] == "unchanged"]
        if unchanged:
            typer.secho("镜头无变化 unchanged: " + ", ".join(unchanged),
                        fg=typer.colors.BRIGHT_BLACK)


@app.command()
def doctor(as_json: bool = typer.Option(False, "--json")):
    """Environment health: ffmpeg, fonts, disk, project integrity (§14).

    The probe logic lives in build/doctor.py — one engine core (§2) — this
    command only finds the project (if any) and renders the checks it returns.
    """
    from .build.doctor import run_doctor

    try:
        project: Optional[Project] = Project.find(Path.cwd())
    except ProjectError:
        project = None
    info = run_doctor(project)
    if as_json:
        _emit({"checks": [{"name": c["name"], "ok": c["ok"], "detail": c["detail"]}
                          for c in info["checks"]], "ok": info["ok"]}, True)
    else:
        for c in info["checks"]:
            typer.echo(c["line"])
    raise typer.Exit(0 if info["ok"] else 1)


@app.command()
def gc(hard: bool = typer.Option(False, "--hard"),
       as_json: bool = typer.Option(False, "--json")):
    """Reclaim space: segment cache and proxies. --hard (interactive) also
    removes unselected takes. imports/ and final/ are NEVER touched (§14)."""
    project = _project()
    if hard and not _interactive():
        _fail("gc --hard is interactive-only")
    do_hard = hard and typer.confirm(
        "删除所有未选中的 take 媒体本体?(选中、imports、final 不受影响)"
    )
    freed = 0
    ghost_sidecars = 0
    with _write_lock(project):
        for d in (project.segments_dir, project.proxy_dir):
            for f in d.glob("*"):
                if f.is_file():
                    freed += f.stat().st_size
                    f.unlink()
        if do_hard:
            for sid in project.shot_ids():
                shot = project.load_shot(sid)
                for take in project.takes(sid):
                    if take.name != shot.status.selected_take and take.media_path:
                        freed += take.media_path.stat().st_size
                        take.media_path.unlink()
                        # round-W #35: the sidecar (which also carries the
                        # take's virtual-trim / timing fields) rides along —
                        # otherwise a media-less "ghost take" sidecar lingers
                        # forever, still showing up in candidate listings,
                        # numbering (next_take_name), and stats.
                        if take.sidecar_path.exists():
                            take.sidecar_path.unlink()
                            ghost_sidecars += 1
        append_event(project.root, ACTOR, "gc",
                     {"freed_bytes": freed, "hard": hard, "sidecars_removed": ghost_sidecars})
    if as_json:
        _emit({"freed_bytes": freed, "hard": hard, "sidecars_removed": ghost_sidecars}, True)
    else:
        typer.secho(f"freed {freed / 1e6:.1f} MB", fg=typer.colors.GREEN)
        if ghost_sidecars:
            typer.secho(f"  同步删除 {ghost_sidecars} 个 take sidecar(避免残留 ghost take)",
                        fg=typer.colors.BRIGHT_BLACK)


@app.command("rebuild-index")
def rebuild_index(as_json: bool = typer.Option(False, "--json")):
    """Recreate the disposable runtime dir from text + media (§3: SQLite may
    explode at any time). Rescans shot states and re-derives the run ledger
    from take sidecars on disk (§3: sidecars are the authoritative source)."""
    from .build.stale import evaluate_all

    project = _project()
    project.runtime_dir.mkdir(exist_ok=True)
    (project.runtime_dir / "logs").mkdir(exist_ok=True)
    statuses = {s.shot_id: s.state.value for s in evaluate_all(project)}
    # Re-derive the run ledger from sidecars; best-effort — the DB is disposable
    # and a rebuild hiccup must never sink `rebuild-index` (§3).
    ledger = {"runs": 0, "pending_jobs": 0}
    try:
        from .runtime.state import RuntimeState

        with RuntimeState(project.root) as state:
            ledger = state.rebuild(project)
    except Exception as exc:  # disposable state: warn (to stderr) and carry on
        typer.secho(f"⚠ ledger rebuild skipped: {exc}", fg=typer.colors.YELLOW, err=True)
    if as_json:
        _emit({"shots": statuses, "runs": ledger["runs"],
               "pending_jobs": ledger["pending_jobs"]}, True)
    else:
        typer.echo(f"runtime rebuilt; {len(statuses)} shots scanned: "
                   + json.dumps(statuses, ensure_ascii=False))
        typer.echo(f"ledger: {ledger['runs']} runs, {ledger['pending_jobs']} pending jobs")


@app.command()
def schema(out: Optional[Path] = typer.Option(None, help="write one .schema.json per model into this directory")):
    """Export the JSON Schemas of every truth-file model (§4, §12)."""
    from .core.models import export_json_schemas

    schemas = export_json_schemas()
    if out is None:
        typer.echo(json.dumps(schemas, ensure_ascii=False, indent=2))
        return
    from .core.yamlio import write_json

    out.mkdir(parents=True, exist_ok=True)
    for name, schema_dict in schemas.items():
        write_json(out / f"{name}.schema.json", schema_dict)
    typer.secho(f"wrote {len(schemas)} schemas → {out}", fg=typer.colors.GREEN)


# -------------------------------------------------------- private library

lib_app = typer.Typer(no_args_is_help=True,
                      help="Private asset library (local, user-level, never uploaded). "
                           "个人素材库:内容寻址去重 + 标签 + 预览,可复用到任意项目。")
app.add_typer(lib_app, name="lib")


def _lib():
    from .core.library import Library

    return Library()


def _lib_entry_public(entry: dict) -> dict:
    """The index row as the CLI/GUI surface it (hash8 handle up front)."""
    from .core.library import _hex

    return {"hash8": _hex(entry["hash"])[:8], **entry}


@lib_app.command("add")
def lib_add(
    files: list[Path],
    tag: Optional[str] = typer.Option(None, "--tag", help="comma-separated tags"),
    note: Optional[str] = typer.Option(None, "--note", help="a free-text note"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Copy files into the private library, deduped by content hash. The source
    is never moved or deleted; re-adding identical bytes just merges tags."""
    from .core.library import LibraryError, _hex

    lib = _lib()
    tags = [t.strip() for t in (tag or "").split(",") if t.strip()]
    results: list[dict] = []
    for f in files:
        if not f.exists():
            _fail(f"not found: {f}")
        try:
            res = lib.add(f, tags=tags, note=note)
        except LibraryError as exc:
            _fail(str(exc))
        results.append({"name": res["entry"]["name"],
                        "hash8": _hex(res["entry"]["hash"])[:8],
                        "deduped": res["deduped"]})
    if as_json:
        _emit({"added": results, "library": str(lib.root)}, True)
    else:
        for r in results:
            if r["deduped"]:
                typer.secho(f"already in library: {r['name']} ({r['hash8']}) — tags merged",
                            fg=typer.colors.YELLOW)
            else:
                typer.secho(f"added {r['name']} ({r['hash8']})", fg=typer.colors.GREEN)


@lib_app.command("list")
def lib_list(
    tag: Optional[str] = typer.Option(None, "--tag", help="only assets with this tag"),
    kind: Optional[str] = typer.Option(None, "--kind", help="video | image | audio | other"),
    as_json: bool = typer.Option(False, "--json"),
):
    """List library assets (optionally filtered by tag / kind)."""
    lib = _lib()
    entries = lib.list_assets(tag=tag, kind=kind)
    if as_json:
        _emit({"library": str(lib.root),
               "assets": [_lib_entry_public(e) for e in entries]}, True)
        return
    if not entries:
        typer.echo(f"素材库为空 empty library ({lib.root})")
        return
    typer.secho(f"素材库 library ({lib.root}) — {len(entries)} 项", fg=typer.colors.CYAN)
    for e in entries:
        pub = _lib_entry_public(e)
        tags = f"  [{', '.join(e['tags'])}]" if e.get("tags") else ""
        size_mb = (e.get("size") or 0) / 1e6
        typer.echo(f"  {pub['hash8']}  {e['kind']:<6}  {size_mb:6.2f}MB  {e['name']}{tags}")
        if e.get("note"):
            typer.secho(f"        ↳ {e['note']}", fg=typer.colors.BRIGHT_BLACK)


@lib_app.command("show")
def lib_show(hash8: str, as_json: bool = typer.Option(False, "--json")):
    """Show one library asset by its hash handle."""
    from .core.library import LibraryError

    lib = _lib()
    try:
        entry = lib.get(hash8)
    except LibraryError as exc:
        _fail(str(exc))
    pub = _lib_entry_public(entry)
    pub["blob_path"] = str(lib.blob_path(entry))
    if as_json:
        _emit(pub, True)
        return
    typer.secho(f"{pub['hash8']}  {entry['name']}", fg=typer.colors.CYAN)
    typer.echo(f"  kind    {entry['kind']}")
    typer.echo(f"  size    {(entry.get('size') or 0) / 1e6:.2f} MB")
    typer.echo(f"  hash    {entry['hash']}")
    typer.echo(f"  blob    {pub['blob_path']}")
    typer.echo(f"  tags    {', '.join(entry.get('tags') or []) or '—'}")
    typer.echo(f"  added   {entry.get('added')}")
    if entry.get("note"):
        typer.echo(f"  note    {entry['note']}")
    if entry.get("thumb"):
        typer.echo(f"  thumb   {lib.root / entry['thumb']}")


@lib_app.command("use")
def lib_use(
    hash8: str,
    as_: str = typer.Option("refs", "--as", help="refs (media/refs) | imports (media/imports)"),
    name: Optional[str] = typer.Option(None, "--name", help="destination filename (default: original)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Copy a library asset INTO the current project (honoring no-overwrite
    suffixing). The project copy is a normal human asset from then on; the
    library stays independent."""
    from .core.library import LibraryError

    if as_ not in ("refs", "imports"):
        _fail("--as must be 'refs' or 'imports'")
    lib = _lib()
    try:
        entry = lib.get(hash8)
    except LibraryError as exc:
        _fail(str(exc))
    blob = lib.blob_path(entry)
    if not blob.exists():
        _fail(f"library blob missing on disk: {blob}")

    project = _project()
    dest_dir = project.imports_dir if as_ == "imports" else project.refs_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(entry["blob"]).suffix
    stem = Path(name).stem if name else Path(entry["name"]).stem
    suffix = Path(name).suffix if (name and Path(name).suffix) else ext
    dest = dest_dir / f"{stem}{suffix}"
    n = 2
    while dest.exists():  # project no-overwrite suffixing (§3), same as import
        dest = dest_dir / f"{stem}_{n}{suffix}"
        n += 1
    shutil.copy2(blob, dest)
    rel = project.relpath(dest)
    append_event(project.root, ACTOR, "lib_use",
                 {"hash": entry["hash"], "name": entry["name"], "as": as_, "dest": rel})
    if as_json:
        _emit({"dest": rel, "as": as_, "hash": entry["hash"], "name": entry["name"]}, True)
    else:
        typer.secho(f"used {entry['name']} → {rel}", fg=typer.colors.GREEN)


@lib_app.command("rm")
def lib_rm(
    hash8: str,
    yes: bool = typer.Option(False, "--yes", help="confirm removal (required)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Remove an asset from the library (library-side only; projects untouched).
    Requires --yes — the library is the user's, so deletion is explicit."""
    from .core.library import LibraryError

    lib = _lib()
    try:
        entry = lib.get(hash8)
    except LibraryError as exc:
        _fail(str(exc))
    if not yes:
        _fail(f"refusing to remove {entry['name']} without --yes "
              "(库属于你,删除需显式确认)")
    lib.remove(hash8)
    if as_json:
        _emit({"removed": entry["hash"], "name": entry["name"]}, True)
    else:
        typer.secho(f"removed {entry['name']} from library", fg=typer.colors.YELLOW)


@app.command("serve-mcp")
def serve_mcp(
    agent_profile: str = typer.Option(
        "collaborative", "--agent-profile",
        help="Agent surface profile: 'collaborative' (default, byte-identical to "
        "today) or 'unattended' — the opt-in boundary for a self-driving agent "
        "(reads + proposals stay; self-confirming spend and paid redo are hidden "
        "and refused; shot writes require an expected_rev CAS token; build is "
        "dry-run-only). This flag is the ONLY way to select it — project.yaml, "
        "skills, shots and tool arguments can never escalate the profile."),
):
    """MCP server over stdio (§11) — a thin wrapper over the same core. Dangerous
    commands (unlock, gc --hard) are never on this surface; Claude Code drives
    everything else here or via files + this CLI, two equivalent paths (§11)."""
    project = _project()
    from .mcp.policy import PROFILES
    from .mcp.server import main as mcp_main

    if agent_profile not in PROFILES:
        _fail(f"--agent-profile 必须是 {'/'.join(sorted(PROFILES))} 之一(实际 {agent_profile!r})")
    raise typer.Exit(mcp_main(
        ["--project", str(project.root), "--agent-profile", agent_profile]
    ))


# ============================================================ providers group
# `manju providers …` — manage provider manifests (§8.2/§8.6, goal item 1).
# Keys are NEVER printed: list/show/check report the env-var NAME and whether it
# is set, and show masks any secret-ish value.

providers_app = typer.Typer(no_args_is_help=True,
                            help="Manage provider manifests (§8.2/§8.6).")
app.add_typer(providers_app, name="providers")

_MASK = "***"
# key names whose VALUE must never be shown (key_env holds an env-var NAME, not
# a secret, so it is excluded). round-W #73: widened to cover common secret
# HEADER names (authorization/cookie/bearer) and signed-URL vocabulary
# (signature) that a manifest field might carry even outside an "auth:" block
# — e.g. a raw header map for a custom provider.
_SECRET_KEY_RE = re.compile(
    r"(secret|token|password|passwd|credential|api[_-]?key|apikey|access[_-]?key|"
    r"authorization|cookie|signature|bearer|\bkey\b)",
    re.IGNORECASE,
)

# round-W #73: query-param NAMES inside a URL-shaped string value that leak a
# credential even when the surrounding field name looks innocuous (an
# `endpoint`/`webhook_url`/`poll.url` string that happens to embed a signed
# URL's `?token=...`/`?signature=...`). Only the VALUE is masked — the param
# name and the rest of the URL stay visible so the shape is still legible.
_SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:token|signature|sig|key|api[_-]?key|apikey|access[_-]?key|"
    r"password|secret|bearer)=)[^&#\s]+"
)


def _scrub_url_query(value: str) -> str:
    """Mask sensitive query-param VALUES inside a URL-shaped string, keeping
    the param name and everything else — most string values are plain text,
    not URLs, so this is a cheap no-op for those (round-W #73)."""
    if "://" not in value or "?" not in value:
        return value
    return _SECRET_QUERY_RE.sub(lambda m: m.group(1) + _MASK, value)


def _mask_secrets(obj):
    """Recursively mask any value whose KEY looks secret-ish (never print
    keys), and scrub credential-shaped query params inside URL-shaped string
    VALUES even under an innocuous-looking key (round-W #73)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _SECRET_KEY_RE.search(str(k)) and k != "key_env":
                out[k] = _MASK
            else:
                out[k] = _mask_secrets(v)
        return out
    if isinstance(obj, list):
        return [_mask_secrets(v) for v in obj]
    if isinstance(obj, str):
        return _scrub_url_query(obj)
    return obj


def _adapter_short(adapter: str) -> str:
    from .providers.manifest import ADAPTER_ALIASES

    for alias, full in ADAPTER_ALIASES.items():
        if full == adapter:
            return alias
    return adapter.split(":")[-1] if ":" in adapter else adapter


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - len(s))


@providers_app.command("list")
def providers_list(as_json: bool = typer.Option(False, "--json")):
    """Every discovered manifest: id, type, adapter, capabilities, whether its
    key env var is set (never the value), enabled/disabled, doctor verdict."""
    from .providers.manifest import load_manifests

    manifests, load_errors = load_manifests()
    rows = []
    for pid, m in sorted(manifests.items()):
        key_env = m.auth.key_env
        problems = m.validate_for_generic()
        rows.append({
            "id": pid,
            "type": m.type,
            "adapter": m.adapter,
            "adapter_short": _adapter_short(m.adapter),
            "capabilities": list(m.capabilities),
            "key_env": key_env,
            "key_set": (bool(os.environ.get(key_env)) if key_env else None),
            "enabled": not m.disabled,
            "doctor_ok": not problems,
            "doctor": "ok" if not problems else "; ".join(problems),
        })
    if as_json:
        _emit({"providers": rows, "errors": load_errors}, True)
        return
    if not rows and not load_errors:
        typer.echo("no provider manifests configured "
                   "(~/.manju/providers, §8.6) — try: manju providers add …")
        return
    idw = max([len("ID")] + [len(r["id"]) for r in rows])
    for r in rows:
        key = (("✓ " if r["key_set"] else "✗ ") + r["key_env"]) if r["key_env"] else "—"
        status = "enabled" if r["enabled"] else "disabled"
        verdict = "✓ ok" if r["doctor_ok"] else f"✗ {r['doctor']}"
        color = (typer.colors.GREEN if r["enabled"] and r["doctor_ok"]
                 else typer.colors.YELLOW if r["enabled"] else typer.colors.BRIGHT_BLACK)
        typer.secho(
            f"{_pad(r['id'], idw)}  {_pad(r['type'], 5)}  {_pad(r['adapter_short'], 12)}  "
            f"{status:8}  key={key}  {verdict}",
            fg=color,
        )
        if r["capabilities"]:
            typer.secho(f"{' ' * idw}  caps: {', '.join(r['capabilities'])}",
                        fg=typer.colors.BRIGHT_BLACK)
    for e in load_errors:
        typer.secho(f"✗ {e}", fg=typer.colors.RED)


@providers_app.command("add")
def providers_add(
    provider_id: str = typer.Argument(..., metavar="ID"),
    type_: str = typer.Option(..., "--type", help="video | image | tts | asr"),
    adapter: Optional[str] = typer.Option(
        None, "--adapter",
        help="generic_cloud | comfyui | local_cmd | generic_tts | generic_asr "
             "(default: by --type)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Scaffold ~/.manju/providers/<id>/provider.yaml from a fully-commented
    template (★ = fields to fill). Refuses to overwrite an existing manifest."""
    from .core.yamlio import atomic_write_text, read_yaml
    from .providers.manifest import (
        PROVIDER_TYPES,
        default_adapter_for_type,
        provider_manifest_dir,
        scaffold_template,
    )

    if type_ not in PROVIDER_TYPES:
        _fail(f"--type must be one of {list(PROVIDER_TYPES)}, got {type_!r}")
    try:
        provider_dir = provider_manifest_dir(provider_id)  # goal item 72
    except ValueError as exc:
        _fail(str(exc))
    adapter = adapter or default_adapter_for_type(type_)
    try:
        text = scaffold_template(provider_id, type_, adapter)
    except ValueError as exc:
        _fail(str(exc))
    dest = provider_dir / "provider.yaml"
    if dest.exists():
        _fail(f"refusing to overwrite existing manifest: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(dest, text)

    key_env = None
    try:
        key_env = (read_yaml(dest) or {}).get("auth", {}).get("key_env")
    except Exception:
        pass
    steps = ["fill the ★ fields in the file"]
    if key_env:
        steps.append(f"export the API key:  export {key_env}=…")
    steps.append(f"verify offline:      manju providers check {provider_id}")
    if as_json:
        _emit({"created": str(dest), "adapter": adapter, "type": type_,
               "key_env": key_env, "next_steps": steps}, True)
        return
    typer.secho(f"created {dest}", fg=typer.colors.GREEN)
    typer.echo("next steps:")
    for i, s in enumerate(steps, 1):
        typer.echo(f"  {i}. {s}")


def _providers_check_sections(provider_id: str, m, manifest_errors: list) -> dict:
    """DR04 structured sections for ``providers check`` — global manifest
    errors, this provider's credential env (name + presence, never a value),
    projection self-consistency (recompute → same digest, provider present),
    routing references (validate the active routing config where a project is
    resolvable), and warnings (the opaque LEGACY ``max_resolution``; disabled)."""
    from .providers.catalog import project_provider_capabilities

    key_env = m.auth.key_env
    credential_presence = {
        "env": key_env,
        "set": bool(os.environ.get(key_env)) if key_env else None,
    }
    proj_a = project_provider_capabilities()
    proj_b = project_provider_capabilities()
    in_projection = any(p["provider_id"] == provider_id for p in proj_a["providers"])
    projection_consistency = {
        "ok": proj_a["projection_digest"] == proj_b["projection_digest"] and in_projection,
        "projection_digest": proj_a["projection_digest"],
        "in_projection": in_projection,
    }
    routing_references: dict = {"problems": [], "ok": True, "project": False}
    try:
        from .providers.routing import validate_config

        project = Project.find(Path.cwd())
        problems = validate_config(project)
        routing_references = {"problems": problems, "ok": not problems, "project": True}
    except Exception:
        pass
    warnings: list[str] = []
    if m.limits.max_resolution:
        warnings.append(
            f"limits.max_resolution {m.limits.max_resolution!r} is an opaque LEGACY "
            "limit — surfaced verbatim, never used to block a request (§8.6)")
    if m.disabled:
        warnings.append(f"{provider_id} is disabled (manju providers enable {provider_id})")
    return {
        "manifest_errors": list(manifest_errors),
        "credential_presence": credential_presence,
        "projection_consistency": projection_consistency,
        "routing_references": routing_references,
        "warnings": warnings,
    }


@providers_app.command("check")
def providers_check(
    provider_id: str = typer.Argument(..., metavar="ID"),
    live: bool = typer.Option(False, "--live",
                              help="also run the cheap, zero-cost reachability probe"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Offline doctor probes for ONE provider, each with a one-line fix hint.
    --live adds a free reachability check where the adapter defines one
    (ComfyUI /system_stats; generic REST only if ping_url is set — never a
    paid call)."""
    from .providers.manifest import (
        fix_hint,
        load_manifests,
        providers_dir,
        reachability_probe,
    )

    manifests, manifest_errors = load_manifests()
    m = manifests.get(provider_id)
    if m is None:
        _fail(f"no such provider {provider_id!r} "
              f"(looked under {providers_dir()}) — see `manju providers list`")
    problems = m.validate_for_generic()
    findings = [{"problem": p, "fix": fix_hint(p, m)} for p in problems]
    live_info = None
    if live:
        ok, detail = reachability_probe(m)
        live_info = {"ok": ok, "detail": detail}
    passed = (not problems) and not (live_info and live_info["ok"] is False)
    # DR04 structured sections (§10.2 — never a credential VALUE; presence is
    # allowed here, this is the offline doctor, not the derived projection).
    sections = _providers_check_sections(provider_id, m, manifest_errors)
    if as_json:
        _emit({"id": provider_id, "ok": passed, "enabled": not m.disabled,
               "problems": findings, "live": live_info, **sections}, True)
        raise typer.Exit(0 if passed else 1)
    for w in sections["warnings"]:
        typer.secho(f"⚠ {w}", fg=typer.colors.YELLOW)
    if m.disabled:
        typer.secho(f"• {provider_id} is disabled "
                    f"(manju providers enable {provider_id})", fg=typer.colors.BRIGHT_BLACK)
    if not problems:
        typer.secho(f"✓ {provider_id}: offline checks pass", fg=typer.colors.GREEN)
    else:
        for f in findings:
            typer.secho(f"✗ {f['problem']}", fg=typer.colors.RED)
            typer.secho(f"  → fix: {f['fix']}", fg=typer.colors.YELLOW)
    if live_info is not None:
        glyph, color = (("✓", typer.colors.GREEN) if live_info["ok"] else
                        ("•", typer.colors.BRIGHT_BLACK) if live_info["ok"] is None else
                        ("✗", typer.colors.RED))
        typer.secho(f"{glyph} live: {live_info['detail']}", fg=color)
    raise typer.Exit(0 if passed else 1)


def _set_disabled_in_text(text: str, disabled: bool) -> str:
    """Flip (or insert) the top-level ``disabled:`` key while preserving every
    comment in the manifest — enable/disable is a policy switch, not a rewrite."""
    val = "true" if disabled else "false"
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.match(r"^disabled\s*:", line):  # top-level key, no indent
            lines[i] = f"disabled: {val}"
            return "\n".join(lines)
    anchor = None
    for i, line in enumerate(lines):
        if re.match(r"^(adapter|id)\s*:", line):
            anchor = i
    lines.insert((anchor + 1) if anchor is not None else 0, f"disabled: {val}")
    return "\n".join(lines)


def _toggle_provider(provider_id: str, disabled: bool, as_json: bool) -> None:
    from .core.yamlio import atomic_write_text
    from .providers.manifest import provider_manifest_dir

    try:
        path = provider_manifest_dir(provider_id) / "provider.yaml"  # goal item 72
    except ValueError as exc:
        _fail(str(exc))
    if not path.exists():
        _fail(f"no such provider {provider_id!r} (looked at {path})")
    text = path.read_text(encoding="utf-8")
    atomic_write_text(path, _set_disabled_in_text(text, disabled))
    state = "disabled" if disabled else "enabled"
    if as_json:
        _emit({"id": provider_id, "enabled": not disabled}, True)
        return
    typer.secho(f"{provider_id} {state}", fg=typer.colors.GREEN)


@providers_app.command("enable")
def providers_enable(provider_id: str = typer.Argument(..., metavar="ID"),
                     as_json: bool = typer.Option(False, "--json")):
    """Clear the manifest's ``disabled`` flag so the provider can be selected."""
    _toggle_provider(provider_id, False, as_json)


@providers_app.command("disable")
def providers_disable(provider_id: str = typer.Argument(..., metavar="ID"),
                      as_json: bool = typer.Option(False, "--json")):
    """Set ``disabled: true`` — the provider is never selected by the fallback
    chain or a routing strategy; naming it explicitly becomes a build error."""
    _toggle_provider(provider_id, True, as_json)


@providers_app.command("show")
def providers_show(provider_id: str = typer.Argument(..., metavar="ID"),
                   as_json: bool = typer.Option(False, "--json")):
    """Print the manifest with any secret-ish value masked (keys never shown)."""
    from .core.yamlio import dump_yaml, read_yaml
    from .providers.manifest import provider_manifest_dir

    try:
        path = provider_manifest_dir(provider_id) / "provider.yaml"  # goal item 72
    except ValueError as exc:
        _fail(str(exc))
    if not path.exists():
        _fail(f"no such provider {provider_id!r} (looked at {path})")
    masked = _mask_secrets(read_yaml(path) or {})
    if as_json:
        _emit(masked, True)
        return
    typer.echo(dump_yaml(masked))


@providers_app.command("catalog")
def providers_catalog(
    as_json: bool = typer.Option(False, "--json"),
    output: Optional[Path] = typer.Option(
        None, "--output",
        help="write the derived projection (JSON) atomically to this path"),
):
    """The shared provider capability projection
    (``manju.provider-capability-projection/v1``, DR04): every provider's
    capabilities, limits (``max_resolution`` surfaced VERBATIM — an opaque LEGACY
    limit, never guessed into width/height), reference caps, cost, credential env
    NAMES (never values or presence), free-probe availability, and per-profile
    digests. DERIVED — safe to delete and regenerate, never read by a build.
    ``--output`` writes an atomic report; ``--json`` prints the projection."""
    from .core.yamlio import atomic_write_text
    from .providers.catalog import project_provider_capabilities

    proj = project_provider_capabilities()
    if output is not None:
        atomic_write_text(output, json.dumps(proj, ensure_ascii=False, indent=2) + "\n")
    if as_json:
        _emit(proj, True)
        return
    providers = proj["providers"]
    if not providers:
        typer.echo("no providers discovered (~/.manju/providers, §8.6)")
    idw = max([len("ID")] + [len(p["provider_id"]) for p in providers], default=2)
    for p in providers:
        caps = ", ".join(c["profile_id"].split("#", 1)[1]
                         for c in p["capabilities"]) or "—"
        cred = p["credential_requirements"][0] if p["credential_requirements"] else "—"
        status = "enabled" if p["enabled"] else "disabled"
        probe = "free-probe" if p["free_probe_available"] else "—"
        typer.secho(
            f"{_pad(p['provider_id'], idw)}  {_pad(p['provider_type'], 6)}  "
            f"{_pad(p['source']['kind'], 8)}  {status:8}  key={cred}  {probe}",
            fg=(typer.colors.GREEN if p["enabled"] else typer.colors.BRIGHT_BLACK))
        typer.secho(f"{' ' * idw}  caps: {caps}", fg=typer.colors.BRIGHT_BLACK)
        mr = p["limits"].get("max_resolution")
        if mr:  # opaque LEGACY limit — surfaced verbatim, never interpreted
            typer.secho(f"{' ' * idw}  max_resolution: {mr} "
                        "(LEGACY — opaque, not interpreted)", fg=typer.colors.YELLOW)
    for e in proj["errors"]:
        typer.secho(f"✗ {e}", fg=typer.colors.RED)
    typer.secho(f"projection_digest: {proj['projection_digest']}",
                fg=typer.colors.BRIGHT_BLACK)
    if output is not None:
        typer.secho(f"wrote {output}", fg=typer.colors.GREEN)


# --------------------------------------------------------- qualification (AI_IDE_14)
# `manju providers qualify` / `providers qualification` — derive a provider
# capability's qualification level from a fixed low-cost canary. Audit-first,
# red-first, DERIVED-only: the report is a deletable evidence projection, never a
# build input. Real network is operator-gated behind --run + --max-cost + the
# ask_before spend gate; --dry-run never touches a transport.

_QUAL_COLOR = {
    "UNTESTED": typer.colors.BRIGHT_BLACK,
    "CONFIG_VALID": typer.colors.CYAN,
    "DRY_RUN_VALID": typer.colors.BLUE,
    "CANARY_SUBMIT_PASSED": typer.colors.GREEN,
    "CANARY_ARTIFACT_PASSED": typer.colors.GREEN,
    "RECOVERY_PASSED": typer.colors.GREEN,
    "PRODUCTION_READY": typer.colors.BRIGHT_GREEN,
    "STALE": typer.colors.YELLOW,
    "BLOCKED": typer.colors.RED,
}


@providers_app.command("qualify")
def providers_qualify(
    provider_id: str = typer.Argument(..., metavar="ID"),
    capability: str = typer.Option(..., "--capability",
                                   help="capability to qualify, e.g. image_to_video / tts"),
    run: bool = typer.Option(False, "--run",
                             help="run the REAL canary (operator-gated, PAID; needs --max-cost)"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="config + fixture + estimate, NO network (the default)"),
    max_cost: Optional[float] = typer.Option(
        None, "--max-cost", help="hard cap on the canary estimate — required with --run (§5)"),
    yes: bool = typer.Option(False, "--yes",
                             help="pre-approve the ask_before spend gate for --run"),
    live: bool = typer.Option(False, "--live",
                              help="also run the free GET-only health probe (never a submit)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Qualify ONE provider capability against its fixed low-cost canary fixture.

    Default (or --dry-run) validates config + fixture + estimate with NO network
    and records DRY_RUN_VALID. --run is the operator-gated PAID canary: it is
    refused without --max-cost (and if the estimate exceeds it), passes the
    ask_before spend gate (unless --yes), and drives a SINGLE submission with NO
    fallback. The report is a deletable evidence projection under
    reports/providers/qualification/ — never a build input."""
    from .providers.base import ProviderFailure
    from .providers.qualification import CanaryError, qualify

    if run and dry_run:
        _fail("choose one of --dry-run or --run, not both", code="bad_args")
    project = _project()
    mode = "run" if run else "dry_run"
    try:
        report = qualify(project, provider_id, capability, mode=mode,
                         max_cost=max_cost, assume_yes=yes, health_probe=live)
    except CanaryError as exc:
        _fail(str(exc), code=exc.code)
    except ValueError as exc:
        _fail(str(exc), code="bad_capability")
    except ProviderFailure as exc:  # a real --run that reached (and failed at) the API
        _fail(f"canary submit failed: {exc} "
              f"(disposition={getattr(exc, 'disposition', None)})",
              code="canary_submit_failed")
    if as_json:
        _emit(report, True)
        return
    state = report["state"]
    typer.secho(f"{report['provider_id']}#{report['capability']}: {state}",
                fg=_QUAL_COLOR.get(state, None), bold=True)
    typer.echo(f"  level={report['level']}  mode={report['mode']}  "
               f"transport={report['transport']}  "
               f"estimate={report['estimated_cost']} {report['currency'] or ''}")
    for r in report.get("reasons", []):
        typer.secho(f"  · {r}", fg=typer.colors.BRIGHT_BLACK)
    ac = report.get("artifact_checks")
    if ac:
        for c in ac["checks"]:
            glyph = "✓" if c["ok"] else ("•" if c["ok"] is None else "✗")
            color = (typer.colors.GREEN if c["ok"] else
                     typer.colors.BRIGHT_BLACK if c["ok"] is None else typer.colors.RED)
            typer.secho(f"    {glyph} {c['check']}: {c['detail']}", fg=color)
    rec = report.get("recovery")
    if rec:
        typer.secho(f"  recovery: {'passed' if rec['passed'] else 'FAILED'} "
                    f"(restored={rec['restored_state']}, resubmit_calls={rec['resubmit_calls']})",
                    fg=typer.colors.GREEN if rec["passed"] else typer.colors.RED)
    if report.get("environment_note"):
        typer.secho(f"  note: {report['environment_note']}", fg=typer.colors.YELLOW)
    typer.secho(f"  report: reports/providers/qualification/"
                f"{report['provider_id']}__{report['capability']}.json (derived — deletable)",
                fg=typer.colors.BRIGHT_BLACK)


@providers_app.command("qualification")
def providers_qualification(
    provider_id: Optional[str] = typer.Argument(None, metavar="[ID]"),
    capability: Optional[str] = typer.Option(None, "--capability"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Show the qualification matrix (capability × level) re-derived LIVE from
    the current manifests + any recorded evidence, so a stale report reads STALE.
    Pass an ID (and optionally --capability) to focus on one provider."""
    from .providers.qualification import (
        EVIDENCE_CORRUPT,
        declared_facts,
        qualification_matrix,
        qualification_state,
        read_qualification_evidence,
        read_report,
    )

    project = _project()
    if provider_id and capability:
        declared = declared_facts(provider_id, capability)
        # 14_21 closeout: the state derives from the DURABLE evidence store;
        # the report JSON below is a display projection only (never an input).
        stored = read_report(project, provider_id, capability)
        evidence, ev_err = read_qualification_evidence(project, provider_id,
                                                       capability)
        if ev_err == EVIDENCE_CORRUPT:
            derived = {"state": "BLOCKED", "level": "UNTESTED", "stale": False,
                       "blocked_reason": EVIDENCE_CORRUPT,
                       "reasons": ["durable qualification evidence corrupt/"
                                   "unreadable — failing closed"],
                       "bindings": None}
        else:
            derived = qualification_state(provider_id, capability,
                                          evidence=evidence, declared=declared)
        out = {"provider_id": provider_id, "capability": capability,
               "state": derived["state"], "level": derived["level"],
               "stale": derived["stale"], "blocked_reason": derived["blocked_reason"],
               "reasons": derived["reasons"], "bindings": derived["bindings"],
               "report": stored}
        if as_json:
            _emit(out, True)
            return
        typer.secho(f"{provider_id}#{capability}: {derived['state']}",
                    fg=_QUAL_COLOR.get(derived["state"], None), bold=True)
        for r in derived["reasons"]:
            typer.secho(f"  · {r}", fg=typer.colors.BRIGHT_BLACK)
        return

    matrix = qualification_matrix(project)
    rows = matrix["rows"]
    if provider_id:
        rows = [r for r in rows if r["provider_id"] == provider_id]
    if as_json:
        _emit({"schema": matrix["schema"], "rows": rows, "errors": matrix["errors"]}, True)
        return
    if not rows:
        typer.echo("no provider capabilities to qualify "
                   "(~/.manju/providers, §8.6) — try: manju providers add …")
        return
    idw = max([len("ID")] + [len(r["provider_id"]) for r in rows])
    capw = max([len("CAPABILITY")] + [len(r["capability"]) for r in rows])
    for r in rows:
        tag = "STALE" if r["stale"] else r["state"]
        typer.secho(
            f"{_pad(r['provider_id'], idw)}  {_pad(r['capability'], capw)}  "
            f"{_pad(r['kind'], 5)}  {r['state']}",
            fg=_QUAL_COLOR.get(r["state"], None))
    for e in matrix["errors"]:
        typer.secho(f"✗ {e}", fg=typer.colors.RED)


# =============================================================== routing group
# `manju route …` — inspect the active routing strategy (goal item 9).

route_app = typer.Typer(no_args_is_help=True,
                        help="Inspect model-routing strategies (goal 9).")
app.add_typer(route_app, name="route")


@route_app.command("list")
def route_list_cmd(as_json: bool = typer.Option(False, "--json")):
    """List routing strategies (built-in + user/project), marking the active one."""
    from .providers.routing import RoutingError, list_strategies

    try:
        project: Optional[Project] = Project.find(Path.cwd())
    except ProjectError:
        project = None
    try:
        info = list_strategies(project)
    except RoutingError as exc:
        _fail(str(exc))
    if as_json:
        _emit(info, True)
        return
    if info["sources"]:
        typer.secho(f"routing.yaml sources: {', '.join(info['sources'])} "
                    "(project wins)", fg=typer.colors.BRIGHT_BLACK)
    else:
        typer.secho("no routing.yaml — default §8.4 behaviour "
                    "(built-in strategies still available)", fg=typer.colors.BRIGHT_BLACK)
    for s in info["strategies"]:
        mark = "→" if s["active"] else " "
        tag = "builtin" if s["builtin"] else "custom"
        extra = f" priority={s['priority']}" if s["priority"] else ""
        typer.secho(
            f" {mark} {_pad(s['name'], 14)} [{tag}]  rules={s['rules']}  "
            f"else={s['else']}{extra}",
            fg=typer.colors.CYAN if s["active"] else None,
        )
    for t in info.get("tiers", []):
        typer.secho(f"   tier {_pad(t['name'], 12)} {t.get('description') or ''} "
                    f"→ use {t['use']}", fg=typer.colors.BRIGHT_BLACK)


@route_app.command("explain")
def route_explain_cmd(shot_id: str = typer.Argument(..., metavar="SHOT"),
                      as_json: bool = typer.Option(False, "--json")):
    """Explain which provider the active strategy picks for a shot: which rule
    fired, which were skipped and why (the debuggability requirement)."""
    from .providers.routing import RoutingError, explain

    project = _project()
    try:
        shot = project.load_shot(shot_id)
    except ProjectError as exc:
        _fail(str(exc))
    try:
        info = explain(project, shot)
    except RoutingError as exc:
        _fail(str(exc))
    if as_json:
        _emit(info, True)
        return
    typer.secho(f"shot {info['shot']} — strategy: {info['strategy']}"
                + (f"  (routing.yaml: {', '.join(info['sources'])})" if info["sources"]
                   else "  (no routing.yaml — §8.4 default)"),
                fg=typer.colors.CYAN)
    if info["explicit_provider"]:
        typer.echo(f"  explicit provider: {info['explicit_provider']} (always wins)")
    if info.get("tier"):
        typer.echo(f"  tier: {info['tier']}")
    if info["fired_rule"] is not None:
        fr = info["fired_rule"]
        typer.secho(f"  ✓ rule #{fr['index']} fired: match {fr['match']} → use {fr['use']}",
                    fg=typer.colors.GREEN)
    elif info.get("fired_tier") is not None:
        ft = info["fired_tier"]
        typer.secho(f"  ✓ tier {ft['name']} fired: {ft.get('description') or ''} "
                    f"→ use {ft['use']}", fg=typer.colors.GREEN)
    else:
        typer.echo(f"  no rule/tier matched → else: {info['else']}")
    typer.secho(f"  order: {' → '.join(info['order']) or '(none)'}",
                fg=typer.colors.CYAN)
    typer.echo(f"  chosen: {info['chosen'] or '(none)'}")
    for sk in info["skipped"]:
        who = f"rule #{sk['rule']}" if "rule" in sk else f"provider {sk['provider']}"
        typer.secho(f"  · skipped {who}: {sk['reason']}", fg=typer.colors.BRIGHT_BLACK)


# `manju routing explain [<shot>]` — the per-shot decision + cost table (goal 15).
# Distinct from `route explain <shot>` (which dumps one shot's rule-firing
# internals): this reads EVERY shot (or one), naming the chosen provider, WHY
# (explicit / rule / tier / fallback), the fallback order after the head, and the
# estimated cost via the SAME estimators plan.py/dry-run use, in a 中文 table.

routing_app = typer.Typer(no_args_is_help=True,
                          help="Per-shot routing decisions + cost (goal 15).")
app.add_typer(routing_app, name="routing")

_WHY_CN = {"explicit": "钦定", "rule": "规则", "tier": "分级", "fallback": "兜底"}


@routing_app.command("explain")
def routing_explain_cmd(
    shot_id: Optional[str] = typer.Argument(
        None, metavar="[SHOT]", help="one shot; omit to explain every shot"),
    mode: Optional[str] = typer.Option(
        None, "--mode", help="preview a build mode's routing bias: "
                             "quality | balanced | speed (goal 14)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Explain, per shot, which provider routing chooses and WHY — chosen head,
    reason (钦定/规则/分级/兜底), the fallback order after it, and the estimated
    cost (the same estimators `build --dry-run` uses). --mode previews a build
    mode's bias without generating anything."""
    from .core.models import BUILD_MODE_NAMES
    from .gui.plan import routing_explain
    from .providers.routing import RoutingError

    project = _project()
    if mode is not None and mode not in BUILD_MODE_NAMES:
        _fail(f"--mode must be one of {BUILD_MODE_NAMES}, got {mode!r}")
    ids = None
    if shot_id is not None:
        try:
            project.load_shot(shot_id)  # validate it exists
        except ProjectError as exc:
            _fail(str(exc))
        ids = [shot_id]
    try:
        info = routing_explain(project, ids, mode=mode)
    except RoutingError as exc:
        _fail(str(exc))
    if as_json:
        _emit(info, True)
        return
    head = "routing.yaml: " + (", ".join(info["sources"]) + " (project wins)"
                               if info["sources"] else "无 — §8.4 default")
    if info["mode"]:
        head += f"  ·  mode={info['mode']}"
    typer.secho(head, fg=typer.colors.BRIGHT_BLACK)
    typer.secho(f" {_pad('镜头', 8)} {_pad('选用(head)', 18)} {_pad('原因', 6)} "
                f"{_pad('预估', 8)} 兜底顺序", fg=typer.colors.CYAN)
    total = 0.0
    currency = None
    for r in info["rows"]:
        total += float(r["estimated_cost"] or 0)
        currency = currency or r.get("currency")
        why = _WHY_CN.get(r["why"], r["why"])
        rest = " → ".join(r["fallback_order"]) or "(无)"
        typer.echo(
            f" {_pad(r['shot'], 8)} {_pad(r['chosen'] or '(none)', 18)} "
            f"{_pad(why, 6)} {_pad(str(r['estimated_cost']), 8)} {rest}")
        typer.secho(f"    ↳ {r['why_detail']}", fg=typer.colors.BRIGHT_BLACK)
    typer.secho(f" 合计预估 ≈ {round(total, 6)} {currency or ''}", fg=typer.colors.CYAN)

# ============================================================= director group
# `manju director …` — the AI-director loop as one auditable contract (goal 17):
# read state → propose → cost/impact → confirm → execute → diff → suggest next.
# Every subcommand carries --json for agents; human output is 中文 (§10 glossary:
# 试跑/花费护栏/修复方案/待更新). The LLM lives in the external agent (§0); Manju
# only ships the deterministic six-step contract the agent (or GUI user) drives.

director_app = typer.Typer(no_args_is_help=True,
                           help="AI 导演协作环:提案→花费/影响→确认→执行→差异→下一步(goal 17)。")
app.add_typer(director_app, name="director")


def _director_actions(from_file: Optional[Path], actions_json: Optional[str]) -> tuple[list, str]:
    """Resolve the action list (+ optional why) from --from-file (YAML: a list,
    or a {why, actions} mapping) or --actions-json (a JSON array)."""
    import json as _json

    from .core.yamlio import read_yaml

    if bool(from_file) == bool(actions_json):
        _fail("pass exactly one of --from-file / --actions-json")
    why = ""
    if from_file is not None:
        if not from_file.exists():
            _fail(f"not found: {from_file}")
        data = read_yaml(from_file)
    else:
        try:
            data = _json.loads(actions_json)
        except _json.JSONDecodeError as exc:
            _fail(f"--actions-json is not valid JSON: {exc}")
    if isinstance(data, dict):
        why = str(data.get("why") or "")
        actions = data.get("actions")
    else:
        actions = data
    if not isinstance(actions, list) or not actions:
        _fail("plan must be a non-empty list of actions (or {why, actions:[…]})")
    return actions, why


def _director_print_proposal(proposal, *, current: Optional[bool] = None) -> None:
    typer.secho(f"提案 {proposal.id}  [{proposal.state}]"
                + ("  · 待更新(expired)" if current is False else ""),
                fg=typer.colors.CYAN, bold=True)
    if proposal.why:
        typer.echo(f"  为什么 why: {proposal.why}")
    cur = proposal.currency or ""
    typer.echo(f"  预估花费 est cost: {proposal.estimated_cost:g} {cur}"
               "   （试跑 dry-run 口径,花费护栏在执行时再次把关)")
    for i, pa in enumerate(proposal.actions):
        a = pa.action
        shots = ", ".join(pa.impact.get("shots") or []) or "—"
        outs = ", ".join(pa.impact.get("outputs") or []) or "—"
        c = f"  花费≈{pa.estimated_cost:g} {pa.currency or ''}" if pa.estimated_cost else ""
        typer.echo(f"  #{i} {a.get('type')} {_director_action_brief(a)}"
                   f"   影响镜头:{shots} · 产物:{outs}{c}")
        if pa.result is not None:
            mark = "✓" if pa.result.get("ok") else "✗"
            typer.echo(f"      {mark} {pa.result.get('error') or '完成'}")


def _director_action_brief(a: dict) -> str:
    t = a.get("type")
    if t == "build":
        return f"target={a.get('target')} gen={a.get('gen')}"
    if t in ("redo", "voice"):
        return f"{a.get('shot')}" + (f" provider={a['provider']}" if a.get("provider") else "")
    if t == "repair":
        return f"{a.get('op')} {a.get('shot')}"
    if t in ("captions", "packaging", "rollback"):
        return f"{a.get('op')}" + (f" {a.get('shot') or a.get('path') or ''}").rstrip()
    if t == "snapshot":
        return a.get("label") or ""
    if t == "mixer":
        return ", ".join((a.get("changes") or {}).keys())
    return ""


@director_app.command("propose")
def director_propose(
    from_file: Optional[Path] = typer.Option(None, "--from-file", "-f",
        help="YAML plan file: a list of actions, or {why, actions:[…]}"),
    actions_json: Optional[str] = typer.Option(None, "--actions-json",
        help="a JSON array of action objects (agent path)"),
    why: Optional[str] = typer.Option(None, "--why", help="one line: why this plan"),
    as_json: bool = typer.Option(False, "--json"),
):
    """PROPOSE a plan (step 1) — validate + annotate impact/cost, persist to
    reports/proposals/<id>.yaml. Nothing runs and nothing is spent."""
    from .build.director import DirectorError, propose

    project = _project()
    actions, file_why = _director_actions(from_file, actions_json)
    try:
        proposal = propose(project, actions, why=(why if why is not None else file_why),
                           actor=ACTOR)
    except DirectorError as exc:
        _fail(str(exc))
    if as_json:
        _emit(proposal.model_dump(), True)
        return
    _director_print_proposal(proposal, current=True)
    typer.secho(f"→ 确认:manju director confirm {proposal.id}", fg=typer.colors.BRIGHT_BLACK)


@director_app.command("list")
def director_list(as_json: bool = typer.Option(False, "--json")):
    """List proposals, newest first, with their state and a 待更新 flag."""
    from .build.director import _is_current, list_proposals

    project = _project()
    proposals = list_proposals(project)
    rows = [pr.summary(current=_is_current(project, pr)) for pr in proposals]
    if as_json:
        _emit({"proposals": rows}, True)
        return
    if not rows:
        typer.echo("暂无提案 — manju director propose 起草一个")
        return
    typer.secho("导演提案 / director proposals (新→旧)", fg=typer.colors.CYAN)
    for r in rows:
        flag = " 待更新" if (r["current"] is False and r["state"] in ("proposed", "confirmed")) else ""
        cur = r["currency"] or ""
        typer.echo(f"  {r['id']}  [{r['state']}{flag}]  {r['actions']} 步  "
                   f"≈{r['estimated_cost']:g} {cur}  [{r['actor']}]  {r['why'][:40]}")


@director_app.command("show")
def director_show(proposal_id: str, as_json: bool = typer.Option(False, "--json")):
    """Show one proposal in full: actions, impact, 试跑 cost, and (if run) result."""
    from .build.director import DirectorError, _is_current, load_proposal

    project = _project()
    try:
        proposal = load_proposal(project, proposal_id)
    except DirectorError as exc:
        _fail(str(exc))
    current = _is_current(project, proposal)
    if as_json:
        payload = proposal.model_dump()
        payload["current"] = current
        _emit(payload, True)
        return
    _director_print_proposal(proposal, current=current)


@director_app.command("confirm")
def director_confirm(proposal_id: str, as_json: bool = typer.Option(False, "--json")):
    """CONFIRM a proposal (step 3) — the explicit approve-before-execute gate.
    Never implied; a proposal whose project moved is refused as 待更新."""
    from .build.director import DirectorError, confirm

    project = _project()
    try:
        proposal = confirm(project, proposal_id, actor=ACTOR)
    except DirectorError as exc:
        _fail(str(exc))
    if as_json:
        _emit(proposal.model_dump(), True)
        return
    typer.secho(f"已确认 {proposal.id} — 执行:manju director run {proposal.id}",
                fg=typer.colors.GREEN)


@director_app.command("reject")
def director_reject(proposal_id: str, as_json: bool = typer.Option(False, "--json")):
    """Reject a proposal (kept on disk — history only grows)."""
    from .build.director import DirectorError, reject

    project = _project()
    try:
        proposal = reject(project, proposal_id, actor=ACTOR)
    except DirectorError as exc:
        _fail(str(exc))
    if as_json:
        _emit(proposal.model_dump(), True)
        return
    typer.secho(f"已否决 {proposal.id}", fg=typer.colors.YELLOW)


@director_app.command("run")
def director_run(proposal_id: str, as_json: bool = typer.Option(False, "--json")):
    """EXECUTE a confirmed proposal (steps 4-6): run in order, auto-snapshot
    first (还原 一步到位), stop at the first failure, then show the diff and the
    next-step suggestions. Paid steps ride the confirmed proposal's assume_yes."""
    from .build.director import DirectorError, execute

    project = _project()
    try:
        outcome = execute(project, proposal_id, actor=ACTOR)
    except DirectorError as exc:
        _fail(str(exc))
    if as_json:
        _emit(outcome.to_dict(), True)
        return
    mark = "✓ 完成" if outcome.ok else "✗ 失败(第一处失败即停)"
    color = typer.colors.GREEN if outcome.ok else typer.colors.RED
    typer.secho(f"{mark}  {outcome.proposal_id}  [{outcome.state}]", fg=color, bold=True)
    snap = outcome.snapshot or {}
    if snap.get("sha"):
        typer.echo(f"  存档点 snapshot: {snap['sha']}（还原:manju rollback file / snapshot）")
    elif snap.get("note"):
        typer.secho(f"  存档点跳过: {snap['note']}", fg=typer.colors.BRIGHT_BLACK)
    for r in outcome.results:
        m = "✓" if r.get("ok") else "✗"
        typer.echo(f"  {m} #{r['index']} {r['type']}"
                   + (f" — {r.get('error')}" if not r.get("ok") else ""))
    if outcome.failure:
        typer.secho(f"  失败 failure #{outcome.failure.get('id')}: "
                    f"{outcome.failure.get('cause')}", fg=typer.colors.RED)
    out = outcome.diff.get("outputs", {})
    if out.get("new_finals") or out.get("rerendered_finals"):
        typer.echo(f"  产物差异:新成片 {out.get('new_finals') or '—'} · "
                   f"重渲染 {out.get('rerendered_finals') or '—'}")
    if outcome.suggestions:
        typer.secho("  下一步建议 / suggest next:", fg=typer.colors.CYAN)
        for s in outcome.suggestions:
            hint = "（可转为新提案）" if s.get("action") else ""
            typer.echo(f"    · [{s['kind']}] {s['text']}{hint}")


@director_app.command("suggest")
def director_suggest(as_json: bool = typer.Option(False, "--json")):
    """SUGGEST NEXT (step 6, standalone) — deterministic next-step nudges from
    the existing signals (待更新→重做, 质检→修复方案, 缺失→生成, 花费护栏→提醒);
    each carries a ready-made action payload to turn into the next proposal."""
    from .build.director import suggest_next

    project = _project()
    suggestions = [s.to_dict() for s in suggest_next(project)]
    if as_json:
        _emit({"suggestions": suggestions}, True)
        return
    if not suggestions:
        typer.echo("暂无建议 — 一切就绪,或先 manju build")
        return
    typer.secho("下一步建议 / suggest next", fg=typer.colors.CYAN)
    for s in suggestions:
        hint = "  （manju director propose 转为提案）" if s.get("action") else ""
        typer.echo(f"  · [{s['kind']}] {s['text']}{hint}")


# ------------------------------------------------------------------ series


series_app = typer.Typer(
    no_args_is_help=True,
    help="剧集(长片/多集)总括层(goal V-3):series.yaml 伞状目录 + 全局 bible + "
         "episodes/<eid>.manju 普通项目。分集与单项目命令完全兼容。",
)
app.add_typer(series_app, name="series")


def _series(path: Optional[Path] = None):
    """Resolve the series root from cwd — works from inside an episode too
    (Series.find walks past the episode's project.yaml to the series.yaml)."""
    from .core.series import Series, SeriesError

    try:
        return Series.find(path or Path.cwd())
    except SeriesError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@series_app.command("new")
def series_new(
    path: Path,
    name: Optional[str] = typer.Option(None, "--name", help="series name (default: dir name)"),
    description: str = typer.Option("", "--description", help="one-line series description"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Scaffold a series umbrella: series.yaml + 全局 bible/ + episodes/ + script/ + reports/."""
    from .core.series import Series, SeriesError

    try:
        series = Series.create(path, name=name, description=description)
    except SeriesError as exc:
        _fail(str(exc))
    cfg = series.load_config()
    append_event(series.root, ACTOR, "series_new", {"name": cfg.name})
    if as_json:
        _emit({"root": str(series.root), "name": cfg.name}, True)
    else:
        typer.secho(f"created series {series.root}  [{cfg.name}]", fg=typer.colors.GREEN)
        typer.secho("  下一步:manju series new-episode E01 --title <标题>",
                    fg=typer.colors.BRIGHT_BLACK)


@series_app.command("status")
def series_status_cmd(as_json: bool = typer.Option(False, "--json"),
                      health: bool = typer.Option(
                          False, "--health",
                          help="AI_IDE_17 WP3: fold the season-health aggregation "
                               "(release readiness/unresolved submissions/drift/"
                               "refs/variants/locale/budget per episode + season "
                               "ready-never-masks verdict) into the --json output")):
    """跨集状态汇总:每集镜头分布 / 成片 / 花费,加合计行(复用单项目机制,只读)。"""
    from .core.series import SeriesError, series_status

    series = _series()
    try:
        info = series_status(series)
        if health:
            # AI_IDE_17: additive season_health section — pure aggregation of
            # per-episode CURRENT evidence (core/series_state.py); the classic
            # keys above stay byte-identical (basic-series behaviour unchanged).
            from .core.series_state import season_health

            info["season_health"] = season_health(series)
    except SeriesError as exc:
        _fail(str(exc))
    if as_json:
        _emit(info, True)
        return
    if health:
        sh = info.get("season_health") or {}
        flag = "READY" if sh.get("ready") else "NOT READY"
        typer.secho(f"season health: {flag}", fg=(
            typer.colors.GREEN if sh.get("ready") else typer.colors.YELLOW))
        for blk in sh.get("not_ready") or []:
            typer.echo(f"  ✗ {blk['id']}: {blk['reason']}")
    typer.secho(f"剧集 / series  {info['series']}", fg=typer.colors.CYAN)
    for e in info["episodes"]:
        title = f"  {e['title']}" if e.get("title") else ""
        if e.get("error"):
            typer.secho(f"  {e['id']}{title}  ✗ 无法读取:{e['error']}", fg=typer.colors.RED)
            continue
        states = ", ".join(f"{k}={v}" for k, v in (e.get("shots_by_state") or {}).items()) or "—"
        final = e.get("latest_final") or "—"
        cost = f"{e.get('cost', 0.0)} {e.get('currency') or ''}".rstrip()
        typer.echo(f"  {e['id']}{title}  镜头[{e.get('shots_total', 0)}]: {states}  "
                   f"成片: {final}  花费: {cost}")
    t = info["totals"]
    typer.secho(
        f"合计  {t['ok']}/{t['episodes']} 集可读"
        + (f"(broken {t['errors']})" if t.get("errors") else "")
        + f"  镜头 {t['shots']}  成片 {t['finals']}  花费 {t['cost']} {t.get('currency') or ''}".rstrip(),
        fg=typer.colors.BRIGHT_BLACK,
    )


@series_app.command("episodes")
def series_episodes_cmd(as_json: bool = typer.Option(False, "--json")):
    """列出已登记的分集(id / 标题 / 是否已在磁盘上)。"""
    from .core.series import SeriesError

    series = _series()
    try:
        cfg = series.load_config()
    except SeriesError as exc:
        _fail(str(exc))
    rows = []
    for e in cfg.episodes:
        exists = (series.episode_project_dir(e.id) / "project.yaml").exists()
        rows.append({"id": e.id, "title": e.title, "exists": exists,
                     "dir": series.relpath(series.episode_project_dir(e.id))})
    if as_json:
        _emit({"series": cfg.name, "episodes": rows}, True)
        return
    typer.secho(f"分集 / episodes  ({cfg.name})", fg=typer.colors.CYAN)
    if not rows:
        typer.echo("  —  用 manju series new-episode E01 添加")
        return
    for r in rows:
        mark = "" if r["exists"] else "  ⚠ 目录缺失"
        typer.echo(f"  {r['id']}  {r['title'] or ''}  → {r['dir']}{mark}")


@series_app.command("new-episode")
def series_new_episode_cmd(
    eid: str,
    title: str = typer.Option("", "--title", help="episode title"),
    preset: Optional[str] = typer.Option(None, "--preset", help="preset kit (see `manju presets`)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """在 episodes/<eid>.manju 下新建一个普通项目,并从剧集 bible 播种其 bible。"""
    from .core.series import SeriesError, new_episode

    series = _series()
    try:
        project = new_episode(series, eid, title=title, preset=preset, actor=ACTOR)
    except SeriesError as exc:
        _fail(str(exc))
    rel = series.relpath(project.root)
    if as_json:
        _emit({"episode": eid, "title": title, "dir": rel,
               "preset": project.load_config().preset}, True)
    else:
        typer.secho(f"created episode {eid} → {rel}", fg=typer.colors.GREEN)
        typer.secho("  它是一个普通 .manju 项目:cd 进去用任意 manju 命令(check/build/…)",
                    fg=typer.colors.BRIGHT_BLACK)


@series_app.command("sync-bible")
def series_sync_bible_cmd(
    apply: bool = typer.Option(False, "--apply", help="write the ADD candidates (default: report only)"),
    force: list[str] = typer.Option(
        [], "--force", help="overwrite a DIVERGED entry explicitly: kind:id "
        "(repeatable; refused when the episode entry's locked fields would change)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """把剧集 bible 保守同步到各分集:缺失→可新增,不同→只报告(除非 --force kind:id)。"""
    from .core.series import SeriesError, sync_bible

    series = _series()
    try:
        report = sync_bible(series, apply=apply, force=list(force), actor=ACTOR)
    except SeriesError as exc:
        _fail(str(exc))
    if as_json:
        _emit(report, True)
        return
    mode = "已应用 apply" if report["apply"] else "预览 report(未改动)"
    typer.secho(f"剧集 bible 同步 / sync-bible  [{mode}]", fg=typer.colors.CYAN)
    for e in report["episodes"]:
        if e.get("error"):
            typer.secho(f"  {e['id']}  ✗ {e['error']}", fg=typer.colors.RED)
            continue
        if e["added"]:
            typer.secho(f"  {e['id']} 新增 add: {', '.join(e['added'])}", fg=typer.colors.GREEN)
        if e["overwritten"]:
            typer.secho(f"  {e['id']} 覆盖 overwrite: {', '.join(e['overwritten'])}",
                        fg=typer.colors.YELLOW)
        if e["diverged"]:
            typer.secho(f"  {e['id']} 分歧 diverged(只报告,--force 覆盖): "
                        f"{', '.join(e['diverged'])}", fg=typer.colors.MAGENTA)
        for ref in e["refused"]:
            typer.secho(f"  {e['id']} 拒绝 refused: {ref['entry']}(锁定字段 "
                        f"{', '.join(ref['locked'])} 会变)", fg=typer.colors.RED)
    t = report["totals"]
    typer.secho(f"合计  新增 {t['added']}  分歧 {t['diverged']}  覆盖 {t['overwritten']}  "
                f"拒绝 {t['refused']}  已同步 {t['in_sync']}", fg=typer.colors.BRIGHT_BLACK)
    if report["unused_force"]:
        typer.secho(f"  ⚠ 未命中的 --force: {', '.join(report['unused_force'])}",
                    fg=typer.colors.YELLOW)


@series_app.command("characters")
def series_characters_cmd(as_json: bool = typer.Option(False, "--json")):
    """全局角色视图:每个角色的分集在场/分歧/出场(复用 manju appearances)。"""
    from .core.series import SeriesError, series_characters

    series = _series()
    try:
        view = series_characters(series)
    except SeriesError as exc:
        _fail(str(exc))
    if as_json:
        _emit(view, True)
        return
    typer.secho(f"全局角色 / characters  ({view['series']})  分集: "
                f"{', '.join(view['episodes']) or '—'}", fg=typer.colors.CYAN)
    for c in view["characters"]:
        name = f"  {c['name']}" if c.get("name") else ""
        typer.secho(f"  {c['id']}{name}", fg=typer.colors.WHITE)
        for cell in c["episodes"]:
            if cell.get("error"):
                typer.secho(f"      {cell['id']}: ✗ {cell['error']}", fg=typer.colors.RED)
                continue
            if not cell.get("present"):
                typer.secho(f"      {cell['id']}: 不在分集 bible", fg=typer.colors.BRIGHT_BLACK)
                continue
            flag = " · 分歧 diverged" if cell.get("diverged") else ""
            shots = cell.get("appearances") or []
            tail = f" · 出场 {', '.join(shots)}" if shots else " · 无出场"
            typer.echo(f"      {cell['id']}: 在场{flag}{tail}")
    if view["episode_only"]:
        typer.secho("仅存在于分集 bible / episode-only:", fg=typer.colors.YELLOW)
        for eo in view["episode_only"]:
            typer.echo(f"  {eo['id']}  → {', '.join(eo['episodes'])}")


@series_app.command("continuity")
def series_continuity_cmd(as_json: bool = typer.Option(False, "--json")):
    """全局连续性看板(只读):每集 完整/缺素材/有问题/待同步 结论 + 角色/场景/道具跨集矩阵 +
    语音/包装偏差参考性提示(见 core/series.py:series_continuity 的结论优先级说明)。"""
    from .core.series import SeriesError, series_continuity

    series = _series()
    try:
        view = series_continuity(series)
    except SeriesError as exc:
        _fail(str(exc))
    if as_json:
        _emit(view, True)
        return

    verdict_color = {
        "完整": typer.colors.GREEN, "待同步": typer.colors.YELLOW,
        "缺素材": typer.colors.MAGENTA, "有问题": typer.colors.RED,
    }
    typer.secho(f"剧集连续性 / continuity  ({view['series']})", fg=typer.colors.CYAN)
    for e in view["episodes"]:
        if e.get("error"):
            typer.secho(f"  {e['id']}  ✗ 无法读取:{e['error']}", fg=typer.colors.RED)
            continue
        by = e.get("shots_by_state") or {}
        states = ", ".join(f"{k}={v}" for k, v in by.items()) or "—"
        bible = e.get("bible_sync") or {}
        refs = e.get("refs") or {}
        title = f" {e['title']}" if e.get("title") else ""
        typer.secho(
            f"  {e['id']}{title}  [{e['verdict']}]  镜头[{e.get('shots_total', 0)}]: {states}  "
            f"检查 错误{e.get('check_errors', 0)}/警告{e.get('check_warnings', 0)}  "
            f"同步 新增{bible.get('added', 0)}/分歧{bible.get('diverged', 0)}  "
            f"引用 孤儿{refs.get('orphan', 0)}/缺失{refs.get('missing', 0)}",
            fg=verdict_color.get(e["verdict"], typer.colors.WHITE),
        )
    t = view["totals"]
    typer.secho(
        f"合计  完整 {t.get('complete', 0)}  缺素材 {t.get('missing_assets', 0)}  "
        f"有问题 {t.get('problem', 0)}  待同步 {t.get('needs_sync', 0)}"
        + (f"  broken {t['errors']}" if t.get("errors") else ""),
        fg=typer.colors.BRIGHT_BLACK,
    )
    if view["needs_sync"]:
        typer.secho(f"  待同步分集: {', '.join(view['needs_sync'])}  "
                    "→ manju series sync-bible", fg=typer.colors.YELLOW)
    if view.get("packaging_outliers"):
        typer.secho("  包装参考性提示 packaging outliers(不代表错误):",
                    fg=typer.colors.BRIGHT_BLACK)
        for o in view["packaging_outliers"]:
            eps = ", ".join(f"{x['episode']}={x['value']}" for x in o["outliers"])
            typer.echo(f"    {o['field']}: 多数={o['majority']}  例外: {eps}")


@series_app.command("split-script")
def series_split_script_cmd(
    file: Path,
    apply: bool = typer.Option(False, "--apply", help="create missing episodes + write scripts"),
    force: list[str] = typer.Option(
        [], "--force", help="overwrite an episode's edited/diverged script.md "
        "explicitly: episode id (repeatable)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """按显式标记(# E01 <标题> / ## E01)确定性地把长稿拆到各分集 story/script.md。
    已存在且内容不同的分集脚本不会被覆盖(可能是人工改过),需 --force <eid>。"""
    from .core.series import SeriesError, split_script

    series = _series()
    try:
        report = split_script(series, file, apply=apply, force=list(force), actor=ACTOR)
    except SeriesError as exc:
        _fail(str(exc))
    if as_json:
        _emit(report, True)
        return
    mode = "已应用 apply" if report["apply"] else "预览 report(未改动)"
    typer.secho(f"长稿拆分 / split-script  [{mode}]  ← {report['source']}",
                fg=typer.colors.CYAN)
    for e in report["episodes"]:
        if e.get("refused"):
            typer.secho(f"  {e['eid']}  {e['title'] or ''}  [拒绝 refused,未写入]  "
                        f"→ {e['script_path']}", fg=typer.colors.RED)
            typer.secho(f"    {e.get('note', '')}", fg=typer.colors.RED)
            continue
        state = ("新建 created" if e["created"] else
                 ("已存在 exists" if e["exists"] else "将新建 would-create"))
        typer.echo(f"  {e['eid']}  {e['title'] or ''}  [{state}]  → {e['script_path']}  "
                   f"({e['chars']} 字)")
    if not report["apply"]:
        typer.secho("  加 --apply 才会创建分集并写入脚本", fg=typer.colors.BRIGHT_BLACK)
    if report.get("unused_force"):
        typer.secho(f"  ⚠ 未命中的 --force: {', '.join(report['unused_force'])}",
                    fg=typer.colors.YELLOW)


@series_app.command("outline")
def series_outline_cmd(
    package: Path,
    apply: bool = typer.Option(False, "--apply",
                               help="人工确认后创建分集(走既有 new-episode 路径)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """AI_IDE_17 WP4:EpisodeOutlinePackage 检查/应用。Skill/Director 提出拆集提案
    (source spans、标题、目标时长、cliffhanger 仅为建议字段);引擎只验证 span
    覆盖/顺序/重复。默认零写入 inspect;--apply 经既有 new_episode 创建分集。"""
    from .build.seriespack import (SeriesPackError, apply_outline,
                                   inspect_outline, load_outline_package)

    series = _series()
    try:
        pkg = load_outline_package(package)
        report = inspect_outline(series, pkg)
        applied = apply_outline(series, pkg, actor=ACTOR) if apply else None
    except SeriesPackError as exc:
        _fail(str(exc))
        return
    if as_json:
        _emit({"inspect": report, "applied": applied}, True)
        return
    ok = "通过" if report["ok"] else "未通过"
    typer.secho(f"outline 检查 {ok}  (source={report.get('source_script') or '—'})",
                fg=typer.colors.GREEN if report["ok"] else typer.colors.RED)
    for d in report["diagnostics"]:
        typer.secho(f"  ✗ {d['code']}  {d.get('episode') or ''}  {d.get('detail') or ''}",
                    fg=typer.colors.RED)
    for row in report["episodes"]:
        cliff = f"  悬念: {row['cliffhanger']}" if row.get("cliffhanger") else ""
        typer.echo(f"  {row['id']}  {row['title']}  span={row['span']}  "
                   f"{'已存在' if row['exists'] else '将新建'}{cliff}")
    if applied:
        typer.secho(f"  已应用:创建 {sum(1 for r in applied['applied'] if r['created'])} 集",
                    fg=typer.colors.GREEN)
    elif not apply:
        typer.secho("  零写入检查;加 --apply 才创建分集", fg=typer.colors.BRIGHT_BLACK)


# ===================================================== AI_IDE_19: understand → edit
# Media understanding, explainable semantic editing, cutdown and smart reframe.
# Every command below is DERIVED / read-only or a ZERO-WRITE proposal — none write
# the Timeline; the LLM/VLM never does either (contract §1). A real cloud analyzer
# / bridge is gated behind AI_IDE_14 qualification and refuses when unqualified.


@app.command("analyze")
def analyze_cmd(
    media: Path = typer.Argument(..., help="the exact source media to bind analysis to"),
    fixture: Optional[Path] = typer.Option(None, "--fixture",
        help="a committed JSON observation set — fixture analysis FIRST (contract §2)"),
    provider: Optional[str] = typer.Option(None, "--provider",
        help="a cloud video-understanding provider (gated behind AI_IDE_14 qualification)"),
    write: bool = typer.Option(False, "--write", help="materialise the deletable report"),
    as_json: bool = typer.Option(False, "--json"),
):
    """WP1: produce a `manju.media-analysis/v1` derived evidence document bound to
    the EXACT media hash. Fixture analysis is deterministic + offline; the cloud
    slot refuses ANALYZER_NOT_QUALIFIED until a real provider is qualified."""
    import json as _json
    from .core.hashing import hash_file
    from .media import analysis as _an

    project = _project()
    if not media.exists():
        _fail(f"media not found: {media}", code="no_media")
        return
    src_hash = hash_file(media)
    if provider:
        # the gate lives in the provider layer (build-boundary guard); cli calls it.
        from .providers.qualification import analyzer_admission
        decision = analyzer_admission(provider, _an.CAPABILITY, project=project)
        decision["source_media_hash"] = src_hash
        if as_json:
            _emit(decision, True)
        else:
            typer.echo(f"analyzer {provider}: "
                       f"{'admitted' if decision['admitted'] else decision['refusal']}"
                       f" — {decision['reason']}")
        return
    if not fixture or not fixture.exists():
        _fail("fixture analysis needs --fixture <committed json> (contract §2)",
              code="no_fixture")
        return
    try:
        obs = _json.loads(fixture.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(f"bad fixture json: {exc}", code="bad_fixture")
        return
    ev = _an.analyze_with_fixture(src_hash, obs, source_ref=str(media))
    if write:
        _an.write_report(project, ev)
    if as_json:
        _emit(ev, True)
    else:
        typer.secho(f"analyzed {media.name}  hash={src_hash[:16]}…  "
                    f"unknown_axes={ev['unknown_axes']}", fg=typer.colors.GREEN)


@app.command("segments")
def segments_cmd(
    report: Path = typer.Argument(..., help="a media-analysis report JSON"),
    as_json: bool = typer.Option(False, "--json"),
):
    """WP2: derive coherent A/V segments (read-only) from analysis evidence."""
    import json as _json
    from .build import segments as _seg

    _project()
    try:
        ev = _json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(f"bad report: {exc}", code="bad_report")
        return
    segs = _seg.derive_segments(ev)
    if as_json:
        _emit(segs, True)
    else:
        for s in segs:
            typer.echo(f"  [{s['start_ms']}-{s['end_ms']}] scene={s['scene']} "
                       f"risk={s['cut_risk']} dialogue_ok={s['dialogue_complete']}")


@app.command("reframe")
def reframe_cmd(
    report: Path = typer.Argument(..., help="a media-analysis report JSON (ROI tracks)"),
    target: str = typer.Option(..., "--target", help="target WxH, e.g. 1080x1920"),
    source: str = typer.Option(..., "--source", help="source WxH, e.g. 1920x1080"),
    max_px_per_s: float = typer.Option(600.0, "--max-px-s", help="crop jump rate limit"),
    as_json: bool = typer.Option(False, "--json"),
):
    """WP4: compile ROI tracks → crop keyframes (read-only). Rate-limited, safe-area
    aware, multi-subject → needs_manual, infeasible → blanking. FORMAT_ONLY."""
    import json as _json
    from .media import analysis as _an, reframe as _rf

    _project()
    try:
        ev = _json.loads(report.read_text(encoding="utf-8"))
        sw, sh = (int(x) for x in source.lower().split("x"))
        tw, th = (int(x) for x in target.lower().split("x"))
    except (OSError, ValueError) as exc:
        _fail(f"bad input: {exc}", code="bad_input")
        return
    out = _rf.compile_crop_keyframes(_an.roi_tracks(ev), source_wh=(sw, sh),
                                     target_wh=(tw, th), max_px_per_s=max_px_per_s)
    if as_json:
        _emit(out, True)
    else:
        typer.secho(f"reframe {source}→{target}: status={out['status']} "
                    f"strategy={out['strategy']} keyframes={len(out['keyframes'])}",
                    fg=typer.colors.GREEN if out["status"] == _rf.OK else typer.colors.YELLOW)


@app.command("rough-cut")
def rough_cut_cmd(
    align: Path = typer.Argument(..., help="an AI_IDE_18 <take>.align.json evidence file"),
    as_json: bool = typer.Option(False, "--json"),
):
    """WP5a: annotate-only speech rough-cut proposal (never deletes; reversible;
    never touches the Timeline). Built from AI_IDE_18 word/speaker evidence."""
    import json as _json
    from .qc import roughcut as _rc

    _project()
    try:
        ev = _json.loads(align.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(f"bad align evidence: {exc}", code="bad_align")
        return
    prop = _rc.rough_cut_proposal(ev)
    if as_json:
        _emit(prop, True)
    else:
        typer.secho(f"rough cut: {len(prop['annotations'])} annotation(s) "
                    f"[default={prop['default_action']}, reversible]",
                    fg=typer.colors.GREEN)
        for a in prop["annotations"]:
            typer.echo(f"  {a['kind']}  [{a['start_ms']}-{a['end_ms']}]  {a['reason']}")


@app.command("tool")
def tool_cmd(
    op: str = typer.Argument(..., help="a whitelisted edit op"),
    dry_run: bool = typer.Option(True, "--dry-run/--resolve"),
    as_json: bool = typer.Option(False, "--json"),
):
    """WP6: describe a whitelisted intent op's EXISTING deterministic executor.
    Off-whitelist is refused; there is no LLM planner in core. (Programmatic
    resolve/dry-run with real args lives in build.toolmap.)"""
    from .build import toolmap as _tm

    _project()
    spec = _tm.TOOL_WHITELIST.get(op)
    if spec is None:
        _fail(f"op {op!r} is not on the tool whitelist {list(_tm.whitelist_ops())} "
              f"— refused, never improvised", code="tool_refused")
        return
    out = {"op": op, "executor": spec["executor"], "kind": spec["kind"],
           "priced": 0.0, "deterministic": True,
           "required": list(_tm._REQUIRED.get(op, ()))}
    if as_json:
        _emit(out, True)
    else:
        typer.echo(f"{op} → {out['executor']}  (whitelist: {list(_tm.whitelist_ops())})")


# ===================================================== 14_21 CLOSEOUT: bridge CLI
# `manju bridge plan|run|adopt` — THIN wrappers over the ONE bridge service
# (build.bridge.*, gated in the provider layer via providers.base.dispatch_bridge
# → providers.qualification.bridge_admission). No second gate, no second digest
# scheme, no bridge ledger. Honest JSON; refs stay project-relative (no
# absolute paths, no secrets).

bridge_app = typer.Typer(
    no_args_is_help=True,
    help="生成式转场 bridge:plan / run / adopt(同一服务、Provider 层准入门)")
app.add_typer(bridge_app, name="bridge")


@bridge_app.command("plan")
def bridge_plan_cmd(
    prev: Path = typer.Option(..., "--prev", help="前一镜头 END 帧文件"),
    next_frame: Path = typer.Option(..., "--next", help="后一镜头 START 帧文件"),
    duration_ms: int = typer.Option(..., "--duration-ms", help="bridge 时长(毫秒)"),
    direction: Optional[str] = typer.Option(None, "--direction",
                                            help="运动/方向约束,如 left_to_right"),
    description: Optional[str] = typer.Option(None, "--description",
                                              help="转场描述(进入 request digest)"),
    out: Optional[Path] = typer.Option(None, "--out", help="把 plan JSON 写到此文件"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Bind the bridge inputs: BOTH endpoint frames as real files (hashed here),
    duration, direction, description. Zero-write unless --out."""
    import json as _json

    from .build.bridge import BridgeError, plan_bridge

    project = _project()
    try:
        plan = plan_bridge(prev_end_frame=prev, next_start_frame=next_frame,
                           duration_ms=duration_ms, direction=direction,
                           description=description, project_root=project.root)
    except (BridgeError, OSError) as exc:
        _fail(str(exc), code="bad_plan")
        return
    if out is not None:
        out.write_text(_json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    if as_json:
        _emit(plan, True)
        return
    typer.secho(f"bridge plan: digest={plan['request_digest'][:23]}…  "
                f"duration={plan['duration_ms']}ms", fg=typer.colors.GREEN)
    typer.echo(f"  prev={plan['prev_end_frame']}  next={plan['next_start_frame']}")
    if out is not None:
        typer.secho(f"  written: {out.name}", fg=typer.colors.BRIGHT_BLACK)


@bridge_app.command("run")
def bridge_run_cmd(
    shot: str = typer.Argument(..., help="镜头 id(spec 身份来源)"),
    plan_file: Path = typer.Option(..., "--plan", help="bridge plan JSON 文件"),
    provider: str = typer.Option(..., "--provider", help="视频 provider id"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Execute a planned bridge through the ONE gated service. An unqualified
    provider refuses in the provider-layer gate with transport 0."""
    import json as _json

    from .build.bridge import BridgeError, bridge_lineage, execute_bridge
    from .providers.base import ProviderFailure
    from .providers.registry import get_provider

    project = _project()
    try:
        plan = _json.loads(plan_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(f"bad plan file: {exc}", code="bad_plan")
        return
    try:
        prov = get_provider(provider)
    except KeyError as exc:
        _fail(str(exc), code="unknown_provider")
        return
    try:
        take = execute_bridge(project, shot, plan, provider=prov)
    except BridgeError as exc:
        _fail(str(exc), code="bridge_refused")
        return
    except ProviderFailure as exc:
        _fail(str(exc),
              code=str(exc.detail.get("code") or exc.kind.value))
        return
    lineage = bridge_lineage(plan, take, shot=shot)
    if as_json:
        _emit({"shot": shot, "take": take.name, "lineage": lineage}, True)
        return
    typer.secho(f"bridge take {take.name} registered for {shot} "
                f"(transition CANDIDATE — adopt 前不得进 final)",
                fg=typer.colors.GREEN)


@bridge_app.command("adopt")
def bridge_adopt_cmd(
    lineage_file: Path = typer.Option(..., "--lineage", help="bridge lineage JSON"),
    review_file: Path = typer.Option(..., "--review",
                                     help="当前绑定的 accepted Assurance JSON"),
    media: Optional[Path] = typer.Option(None, "--media",
                                         help="bridge 输出媒体文件(校验精确字节)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Adopt a reviewed bridge: consumes a CURRENT-bound accepted Assurance and
    emits a zero-write adoption Proposal — it never writes Shot/selected_take."""
    import json as _json

    from .build.bridge import BridgeError, adopt_bridge

    _project()
    try:
        lineage = _json.loads(lineage_file.read_text(encoding="utf-8"))
        review = _json.loads(review_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(f"bad input: {exc}", code="bad_input")
        return
    try:
        adopted = adopt_bridge(lineage, review=review, media_path=media)
    except BridgeError as exc:
        _fail(str(exc), code="adopt_refused")
        return
    if as_json:
        _emit(adopted, True)
        return
    prop = adopted["proposal"]
    typer.secho(f"bridge {adopted.get('take')} adopted — PROPOSAL only "
                f"(do_not_execute_automatically={prop['do_not_execute_automatically']})",
                fg=typer.colors.GREEN)
    typer.echo(f"  source patch: {prop['source_patch']['field']} → "
               f"{prop['source_patch']['proposed_value']}  "
               f"via {prop['source_patch']['apply_via']}")


if __name__ == "__main__":
    app()
