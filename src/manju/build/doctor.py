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

import os
import shutil
import sys
from typing import Any

from ..core.check import run_check
from ..core.container import Project

# W2 (§4.5): os.name is process-constant; a module flag keeps the Windows-row
# dispatch patchable in tests without touching the global ``os`` module.
_IS_WINDOWS = os.name == "nt"


# ------------------------------------------------------------ Windows probes
# Tiny, individually-guarded, individually-stubbable. Every probe returns
# ``None`` for UNKNOWN — an unknown is REPORTED as unknown, never upgraded to a
# ✓ (§ UNKNOWN 不得猜成 PASS). All resulting rows are INFORMATIONAL: doctor's
# gating set (env tools + provider manifests + project check) is unchanged.


def _win_long_paths_enabled() -> bool | None:
    """HKLM FileSystem\\LongPathsEnabled — READ-ONLY (the plan forbids doctor
    from ever writing the registry). None = could not read (report unknown)."""
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "LongPathsEnabled")
        return bool(value)
    except Exception:
        return None


def _win_volume_fs(path: Any) -> str | None:
    """Filesystem name of the volume holding ``path`` (GetVolumeInformationW),
    or None when undecidable."""
    try:
        import ctypes

        root = os.path.splitdrive(os.path.abspath(str(path)))[0] + "\\"
        buf = ctypes.create_unicode_buffer(64)
        ok = ctypes.WinDLL("kernel32").GetVolumeInformationW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(root), None, 0, None, None, None, buf, 64
        )
        return buf.value or None if ok else None
    except Exception:
        return None


def _win_drive_remote(path: Any) -> bool | None:
    """True when ``path`` sits on a network drive (GetDriveTypeW == 4), None
    when undecidable."""
    try:
        import ctypes

        root = os.path.splitdrive(os.path.abspath(str(path)))[0] + "\\"
        kind = ctypes.WinDLL("kernel32").GetDriveTypeW(ctypes.c_wchar_p(root))  # type: ignore[attr-defined]
        return int(kind) == 4  # DRIVE_REMOTE
    except Exception:
        return None


