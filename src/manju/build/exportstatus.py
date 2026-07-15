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
``media.packaging._stale_final_status`` run, the SAME caption compile the
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
from ..core.hashing import hash_file, hash_value

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
# WP2 §4.4: the sibling flock that serializes EVERY verifications.jsonl append
# (this draft verification AND 07C baseline approval) through the one shared
# core.events coordinator, so the two writers never tear/truncate each other.
VERIFICATIONS_LOCK = "verifications.lock"  # under reports/


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
        # stance `manju explain` and media.packaging._stale_final_status take.
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


# round-W #65: a bounded fingerprint of every media file a draft references —
# a local copy of the media-extension notion `media/webpreview.py` already
# duplicates for the same "stay decoupled from the engine's full MEDIA_EXTS
# list" reason.
_DRAFT_MEDIA_EXTS = {
    ".mp4", ".mov", ".mkv", ".webm", ".m4v",
    ".png", ".jpg", ".jpeg",
    ".wav", ".mp3", ".m4a", ".flac",
}


def _referenced_media_paths(node: Any, found: set[str] | None = None) -> list[str]:
    """Every absolute, existing, media-extension file path found anywhere in a
    parsed draft JSON tree (round-W #65).

    Schema-agnostic ON PURPOSE: the skeleton draft (``jianying.py``) and the
    native pyJianYingDraft/pycapcut schema both embed absolute source paths as
    plain strings (``materials[].path`` in the skeleton; a differently-shaped
    but equally string-valued field in the native schema) — walking every
    string leaf finds either without hand-parsing two different, and
    partially undocumented, schemas. Sorted + deduped for a deterministic
    basis."""
    if found is None:
        found = set()
    if isinstance(node, dict):
        for v in node.values():
            _referenced_media_paths(v, found)
    elif isinstance(node, list):
        for v in node:
            _referenced_media_paths(v, found)
    elif isinstance(node, str) and Path(node).suffix.lower() in _DRAFT_MEDIA_EXTS:
        try:
            p = Path(node)
            if p.is_absolute() and p.is_file():
                found.add(node)
        except OSError:
            pass
    return sorted(found)


def _verification_basis(path: Path) -> str | None:
    """The hash :func:`mark_verified` records and :func:`_draft_row` compares
    against — the draft JSON's own bytes PLUS a bounded fingerprint of every
    media file it references (round-W #65).

    Full content-hashing every referenced media file on every export-status
    read would be expensive for a multi-take project (renders/proxies can run
    hundreds of MB) with no real upside over a (size, mtime) fingerprint for
    THIS purpose — a human confirming "yes, this draft opens correctly in
    JianYing" is attesting to bytes existing in a certain shape, not defending
    against a hostile substitution, so (size, mtime_ns) is the documented,
    bounded choice the review's own "if full hashing is too slow" escape
    hatch offers. Replacing a referenced media file (new size or mtime, even
    at the same path) therefore moves this basis and flips 已确认 back to
    待人工确认. Returns ``None`` when the draft itself cannot be read."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    fingerprint: list[Any] = []
    for media in _referenced_media_paths(data):
        try:
            st = Path(media).stat()
            fingerprint.append([media, st.st_size, st.st_mtime_ns])
        except OSError:
            fingerprint.append([media, None, None])
    return hash_value({"draft": hash_file(path), "media": fingerprint})


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

    A draft shows 已人工确认 only while its verification basis still equals the
    one recorded here — the draft JSON's own bytes PLUS a bounded fingerprint
    of every media file it references (round-W #65: :func:`_verification_basis`
    — binding verified-ness to the JSON alone let a human's "yes, this opens
    correctly" survive a referenced take/proxy file being silently replaced,
    since the draft JSON text never changes when only the MEDIA it points at
    does). Regenerating the draft OR replacing a referenced media file moves
    the basis and the row falls back to 待人工确认. Raises
    :class:`ExportStatusError` for a non-draft kind, a missing artifact, or an
    unreadable one (you cannot verify a draft that cannot even be read)."""
    if kind not in DRAFT_KINDS:
        raise ExportStatusError(
            f"only desktop drafts are human-verifiable: {DRAFT_KINDS} (got {kind!r})")
    name = _safe(lambda: project.load_config().name) or project.root.name
    path = _draft_path(project, kind, name)
    if path is None or not path.exists():
        raise ExportStatusError(
            f"no {kind} draft to verify — run `manju export --{kind}` first")
    basis = _verification_basis(path)
    if basis is None:
        raise ExportStatusError(f"{kind} draft could not be read — cannot verify: {path}")
    record = {
        "ts": _now_iso(),
        "kind": kind,
        "path_hash": basis,
        "actor": actor,
        "note": note or "",
    }
    # WP2 §4.4: route through the ONE shared append coordinator against
    # verifications.jsonl/.lock — the same locked helper 07C baseline approval
    # uses — so a draft verification and a baseline approval racing this log
    # serialize instead of interleaving/truncating.
    from ..core.events import append_jsonl_line

    dest = _verifications_path(project)
    dest.parent.mkdir(parents=True, exist_ok=True)
    append_jsonl_line(project.reports_dir, record, durable=True, required=False,
                      file_name=VERIFICATIONS_FILE, lock_name=VERIFICATIONS_LOCK)
    return record


