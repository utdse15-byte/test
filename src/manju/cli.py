"""manju CLI (§11). Every read command supports --json — the AI collaboration
surface is files + this CLI (§2); MCP is a thin wrapper over the same core.

Hard rules enforced here (§5, §10): `unlock` only works on an interactive
terminal with confirmation and is never exposed over MCP; nothing under
media/imports is ever deleted; renders/final only grows.
"""

from __future__ import annotations

import json
import os
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


# --------------------------------------------------------------------- new


@app.command()
def new(
    name: str,
    vertical: bool = typer.Option(True, "--vertical/--horizontal"),
    path: Optional[Path] = typer.Option(None, help="parent directory (default: cwd)"),
):
    """Create a <name>.manju project directory (§3)."""
    dest = (path or Path.cwd()) / name
    project = Project.create(dest, name=name, vertical=vertical)
    append_event(project.root, ACTOR, "new", {"name": name})
    typer.secho(f"created {project.root}", fg=typer.colors.GREEN)


# ------------------------------------------------------------------ status


@app.command()
def status(as_json: bool = typer.Option(False, "--json")):
    """Takeover entry point: phase, gaps, spend, next step (§10)."""
    from .build.status import project_status

    info = project_status(_project())
    if as_json:
        _emit(info, True)
        return
    typer.echo(f"项目  {info['project']}  {info['resolution']}  mode={info['mode']}")
    typer.echo(f"镜头  共 {info['shots_total']}: " + ", ".join(
        f"{k}={len(v)}" for k, v in info["shots_by_state"].items()) if info["shots_by_state"] else "镜头  0")
    tl = info["timeline"]
    typer.echo(f"时间线  {'✓ ' + str(tl['duration_ms']) + 'ms (' + str(tl['mode']) + ')' if tl['exists'] else '—'}")
    typer.echo(f"成片  {info['latest_final'] or '—'}")
    if info["qc"]:
        typer.echo(f"QC   errors={info['qc'].get('errors')} warnings={info['qc'].get('warnings')}")
    typer.echo(f"花费  {info['total_cost']} {info['currency']}"
               + (f" / 预算 {info['budget_limit']}" if info["budget_limit"] else ""))
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


@app.command("import")
def import_(files: list[Path]):
    """Register media into media/imports (sacred: never deleted, §3)."""
    project = _project()
    registered = []
    for f in files:
        if not f.exists():
            _fail(f"not found: {f}")
        dest = project.imports_dir / f.name
        n = 2
        while dest.exists():  # imports are never overwritten either
            dest = project.imports_dir / f"{f.stem}_{n}{f.suffix}"
            n += 1
        shutil.copy2(f, dest)
        registered.append(project.relpath(dest))
    append_event(project.root, ACTOR, "import", {"files": registered})
    for r in registered:
        typer.secho(f"imported {r}", fg=typer.colors.GREEN)


# ------------------------------------------------------------------- build


@app.command()
def build(
    target: str = typer.Option("final", help="proxy | final | exports | qc"),
    gen: str = typer.Option("missing", help="missing | auto | off"),
    regen_stale: bool = typer.Option(False, "--regen-stale"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    as_json: bool = typer.Option(False, "--json"),
):
    """One-command build: fill gaps → timeline → render → QC → exports (§11)."""
    from .build.graph import run_build

    if target not in ("proxy", "final", "exports", "qc"):
        _fail(f"unknown target: {target}")
    result = run_build(_project(), target=target, gen=gen,
                       regen_stale=regen_stale, dry_run=dry_run, actor=ACTOR)
    if as_json:
        _emit(result.to_dict(), True)
    else:
        if dry_run:
            typer.echo(f"计划任务 {len(result.plan)} 项,预估成本 {result.estimated_cost}")
            for p in result.plan:
                typer.echo(f"  {p['shot']}: {p['reason']} → {p['provider']} "
                           f"×{p['candidates']} ({p['duration_ms']}ms) ≈{p['estimated_cost']}")
        for w in result.warnings:
            typer.secho(f"⚠ {w}", fg=typer.colors.YELLOW)
        for e in result.errors:
            typer.secho(f"✗ {e}", fg=typer.colors.RED)
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
    shot_id: str,
    candidates: Optional[int] = typer.Option(None),
    provider: Optional[str] = typer.Option(None),
    seed: Optional[int] = typer.Option(None),
):
    """Force new takes for one shot (append-only; existing selection stands)."""
    from .build.graph import redo_shot

    takes = redo_shot(_project(), shot_id, candidates=candidates,
                      provider=provider, seed=seed, actor=ACTOR)
    typer.secho(f"{shot_id}: new takes {', '.join(takes)}", fg=typer.colors.GREEN)