def run_doctor(project: Project | None = None, *, windows: bool | None = None) -> dict[str, Any]:
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

    # W2 (§4.5) output hygiene at the ONE choke point: every row passes the
    # narrow supportbundle composition — secrets/signed URLs mask, PRIVATE-
    # rooted paths (C:\Users\<name>, /home/<name>) collapse to basenames,
    # while diagnostic system paths (/usr/bin/ffmpeg) stay readable. A doctor
    # paste can no longer leak a username. Degrades to identity if the
    # redaction seam itself cannot import (doctor must never crash on it).
    try:
        from ..core.supportbundle import redact_private_text as _redact
    except Exception:  # pragma: no cover — defensive only
        def _redact(text: str) -> str:
            return text

    def add(name: str, chk_ok: bool, detail: str, line: str) -> None:
        checks.append({"name": name, "ok": chk_ok,
                       "detail": _redact(detail), "line": _redact(line)})

    # ---- the interpreter itself (§4.5: doctor states the Python running it)
    import platform as _platform

    add("python", True,
        f"{_platform.python_version()} ({_platform.machine()}) — {sys.executable}",
        f"✓ python: {_platform.python_version()} ({_platform.machine()}) — {sys.executable}")

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

    # ---- W5.4: hardware-encoder FACTS (informational, never gates). LISTED
    # is not VERIFIED — a listed encoder still fails without its driver (the
    # authoring container itself lists nvenc/qsv with no GPU present); and
    # eligibility never auto-enables anything (the encode path stays libx264).
    # Owner: media/ffmpeg (build/ may never consult the toolchain-manifest
    # module — its boundary pin; media is doctor's existing import surface).
    if shutil.which("ffmpeg"):
        try:
            from ..media.ffmpeg import encoder_inventory, hw_encode_eligibility

            verdict = hw_encode_eligibility(encoder_inventory())
            if verdict["eligible"]:
                names = ", ".join(verdict["candidates"])
                add("hw_encoders", True, f"listed: {names} (LISTED ≠ VERIFIED)",
                    f"• hw encoders: {names} — ffmpeg 已编入(LISTED),但未经真实"
                    "编码验证,且编码路径仍是 libx264(永不自动启用)")
            else:
                add("hw_encoders", True, "none listed (libx264 software path)",
                    "• hw encoders: 无 — 此 ffmpeg 未编入 h264 硬件编码器,"
                    "软件路径 libx264")
        except Exception as exc:
            add("hw_encoders", True, str(exc),
                f"⚠ hw-encoder 探测异常(信息行,不影响退出码): {exc}")

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

    # ---- W2 (§4.5): Windows environment rows. Auto on a Windows host; forced
    # by `manju doctor --windows` (which on a non-Windows host must say
    # PLAINLY that the probes were skipped — never fabricate Windows facts).
    if _IS_WINDOWS:
        _add_windows_rows(add, project)
    elif windows is True:
        add("windows", True,
            "非 Windows 主机 — Windows 探测已跳过 (not Windows; probes skipped)",
            "• windows: 非 Windows 主机 — Windows 探测已跳过")

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
        # Continuity wave: backup age — doctor is the health surface, and for
        # personal data the one health fact that costs real tears is "when
        # did I last make a full backup". Reads `.manju/last_pack.json`
        # (written by `manju pack`; in PACK_EXCLUDE territory ON PURPOSE —
        # pack stays read-only on the tree it archives, so the W5 two-packs-
        # byte-identical pins hold). `.manju` is disposable, so a wiped
        # marker degrades toward "建议备份" — never toward false confidence.
        # Advisory ONLY (✓/⚠/•), never gates ok.
        try:
            import json as _json_bak

            from ..core.events import humanize_age

            marker = project.runtime_dir / "last_pack.json"
            last_pack = (_json_bak.loads(marker.read_text(encoding="utf-8"))
                         if marker.exists() else None)
            if last_pack is not None:
                age = humanize_age(str(last_pack.get("ts", ""))) or "时间未知"
                pkg = last_pack.get("name", "")
                stale = "天前" in age and int(age.split(" ")[0]) > 14
                add("backup", True, f"last pack {last_pack.get('ts', '?')}",
                    f"{'⚠' if stale else '✓'} backup: 上次整包备份 {age}"
                    + (f"({pkg})" if pkg else "")
                    + (" — 超过两周,建议 manju pack" if stale else ""))
            else:
                add("backup", True, "no pack marker",
                    "• backup: 没有整包备份记录 — manju pack "
                    "可把整个项目打成一个 .manjupkg")
        except Exception as exc:  # the row must never take doctor down
            add("backup", True, str(exc), f"• backup: 无法判读({exc})")
        report = run_check(project)
        add("project_check", report.ok,
            "ok" if report.ok else f"{len(report.errors)} errors",
            f"{'✓' if report.ok else '✗'} project check: "
            f"{'ok' if report.ok else f'{len(report.errors)} errors'}")
        ok = ok and report.ok

        # ---- W4: a declared color.input_transform needs zscale (libzimg) in
        # THIS ffmpeg — some Windows builds ship without it, and the honest
        # place to find out is doctor, not the first failed render. Probed
        # ONLY when a project actually declares a transform (zero cost for
        # everyone else); advisory ⚠, never gates ok (render errors loudly).
        try:
            import subprocess

            _color = getattr(project.load_config(), "color", None)
            _transform = getattr(_color, "input_transform", None)
            if _transform is not None and shutil.which("ffmpeg"):
                probe = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-filters"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                )
                has_zscale = " zscale " in probe.stdout
                add("color_transform", True,
                    f"{_transform}: zscale {'available' if has_zscale else 'MISSING'}",
                    f"{'✓' if has_zscale else '⚠'} color.input_transform "
                    f"({_transform}): "
                    + ("zscale 滤镜可用" if has_zscale else
                       "此 ffmpeg 缺 zscale(libzimg)— 渲染会失败;请换带 zimg 的构建"
                       "(gyan.dev full/essentials 均含)"))
        except Exception as exc:
            add("color_transform", True, str(exc),
                f"⚠ color 探测异常(建议诊断,不影响退出码): {exc}")

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


# ----------------------------------------------------------- Windows rows


def _sync_dir_marker(root_str: str, env: "dict | os._Environ") -> str | None:
    """UX audit F33: which sync client (if any) holds this project — the
    OneDrive-only, case-sensitive check missed Dropbox and the China-common
    clients the owner is likelier to use. Pure; substring match is deliberate
    (an informational ⚠ row that never gates; a rare false positive beats a
    silent sharing-violation mystery). Env detection covers the OneDrive vars
    that point at a differently-named sync root."""
    onedrive = env.get("OneDrive") or env.get("OneDriveConsumer")
    if onedrive and root_str.startswith(str(onedrive)):
        return "OneDrive"
    folded = root_str.casefold()
    for marker in ("OneDrive", "Dropbox", "坚果云", "Nutstore", "百度网盘"):
        if marker.casefold() in folded:
            return marker
    return None


