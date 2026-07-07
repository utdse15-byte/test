"""`manju doctor` engine (§14 environment probes) — health checks as data.

The CLI is a client of this engine core (§2): this module builds the structured
checks list the CLI used to assemble inline, so the exact same probes can be
rendered as human ``line`` strings by the CLI or served as JSON by any future
HTTP surface. Probed here: required binaries (ffmpeg/ffprobe; git optional),
CJK fonts, the optional toolbelt (§2.5 adapter wall: absence is a fact, not a
failure), provider manifests (§8.6: manifest validation happens HERE, not at
first spend, and covers every configured adapter — cloud, comfyui, local_cmd),
and — when a project is given — disk space, an informational build-lock/cache
read, and project integrity.

Pure function: no typer, no printing, no exits. Media and provider modules are
imported lazily inside :func:`run_doctor` so their absence degrades exactly as
it did in the CLI.
"""

from __future__ import annotations

import shutil
from typing import Any

from ..core.check import run_check
from ..core.container import Project


def run_doctor(project: Project | None = None) -> dict[str, Any]:
    """Environment health: ffmpeg, fonts, disk, project integrity (§14).

    Returns ``{"checks": [...], "ok": bool}`` where every check is
    ``{"name": str, "ok": bool, "detail": str, "line": str}`` and ``line``
    is the human rendering (✓/✗/•/⚠ glyphs). ``ok`` aggregates the gating
    checks only: env tools (ffmpeg/ffprobe required, git optional) +
    provider manifests (§8.6) + the project check; font/toolbelt/disk/
    build-lock/cache entries are informational. When ``project`` is None the
    project-specific checks are skipped and a "no project in cwd (environment
    checks only)" entry is added instead — exactly the CLI's no-project
    behaviour.
    """
    checks: list[dict[str, Any]] = []
    ok = True

    def add(name: str, chk_ok: bool, detail: str, line: str) -> None:
        checks.append({"name": name, "ok": chk_ok, "detail": detail, "line": line})

    for tool in ("ffmpeg", "ffprobe", "git"):
        found = shutil.which(tool)
        add(tool, found is not None, found or "NOT FOUND",
            f"{'✓' if found else '✗'} {tool}: {found or 'NOT FOUND'}")
        ok = ok and (found is not None or tool == "git")  # git is optional
    try:
        from ..media.card import find_font

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
        from ..media.html_card import find_chromium

        chromium = find_chromium()
        add("chromium", True,
            str(chromium) if chromium else "not found (html_render → drawtext 兜底)",
            f"{'✓' if chromium else '•'} chromium (html_render): "
            f"{chromium or 'not found — 文字卡走 drawtext 兜底'}")
    except ImportError:
        pass

    # ---- provider manifests (§8.6): a bad fill fails HERE, not at first spend
    try:
        from ..providers.manifest import load_manifests
        from ..providers.registry import manifest_errors

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

    if project is not None:
        free_gb = shutil.disk_usage(project.root).free / 1e9
        add("disk_free", free_gb > 2, f"{free_gb:.1f} GB",
            f"{'✓' if free_gb > 2 else '⚠'} disk free: {free_gb:.1f} GB")
        # an active (or crashed) build lock is a fact worth stating here —
        # doctor is where people look when "nothing works"; informational,
        # never gates ok. Read as a plain JSON file (§3: no lock machinery
        # needed to REPORT one).
        lock_path = project.runtime_dir / "build.lock"
        if lock_path.exists():
            try:
                import json as _json

                holder = _json.loads(lock_path.read_text(encoding="utf-8"))
                detail = (f"pid {holder.get('pid', '?')} actor={holder.get('actor', '?')} "
                          f"since {holder.get('started', '?')}")
            except Exception:
                detail = "lock file unreadable"
            add("build_lock", True, detail,
                f"⚠ build lock held: {detail} — 若确认无进程在跑,删除 "
                f"{project.relpath(lock_path)}")
        # disposable caches: how much `manju gc` would reclaim
        cache_bytes = 0
        for d in (project.segments_dir, project.proxy_dir,
                  project.runtime_dir / "webpreview"):
            if d.exists():
                cache_bytes += sum(f.stat().st_size for f in d.glob("*") if f.is_file())
        if cache_bytes:
            add("gc_reclaimable", True, f"{cache_bytes / 1e6:.1f} MB",
                f"• reclaimable caches: {cache_bytes / 1e6:.1f} MB "
                "(segments/proxy/webpreview — manju gc)")
        report = run_check(project)
        add("project_check", report.ok,
            "ok" if report.ok else f"{len(report.errors)} errors",
            f"{'✓' if report.ok else '✗'} project check: "
            f"{'ok' if report.ok else f'{len(report.errors)} errors'}")
        ok = ok and report.ok
    else:
        add("project", True, "no project in cwd (environment checks only)",
            "• no project in cwd (environment checks only)")

    return {"checks": checks, "ok": ok}
