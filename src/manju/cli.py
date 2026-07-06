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
):
    """Create a <name>.manju project directory (§3).

    With --preset, the kit pre-fills project.yaml / rules.yaml / packaging.yaml
    and the story scaffolds, then stops touching the project — everything it
    wrote is plain, hand-editable text (P3). No --preset ⇒ the generic scaffold.
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


# ----------------------------------------------------------------- presets


@app.command()
def presets(as_json: bool = typer.Option(False, "--json")):
    """List the preset kits available to `manju new --preset` (P3)."""
    from .presets import list_presets

    specs = list_presets()
    if as_json:
        _emit([s.to_public_dict() for s in specs], True)
        return
    name_w = max(len(s.name) for s in specs)
    title_w = max(len(s.title) for s in specs)
    typer.secho(f"{'NAME'.ljust(name_w)}  {'TITLE'.ljust(title_w)}  ASPECT  DESCRIPTION",
                fg=typer.colors.CYAN)
    for s in specs:
        typer.echo(f"{s.name.ljust(name_w)}  {s.title.ljust(title_w)}  "
                   f"{s.aspect().ljust(6)}  {s.description}")
    typer.secho("用法 / usage: manju new <名字> --preset <name>", fg=typer.colors.BRIGHT_BLACK)


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
def import_(files: list[Path], as_json: bool = typer.Option(False, "--json")):
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
    # §11: derived previews (thumbnail/waveform) into the disposable runtime
    # dir; the segment cache is the lazy proxy (§7 ①). Best-effort only.
    previews: dict[str, str] = {}
    try:
        from .media.preview import make_preview

        for rel in registered:
            preview = make_preview(project.resolve(rel), project.runtime_dir / "thumbs")
            if preview is not None:
                previews[rel] = project.relpath(preview)
    except ImportError:
        pass
    append_event(project.root, ACTOR, "import", {"files": registered, "previews": previews})
    if as_json:
        _emit({"imported": registered, "previews": previews}, True)
    else:
        for r in registered:
            typer.secho(f"imported {r}", fg=typer.colors.GREEN)
        for src_rel, thumb in previews.items():
            typer.echo(f"  preview: {thumb}")


# ------------------------------------------------------------------- build


@app.command()
def build(
    target: str = typer.Option("final", help="proxy | final | exports | qc"),
    gen: str = typer.Option("missing", help="missing | auto | off"),
    regen_stale: bool = typer.Option(False, "--regen-stale"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(False, "--force",
                               help="re-render even if the final content key matches (FIX-A)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """One-command build: fill gaps → timeline → render → QC → exports (§11)."""
    from .build.graph import run_build

    if target not in ("proxy", "final", "exports", "qc"):
        _fail(f"unknown target: {target}")
    result = run_build(_project(), target=target, gen=gen,
                       regen_stale=regen_stale, dry_run=dry_run, force=force, actor=ACTOR)
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
    as_json: bool = typer.Option(False, "--json"),
):
    """Force new takes for one shot (append-only; existing selection stands)."""
    from .build.graph import redo_shot

    takes = redo_shot(_project(), shot_id, candidates=candidates,
                      provider=provider, seed=seed, actor=ACTOR)
    if as_json:
        _emit({"shot": shot_id, "takes": takes}, True)
    else:
        typer.secho(f"{shot_id}: new takes {', '.join(takes)}", fg=typer.colors.GREEN)


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
def auto(prompt_text: str):
    """Autopilot v1 (§10): a thin shell over `claude -p`. Claude Code runs the
    Manju playbook (SKILL.md) end to end. No LLM SDK — strictly a subprocess."""
    import subprocess

    import manju

    if shutil.which("claude") is None:
        _fail("claude CLI not found — autopilot drives Claude Code (§10); "
              "install it or run the playbook manually")
    project = _project()

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

    append_event(project.root, ACTOR, "auto", {"prompt": prompt_text})
    env = dict(os.environ)
    env["MANJU_ACTOR"] = "ai"  # every action the driven agent takes is logged as ai
    proc = subprocess.run(["claude", "-p", composed], cwd=project.root, env=env)
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


@app.command()
def repair(auto: bool = typer.Option(False, "--auto"),
           as_json: bool = typer.Option(False, "--json")):
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
    if as_json:
        _emit({"repaired": done, "remaining": left}, True)
    else:
        typer.echo(f"repaired {done}, remaining for human review: {left}")


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
        _fail(str(exc))
    except ValidationError as exc:  # a malformed packaging.yaml, same envelope
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


# ------------------------------------------------------------------- voice


@app.command()
def voice(
    shot_id: str,
    provider: Optional[str] = typer.Option(None, help="tts manifest id (default: first configured)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Synthesize a NEW voice take for one shot (M3, append-only — the newest
    take wins on the next build). Use this to redo a stale voice (§4.3:
    builds flag stale voices but never redo them on their own)."""
    from .providers.tts import TtsUnavailable, get_tts_provider

    project = _project()
    shot = project.load_shot(shot_id)
    if not shot.dialogue.text:
        _fail(f"{shot_id} has no dialogue.text to voice")
    try:
        tts = get_tts_provider(provider)
        media = tts.synthesize(project, shot, project.load_bible())
    except TtsUnavailable as exc:
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
    """Archive the project into a single .manjupkg (zip) for backup/migration.

    FIX-E: the original project directory name rides in the zip comment, so
    the archive file can be renamed freely without losing the identity."""
    project = _project()
    out = out or project.root.parent / (project.root.stem + ".manjupkg")
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
            zf.write(path, rel)
    typer.secho(f"packed → {out}", fg=typer.colors.GREEN)


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
def doctor(as_json: bool = typer.Option(False, "--json")):
    """Environment health: ffmpeg, fonts, disk, project integrity (§14)."""
    # Build a structured checks list first, then render human or JSON from it —
    # exit code stays: env tools (git optional) + project check gate `ok`.
    checks: list[dict] = []
    ok = True

    def add(name: str, chk_ok: bool, detail: str, line: str) -> None:
        checks.append({"name": name, "ok": chk_ok, "detail": detail, "line": line})

    for tool in ("ffmpeg", "ffprobe", "git"):
        found = shutil.which(tool)
        add(tool, found is not None, found or "NOT FOUND",
            f"{'✓' if found else '✗'} {tool}: {found or 'NOT FOUND'}")
        ok = ok and (found is not None or tool == "git")  # git is optional
    try:
        from .media.card import find_font

        font = find_font()
        add("cjk_font", font is not None,
            str(font) if font else "none found (cards will render boxes)",
            f"{'✓' if font else '⚠'} CJK font: {font or 'none found (cards will render boxes)'}")
    except ImportError:
        add("cjk_font", False, "media module unavailable", "⚠ media module unavailable")

    # ---- toolbelt probes (§2.5 adapter wall: absence is a fact, not a failure)
    import importlib.util as _ilu

    for lib, purpose in (("pyJianYingDraft", "剪映草稿主路"),
                         ("pycapcut", "国际 CapCut 草稿"),
                         ("mcp_video", "QC 质量门/媒体分析")):
        present = _ilu.find_spec(lib) is not None
        add(lib, True, "installed" if present else f"not installed ({purpose})",
            f"{'✓' if present else '•'} {lib}: "
            f"{'installed' if present else f'not installed — {purpose} 走替代路径'}")
    for binary, purpose in (("capcut-cli", "剪映草稿副路 lint"),
                            ("tesseract", "must_show OCR 机检")):
        found = shutil.which(binary)
        add(binary, True, found or f"not installed ({purpose})",
            f"{'✓' if found else '•'} {binary}: {found or f'not installed — {purpose} 降级'}")
    try:
        from .media.html_card import find_chromium

        chromium = find_chromium()
        add("chromium", True,
            str(chromium) if chromium else "not found (html_render → drawtext 兜底)",
            f"{'✓' if chromium else '•'} chromium (html_render): "
            f"{chromium or 'not found — 文字卡走 drawtext 兜底'}")
    except ImportError:
        pass

    # ---- provider manifests (§8.6): a bad fill fails HERE, not at first spend
    try:
        from .providers.manifest import load_manifests
        from .providers.registry import manifest_errors

        manifests, load_errors = load_manifests()
        for pid, manifest in sorted(manifests.items()):
            problems = manifest.validate_for_generic()
            probe_ok = not problems
            detail = "ok" if probe_ok else "; ".join(problems)
            add(f"provider:{pid}", probe_ok, detail,
                f"{'✓' if probe_ok else '✗'} provider {pid} ({manifest.type}): {detail}")
            ok = ok and probe_ok
        for err in load_errors + [e for e in manifest_errors() if e not in load_errors]:
            add("provider_manifest", False, err, f"✗ provider manifest: {err}")
            ok = False
        if not manifests:
            add("providers", True, "no cloud provider manifests configured",
                "• no cloud provider manifests configured (~/.manju/providers, §8.6)")
    except Exception as exc:
        add("providers", False, str(exc), f"⚠ provider probe errored: {exc}")

    try:
        project = Project.find(Path.cwd())
        free_gb = shutil.disk_usage(project.root).free / 1e9
        add("disk_free", free_gb > 2, f"{free_gb:.1f} GB",
            f"{'✓' if free_gb > 2 else '⚠'} disk free: {free_gb:.1f} GB")
        report = run_check(project)
        add("project_check", report.ok,
            "ok" if report.ok else f"{len(report.errors)} errors",
            f"{'✓' if report.ok else '✗'} project check: "
            f"{'ok' if report.ok else f'{len(report.errors)} errors'}")
        ok = ok and report.ok
    except ProjectError:
        add("project", True, "no project in cwd (environment checks only)",
            "• no project in cwd (environment checks only)")
    if as_json:
        _emit({"checks": [{"name": c["name"], "ok": c["ok"], "detail": c["detail"]}
                          for c in checks], "ok": ok}, True)
    else:
        for c in checks:
            typer.echo(c["line"])
    raise typer.Exit(0 if ok else 1)


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


@app.command("serve-mcp")
def serve_mcp():
    """MCP server over stdio (§11) — a thin wrapper over the same core. Dangerous
    commands (unlock, gc --hard) are never on this surface; Claude Code drives
    everything else here or via files + this CLI, two equivalent paths (§11)."""
    project = _project()
    from .mcp.server import main as mcp_main

    raise typer.Exit(mcp_main(["--project", str(project.root)]))


if __name__ == "__main__":
    app()