def _add_windows_rows(add, project: Project | None) -> None:
    """The §4.5 checklist as INFORMATIONAL rows (never gates ``ok``): Windows
    version, long-path policy, project volume/drive advisories, OneDrive
    advisory, config-dir writability, Edge/Chrome, install mode + rollback.
    Each probe degrades to an honest unknown — never a guessed ✓."""
    import platform as _platform

    add("windows_version", True, _platform.platform(),
        f"✓ windows: {_platform.platform()}")

    lp = _win_long_paths_enabled()
    if lp is True:
        add("long_paths", True, "LongPathsEnabled=1", "✓ long paths: 已启用(LongPathsEnabled=1)")
    elif lp is False:
        add("long_paths", True,
            "LongPathsEnabled=0 — 深层中文/CJK 路径可能超过 260 字符限制而失败;"
            "管理员可启用 long path policy(doctor 只读,绝不改注册表)",
            "⚠ long paths: 未启用(LongPathsEnabled=0)— 深层路径可能失败")
    else:
        add("long_paths", True, "unknown(未知)— 无法读取 long path policy",
            "• long paths: 未知 — 无法读取策略(不猜测)")

    if project is not None:
        fs = _win_volume_fs(project.root)
        if fs == "NTFS":
            add("filesystem", True, "NTFS", "✓ filesystem: NTFS")
        elif fs:
            add("filesystem", True,
                f"{fs} — 非 NTFS:锁/流/权限语义受限,建议把项目放在 NTFS 卷",
                f"⚠ filesystem: {fs}(非 NTFS)— 建议 NTFS 卷")
        else:
            add("filesystem", True, "unknown(未知)", "• filesystem: 未知(不猜测)")

        remote = _win_drive_remote(project.root)
        if remote is True:
            add("project_drive", True,
                "网络盘 — 文件锁与原子替换在网络盘上不可靠,建议本地盘",
                "⚠ project drive: 网络盘 — 建议把项目移到本地盘")
        elif remote is False:
            add("project_drive", True, "本地盘", "✓ project drive: 本地盘")
        else:
            add("project_drive", True, "unknown(未知)", "• project drive: 未知(不猜测)")

        marker = _sync_dir_marker(str(project.root), os.environ)
        if marker:
            add("onedrive", True,
                f"项目在 {marker} 同步目录内 — 同步器持有文件句柄会造成 sharing "
                "violation 与占位文件问题;建议排除同步或移出",
                f"⚠ 同步目录: 项目在 {marker} 内 — 建议排除同步或移出")

    # config dir writability (~/.manju — providers/routing/recents/skills/library)
    from pathlib import Path as _Path

    cfg = _Path.home() / ".manju"
    try:
        cfg.mkdir(parents=True, exist_ok=True)
        probe = cfg / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("config_writable", True, "~/.manju 可写", "✓ config dir: ~/.manju 可写")
    except OSError as exc:
        add("config_writable", True, f"~/.manju 不可写 — {exc}",
            "⚠ config dir: ~/.manju 不可写 — providers/routing/recents 将不可用")

    # Edge/Chrome (the board's §5.6 app-mode host; informational)
    try:
        from ..media.html_card import find_chromium, find_edge

        edge = find_edge()
        chromium = find_chromium()
        browser = edge or chromium
        add("edge_chrome", True,
            str(browser) if browser else "未找到 Edge/Chrome(board --app 将退回默认浏览器)",
            f"{'✓' if browser else '•'} browser (board --app): "
            f"{browser or '未找到 Edge/Chrome — 退回默认浏览器'}")
    except ImportError:
        pass

    # install mode + current/rollback version (W2 installer layout, §4.1/§4.3)
    import manju as _manju

    mode = "pip (site-packages)" if "site-packages" in str(getattr(_manju, "__file__", "")) \
        else "source checkout (editable)"
    facets = [f"version {getattr(_manju, '__version__', '?')}", mode]
    localapp = os.environ.get("LOCALAPPDATA")
    if localapp:
        appdir = _Path(localapp) / "Manju" / "App"
        try:
            current = (appdir / "current.txt").read_text(encoding="utf-8").strip()
            facets.append(f"launcher current: {current}")
            previous = (appdir / "previous.txt").read_text(encoding="utf-8").strip()
            facets.append(f"rollback target: {previous}(update-manju.ps1 -Rollback)")
        except OSError:
            pass
    add("app_install", True, ";".join(facets), f"• install: {';'.join(facets)}")


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