# ------------------------------------------------------------------ select


@app.command()
def select(
    shot_id: str,
    take: Optional[str] = typer.Argument(None),
    file: Optional[Path] = typer.Option(None, "--file", help="register a human file as a manual take and select it"),
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
    typer.secho(f"{shot_id}: selected {take}", fg=typer.colors.GREEN)


# ------------------------------------------------------------- lock/unlock


@app.command()
def lock(shot_id: str, field: str):
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
    typer.secho(f"{shot_id}: locked {field}", fg=typer.colors.GREEN)


@app.command()
def unlock(shot_id: str, field: str):
    """Interactive terminal only + confirmation; never exposed over MCP (§5)."""
    if not sys.stdin.isatty():
        _fail("unlock is interactive-only: run it yourself in a terminal (§5). "
              "AI agents: write a proposal to proposals/ instead.")
    if not typer.confirm(f"确认解锁 {shot_id}.{field}?"):
        raise typer.Exit(1)
    project = _project()

    def mutate(d):
        locked = d.get("locked") or {}
        if isinstance(locked, dict) and field in locked:
            del locked[field]
        d["locked"] = locked

    project.update_shot_raw(shot_id, mutate)
    append_event(project.root, ACTOR, "unlock", {"shot": shot_id, "field": field})
    typer.secho(f"{shot_id}: unlocked {field}", fg=typer.colors.YELLOW)


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
        typer.echo(f"QC: {errors} errors, {warns} warnings → {project.relpath(paths['md'])}"
                   if "md" in {k: k for k in paths} else f"QC: {errors} errors, {warns} warnings")
        typer.secho("qc ok" if report.ok else "qc found errors",
                    fg=typer.colors.GREEN if report.ok else typer.colors.RED)
    if not report.ok:
        raise typer.Exit(1)


@app.command()
def repair(auto: bool = typer.Option(False, "--auto")):
    """Execute auto-safe items from repair_plan.yaml (redo/degrade); the rest
    stay for humans (§9). Repair is just 'edit generation params and rebuild'."""
    from .build.graph import redo_shot
    from .core.yamlio import read_yaml

    project = _project()
    plan_path = project.reports_dir / "repair_plan.yaml"
    if not plan_path.exists():
        _fail("no repair_plan.yaml — run `manju qc` first")
    plan = read_yaml(plan_path) or {}
    issues = plan.get("issues", [])
    done, left = 0, 0
    for issue in issues:
        if auto and issue.get("auto_safe") and issue.get("shot") and \
                issue.get("action") in ("redo_new_seed", "degrade_fallback"):
            try:
                redo_shot(project, issue["shot"], actor=ACTOR)
                done += 1
            except Exception as exc:  # a failed repair goes back on the human pile
                typer.secho(f"⚠ {issue['shot']}: repair failed — {exc}", fg=typer.colors.YELLOW)
                left += 1
        else:
            left += 1
    typer.echo(f"repaired {done}, remaining for human review: {left}")


# ------------------------------------------------------------------ export


@app.command()
def export(
    jianying: bool = typer.Option(False, "--jianying"),
    srt: bool = typer.Option(False, "--srt"),
    otio: bool = typer.Option(False, "--otio"),
):
    """Export drafts/captions from the compiled timeline (§11)."""
    project = _project()
    timeline = project.load_timeline()
    if timeline is None:
        _fail("no timeline.json — run `manju build` first")
    if not (jianying or srt or otio):
        srt = otio = True
    outputs: dict[str, Path] = {}
    if srt:
        from .exporters.srt_ass import export_captions

        outputs.update(export_captions(project, timeline))
    if otio:
        from .exporters.otio import export_otio

        outputs["otio"] = export_otio(project, timeline)
    if jianying:
        from .exporters.jianying import export_jianying

        outputs["jianying"] = export_jianying(project, timeline)
    append_event(project.root, ACTOR, "export", {k: project.relpath(v) for k, v in outputs.items()})
    for k, v in outputs.items():
        typer.secho(f"{k}: {project.relpath(v)}", fg=typer.colors.GREEN)


# ------------------------------------------------------------------- board


@app.command()
def board():
    """Static HTML review board — the director's workbench (§1-⑦)."""
    from .board.board import generate_board

    project = _project()
    path = generate_board(project)
    typer.secho(f"board: {project.relpath(path)}", fg=typer.colors.GREEN)


# -------------------------------------------------------------- pack/unpack

PACK_EXCLUDE = (".manju/", ".git/")


@app.command()
def pack(out: Optional[Path] = typer.Option(None)):
    """Archive the project into a single .manjupkg (zip) for backup/migration."""
    project = _project()
    out = out or project.root.parent / (project.root.stem + ".manjupkg")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(project.root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(project.root).as_posix()
            if any(rel.startswith(prefix) for prefix in PACK_EXCLUDE):
                continue
            zf.write(path, rel)
    typer.secho(f"packed → {out}", fg=typer.colors.GREEN)


@app.command()
def unpack(archive: Path, dest: Optional[Path] = typer.Option(None)):
    """Restore a .manjupkg into a project directory."""
    if not archive.exists():
        _fail(f"not found: {archive}")
    dest = dest or archive.parent / (archive.stem + ".manju")
    if dest.exists():
        _fail(f"destination exists, refusing to overwrite: {dest}")
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)
    (dest / ".manju").mkdir(exist_ok=True)
    typer.secho(f"unpacked → {dest}", fg=typer.colors.GREEN)


# ------------------------------------------------------------------- misc


@app.command()
def events(n: int = typer.Option(20, "-n"), as_json: bool = typer.Option(False, "--json")):
    """Tail the collaboration log (who did what, when — §10)."""
    entries = tail_events(_project().root, n)
    if as_json:
        _emit(entries, True)
    else:
        for e in entries:
            typer.echo(f"{e.get('ts','?')}  [{e.get('actor','?')}]  {e.get('action','?')}  "
                       f"{json.dumps(e.get('detail', {}), ensure_ascii=False)}")


@app.command()
def doctor():
    """Environment health: ffmpeg, fonts, disk, project integrity (§14)."""
    ok = True
    for tool in ("ffmpeg", "ffprobe", "git"):
        found = shutil.which(tool)
        typer.echo(f"{'✓' if found else '✗'} {tool}: {found or 'NOT FOUND'}")
        ok = ok and (found is not None or tool == "git")
    try:
        from .media.card import find_font

        font = find_font()
        typer.echo(f"{'✓' if font else '⚠'} CJK font: {font or 'none found (cards will render boxes)'}")
    except ImportError:
        typer.echo("⚠ media module unavailable")
    try:
        project = Project.find(Path.cwd())
        free_gb = shutil.disk_usage(project.root).free / 1e9
        typer.echo(f"{'✓' if free_gb > 2 else '⚠'} disk free: {free_gb:.1f} GB")
        report = run_check(project)
        typer.echo(f"{'✓' if report.ok else '✗'} project check: "
                   f"{'ok' if report.ok else f'{len(report.errors)} errors'}")
        ok = ok and report.ok
    except ProjectError:
        typer.echo("• no project in cwd (environment checks only)")
    raise typer.Exit(0 if ok else 1)


@app.command()
def gc(hard: bool = typer.Option(False, "--hard")):
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
        if not sys.stdin.isatty():
            _fail("gc --hard is interactive-only")
        if typer.confirm("删除所有未选中的 take 媒体本体?(选中、imports、final 不受影响)"):
            for sid in project.shot_ids():
                shot = project.load_shot(sid)
                for take in project.takes(sid):
                    if take.name != shot.status.selected_take and take.media_path:
                        freed += take.media_path.stat().st_size
                        take.media_path.unlink()
    append_event(project.root, ACTOR, "gc", {"freed_bytes": freed, "hard": hard})
    typer.secho(f"freed {freed / 1e6:.1f} MB", fg=typer.colors.GREEN)


@app.command("rebuild-index")
def rebuild_index():
    """Recreate the disposable runtime dir from text + media (§3: SQLite may
    explode at any time). M0 keeps no SQLite yet — this recreates the layout
    and reports what was reconstructed."""
    project = _project()
    project.runtime_dir.mkdir(exist_ok=True)
    (project.runtime_dir / "logs").mkdir(exist_ok=True)
    statuses = {s.shot_id: s.state.value for s in __import__(
        "manju.build.stale", fromlist=["evaluate_all"]).evaluate_all(project)}
    typer.echo(f"runtime rebuilt; {len(statuses)} shots scanned: "
               + json.dumps(statuses, ensure_ascii=False))


@app.command("serve-mcp")
def serve_mcp():
    """MCP server (M2, §11) — thin wrapper over the same core. Not yet wired."""
    _fail("serve-mcp lands in M2 — Claude Code can already drive everything "
          "via files + this CLI (--json), which is the equivalent surface (§11)")


if __name__ == "__main__":
    app()
