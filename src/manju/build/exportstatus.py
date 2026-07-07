"""Export center — the deliverable status engine (round-U, goal item 4).

One honest table of every finished-output a project ships, each row carrying a
FRESHNESS verdict drawn from the exact vocabulary REPORTS/ROUND-U-REFERENCES §3
settled on (borrowing GitHub Actions' ``stale``/``action_required`` and Adobe
Media Encoder's "Done Warning"):

    上新   / up_to_date   — the deliverable's stored key/mtime matches what the
                            current specs would produce (Nx "outputs match cache")
    待更新 / stale        — upstream moved; this is old, regenerate to catch up
    缺失   / missing      — never produced
    有问题 / problematic  — the file exists but is broken: zero bytes, or its own
                            key sidecar is absent (a crashed render), or its draft
                            JSON references media that is gone (lint)
    待人工确认 / needs_manual — Manju cannot itself decide: a desktop draft it
                            cannot prove opens in JianYing/CapCut, or a case where
                            the current key could not be recomputed
    已人工确认 / verified — a human marked the draft verified AND the artifact's
                            content hash still matches the verified one

Nine deliverables, EXACTLY: final, proxy, SRT, ASS, OTIO, 剪映草稿 (JianYing
draft), CapCut 草稿, cover, teaser.

This is pure vocabulary/presentation over machinery that already exists — it
reuses the SAME content keys the render skip writes (media.render.final_content_key
+ the ``.key.json`` sidecars), the SAME staleness recompile ``manju explain`` /
``media.packaging._stale_final_warning`` run, the SAME caption compile the
exporter runs, and the SAME cover/teaser cache-key formula ``manju package``
writes. It never invents a second staleness path.

The two desktop draft kinds are ALWAYS at best 待人工确认 until a human marks
them verified with :func:`mark_verified`: Manju has no desktop app to open a
draft in, and that honesty is the point. Verification is truth-is-text — an
append-only ``reports/verifications.jsonl`` line ``{ts, kind, path_hash, actor,
note}`` — and a draft shows 已人工确认 only while the artifact's current hash
still equals the verified one (regenerate → the hash moves → back to 待人工确认).

CLI (:func:`manju.cli.exports`) and GUI (``/exports``) both render exactly what
:func:`deliverables` returns, so the two can never disagree.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ..core.container import Project
from ..core.hashing import hash_file

# ------------------------------------------------------------- freshness axis


class Freshness(str, Enum):
    UP_TO_DATE = "up_to_date"      # 上新
    STALE = "stale"               # 待更新
    MISSING = "missing"           # 缺失
    PROBLEMATIC = "problematic"   # 有问题
    NEEDS_MANUAL = "needs_manual"  # 待人工确认
    VERIFIED = "verified"         # 已人工确认 (drafts only)


FRESHNESS_ZH: dict[Freshness, str] = {
    Freshness.UP_TO_DATE: "上新",
    Freshness.STALE: "待更新",
    Freshness.MISSING: "缺失",
    Freshness.PROBLEMATIC: "有问题",
    Freshness.NEEDS_MANUAL: "待人工确认",
    Freshness.VERIFIED: "已人工确认",
}

# The two desktop-editor draft kinds Manju cannot itself verify (§14).
DRAFT_KINDS = ("jianying", "capcut")

VERIFICATIONS_FILE = "verifications.jsonl"  # under reports/


@dataclass
class DeliverableRow:
    kind: str                       # final|proxy|srt|ass|otio|jianying|capcut|cover|teaser
    label: str                      # 中文 label (生成来源 glossary vocabulary)
    path: str | None                # project-relative path, or None when absent
    freshness: Freshness
    basis: str                      # the honest one-line evidence for the verdict
    openable: bool = False          # does a file exist that a human can open?
    open_hint: str | None = None    # 中文 hint on how to open it
    version: str | None = None      # v1/v2… (final numbering) where applicable
    verifiable: bool = False        # a draft that can be 标记已人工确认
    verified_by: str | None = None
    verified_at: str | None = None
    verified_note: str | None = None

    @property
    def freshness_zh(self) -> str:
        return FRESHNESS_ZH[self.freshness]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "path": self.path,
            "freshness": self.freshness.value,
            "freshness_zh": self.freshness_zh,
            "basis": self.basis,
            "openable": self.openable,
            "open_hint": self.open_hint,
            "version": self.version,
            "verifiable": self.verifiable,
            "verified_by": self.verified_by,
            "verified_at": self.verified_at,
            "verified_note": self.verified_note,
        }


# ----------------------------------------------------------- shared context


@dataclass
class _Ctx:
    """Everything the rows need, gathered ONCE and best-effort — a broken
    project must degrade to honest verdicts, never a traceback."""

    project: Project
    name: str
    config: Any
    rules: Any
    packaging: Any
    newest_final: Path | None
    timeline: Any                     # the effective (recompiled) timeline, or None
    timeline_note: str                # why timeline/keys are unavailable, if so
    final_key: str | None             # current recomputed final content key
    proxy_key: str | None             # current recomputed proxy content key
    _final_hash: str | None = field(default=None)

    def final_hash(self) -> str | None:
        """Content hash of the newest final (cached — cover/teaser both need it)."""
        if self.newest_final is None:
            return None
        if self._final_hash is None:
            try:
                self._final_hash = hash_file(self.newest_final)
            except OSError:
                return None
        return self._final_hash


def _gather(project: Project) -> _Ctx:
    config = _safe(lambda: project.load_config())
    name = config.name if config is not None else project.root.name
    rules = _safe(lambda: project.load_rules())
    packaging = _safe(lambda: project.load_packaging())
    newest_final = _safe(lambda: project.newest_final_path())

    timeline = None
    timeline_note = ""
    final_key = proxy_key = None
    try:
        # The effective timeline is what a build would render: the recompiled
        # one (auto mode) or the human timeline.json (manual mode) — exactly the
        # stance `manju explain` and media.packaging._stale_final_warning take.
        if rules is not None and getattr(rules, "mode", None) == "manual":
            timeline = project.load_timeline()
            if timeline is None:
                timeline_note = "manual 模式但无 timeline.json"
        else:
            from ..media.probe import probe_duration_ms
            from ..timeline.compiler import compile_timeline, gather_compile_input

            timeline = compile_timeline(gather_compile_input(project, probe_duration_ms))
    except Exception as exc:  # empty project / uncompilable specs → degrade
        timeline = _safe(lambda: project.load_timeline())
        timeline_note = "时间线暂不可编译:" + " ".join(str(exc).split())[:120]

    if timeline is not None:
        try:
            from ..media.render import final_content_key

            ass = project.captions_dir / "captions.ass"
            ass_arg = ass if ass.exists() else None
            final_key = final_content_key(project, timeline, ass_file=ass_arg, target="final")
            proxy_key = final_content_key(project, timeline, ass_file=ass_arg, target="proxy")
        except Exception as exc:
            timeline_note = timeline_note or ("内容键重算失败:" + " ".join(str(exc).split())[:120])

    return _Ctx(project=project, name=name, config=config, rules=rules,
                packaging=packaging, newest_final=newest_final, timeline=timeline,
                timeline_note=timeline_note, final_key=final_key, proxy_key=proxy_key)


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


# ------------------------------------------------------------------ helpers


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _version_of(final_path: Path) -> str | None:
    m = re.match(r"final_v(\d+)$", final_path.stem)
    return f"v{m.group(1)}" if m else None


def _read_final_key_sidecar(final_path: Path) -> str | None:
    from ..media.render import _read_key_sidecar

    return _read_key_sidecar(final_path)


def _read_pkg_key(media_path: Path) -> str | None:
    from ..media.packaging import _read_key

    return _read_key(media_path)


# ---------------------------------------------------------- verification I/O


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _verifications_path(project: Project) -> Path:
    return project.reports_dir / VERIFICATIONS_FILE


def _latest_verification(project: Project, kind: str) -> dict[str, Any] | None:
    """The newest ``reports/verifications.jsonl`` record for ``kind`` (or None).
    Torn/invalid lines are skipped, mirroring the events-log discipline (§3)."""
    path = _verifications_path(project)
    if not path.exists():
        return None
    latest: dict[str, Any] | None = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("kind") == kind:
                latest = rec
    except OSError:
        return None
    return latest


def _draft_path(project: Project, kind: str, name: str) -> Path | None:
    """On-disk path of a draft kind's artifact.

    - jianying: the deterministic SKELETON ``exports/jianying/<name>/
      draft_content.json`` (the one ``manju export --jianying`` always produces
      and ``jianying.lint_draft`` understands);
    - capcut: the native ``exports/capcut/<name>_native/draft_content.json``
      (pycapcut, the only CapCut path)."""
    if kind == "jianying":
        return project.exports_dir / "jianying" / name / "draft_content.json"
    if kind == "capcut":
        return project.exports_dir / "capcut" / f"{name}_native" / "draft_content.json"
    return None


class ExportStatusError(RuntimeError):
    pass


def mark_verified(project: Project, kind: str, actor: str, note: str = "") -> dict[str, Any]:
    """Append a human verification for a desktop draft (truth-is-text, §3).

    A draft shows 已人工确认 only while its content hash still equals the one
    recorded here; regenerating the draft moves the hash and the row falls back
    to 待人工确认. Raises :class:`ExportStatusError` for a non-draft kind or a
    missing artifact (you cannot verify a draft that does not exist)."""
    if kind not in DRAFT_KINDS:
        raise ExportStatusError(
            f"only desktop drafts are human-verifiable: {DRAFT_KINDS} (got {kind!r})")
    name = _safe(lambda: project.load_config().name) or project.root.name
    path = _draft_path(project, kind, name)
    if path is None or not path.exists():
        raise ExportStatusError(
            f"no {kind} draft to verify — run `manju export --{kind}` first")
    record = {
        "ts": _now_iso(),
        "kind": kind,
        "path_hash": hash_file(path),
        "actor": actor,
        "note": note or "",
    }
    dest = _verifications_path(project)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


# ------------------------------------------------------------------- rows


def _final_row(ctx: _Ctx) -> DeliverableRow:
    project = ctx.project
    newest = ctx.newest_final
    if newest is None:
        return DeliverableRow("final", "成片 Final", None, Freshness.MISSING,
                              "从未渲染成片(renders/final 无 final_v*)")
    rel = project.relpath(newest)
    version = _version_of(newest)
    hint = f"文件:{rel}"
    if _size(newest) == 0:
        return DeliverableRow("final", "成片 Final", rel, Freshness.PROBLEMATIC,
                              "成片文件 0 字节(渲染中断?重新 manju build)",
                              openable=False, open_hint=hint, version=version)
    existing = _read_final_key_sidecar(newest)
    if existing is None:
        return DeliverableRow("final", "成片 Final", rel, Freshness.PROBLEMATIC,
                              "缺内容键 sidecar final_vN.key.json(渲染可能未完成)",
                              openable=True, open_hint=hint, version=version)
    if ctx.final_key is None:
        return DeliverableRow(
            "final", "成片 Final", rel, Freshness.NEEDS_MANUAL,
            "无法重算当前内容键,请人工确认" + (f":{ctx.timeline_note}" if ctx.timeline_note else ""),
            openable=True, open_hint=hint, version=version)
    if existing == ctx.final_key:
        return DeliverableRow("final", "成片 Final", rel, Freshness.UP_TO_DATE,
                              "内容键匹配:sidecar final_key == 当前重算(规格未变)",
                              openable=True, open_hint=hint, version=version)
    return DeliverableRow("final", "成片 Final", rel, Freshness.STALE,
                          "内容键不一致:规格已改,重新 manju build 出新版",
                          openable=True, open_hint=hint, version=version)


def _proxy_row(ctx: _Ctx) -> DeliverableRow:
    project = ctx.project
    path = project.proxy_dir / "proxy.mp4"
    if not path.exists():
        return DeliverableRow("proxy", "预览版 低清代理", None, Freshness.MISSING,
                              "未生成预览版 renders/proxy/proxy.mp4(manju build --target proxy)")
    rel = project.relpath(path)
    hint = f"文件:{rel}"
    if _size(path) == 0:
        return DeliverableRow("proxy", "预览版 低清代理", rel, Freshness.PROBLEMATIC,
                              "proxy 文件 0 字节(渲染中断?)", openable=False, open_hint=hint)
    existing = _read_final_key_sidecar(path)
    if existing is None:
        return DeliverableRow("proxy", "预览版 低清代理", rel, Freshness.PROBLEMATIC,
                              "缺内容键 sidecar proxy.key.json(渲染可能未完成)",
                              openable=True, open_hint=hint)
    if ctx.proxy_key is None:
        return DeliverableRow("proxy", "预览版 低清代理", rel, Freshness.NEEDS_MANUAL,
                              "无法重算当前内容键,请人工确认", openable=True, open_hint=hint)
    if existing == ctx.proxy_key:
        return DeliverableRow("proxy", "预览版 低清代理", rel, Freshness.UP_TO_DATE,
                              "内容键匹配:proxy.key.json == 当前重算(规格未变)",
                              openable=True, open_hint=hint)
    return DeliverableRow("proxy", "预览版 低清代理", rel, Freshness.STALE,
                          "内容键不一致:规格已改,重新 build 预览版", openable=True, open_hint=hint)


def _caption_row(ctx: _Ctx, kind: str) -> DeliverableRow:
    project = ctx.project
    label = "SRT 字幕 外挂" if kind == "srt" else "ASS 字幕 烧录样式"
    path = project.captions_dir / f"captions.{kind}"
    if not path.exists():
        return DeliverableRow(kind, label, None, Freshness.MISSING,
                              f"captions/captions.{kind} 未生成(manju build / export)")
    rel = project.relpath(path)
    hint = f"文件:{rel}"
    if _size(path) == 0:
        return DeliverableRow(kind, label, rel, Freshness.PROBLEMATIC,
                              f"captions.{kind} 0 字节", openable=False, open_hint=hint)

    manual = ctx.rules is not None and getattr(ctx.rules.captions, "mode", None) == "manual"
    if manual:
        # In manual mode the human SRT IS the truth that renders, and the ASS is
        # re-burned from it — neither can be "stale" against the auto-compiler.
        basis = ("manual 人工字幕:captions.srt 即所用真相(§3)" if kind == "srt"
                 else "manual 模式:ASS 由人工 SRT 逐字重烧(§3)")
        return DeliverableRow(kind, label, rel, Freshness.UP_TO_DATE, basis,
                              openable=True, open_hint=hint)

    if ctx.timeline is None:
        return DeliverableRow(kind, label, rel, Freshness.NEEDS_MANUAL,
                              "无时间线可逐字比对,请人工确认" + (
                                  f":{ctx.timeline_note}" if ctx.timeline_note else ""),
                              openable=True, open_hint=hint)
    try:
        from ..exporters.srt_ass import _caption_style, compile_ass, compile_srt

        style = _caption_style(project)
        max_chars = style.get("max_chars_per_line")
        if kind == "srt":
            expected = compile_srt(ctx.timeline, max_chars_per_line=max_chars)
        else:
            expected = compile_ass(ctx.timeline, width=ctx.timeline.width,
                                   height=ctx.timeline.height, style=style)
        on_disk = path.read_text(encoding="utf-8")
    except Exception:
        return DeliverableRow(kind, label, rel, Freshness.NEEDS_MANUAL,
                              "字幕重新编译比对失败,请人工确认", openable=True, open_hint=hint)
    if on_disk == expected:
        return DeliverableRow(kind, label, rel, Freshness.UP_TO_DATE,
                              "与当前时间线字幕逐字一致(重新编译比对)",
                              openable=True, open_hint=hint)
    return DeliverableRow(kind, label, rel, Freshness.STALE,
                          "时间线字幕已改:磁盘文件 ≠ 重新编译(manju build / export 更新)",
                          openable=True, open_hint=hint)


def _otio_row(ctx: _Ctx) -> DeliverableRow:
    project = ctx.project
    path = project.exports_dir / "otio" / f"{ctx.name}.otio"
    if not path.exists():
        return DeliverableRow("otio", "OTIO 交换格式", None, Freshness.MISSING,
                              "exports/otio 无 .otio(manju export --otio)")
    rel = project.relpath(path)
    hint = f"文件:{rel}"
    if _size(path) == 0:
        return DeliverableRow("otio", "OTIO 交换格式", rel, Freshness.PROBLEMATIC,
                              "OTIO 文件 0 字节", openable=False, open_hint=hint)
    tl_path = project.timeline_path
    if not tl_path.exists():
        return DeliverableRow("otio", "OTIO 交换格式", rel, Freshness.NEEDS_MANUAL,
                              "无 timeline.json 可比对时间(mtime),请人工确认",
                              openable=True, open_hint=hint)
    try:
        fresh = path.stat().st_mtime >= tl_path.stat().st_mtime
    except OSError:
        return DeliverableRow("otio", "OTIO 交换格式", rel, Freshness.NEEDS_MANUAL,
                              "mtime 读取失败,请人工确认", openable=True, open_hint=hint)
    if fresh:
        return DeliverableRow("otio", "OTIO 交换格式", rel, Freshness.UP_TO_DATE,
                              "OTIO 不早于 timeline.json(mtime 比对)",
                              openable=True, open_hint=hint)
    return DeliverableRow("otio", "OTIO 交换格式", rel, Freshness.STALE,
                          "OTIO 早于 timeline.json:时间线已改(mtime 比对),重新 export",
                          openable=True, open_hint=hint)


def _draft_row(ctx: _Ctx, kind: str) -> DeliverableRow:
    project = ctx.project
    label = "剪映草稿 JianYing" if kind == "jianying" else "CapCut 草稿"
    path = _draft_path(project, kind, ctx.name)
    if kind == "jianying":
        open_hint = (f"在剪映(pinned 版·关自动更新 §14)中打开草稿目录 "
                     f"exports/jianying/{ctx.name}/")
    else:
        open_hint = f"在 CapCut 中打开草稿目录 exports/capcut/{ctx.name}_native/"

    if path is None or not path.exists():
        made_by = "manju export --jianying" if kind == "jianying" else "manju export --capcut(需 pycapcut)"
        return DeliverableRow(kind, label, None, Freshness.MISSING,
                              f"未导出草稿({made_by})")
    rel = project.relpath(path)
    if _size(path) == 0:
        return DeliverableRow(kind, label, rel, Freshness.PROBLEMATIC,
                              "草稿 draft_content.json 0 字节", open_hint=open_hint)

    # 有问题: the JianYing skeleton is lintable (missing media / overlaps); the
    # capcut native draft has a different schema, so only sanity-check its JSON.
    problem = _draft_problem(project, kind, path)
    if problem:
        return DeliverableRow(kind, label, rel, Freshness.PROBLEMATIC,
                              f"草稿存在问题:{problem}", openable=True, open_hint=open_hint)

    # Desktop drafts can never be better than 待人工确认 until a human confirms
    # they open — Manju has no desktop app to prove it (§14). 已人工确认 holds
    # only while the current hash still equals the verified one.
    verify = _latest_verification(project, kind)
    if verify is not None:
        try:
            current = hash_file(path)
        except OSError:
            current = None
        if current is not None and verify.get("path_hash") == current:
            return DeliverableRow(
                kind, label, rel, Freshness.VERIFIED,
                f"人工已确认可在桌面 App 打开(hash 未变) · {verify.get('actor', '?')}"
                f" @ {str(verify.get('ts', ''))[:19]}",
                openable=True, open_hint=open_hint, verifiable=True,
                verified_by=verify.get("actor"), verified_at=verify.get("ts"),
                verified_note=verify.get("note") or None)
        return DeliverableRow(
            kind, label, rel, Freshness.NEEDS_MANUAL,
            "桌面草稿已重生成:上次人工确认对应的 hash 已失效,需重新确认(§14)",
            openable=True, open_hint=open_hint, verifiable=True)
    app = "剪映" if kind == "jianying" else "CapCut"
    return DeliverableRow(
        kind, label, rel, Freshness.NEEDS_MANUAL,
        f"桌面草稿:Manju 无法自证能在 {app} 中打开,需人工确认后标记(§14)",
        openable=True, open_hint=open_hint, verifiable=True)


def _draft_problem(project: Project, kind: str, path: Path) -> str | None:
    """First lint problem for a draft, or None. JianYing skeleton → the real
    ``jianying.lint_draft`` (missing media / overlap / bad µs); CapCut native →
    just a readable-JSON sanity check (its schema is pyJianYingDraft's, not the
    skeleton's, so lint_draft does not apply)."""
    try:
        if kind == "jianying":
            from ..exporters.jianying import lint_draft

            problems = lint_draft(path, project)
            return " ".join(str(problems[0]).split()) if problems else None
        from ..core.yamlio import read_json

        data = read_json(path)
        if not isinstance(data, dict):
            return "draft_content.json 不是 JSON 对象"
        return None
    except Exception as exc:
        return "draft 无法读取:" + " ".join(str(exc).split())[:80]


def _cover_row(ctx: _Ctx) -> DeliverableRow:
    project = ctx.project
    path = project.exports_dir / "packaging" / "cover.png"
    if not path.exists():
        return DeliverableRow("cover", "封面 Cover", None, Freshness.MISSING,
                              "exports/packaging 无 cover.png(manju package)")
    rel = project.relpath(path)
    hint = f"文件:{rel}"
    if _size(path) == 0:
        return DeliverableRow("cover", "封面 Cover", rel, Freshness.PROBLEMATIC,
                              "cover.png 0 字节", openable=False, open_hint=hint)
    sidecar_key = _read_pkg_key(path)
    if sidecar_key is None:
        return DeliverableRow("cover", "封面 Cover", rel, Freshness.PROBLEMATIC,
                              "cover 缺 key.json sidecar", openable=True, open_hint=hint)
    if ctx.newest_final is None or ctx.config is None:
        return DeliverableRow("cover", "封面 Cover", rel, Freshness.NEEDS_MANUAL,
                              "无 final 可比对(封面自成片切出),请人工确认",
                              openable=True, open_hint=hint)
    final_hash = ctx.final_hash()
    if final_hash is None or ctx.packaging is None:
        return DeliverableRow("cover", "封面 Cover", rel, Freshness.NEEDS_MANUAL,
                              "封面内容键重算失败,请人工确认", openable=True, open_hint=hint)
    try:
        from ..media.packaging import cover_cache_key

        expected = cover_cache_key(ctx.config, final_hash, ctx.packaging.cover)
    except Exception:
        return DeliverableRow("cover", "封面 Cover", rel, Freshness.NEEDS_MANUAL,
                              "封面内容键重算失败,请人工确认", openable=True, open_hint=hint)
    if sidecar_key == expected:
        return DeliverableRow("cover", "封面 Cover", rel, Freshness.UP_TO_DATE,
                              "cover key 匹配(final 字节 + 封面规格 未变)",
                              openable=True, open_hint=hint)
    return DeliverableRow("cover", "封面 Cover", rel, Freshness.STALE,
                          "cover key 不一致:final 或封面规格已改,manju package 重切",
                          openable=True, open_hint=hint)


def _teaser_row(ctx: _Ctx) -> DeliverableRow:
    project = ctx.project
    path = project.exports_dir / "packaging" / "teaser.mp4"
    enabled = ctx.packaging is not None and getattr(ctx.packaging.teaser, "enabled", False)
    if not path.exists():
        note = ("packaging.yaml 中 teaser 未启用(manju package 不产出)" if not enabled
                else "预告未生成(manju package)")
        return DeliverableRow("teaser", "预告 Teaser", None, Freshness.MISSING, note)
    rel = project.relpath(path)
    hint = f"文件:{rel}"
    if _size(path) == 0:
        return DeliverableRow("teaser", "预告 Teaser", rel, Freshness.PROBLEMATIC,
                              "teaser.mp4 0 字节", openable=False, open_hint=hint)
    sidecar_key = _read_pkg_key(path)
    if sidecar_key is None:
        return DeliverableRow("teaser", "预告 Teaser", rel, Freshness.PROBLEMATIC,
                              "teaser 缺 key.json sidecar", openable=True, open_hint=hint)
    if ctx.newest_final is None or ctx.config is None:
        return DeliverableRow("teaser", "预告 Teaser", rel, Freshness.NEEDS_MANUAL,
                              "无 final 可比对(预告自成片切出),请人工确认",
                              openable=True, open_hint=hint)
    final_hash = ctx.final_hash()
    if final_hash is None or ctx.packaging is None:
        return DeliverableRow("teaser", "预告 Teaser", rel, Freshness.NEEDS_MANUAL,
                              "预告内容键重算失败,请人工确认", openable=True, open_hint=hint)
    try:
        from ..media.packaging import teaser_cache_key

        expected = teaser_cache_key(ctx.config, final_hash, ctx.packaging.teaser)
    except Exception:
        return DeliverableRow("teaser", "预告 Teaser", rel, Freshness.NEEDS_MANUAL,
                              "预告内容键重算失败,请人工确认", openable=True, open_hint=hint)
    if sidecar_key == expected:
        return DeliverableRow("teaser", "预告 Teaser", rel, Freshness.UP_TO_DATE,
                              "teaser key 匹配(final 字节 + 预告规格 未变)",
                              openable=True, open_hint=hint)
    return DeliverableRow("teaser", "预告 Teaser", rel, Freshness.STALE,
                          "teaser key 不一致:final 或预告规格已改,manju package 重切",
                          openable=True, open_hint=hint)


# --------------------------------------------------------------------- api


def deliverables(project: Project) -> list[DeliverableRow]:
    """The export center's nine deliverable rows, in ship order. Pure and
    read-only — never spends, never mutates. Degrades cleanly on an empty or
    half-built project (every row falls to an honest 缺失/待人工确认)."""
    ctx = _gather(project)
    return [
        _final_row(ctx),
        _proxy_row(ctx),
        _caption_row(ctx, "srt"),
        _caption_row(ctx, "ass"),
        _otio_row(ctx),
        _draft_row(ctx, "jianying"),
        _draft_row(ctx, "capcut"),
        _cover_row(ctx),
        _teaser_row(ctx),
    ]


def deliverables_data(project: Project) -> dict[str, Any]:
    """JSON-ready payload for the CLI ``--json`` and the GUI ``/api/exports``:
    the same rows both surfaces render, so they can never disagree."""
    rows = deliverables(project)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.freshness.value] = counts.get(r.freshness.value, 0) + 1
    return {"deliverables": [r.to_dict() for r in rows], "counts": counts}