# ------------------------------------------------------------------- rows


def _locale_final_langs(project: Project) -> list[str]:
    """Languages with at least one final_v*.mp4 under renders/final/locales/."""
    root = project.final_dir / "locales"
    if not root.is_dir():
        return []
    out: list[str] = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and any(d.glob("final_v*.mp4")):
            out.append(d.name)
    return out


def _final_row(ctx: _Ctx) -> DeliverableRow:
    project = ctx.project
    newest = ctx.newest_final
    if newest is None:
        # C6: do not pretend "no film" when only locale finals exist.
        langs = _locale_final_langs(project)
        if langs:
            return DeliverableRow(
                "final", "成片 Final", None, Freshness.MISSING,
                "无 base 成片(renders/final/),但有 locale 成片: "
                + ", ".join(langs)
                + " — 导出中心按 base 计;locale 用 manju build --lang",
            )
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


def _manual_ass_row(ctx: _Ctx, kind: str, label: str, path, rel: str,
                    hint: str) -> "DeliverableRow":
    """manual-mode ASS freshness (round-W #62): recompile the burn from the
    CURRENT captions.srt + CURRENT style — exactly what `export_captions`
    does on every export in manual mode — and compare byte-for-byte to the
    on-disk ASS. up_to_date only when it's an exact match (i.e. it is both
    newer in CONTENT terms than whatever SRT/style produced it AND consistent
    with the current style inputs); any mismatch (including a missing SRT to
    compare against) is honestly 待更新/待人工确认, never a blind pass."""
    project = ctx.project
    srt_path = project.captions_dir / "captions.srt"
    if not srt_path.exists():
        return DeliverableRow(
            kind, label, rel, Freshness.NEEDS_MANUAL,
            "manual 模式但 captions.srt 缺失,无法比对 ASS 是否为最新烧录,请人工确认",
            openable=True, open_hint=hint)
    try:
        from ..core.models import CaptionLine, Timeline as _Timeline, TimelineTracks
        from ..exporters.srt_ass import _caption_style, compile_ass
        from ..providers.asr import parse_srt

        style = _caption_style(project)
        human = parse_srt(srt_path.read_text(encoding="utf-8"))
        width = ctx.timeline.width if ctx.timeline is not None else (
            ctx.config.width if ctx.config is not None else 1080)
        height = ctx.timeline.height if ctx.timeline is not None else (
            ctx.config.height if ctx.config is not None else 1920)
        human_timeline = _Timeline(
            width=width, height=height,
            tracks=TimelineTracks(captions=[
                CaptionLine(start_ms=s.start_ms, end_ms=s.end_ms, text=s.text)
                for s in human
            ]),
        )
        expected = compile_ass(human_timeline, width=width, height=height,
                               style=style, apply_line_breaks=False)
        on_disk = path.read_text(encoding="utf-8")
    except Exception:
        return DeliverableRow(kind, label, rel, Freshness.NEEDS_MANUAL,
                              "ASS 重新烧录比对失败,请人工确认", openable=True, open_hint=hint)
    if on_disk == expected:
        return DeliverableRow(
            kind, label, rel, Freshness.UP_TO_DATE,
            "manual 模式:与当前 captions.srt + 样式重烧逐字一致",
            openable=True, open_hint=hint)
    return DeliverableRow(
        kind, label, rel, Freshness.STALE,
        "manual 模式:ASS 与当前 captions.srt/样式重烧不一致(SRT 或样式改过未重新 "
        "export)— 重新 manju build / export 更新",
        openable=True, open_hint=hint)


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
        if kind == "srt":
            # In manual mode the human SRT IS the truth that renders — it is
            # definitionally up to date with itself.
            return DeliverableRow(
                kind, label, rel, Freshness.UP_TO_DATE,
                "manual 人工字幕:captions.srt 即所用真相(§3)",
                openable=True, open_hint=hint)
        # round-W #62: the ASS burn is NOT automatically in sync just because
        # manual mode is on — `export_captions` re-burns it from the CURRENT
        # captions.srt + CURRENT style EVERY export, so it only reads
        # up_to_date when the on-disk ASS still matches THAT recompile (the
        # same content-compare basis the non-manual branch below already
        # uses, aimed at the human SRT instead of the compiled timeline). A
        # hand-edited SRT with no re-export since would otherwise show 上新
        # while the burned ASS the human never actually saw goes stale.
        return _manual_ass_row(ctx, kind, label, path, rel, hint)

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
    # only while the current verification BASIS still equals the verified one
    # — the draft JSON's own bytes PLUS the referenced media files' (size,
    # mtime) fingerprint (round-W #65: replacing a referenced take/proxy file
    # in place, with the draft JSON text untouched, now also un-verifies).
    verify = _latest_verification(project, kind)
    if verify is not None:
        current = _verification_basis(path)
        if current is not None and verify.get("path_hash") == current:
            return DeliverableRow(
                kind, label, rel, Freshness.VERIFIED,
                f"人工已确认可在桌面 App 打开(draft+引用媒体均未变)· {verify.get('actor', '?')}"
                f" @ {str(verify.get('ts', ''))[:19]}",
                openable=True, open_hint=open_hint, verifiable=True,
                verified_by=verify.get("actor"), verified_at=verify.get("ts"),
                verified_note=verify.get("note") or None)
        return DeliverableRow(
            kind, label, rel, Freshness.NEEDS_MANUAL,
            "桌面草稿已重生成或引用媒体已变:上次人工确认已失效,需重新确认(§14)",
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


# ----------------------------------------------------- AI_IDE_18 audio masters


# label per masters role (生成来源 glossary vocabulary).
_MASTERS_LABEL = {
    "DIALOGUE_STEM": "对白 分轨 Stem",
    "MUSIC_STEM": "音乐 分轨 Stem",
    "SFX_STEM": "音效/环境 分轨 Stem",
    "FULL_MIX": "全混音 Full Mix",
    "M_AND_E_MASTER": "国际版 M&E 母版",
}


def _masters_rows(ctx: _Ctx) -> list[DeliverableRow]:
    """AI_IDE_18 WP7: one row per real audio master the ``exports/masters``
    index recorded. CONDITIONAL — a project that never ran ``manju masters``
    keeps the historical nine rows byte-identical (the roles stay
    declared-but-unpopulated, contract §6.3). Freshness compares the recorded
    timeline semantic digest against the current one (the SAME digest the 13C
    manifest binds), so a moved cue / dropped shot marks every master 待更新."""
    from ..media.masters import KIND_ROLE, load_index

    index = load_index(ctx.project)
    if not index or not index.get("artifacts"):
        return []
    try:
        from .delivery import timeline_semantic_digest

        current = timeline_semantic_digest(ctx.timeline)
    except Exception:
        current = None
    rows: list[DeliverableRow] = []
    for art in index.get("artifacts", []):
        role = art.get("role")
        kind = art.get("kind") or KIND_ROLE.get(role, "")
        label = _MASTERS_LABEL.get(role, role or "Audio Master")
        rel = art.get("path")
        abspath = (ctx.project.root / rel) if rel else None
        if abspath is None or not abspath.exists():
            rows.append(DeliverableRow(kind, label, rel, Freshness.MISSING,
                                       "母版文件缺失(manju masters 重新生成)"))
            continue
        hint = f"文件:{rel}"
        recorded = art.get("timeline_digest")
        if current is not None and recorded is not None and recorded != current:
            rows.append(DeliverableRow(
                kind, label, rel, Freshness.STALE,
                "时间线语义已改:母版基于旧混音(manju masters 重新生成)",
                openable=True, open_hint=hint))
        else:
            loud = art.get("loudness") or {}
            rows.append(DeliverableRow(
                kind, label, rel, Freshness.UP_TO_DATE,
                f"母版字节存在且时间线未变;实测 integrated "
                f"{loud.get('integrated_lufs')} LUFS / TP "
                f"{loud.get('true_peak_dbtp')} dBTP",
                openable=True, open_hint=hint))
    return rows


def _vtt_row(ctx: _Ctx) -> DeliverableRow | None:
    """AI_IDE_18 WP7: the CAPTIONS_VTT deliverable — CONDITIONAL on a
    captions.vtt existing (so historical projects are unchanged). Compiled from
    the SAME captions track as SRT/ASS; freshness is a verbatim recompile
    compare, exactly like :func:`_caption_row`."""
    project = ctx.project
    path = project.captions_dir / "captions.vtt"
    if not path.exists():
        return None
    rel = project.relpath(path)
    hint = f"文件:{rel}"
    if _size(path) == 0:
        return DeliverableRow("vtt", "WebVTT 字幕", rel, Freshness.PROBLEMATIC,
                              "captions.vtt 0 字节", openable=False, open_hint=hint)
    manual = ctx.rules is not None and getattr(ctx.rules.captions, "mode", None) == "manual"
    if manual or ctx.timeline is None:
        return DeliverableRow("vtt", "WebVTT 字幕", rel, Freshness.NEEDS_MANUAL,
                              "manual/无时间线:请人工确认 WebVTT", openable=True, open_hint=hint)
    try:
        from ..exporters.srt_ass import _caption_style, compile_vtt

        style = _caption_style(project)
        expected = compile_vtt(ctx.timeline, max_chars_per_line=style.get("max_chars_per_line"))
        on_disk = path.read_text(encoding="utf-8")
    except Exception:
        return DeliverableRow("vtt", "WebVTT 字幕", rel, Freshness.NEEDS_MANUAL,
                              "WebVTT 重新编译比对失败,请人工确认", openable=True, open_hint=hint)
    if on_disk == expected:
        return DeliverableRow("vtt", "WebVTT 字幕", rel, Freshness.UP_TO_DATE,
                              "与当前时间线字幕逐字一致(重新编译比对)",
                              openable=True, open_hint=hint)
    return DeliverableRow("vtt", "WebVTT 字幕", rel, Freshness.STALE,
                          "时间线字幕已改:磁盘 ≠ 重新编译(manju export 更新)",
                          openable=True, open_hint=hint)


# --------------------------------------------------------------------- api


def deliverables(project: Project) -> list[DeliverableRow]:
    """The export center's deliverable rows, in ship order. Pure and read-only —
    never spends, never mutates. Degrades cleanly on an empty or half-built
    project (every row falls to an honest 缺失/待人工确认).

    The historical nine rows are always present; AI_IDE_18 appends the WebVTT
    caption and the audio-master rows ONLY when those artifacts exist, so a
    project that never produced them is byte-identical to before (§6.3)."""
    ctx = _gather(project)
    rows = [
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
    # C11: list locale finals as openable deliverable rows (existence honesty).
    for lang in _locale_final_langs(project):
        d = project.final_dir / "locales" / lang
        newest = None
        nums = []
        for p in d.glob("final_v*.mp4"):
            m = re.match(r"final_v(\d+)$", p.stem)
            if m:
                nums.append((int(m.group(1)), p))
        if nums:
            newest = max(nums, key=lambda t: t[0])[1]
        if newest is None:
            continue
        rel = project.relpath(newest)
        rows.append(DeliverableRow(
            f"final_locale_{lang}",
            f"成片 Final ({lang})",
            rel,
            Freshness.NEEDS_MANUAL,
            f"locale 成片存在(内容键比对未接):{rel} — 用 manju build --lang {lang}",
            openable=True,
            open_hint=rel,
            version=_version_of(newest),
        ))
    vtt = _vtt_row(ctx)
    if vtt is not None:
        rows.append(vtt)
    rows.extend(_masters_rows(ctx))
    return rows


def deliverables_data(project: Project) -> dict[str, Any]:
    """JSON-ready payload for the CLI ``--json`` and the GUI ``/api/exports``:
    the same rows both surfaces render, so they can never disagree.

    AI_IDE_07C: exports is the SOLE composition entry for the release assessment.
    The ``release_assessment`` section is an additive, instant, read-only
    derivation folded in here over the SAME ``rows`` (no second gather, no new
    file/schema). It is deterministic, so two reads deep-equal; a lazy import
    keeps :mod:`baseline` (which reads exportstatus) cycle-free."""
    rows = deliverables(project)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.freshness.value] = counts.get(r.freshness.value, 0) + 1
    data: dict[str, Any] = {"deliverables": [r.to_dict() for r in rows], "counts": counts}
    from . import baseline as _baseline

    data["release_assessment"] = _baseline.release_assessment(project, rows=rows)
    return data
