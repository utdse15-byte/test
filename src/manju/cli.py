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
from pathlib import Path
from typing import Optional

import typer

from .core.check import run_check
from .core.container import Project, ProjectError
from .core.events import append_event, tail_events
from .core.locks import seal_lock

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Manju One — a build system for video. 一键出片:manju build")

ACTOR = os.environ.get("MANJU_ACTOR", "human")


def _project(path: Optional[Path] = None) -> Project:
    try:
        return Project.find(path or Path.cwd())
    except ProjectError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


def _emit(data, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _fail(message: str) -> None:
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
    typer.echo(f"花费  {info['total_cost']} {info['currency']}"
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


@app.command("import")
def import_(files: list[Path], as_json: bool = typer.Option(False, "--json")):
    """Register a file into the project. Real footage/audio → media/imports
    (sacred: never deleted, §3). Text (.txt/.md) → story/imports/<name>.md
    instead, as adaptable source text the AI director can turn into a script
    (novel→script). Both honour the same no-overwrite _2 suffixing."""
    project = _project()
    registered: list[str] = []
    story_imports: list[str] = []  # the text drops, for the adapt-me hint
    dup_notes: list[str] = []      # duplicate-content advisories (media only)
    for f in files:
        if not f.exists():
            _fail(f"not found: {f}")
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
        shutil.copy2(f, dest)
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
                 {"files": registered, "story_imports": story_imports, "previews": previews})
    if as_json:
        _emit({"imported": registered, "story_imports": story_imports,
               "previews": previews, "duplicates": dup_notes}, True)
    else:
        for r in registered:
            typer.secho(f"imported {r}", fg=typer.colors.GREEN)
        for src_rel, thumb in previews.items():
            typer.echo(f"  preview: {thumb}")
        for note in dup_notes:
            typer.secho(f"⚠ {note}", fg=typer.colors.YELLOW)
        for r in story_imports:
            typer.secho(f"  提示 hint: {r} 是文本稿,可改编成剧本(novel→script):"
                        "读它 → 写 story/outline.md、story/script.md → 再拆 shots/",
                        fg=typer.colors.BRIGHT_BLACK)


# ------------------------------------------------------------------- build


@app.command()
def build(
    target: str = typer.Option("final", help="proxy | final | exports | qc"),
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
    as_json: bool = typer.Option(False, "--json"),
):
    """One-command build: fill gaps → timeline → render → QC → exports (§11).

    A plan that spends real money stops as waiting_user unless --yes (the §8.3
    ask_before gate); `--dry-run` shows the plan and estimate without spending.
    See `manju spend` for where the money actually went.

    --mode quality|balanced|speed (goal 14) turns one knob for provider strategy
    bias + retry budget + generation concurrency; explicit per-shot providers and
    routing rules are never overridden. `manju routing explain` previews it."""
    from .build.graph import run_build
    from .core.models import BUILD_MODE_NAMES

    if target not in ("proxy", "final", "exports", "qc"):
        _fail(f"unknown target: {target}")
    if mode is not None and mode not in BUILD_MODE_NAMES:
        _fail(f"--mode must be one of {BUILD_MODE_NAMES}, got {mode!r}")
    result = run_build(_project(), target=target, gen=gen,
                       regen_stale=regen_stale, dry_run=dry_run, force=force,
                       actor=ACTOR, assume_yes=yes, mode=mode)
    if as_json:
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
        _fail("redo: pass a shot id (`manju redo S002`) or a batch selector "
              "(--all-stale / --all-missing / --all / --shots S001,S003)")
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
    project = _project()
    if file:
        from .providers.manual import register_manual_take

        info = register_manual_take(project, shot_id, file)
        take = info.name
    if not take:
        _fail("provide a take name or --file")
    if project.get_take(shot_id, take) is None:
        _fail(f"{shot_id} has no take '{take}'")
    project.update_shot_raw(
        shot_id, lambda d: d.setdefault("status", {}).__setitem__("selected_take", take)
    )
    append_event(project.root, ACTOR, "select", {"shot": shot_id, "take": take})
    if as_json:
        _emit({"shot": shot_id, "take": take, "ok": True}, True)
    else:
        typer.secho(f"{shot_id}: selected {take}", fg=typer.colors.GREEN)


# ------------------------------------------------------------- lock/unlock


@app.command()
def lock(shot_id: str, field: str, as_json: bool = typer.Option(False, "--json")):
    """Seal a field's current value with a hash (§5). Build enforces it."""
    project = _project()
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
    and slug scheme are REUSED from manju.mcp.tools so the two share one counter.
    With no --body on a pipe, the body is read from stdin; otherwise it may be
    empty."""
    from .core.yamlio import atomic_write_text
    from .mcp.tools import _next_proposal_number, _slugify  # shared numbering/slug

    project = _project()
    if body is None:
        body = "" if sys.stdin.isatty() else sys.stdin.read()
    project.proposals_dir.mkdir(parents=True, exist_ok=True)
    number = _next_proposal_number(project.proposals_dir)
    path = project.proposals_dir / f"{number:04d}_{_slugify(title)}.md"
    atomic_write_text(path, f"# {title}\n\n{body}\n")
    rel = project.relpath(path)
    append_event(project.root, ACTOR, "propose", {"title": title, "path": rel})
    if as_json:
        _emit({"path": rel}, True)
    else:
        typer.secho(f"proposal → {rel}", fg=typer.colors.GREEN)


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

    import manju

    from .agents import AgentResolutionError, build_command, resolve_agent

    project = _project()
    try:
        template = resolve_agent(
            getattr(project.load_config(), "agent", None), agent
        )
    except AgentResolutionError as exc:
        _fail(str(exc))

    # Prefer a project-local skill, then the repo-bundled skills/manju/SKILL.md.
    skill_text: Optional[str] = None
    for cand in (
        project.root / "skills" / "manju" / "SKILL.md",
        Path(manju.__file__).resolve().parents[2] / "skills" / "manju" / "SKILL.md",
    ):
        if cand.exists():
            skill_text = cand.read_text(encoding="utf-8")
            break

    if skill_text:
        composed = ("按照以下 Manju 操作手册工作:\n\n" + skill_text
                    + "\n\n---\n\n任务:" + prompt_text)
    else:
        composed = _MINI_PLAYBOOK + "\n\n任务:" + prompt_text

    argv = build_command(template, composed)
    if shutil.which(argv[0]) is None:
        _fail(f"agent CLI '{argv[0]}' not found on PATH — install it or pick "
              "another via --agent/MANJU_AGENT/project.yaml:agent")
    append_event(project.root, ACTOR, "auto",
                 {"prompt": prompt_text, "agent": argv[0]})
    env = dict(os.environ)
    env["MANJU_ACTOR"] = "ai"  # every action the driven agent takes is logged as ai
    proc = subprocess.run(argv, cwd=project.root, env=env)
    raise typer.Exit(proc.returncode)


# ---------------------------------------------------------------- qc/repair


@app.command()
def qc(deep: bool = typer.Option(False), as_json: bool = typer.Option(False, "--json")):
    """Three-layer QC; writes reports/qc.json, qc.md, repair_plan.yaml (§9)."""
    from .qc.checks import run_qc as _run_qc
    from .qc.report import write_reports

    project = _project()
    report = _run_qc(project, project.load_timeline(), deep=deep)
    paths = write_reports(project, report)
    if as_json:
        _emit(report.to_dict(), True)
    else:
        errors = sum(1 for i in report.items if i.level == "error")
        warns = sum(1 for i in report.items if i.level == "warn")
        typer.echo(f"QC: {errors} errors, {warns} warnings → {project.relpath(paths['qc_md'])}")
        typer.secho("qc ok" if report.ok else "qc found errors",
                    fg=typer.colors.GREEN if report.ok else typer.colors.RED)
    if not report.ok:
        raise typer.Exit(1)


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


@app.command()
def repair(
    auto: bool = typer.Option(False, "--auto"),
    op: Optional[str] = typer.Option(
        None, "--op", help="explicit repair op: retime|extend|trim|inout|croppad "
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
    as_json: bool = typer.Option(False, "--json"),
):
    """Repair the film (§9). Two modes:

    - `--op <retime|extend|trim|inout|croppad>` runs an explicit ffmpeg repair on
      a shot's take, registering the result as a NEW take (append-only lineage):
        manju repair --op retime  --shot S001 --factor 0.9
        manju repair --op extend  --shot S001 --ms 500 --mode freeze
        manju repair --op trim    --shot S001 --ms 300
        manju repair --op inout   --shot S001 --in-ms 500 --out-ms 1500
        manju repair --op inout   --shot S001 --in-ms 500 --out-ms 1500 --mode reencode
        manju repair --op croppad --shot S001 --mode pad_blur
    - `--auto` executes auto-safe items from repair_plan.yaml (redo/degrade);
      the rest stay for humans. Repair-plan action is 'edit params and rebuild'.
    """
    from .build.graph import redo_shot
    from .core.yamlio import read_yaml

    project = _project()
    if op is not None:
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
    otio: bool = typer.Option(False, "--otio"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Export drafts/captions from the compiled timeline (§11).

    JianYing is dual-path (decision 8): pyJianYingDraft native draft as the
    primary, the diff-stable skeleton + lint as the secondary; capcut-cli
    lints the result when installed. final.mp4/SRT/OTIO always remain the
    fallback exits (§14)."""
    project = _project()
    timeline = project.load_timeline()
    if timeline is None:
        _fail("no timeline.json — run `manju build` first")
    if not (jianying or capcut or srt or otio):
        srt = otio = True
    outputs: dict[str, Path] = {}
    notes: list[str] = []
    if srt:
        from .exporters.srt_ass import export_captions

        outputs.update(export_captions(project, timeline))
    if otio:
        from .exporters.otio import export_otio

        outputs["otio"] = export_otio(project, timeline)
    if jianying:
        from .exporters.jianying import export_jianying
        from .exporters.native_draft import (
            ExporterUnavailable,
            capcut_cli_lint,
            export_jianying_native,
        )

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


# ----------------------------------------------------------------- package


@app.command()
def package(
    force: bool = typer.Option(False, "--force",
                               help="re-cut even if the content key matches"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Cut the cover (+ teaser) out of the current final (§13-14).

    Cover: a frame pulled from the final (frame mode) or a rendered card (card
    mode) → exports/packaging/cover.png at project resolution. Teaser (when
    enabled in packaging.yaml): the final sliced [from_ms, +duration_ms],
    re-encoded with the final params → exports/packaging/teaser.mp4. Both are
    idempotent via .key.json sidecars; --force bypasses. Needs a final —
    without one it points you at `manju build`."""
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
def exports(as_json: bool = typer.Option(False, "--json")):
    """导出中心 Export center — every finished-output's freshness at a glance.

    One honest table over the nine deliverables (成片/预览版/SRT/ASS/OTIO/剪映
    草稿/CapCut 草稿/封面/预告): 上新 / 待更新 / 缺失 / 有问题 / 待人工确认 /
    已人工确认, each with the one-line evidence behind the verdict. Read-only —
    never spends, never mutates. Reads the SAME engine (build/exportstatus) the
    GUI /exports page renders, so the two can never disagree. `--json` for agents."""
    from .build.exportstatus import deliverables_data

    project = _project()
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
    typer.secho("（生成/更新与标记已人工确认见 manju gui → 导出中心）",
                fg=typer.colors.BRIGHT_BLACK)


# ----------------------------------------------------------------- explain


@app.command()
def explain(as_json: bool = typer.Option(False, "--json")):
    """Why will the next build do what it will do? Read-only: per-shot
    picture/voice states with hash evidence, timeline fingerprint diff, and
    final/proxy content-key verdicts. Never mutates, never spends."""
    from .build.explain import explain as _explain

    info = _explain(_project())
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
        typer.echo(line)
    tl = info["timeline"]
    typer.echo(f"时间线  mode={tl['mode']} captions={tl['captions_mode']} → {tl['verdict']}")
    renders = info["renders"]
    for target in ("final", "proxy"):
        if target in renders:
            r = renders[target]
            typer.echo(f"{target:5}  {r.get('latest') or '—'} → {r['verdict']}")


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
        _fail("prompt: pass a shot id (`manju prompt S001`) or --check")
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

    A priced TTS synthesis stops as waiting_user unless --yes — the same §8.3
    ask_before gate build and redo enforce (R7 spend-gate hole closure)."""
    from .build.graph import BuildError, WaitingUser, spend_gate, voice_batch
    from .providers.tts import TtsUnavailable, get_tts_provider

    project = _project()

    batch_flags = [bool(shots), all_shots, missing]
    if any(batch_flags):
        if shot_id is not None:
            _fail("voice: a positional shot id is mutually exclusive with --shots/--all/--missing")
        if sum(batch_flags) > 1:
            _fail("voice: --shots/--all/--missing are mutually exclusive")
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
        _fail("voice: pass a shot id (`manju voice S002`) or a batch selector "
              "(--missing / --all / --shots S001,S003)")
    shot = project.load_shot(shot_id)
    if not shot.dialogue.text:
        _fail(f"{shot_id} has no dialogue.text to voice")
    try:
        tts = get_tts_provider(provider)
        manifest = getattr(tts, "manifest", None)
        cost = getattr(manifest, "cost", None) if manifest is not None else None
        if cost is not None:
            spend_gate(project, cost.per_call, cost.currency, assume_yes=yes,
                       hint=f"确认后重试:manju voice {shot_id} --yes")
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


# -------------------------------------------------------------- transcribe


@app.command()
def transcribe(
    media: Path,
    provider: Optional[str] = typer.Option(None, help="asr manifest id (default: first configured)"),
    from_srt: Optional[Path] = typer.Option(None, "--from-srt", help="manual input: normalize your own SRT"),
    text: Optional[str] = typer.Option(None, "--text", help="manual input: plain transcript, auto-timed over the media"),
    out: Optional[Path] = typer.Option(None, help="output SRT (default captions/transcripts/<name>.srt)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Transcribe imported real footage into an SRT (M4 ASR slot, on demand).

    Three equal on-ramps: a configured cloud ASR manifest (§8.6, type: asr),
    --from-srt with human-made subtitles, or --text with the raw transcript
    (distributed over the media duration). The scripted-drama main line never
    needs this — its captions come from dialogue + TTS alignment."""
    from .providers.asr import (
        AsrUnavailable,
        distribute_text,
        get_asr_provider,
        parse_srt,
        segments_to_srt,
    )

    project = _project()
    media_abs = media if media.is_absolute() else project.root / media
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


@app.command()
def board(
    serve: bool = typer.Option(False, "--serve/--no-serve",
                               help="serve an actionable local workspace instead of writing board.html"),
    port: int = typer.Option(8787, "--port", help="serve port (--serve)"),
    host: str = typer.Option("127.0.0.1", "--host", help="serve host (--serve; localhost only)"),
    open_browser: bool = typer.Option(True, "--open/--no-open",
                                      help="open the board in a browser after binding (--serve)"),
):
    """Review board — the director's workbench (§1-⑦).

    Default: write a static, self-contained ``board.html``. With ``--serve`` it
    becomes a live, ACTIONABLE workspace on localhost: click to select takes,
    redo/rollback shots, build/qc/package/snapshot — a thin veneer over the same
    core the CLI calls (unlock/gc/pack stay off this surface, like MCP §11)."""
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
    and dangerous ops (unlock, gc --hard) are absent, exactly as on MCP."""
    from .gui.server import create_server, discover_workspace

    projects: Optional[dict] = None
    if workspace is not None:
        projects = discover_workspace(workspace)
        if not projects:
            _fail(f"no manju projects found under {workspace}")
        project = next(iter(projects.values()))
        typer.echo("workspace: " + ", ".join(
            f"{slug} ({p.root.name})" for slug, p in projects.items()))
    else:
        project = _project()
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


@app.command()
def pack(out: Optional[Path] = typer.Option(None),
         full: bool = typer.Option(False, "--full",
                                   help="include the rebuildable render caches too")):
    """Archive the project into a single .manjupkg (zip) for backup/migration.

    FIX-E: the original project directory name rides in the zip comment, so
    the archive file can be renamed freely without losing the identity.
    The segment/proxy caches are rebuildable (§3) and excluded by default —
    they can dwarf the truth+imports payload; --full keeps them."""
    project = _project()
    out = out or project.root.parent / (project.root.stem + ".manjupkg")
    excluded_cache = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.comment = json.dumps(
            {"manjupkg": 1, "name": project.root.name}, ensure_ascii=False
        ).encode("utf-8")
        for path in sorted(project.root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(project.root).as_posix()
            if any(rel.startswith(prefix) for prefix in PACK_EXCLUDE):
                continue
            if not full and any(rel.startswith(prefix) for prefix in PACK_CACHE):
                excluded_cache += path.stat().st_size
                continue
            zf.write(path, rel)
    typer.secho(f"packed → {out}", fg=typer.colors.GREEN)
    if excluded_cache:
        typer.echo(f"  跳过可重建缓存 {excluded_cache / 1e6:.1f} MB "
                   "(segments/proxy — 重建即回;--full 保留)")


@app.command()
def unpack(archive: Path, dest: Optional[Path] = typer.Option(
        None, "--dest", help="override the restored directory (default: the original name)")):
    """Restore a .manjupkg. FIX-E: the ORIGINAL project name is restored by
    default (embedded at pack time); --dest overrides it."""
    if not archive.exists():
        _fail(f"not found: {archive}")
    original_name: str | None = None
    with zipfile.ZipFile(archive) as zf:
        try:
            meta = json.loads(zf.comment.decode("utf-8")) if zf.comment else {}
            if isinstance(meta, dict) and meta.get("manjupkg"):
                original_name = str(meta.get("name") or "") or None
        except (json.JSONDecodeError, UnicodeDecodeError):
            original_name = None  # pre-FIX-E archive: fall back to the stem
        if dest is None:
            dest = Path.cwd() / (original_name or (archive.stem + ".manju"))
        if dest.exists():
            _fail(f"destination exists, refusing to overwrite: {dest}")
        zf.extractall(dest)
    (dest / ".manju").mkdir(exist_ok=True)
    typer.secho(f"unpacked → {dest}", fg=typer.colors.GREEN)


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


@app.command()
def refs(shot_id: str = typer.Argument(..., help="the shot to inspect"),
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


@app.command()
def tasks(n: int = typer.Option(20, "-n", help="how many recent ledger rows to show"),
          as_json: bool = typer.Option(False, "--json")):
    """Recent generation jobs from the run ledger — the JOB/QUEUE view (§8.3, goal 19).

    Read-only view of the disposable SQLite ledger: recent runs with provider,
    shot, status (succeeded/failed/moderation-rejected), cost and error tail,
    plus still-in-flight cloud jobs (submitted/polling). Footer aggregates spend
    by provider and the project total (the same numbers `manju status` shows).

    For the MONEY view — per-shot/per-provider breakdowns and estimate-vs-actual
    delta — use `manju spend`; it reads the same ledger through the same query
    helpers, so the two never disagree on the numbers."""
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
    try:
        with RuntimeState(project.root) as state:
            runs = state.run_log(n)
            pending = state.pending_jobs()
            total, currency = state.total_cost()
            by_provider = state.cost_by_provider()
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
    typer.secho("花费 / spend by provider:", fg=typer.colors.BRIGHT_BLACK)
    for row in by_provider:
        cur = row["currency"] or "?"
        typer.echo(f"  {row['provider'] or '—':<14}  {row['cost']:g} {cur}  ({row['runs']} runs)")
    total_cur = currency or "(混合/mixed)"
    typer.secho(f"  合计 / total  {float(total):g} {total_cur}", fg=typer.colors.CYAN)


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

    cur = report["currency"] or "(混合/mixed)"
    typer.secho(f"花费 / spend  (来源/source: {report['source']})", fg=typer.colors.CYAN)
    typer.secho(f"  合计 / total  {report['total']:g} {cur}", fg=typer.colors.CYAN)
    if report["estimated_total"] is not None:
        delta = report["delta"] or 0.0
        sign = "+" if delta >= 0 else ""
        typer.echo(f"  预估 / estimated  {report['estimated_total']:g}"
                   f"   δ(实际−预估) {sign}{delta:g}")
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

    try:
        result = _snapshot(_project(), label)
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
):
    """Diff two finals — what changed between final_vA and final_vB (goal 11).

    Duration/resolution/fps deltas over a per-shot change map (same/different
    take with each take's provider, added/removed/moved/duration-changed),
    caption cue diff, audio-track diff and packaging diff — each with a
    root-cause line correlated from events.jsonl. Reads the per-final timeline
    snapshots persisted at render time; finals rendered before snapshots
    existed degrade to a key-only diff. Default: the latest two finals."""
    from .build.compare import CompareError, compare_finals

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
    freed = 0
    for d in (project.segments_dir, project.proxy_dir):
        for f in d.glob("*"):
            if f.is_file():
                freed += f.stat().st_size
                f.unlink()
    if hard:
        if not _interactive():
            _fail("gc --hard is interactive-only")
        if typer.confirm("删除所有未选中的 take 媒体本体?(选中、imports、final 不受影响)"):
            for sid in project.shot_ids():
                shot = project.load_shot(sid)
                for take in project.takes(sid):
                    if take.name != shot.status.selected_take and take.media_path:
                        freed += take.media_path.stat().st_size
                        take.media_path.unlink()
    append_event(project.root, ACTOR, "gc", {"freed_bytes": freed, "hard": hard})
    if as_json:
        _emit({"freed_bytes": freed, "hard": hard}, True)
    else:
        typer.secho(f"freed {freed / 1e6:.1f} MB", fg=typer.colors.GREEN)


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
def serve_mcp():
    """MCP server over stdio (§11) — a thin wrapper over the same core. Dangerous
    commands (unlock, gc --hard) are never on this surface; Claude Code drives
    everything else here or via files + this CLI, two equivalent paths (§11)."""
    project = _project()
    from .mcp.server import main as mcp_main

    raise typer.Exit(mcp_main(["--project", str(project.root)]))


# ============================================================ providers group
# `manju providers …` — manage provider manifests (§8.2/§8.6, goal item 1).
# Keys are NEVER printed: list/show/check report the env-var NAME and whether it
# is set, and show masks any secret-ish value.

providers_app = typer.Typer(no_args_is_help=True,
                            help="Manage provider manifests (§8.2/§8.6).")
app.add_typer(providers_app, name="providers")

_MASK = "***"
# key names whose VALUE must never be shown (key_env holds an env-var NAME, not
# a secret, so it is excluded).
_SECRET_KEY_RE = re.compile(
    r"(secret|token|password|passwd|credential|api[_-]?key|apikey|access[_-]?key|\bkey\b)",
    re.IGNORECASE,
)


def _mask_secrets(obj):
    """Recursively mask any value whose KEY looks secret-ish (never print keys)."""
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
        providers_dir,
        scaffold_template,
    )

    if type_ not in PROVIDER_TYPES:
        _fail(f"--type must be one of {list(PROVIDER_TYPES)}, got {type_!r}")
    adapter = adapter or default_adapter_for_type(type_)
    try:
        text = scaffold_template(provider_id, type_, adapter)
    except ValueError as exc:
        _fail(str(exc))
    dest = providers_dir() / provider_id / "provider.yaml"
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

    manifests, _ = load_manifests()
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
    if as_json:
        _emit({"id": provider_id, "ok": passed, "enabled": not m.disabled,
               "problems": findings, "live": live_info}, True)
        raise typer.Exit(0 if passed else 1)
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
    from .providers.manifest import providers_dir

    path = providers_dir() / provider_id / "provider.yaml"
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
    from .providers.manifest import providers_dir

    path = providers_dir() / provider_id / "provider.yaml"
    if not path.exists():
        _fail(f"no such provider {provider_id!r} (looked at {path})")
    masked = _mask_secrets(read_yaml(path) or {})
    if as_json:
        _emit(masked, True)
        return
    typer.echo(dump_yaml(masked))


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


if __name__ == "__main__":
    app()
