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

    # Pillow powers the §10 WP6 exact color-stats layer (qc/colorstats.py: PIL
    # integer channel histograms; numpy is absent by design). OPTIONAL — its
    # absence degrades behind the colorstats adapter wall (ColorStatsUnavailable),
    # so this row is INFORMATIONAL (✓/•) and NEVER gates ok, exactly like the
    # toolbelt/optional-tool rows above (add(..., True, ...)). find_spec returns
    # None both when Pillow is genuinely absent and under sys.modules poisoning;
    # the guard also absorbs the ValueError some Pythons raise for a poisoned entry.
    try:
        pil_present = _ilu.find_spec("PIL") is not None
    except (ImportError, ValueError):
        pil_present = False
    add("Pillow", True,
        "installed" if pil_present
        else 'not installed (qc colorstats — pip install "manju[colorstats]")',
        f"{'✓' if pil_present else '•'} Pillow (PIL): "
        + ("installed" if pil_present
           else '未安装 — QC 颜色统计走 pip install "manju[colorstats]" (§10 WP6)'))

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

        # ---- locale overlays (WP4) + interchange exits: ADVISORY diagnostics.
        # These rows surface health as ✓/•/⚠ exactly like the toolbelt/disk
        # probes above and NEVER touch `ok` — doctor's exit-code policy is
        # unchanged (gating stays env-tools + provider manifests + project
        # check). They DELEGATE, never re-validate: locale meta goes through
        # core.locale.load_locale_meta (its structured message is surfaced
        # VERBATIM — the S4/bridge "consult, don't copy" precedent), and an
        # interchange exit gets a stdlib well-formedness + presence probe only
        # (exporters.conform owns the real semantics/drift — the row points
        # there). A probe bug degrades to one ⚠ row, never a traceback (the
        # provider-probe precedent above).
        try:
            _add_locale_rows(project, add)
            _add_interchange_rows(project, add)
        except Exception as exc:
            add("advisory_probe", True, str(exc),
                f"⚠ locale/interchange 探测异常(建议诊断,不影响退出码): {exc}")
    else:
        add("project", True, "no project in cwd (environment checks only)",
            "• no project in cwd (environment checks only)")

    return {"checks": checks, "ok": ok}


# --------------------------------------------------------------------- advisory


def _add_locale_rows(project: Project, add) -> None:
    """One ADVISORY ``locale:<lang>`` row per locale overlay dir (WP4).

    Facets, all sourced from ``core.locale`` (never re-implemented here): whether
    ``lines.yaml`` parses (``load_lines`` — it raises on malformed YAML), whether
    ``meta.yaml`` validates (``load_locale_meta`` — its EXACT structured rejection
    is surfaced VERBATIM on failure), and the declared ``direction`` echoed only
    when the human set one. A parse/validation failure makes the row ⚠ with
    ``ok=False`` — but this row is informational and never gates ``doctor``.
    """
    from ..core.locale import list_locales, load_lines, load_locale_meta, locale_dir

    for lang in list_locales(project):
        facets: list[str] = []
        glyph, row_ok = "✓", True
        # lines.yaml — CONSULT load_lines (it propagates a YAML parse error).
        try:
            facets.append(f"lines.yaml 解析正常({len(load_lines(project, lang))} 行)")
        except Exception as exc:
            glyph, row_ok = "⚠", False
            facets.append(f"lines.yaml 无法解析 — {exc}")
        # meta.yaml — CONSULT load_locale_meta; surface its message VERBATIM.
        try:
            meta = load_locale_meta(project, lang)
            direction = meta.get("direction")
            if direction:
                facets.append(f"meta.yaml 有效(direction: {direction})")
            elif (locale_dir(project, lang) / "meta.yaml").exists():
                facets.append("meta.yaml 有效(未声明 direction)")
            else:
                facets.append("未声明 meta.yaml(方向未定)")
        except Exception as exc:
            glyph, row_ok = "⚠", False
            facets.append(str(exc))  # load_locale_meta's structured message, verbatim
        detail = ";".join(facets)
        add(f"locale:{lang}", row_ok, detail, f"{glyph} locale {lang}: {detail}")


# Each interchange exit: its canonical artifact path (relative to the project)
# and the ONE stdlib well-formedness probe kind. Semantics/drift are NOT checked
# — that is exporters.conform's job, and the row says so.
_INTERCHANGE_EXITS = (
    ("otio", "OTIO 交换格式", "exports/otio/{name}.otio", "json", "manju export --otio"),
    ("edl", "EDL 剪辑单", "exports/edl/{name}.edl", "edl", "manju export --edl"),
    ("fcpxml", "FCPXML", "exports/fcpxml/{name}.fcpxml", "xml", "manju export --fcpxml"),
    ("ttml", "TTML 字幕", "captions/captions.ttml", "xml", "manju export --ttml"),
    ("vtt", "WebVTT 字幕", "captions/captions.vtt", "vtt", "manju export --srt"),
)


def _interchange_problem(path, kind: str) -> str | None:
    """``None`` when the file is well-formed for its ``kind``, else a short
    Chinese reason. STDLIB checks only (json/ElementTree) — never a semantic
    re-validation: XML uses ``ET.fromstring`` on BYTES so the ``<?xml
    encoding?>`` declaration the fcpxml/ttml writers emit is honoured (a decoded
    ``str`` with an encoding decl raises in ElementTree)."""
    if kind == "json":
        import json as _json

        doc = _json.loads(path.read_text(encoding="utf-8"))
        if not (isinstance(doc, dict) and doc.get("OTIO_SCHEMA")):
            return "缺少 OTIO_SCHEMA 顶层标记"
        return None
    if kind == "xml":
        import xml.etree.ElementTree as _ET

        _ET.fromstring(path.read_bytes())  # bytes: honour the encoding declaration
        return None
    first = path.read_text(encoding="utf-8").splitlines()[:1]
    if kind == "edl":
        return None if (first and first[0].startswith("TITLE:")) else "首行缺少 TITLE: 头"
    # vtt
    return None if (first and first[0].startswith("WEBVTT")) else "首行缺少 WEBVTT 签名"


def _add_interchange_rows(project: Project, add) -> None:
    """One ADVISORY ``export:<fmt>`` row per interchange exit (§14 fallback
    exits). Absent ⇒ ``•`` (absence is a fact, not a failure — the toolbelt
    precedent); present + well-formed ⇒ ``✓``; present + malformed/unreadable ⇒
    ``⚠`` with ``ok=False``. None of these gate ``doctor``; the row delegates
    semantics to ``manju.exporters.conform``."""
    try:
        name = project.load_config().name
    except Exception:
        name = project.root.name
    for fmt, label, tmpl, kind, made_by in _INTERCHANGE_EXITS:
        path = project.root / tmpl.format(name=name)
        if not path.exists():
            add(f"export:{fmt}", True, "未导出(缺席不是错误)",
                f"• {label}: 未导出 — 缺席不是错误({made_by})")
            continue
        rel = project.relpath(path)
        try:
            problem = _interchange_problem(path, kind)
        except Exception as exc:
            problem = " ".join(str(exc).split())
        if problem is None:
            add(f"export:{fmt}", True, "存在且良构(语义/丢帧以 conform 为准)",
                f"✓ {label}: {rel} 存在且良构(仅探测格式;语义/丢帧以 conform 为准)")
        else:
            add(f"export:{fmt}", False, f"良构探测失败:{problem}",
                f"⚠ {label}: {rel} 存在但格式探测失败 — {problem}(语义以 conform 为准)")
