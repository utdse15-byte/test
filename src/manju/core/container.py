"""The `xxx.manju/` project container (§3).

Directory-as-project: truth is text, media is append-only, SQLite/runtime can
be deleted at any time. Three disciplines are enforced here at the API level:

- media/imports is sacred: there is NO code path in this module (or anywhere
  in the engine) that deletes or rewrites a file under imports/.
- media/gen and renders/final are append-only: new work always gets a new
  take_NN / final_vN name; nothing is ever overwritten.
- all truth files are written atomically (temp + rename).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .idents import UnsafeIdentifierError, validate_safe_segment, windows_segment_problems
from .models import (
    PROJECT_FORMAT,
    PackagingSpec,
    ProjectConfig,
    ShotIndex,
    ShotSpec,
    TakeSidecar,
    Timeline,
    TimelineRules,
    VoiceTakeSidecar,
)
from .timebase import Rate
from .yamlio import dump_yaml, read_json, read_yaml, write_json, write_yaml

PROJECT_FILE = "project.yaml"

# CLI-P0-001: on-disk signals that a directory holding a `project.yaml` is
# really a Manju project and not an unrelated tool's config file of the same
# name. A candidate qualifies when project.yaml carries the `format` marker
# (PROJECT_FORMAT, seeded by `manju new`) OR any of these structure directories
# exists — the shape `Project.create` always scaffolds. Old projects (created
# before the marker) still resolve via the structure signal, so discovery never
# forces a migration; a bare foreign `project.yaml` matches NEITHER and is
# refused (Project.find / _looks_like_manju_project) instead of being taken over.
_PROJECT_STRUCTURE_SIGNALS = ("story", "shots", "bible", "timeline", ".manju")


def _looks_like_manju_project(root: Path) -> bool:
    """True iff ``root`` holds a real Manju project (CLI-P0-001): its
    project.yaml declares ``format: manju.project/v1`` OR one of the
    :data:`_PROJECT_STRUCTURE_SIGNALS` directories exists. A plain `project.yaml`
    dropped by some other tool has neither, so it is never mistaken for a
    project (and never written into by a manju write command)."""
    pf = root / PROJECT_FILE
    if not pf.exists():
        return False
    # Structure signal first — a cheap stat that short-circuits for every real
    # project (create() always scaffolds these), so the write hot path never
    # re-parses project.yaml just to self-verify.
    if any((root / sig).is_dir() for sig in _PROJECT_STRUCTURE_SIGNALS):
        return True
    try:
        data = read_yaml(pf)
    except Exception:
        data = None
    return (
        isinstance(data, dict)
        and isinstance(data.get("format"), str)
        and data["format"].startswith("manju.project/")
    )
# Round Y (review #4): an ORDERED tuple, not a set. `Project.takes()` picks a
# take's media file by walking this in order, so when a take dir somehow holds
# more than one media file for the same stem (e.g. take_01.mp4 AND take_01.mov)
# the choice is DETERMINISTIC (video > image > audio, highest-fidelity video
# first) across processes/Python versions — a set's iteration order is not.
# `takes()` also surfaces the collision as a conflict on TakeInfo.error so
# `manju check` reports it instead of silently tolerating an ambiguous take.
# Membership tests (`ext in MEDIA_EXTS`) and `sorted(MEDIA_EXTS)` work
# identically on a tuple, so every other caller is unaffected.
MEDIA_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".m4v",
              ".png", ".jpg", ".jpeg",
              ".wav", ".mp3", ".m4a", ".flac")

# The bible is a generic dict-of-files keyed by id (§4). This is the ONE list
# of first-class bible files — scaffolded at `manju new`, merged by load_bible,
# and lock-verified by `manju check`. Adding a file here makes it first-class
# everywhere at once (goal item 11: props.yaml and voices.yaml).
BIBLE_FILES = ("characters", "scenes", "props", "style", "voices")

# goal item 38: generated the gitignore lines FROM MEDIA_EXTS instead of a
# hand-maintained partial list, so a new extension added to MEDIA_EXTS (the
# one canonical media-extension list) can never silently fall out of the
# ignore rules again — `git add -A` at snapshot time must never pick up
# media/gen output regardless of which of these extensions it landed as.
def _media_gitignore_lines() -> str:
    return "\n".join(f"media/gen/**/*{ext}" for ext in sorted(MEDIA_EXTS))


GITIGNORE = f"""\
# Derived and heavy artifacts stay out of git; truth text goes in (§3)
{_media_gitignore_lines()}
media/generated/
media/imports/
renders/
exports/
.manju/
# HISTORY-P0-001: secrets never enter git history via `manju snapshot`. Ignore
# .env and its variants; keep explicit template files (.example/.template/.sample)
# tracked so a project can still ship a redacted example.
.env
.env.*
!.env.example
!.env.template
!.env.sample
"""

# packaging.yaml scaffold header — commented hints for a file that turns nothing
# on by default (round-N). Enabling any section changes build/package output.
# Bible scaffolds. `shots/*.yaml` points the author at these files by id, so an
# empty `{}` left them guessing both the shape and the field names — the one
# place the guided path stopped guiding (there is no `manju bible add`; authoring
# is this file or the GUI's bible editor). Every body below is still an EMPTY
# mapping: a fresh project passes `manju check` byte-for-behaviour unchanged, and
# nothing here is a build input until the author fills it in.
_BIBLE_COMMON_TAIL = """\
#
# 字段都是自由文档,除 name 外没有必填项;引擎只认最外层的 id。
#   name        显示名(留空则直接显示 id)
#   desc        自由描述 —— 生成提示词会读它,写得越具体越稳
#   ref_image   参考图路径(media/refs/... 或 imports/...),让形象跨镜头一致
#   aliases     别名,@提及 与 mentions 会解析(如 [老陈, 陈师傅])
#   locked      锁定字段:写了就不许 AI 改(见 manju check 的 locks 校验)
# 改完用 `manju check` 验证;GUI 里也有可视化的 bible 编辑器。
{}
"""

BIBLE_SCAFFOLD_HEADERS = {
    "characters": """\
# characters.yaml — 角色表。shots/*.yaml 的 characters: [id] 按 id 引用这里。
#
# 示例(去掉 # 即可启用):
# a_ming:
#   name: 阿明
#   desc: 四十岁的修鞋匠,沉默,手上有老茧,常穿褪色的藏青工装。
""" + _BIBLE_COMMON_TAIL,
    "scenes": """\
# scenes.yaml — 场景表。shots/*.yaml 的 scene: id 按 id 引用这里。
#
# 示例(去掉 # 即可启用):
# jie_tou:
#   name: 老街街头
#   desc: 黄昏,潮湿的青石板路,两侧卷闸门半掩的旧店铺,暖黄路灯刚亮。
""" + _BIBLE_COMMON_TAIL,
    "props": """\
# props.yaml — 道具表。在镜头自由文本里用 @id 提及,或写进 desc 由提示词带上。
#
# 示例(去掉 # 即可启用):
# xiu_xie_xiang:
#   name: 修鞋箱
#   desc: 木质,边角磨圆,铜搭扣氧化发黑,里面工具摆得极整齐。
""" + _BIBLE_COMMON_TAIL,
    "style": """\
# style.yaml — 全片视觉基调(镜头级 look 在 timeline/rules.yaml)。
#
# 示例(去掉 # 即可启用):
# look:
#   name: 潮湿黄昏
#   desc: 低饱和暖调,轻微胶片颗粒,高光柔化;避免高对比硬光。
# font:
#   desc: 字幕用思源黑体,字重 Medium
""" + _BIBLE_COMMON_TAIL,
    "voices": """\
# voices.yaml — 角色音色表。id 与 characters.yaml 对应,供 TTS provider 选音。
#
# 示例(去掉 # 即可启用):
# a_ming:
#   name: 阿明
#   desc: 中年男声,偏低沉,语速慢,句尾略下沉。
#   provider_voice: zh-CN-YunjianNeural   # 具体取值见你配置的 provider manifest
""" + _BIBLE_COMMON_TAIL,
}


PACKAGING_SCAFFOLD_HEADER = """\
# packaging.yaml — 片头/片尾卡、封面、预告、信息卡(round-N packaging kit)。
# 默认全部关闭:此文件存在不改变任何构建产物;启用某项后再 `manju build` / `manju package`。
#
#   intro / outro : 设 enabled: true 并填 text(可选 subtext),成为时间线首/尾真实段落,
#                   下游配音/字幕/总时长随之自然平移。模板 template 首选 html 卡片,
#                   无 Chromium 时降级 drawtext(§8.4)。
#   info_cards    : 章节/角色/信息卡,骑在 overlay 轨;at 用 "shot:<id>[:start|:end]" 锚点,
#                   offset_ms 为相对偏移;锚点镜头不存在则跳过并由 QC 提示。
#   cover         : mode: frame 从成片抽帧(frame_ms),或 mode: card 渲染文字封面。
#   teaser        : 设 enabled: true,从成片切 [from_ms, from_ms+duration_ms]。
#   封面/预告由 `manju package` 从当前 final 产出到 exports/packaging/。
"""


@dataclass
class TakeInfo:
    shot_id: str
    name: str  # e.g. "take_03"
    media_path: Path | None  # None if sidecar exists but media is missing
    sidecar_path: Path
    sidecar: TakeSidecar
    # Round W (issue #22): set when the on-disk sidecar failed TakeSidecar's
    # model validation (e.g. a hand-edited reversed/negative source_in_ms /
    # source_out_ms window) — ``sidecar`` above is then a SAFE degraded stand-
    # in (the illegal window dropped back to whole-file 0/None) so numeric
    # code never sees the bad values, while this field keeps the reason
    # visible for `manju check` / QC to surface as a real finding instead of
    # silently tolerating it. ``None`` for every take before this landed and
    # for every take whose sidecar parses cleanly (the overwhelming majority).
    error: str | None = None


class ProjectError(RuntimeError):
    pass


class ProjectNameError(ProjectError):
    """The requested project directory name is not creatable/openable on
    Windows (CLI-P1-004). A subclass of ProjectError so every existing
    ``except ProjectError`` call site keeps catching it."""


class Project:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        if not (self.root / PROJECT_FILE).exists():
            raise ProjectError(
                f"not a manju project (no {PROJECT_FILE}): {self.root} — 该目录缺少 "
                f"{PROJECT_FILE}。确认路径指向 <名字>.manju 项目目录,或用 "
                f"`manju new <名字>` 新建一个。"
            )

    # ------------------------------------------------------------ discovery

    @classmethod
    def find(cls, start: Path | str = ".") -> "Project":
        # CLI-P0-001: a `project.yaml` is only a stopping point when the
        # directory is REALLY a manju project (format marker or structure
        # signal). A bare foreign project.yaml is walked PAST, never taken over
        # — remembered as `bare` so the error can point the owner at it with a
        # migration hint instead of a generic "no project" message.
        p = Path(start).resolve()
        bare: Path | None = None
        for candidate in [p, *p.parents]:
            if not (candidate / PROJECT_FILE).exists():
                continue
            if _looks_like_manju_project(candidate):
                return cls(candidate)
            if bare is None:
                bare = candidate
        if bare is not None:
            raise ProjectError(
                f"{bare} 有 {PROJECT_FILE},但它不像 Manju 项目:既没有 "
                f"`format: {PROJECT_FORMAT}` 标识,也没有 Manju 的结构目录"
                f"({'/、'.join(_PROJECT_STRUCTURE_SIGNALS)}/)。为避免误接管并污染无关目录,"
                f"这里不当作项目处理。若这确是一个老的 Manju 项目,给 {PROJECT_FILE} 加一行 "
                f"`format: {PROJECT_FORMAT}` 即可;否则请 cd 进正确的 <名字>.manju 目录,"
                f"或用 `manju new <名字>` 新建。"
            )
        raise ProjectError(
            f"no manju project found from {p} upward — 当前目录及其所有上级都没有 "
            f"{PROJECT_FILE},所以这里不是 Manju 项目。请 cd 进某个 <名字>.manju "
            f"目录再运行,或用 `manju new <名字>` 新建一个。"
        )

    def verify_manju_identity(self) -> None:
        """CLI-P0-001 write-before second verification: refuse to run a write
        command against a directory that merely happens to hold a `project.yaml`
        but is not a real Manju project (no format marker, no structure signal).
        The safety check lives here in the Project layer (not only in the CLI)
        so every write entry point — CLI, MCP, GUI — is covered. A project built
        through :meth:`create` or discovered through :meth:`find` already
        qualifies; this closes the gap for a ``Project(<explicit path>)`` opened
        straight onto a foreign directory."""
        if not _looks_like_manju_project(self.root):
            raise ProjectError(
                f"拒绝写入 {self.root}:该目录有 {PROJECT_FILE} 但不像 Manju 项目"
                f"(缺少 `format: {PROJECT_FORMAT}` 标识与 Manju 结构目录)。"
                f"为避免污染无关目录,写命令不在此执行。老项目可在 {PROJECT_FILE} 补一行 "
                f"`format: {PROJECT_FORMAT}`,或用 `manju new` 新建。"
            )

    # ------------------------------------------------------------- creation

    @classmethod
    def create(cls, path: Path | str, name: str | None = None, *, vertical: bool = True,
               git_init: bool = True) -> "Project":
        root = Path(path).resolve()
        if root.suffix != ".manju":
            root = root.with_name(root.name + ".manju")
        # CLI-P1-004: the Windows name rules were only ever applied by `check`,
        # i.e. AFTER the directory existed. `CON.manju` / a trailing-dot name is
        # unopenable on the first platform, so refuse before anything is made —
        # same owner the checker uses, no second spelling of the rules.
        problems = windows_segment_problems(root.name)
        if not root.stem:
            problems.append("空项目名(empty project name)")
        if problems:
            raise ProjectNameError(
                "项目名在 Windows 上不可用(refused before creating anything): "
                + "; ".join(problems)
            )
        if (root / PROJECT_FILE).exists():
            raise ProjectError(f"project already exists: {root}")
        name = name or root.stem

        for sub in (
            "story", "bible", "shots", "media/imports", "media/refs", "media/gen",
            "timeline", "captions", "renders/segments", "renders/proxy", "renders/final",
            "exports/jianying", "exports/capcut", "exports/otio",
            "reports/frames", "proposals", ".manju",
        ):
            (root / sub).mkdir(parents=True, exist_ok=True)

        width, height = (1080, 1920) if vertical else (1920, 1080)
        # CLI-P0-001: stamp the format marker so this project is unambiguously a
        # Manju project on discovery (never mistaken for an unrelated project.yaml).
        config = ProjectConfig(name=name, format=PROJECT_FORMAT, width=width, height=height)
        write_yaml(root / PROJECT_FILE, config.model_dump(exclude_none=True))
        write_yaml(root / "shots" / "index.yaml", ShotIndex().model_dump())
        write_yaml(root / "timeline" / "rules.yaml", TimelineRules().model_dump())
        # packaging.yaml sits next to rules.yaml, everything disabled + hints (§13-14)
        # Written through atomic_write_text, not Path.write_text: the latter uses
        # newline=None, which on WINDOWS translates every "\n" into CRLF, so a
        # project scaffolded there would carry different bytes — and different
        # content hashes — than the same project scaffolded anywhere else. Every
        # other truth file already goes through that owner (it pins
        # newline="\n"); these two scaffolds were the exception.
        from .yamlio import atomic_write_text

        atomic_write_text(
            root / "timeline" / "packaging.yaml",
            PACKAGING_SCAFFOLD_HEADER + dump_yaml(PackagingSpec().model_dump()))
        for bible_file in BIBLE_FILES:
            bpath = root / "bible" / f"{bible_file}.yaml"
            if not bpath.exists():
                # A commented scaffold rather than a bare `{}` — same treatment
                # packaging.yaml already gets above, and for the same reason.
                # shots/S001.yaml points the author at "bible/scenes.yaml 中的
                # 场景 id", but an empty file shows neither the shape nor that
                # `name`/`desc` are the ordinary fields; `check` then refuses the
                # shot with "not found in bible" and the guided path dead-ends on
                # a schema the author has to go find. The body still parses as an
                # empty mapping, so a fresh project passes check unchanged.
                # atomic_write_text for the CRLF reason noted on packaging.yaml
                # above — a scaffold must be byte-identical on every platform.
                atomic_write_text(bpath, BIBLE_SCAFFOLD_HEADERS[bible_file])
        # the idea stage (§2: creation belongs to the director) gets scaffolds
        # so a takeover always finds the same three files in the same order
        story_templates = {
            "brief.md": "# 一句话创意\n\n<!-- 一句话说清:谁、在哪、发生什么、为什么抓人 -->\n",
            "outline.md": "# 大纲\n\n<!-- 三幕/起承转合;每行一个节拍,后续一节拍≈一镜头 -->\n",
            "script.md": "# 剧本\n\n<!-- 分场与对白;对白会成为 shots/*.yaml 的 dialogue.text -->\n",
        }
        for fname, template in story_templates.items():
            spath = root / "story" / fname
            if not spath.exists():
                # LF on every platform — same reason as the scaffolds above.
                atomic_write_text(spath, template)
        atomic_write_text(root / ".gitignore", GITIGNORE)
        (root / "events.jsonl").touch()

        if git_init and shutil.which("git") and not (root / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=root, check=False, capture_output=True)

        return cls(root)

    # ----------------------------------------------------------------- paths

    @property
    def shots_dir(self) -> Path:
        return self.root / "shots"

    @property
    def imports_dir(self) -> Path:
        return self.root / "media" / "imports"

    @property
    def story_imports_dir(self) -> Path:
        # Text drops (novels, treatments) land here via `manju import` so the
        # agent can adapt them into story/*.md — tracked truth text, not media.
        return self.root / "story" / "imports"

    @property
    def gen_dir(self) -> Path:
        return self.root / "media" / "gen"

    @property
    def refs_dir(self) -> Path:
        return self.root / "media" / "refs"

    @property
    def timeline_path(self) -> Path:
        return self.root / "timeline" / "timeline.json"

    @property
    def timeline_generated_path(self) -> Path:
        return self.root / "timeline" / "timeline.generated.json"

    @property
    def rules_path(self) -> Path:
        return self.root / "timeline" / "rules.yaml"

    @property
    def packaging_path(self) -> Path:
        # Packaging spec (intro/outro/cover/teaser/info cards). `manju new`
        # scaffolds it all-disabled; a preset carrying packaging overwrites it.
        return self.root / "timeline" / "packaging.yaml"

    @property
    def captions_dir(self) -> Path:
        return self.root / "captions"

    @property
    def segments_dir(self) -> Path:
        return self.root / "renders" / "segments"

    @property
    def proxy_dir(self) -> Path:
        return self.root / "renders" / "proxy"

    @property
    def final_dir(self) -> Path:
        return self.root / "renders" / "final"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def proposals_dir(self) -> Path:
        return self.root / "proposals"

    @property
    def runtime_dir(self) -> Path:
        return self.root / ".manju"

    def resolve(self, relpath: str | Path) -> Path:
        """Project-relative path -> absolute. Rejects escapes above the root."""
        p = (self.root / relpath).resolve()
        if not p.is_relative_to(self.root):
            raise ProjectError(f"path escapes project root: {relpath}")
        return p

    def relpath(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.root).as_posix()

    def safe_served_path(self, rel: str, prefixes: tuple[str, ...]) -> Path | None:
        """The ONE file-serving containment gate shared by the GUI and board
        media routes (Audit 14). Resolve a project-relative served path behind a
        prefix allowlist, refusing any escape above the root AND anything outside
        ``prefixes``. Returns ``None`` on ANY refusal — each caller maps that to
        its own 403 / ``ValueError``, so a future hardening cannot be one-sided.

        The allowlist is checked BEFORE resolving (a cheap reject) AND AGAIN on
        the resolved relpath, so ``media/../x`` (which stays inside the root) and
        a symlink out of an allowed subtree cannot sidestep the prefix gate.
        :meth:`resolve` collapses ``..`` and follows symlinks against the
        already-``.resolve()``d root, so containment is symlink-aware. Callers
        pass their OWN prefix tuple (the two surfaces legitimately allow
        different subtrees) and, if they accept percent-encoded input, ``unquote``
        before calling (the board does; the GUI router already has)."""
        rel = rel.lstrip("/")
        if not any(rel.startswith(p) for p in prefixes):
            return None
        try:
            abspath = self.resolve(rel)  # join → resolve → is_relative_to(root)
        except ProjectError:
            return None
        if not any(self.relpath(abspath).startswith(p) for p in prefixes):
            return None
        return abspath

    # ---------------------------------------------------------------- config

    def load_config(self) -> ProjectConfig:
        return ProjectConfig.model_validate(read_yaml(self.root / PROJECT_FILE) or {})

    def save_config(self, config: ProjectConfig) -> None:
        self.verify_manju_identity()  # CLI-P0-001 write-before verification
        write_yaml(self.root / PROJECT_FILE, config.model_dump(exclude_none=True))

    def edit_rate(self, config: ProjectConfig | None = None) -> Rate:
        """The project's exact edit rate as a :class:`~manju.core.timebase.Rate`
        — the SINGLE accessor for the declared project.fps int->rational
        migration (stage 1).

        Truth precedence: the rational ``edit_rate`` field when project.yaml
        declares one (``ProjectConfig`` guarantees it agrees with ``fps``), else
        the legacy integer ``fps`` promoted to an exact whole-number ``Rate``.
        The result is always a ``Rate`` whose ``nominal_int`` equals ``fps``, so
        this is a safe drop-in wherever an exact rate is wanted while every
        existing consumer keeps reading the plain ``fps`` int unchanged.

        Pass an already-loaded ``config`` to avoid re-reading project.yaml;
        omitted, it is loaded fresh (mirrors how ``fps`` is read via
        ``load_config().fps`` today). NOTHING in the engine consumes this yet —
        the migration is staged (R2+ opt the frame grid / captions / audio in).
        """
        config = config if config is not None else self.load_config()
        if config.edit_rate is not None:
            return config.edit_rate.rate
        return Rate.from_fraction(config.fps)

    # ----------------------------------------------------------------- shots

    def load_index(self) -> ShotIndex:
        path = self.shots_dir / "index.yaml"
        return ShotIndex.model_validate(read_yaml(path) or {}) if path.exists() else ShotIndex()

    def save_index(self, index: ShotIndex) -> None:
        self.verify_manju_identity()  # CLI-P0-001 write-before verification
        write_yaml(self.shots_dir / "index.yaml", index.model_dump())

    def shot_ids(self, *, indexed_only: bool = False) -> list[str]:
        """Order from index.yaml, then any shot files not yet in the index.

        ``indexed_only=True`` (round W, review #5) returns ONLY
        ``shots/index.yaml`` order — a shot that exists on disk but was never
        added to the index is excluded entirely. ``index.yaml`` is the order
        authority; ``manju check`` still WARNS about an unindexed shot (it is
        not silently ignored — see core/check.py), but build/timeline must not
        treat a draft shot as part of the film without an explicit opt-in."""
        ordered = list(self.load_index().order)
        if indexed_only:
            return ordered
        on_disk = sorted(
            p.stem for p in self.shots_dir.glob("*.yaml") if p.name != "index.yaml"
        )
        return ordered + [s for s in on_disk if s not in ordered]

    def _safe_shot_id(self, shot_id: str) -> str:
        """The ONE choke point every shot-id-to-path caller passes through
        (goal item 11) — CLI/MCP/board/GUI arguments, index.yaml entries, and
        model ids all end up here via ``shot_path``/``takes_dir``."""
        try:
            return validate_safe_segment(shot_id, label="shot_id")
        except UnsafeIdentifierError as exc:
            raise ProjectError(str(exc)) from exc

    def shot_path(self, shot_id: str) -> Path:
        return self.shots_dir / f"{self._safe_shot_id(shot_id)}.yaml"

    def load_shot_raw(self, shot_id: str) -> dict[str, Any]:
        """Raw YAML dict — locks are verified against this, not the model,
        so that model defaults can never mask a tampered file."""
        path = self.shot_path(shot_id)
        if not path.exists():
            raise ProjectError(f"shot file not found: {path.name}")
        data = read_yaml(path) or {}
        if not isinstance(data, dict):
            raise ProjectError(f"shot file is not a mapping: {path.name}")
        return data

    def load_shot(self, shot_id: str) -> ShotSpec:
        data = self.load_shot_raw(shot_id)
        data.setdefault("id", shot_id)
        return ShotSpec.model_validate(data)

    def save_shot(self, shot: ShotSpec) -> None:
        self.verify_manju_identity()  # CLI-P0-001 write-before verification
        write_yaml(self.shot_path(shot.id), shot.model_dump(exclude_none=True))

    def update_shot_raw(self, shot_id: str, mutate) -> dict[str, Any]:
        """Edit a shot file as a raw dict and write it back.

        Engine-side writes (select, lock, approve) go through here instead of
        the model so a round-trip never normalizes values a human wrote —
        a lock sealed over `duration: 3` must not break because the model
        would re-emit `3.0`.
        """
        self.verify_manju_identity()  # CLI-P0-001 write-before verification
        data = self.load_shot_raw(shot_id)
        mutate(data)
        write_yaml(self.shot_path(shot_id), data)
        return data

    # ----------------------------------------------------------------- bible

    def load_bible(self) -> dict[str, dict[str, Any]]:
        """Flat id -> entry map merged across the first-class bible files
        (BIBLE_FILES: characters/scenes/props/style/voices). Duplicate ids
        across files are a check error, handled in checks."""
        merged: dict[str, dict[str, Any]] = {}
        for fname in BIBLE_FILES:
            path = self.root / "bible" / f"{fname}.yaml"
            if not path.exists():
                continue
            data = read_yaml(path) or {}
            if not isinstance(data, dict):
                continue
            for key, entry in data.items():
                if isinstance(entry, dict):
                    merged[key] = entry
        return merged

    # ----------------------------------------------------------------- rules

    def load_rules(self) -> TimelineRules:
        if not self.rules_path.exists():
            return TimelineRules()
        return TimelineRules.model_validate(read_yaml(self.rules_path) or {})

    def save_rules(self, rules: TimelineRules) -> None:
        self.verify_manju_identity()  # CLI-P0-001 write-before verification
        write_yaml(self.rules_path, rules.model_dump())

    # ------------------------------------------------------------ packaging

    def load_packaging(self) -> PackagingSpec:
        """Packaging kit spec (round-N). Defaults to an all-disabled spec when
        packaging.yaml is absent — the compiler treats that as no-op (§13-14)."""
        if not self.packaging_path.exists():
            return PackagingSpec()
        return PackagingSpec.model_validate(read_yaml(self.packaging_path) or {})

    def save_packaging(self, packaging: PackagingSpec) -> None:
        self.verify_manju_identity()  # CLI-P0-001 write-before verification
        write_yaml(self.packaging_path, packaging.model_dump())

    # -------------------------------------------------------------- timeline

    def load_timeline(self) -> Timeline | None:
        if not self.timeline_path.exists():
            return None
        return Timeline.model_validate(read_json(self.timeline_path))

    def save_timeline(self, timeline: Timeline, *, generated_only: bool = False) -> Path:
        self.verify_manju_identity()  # CLI-P0-001 write-before verification
        target = self.timeline_generated_path if generated_only else self.timeline_path
        write_json(target, timeline.model_dump())
        return target

    # ----------------------------------------------------------------- takes

    def takes_dir(self, shot_id: str) -> Path:
        return self.gen_dir / self._safe_shot_id(shot_id)

    def takes(self, shot_id: str, *, skip_ghosts: bool = False) -> list[TakeInfo]:
        """Every take sidecar under this shot's take dir, media resolved by
        matching extension (``media_path`` is ``None`` when the sidecar exists
        but no media file does).

        ``skip_ghosts=True`` (round-W #35) additionally drops any sidecar with
        no matching media at all from the returned list — for a human-facing
        LISTING (candidate galleries, "pick a take" prompts, numbering) a
        media-less sidecar is noise, typically left behind by an interrupted
        write or (before this round) ``gc --hard`` deleting a take's media but
        not its sidecar. The DEFAULT stays ``False`` because several existing
        callers need to see a media-less entry to correctly report it (build
        staleness marks the SELECTED take BROKEN when its media is gone, spend
        accounting still owes money already spent on a take whose media was
        later reclaimed, ...) — this is purely an additive, opt-in filter."""
        tdir = self.takes_dir(shot_id)
        if not tdir.exists():
            return []
        infos: list[TakeInfo] = []
        for sidecar_path in sorted(tdir.glob("take_*.yaml")):
            name = sidecar_path.stem
            error: str | None = None
            try:
                raw = read_yaml(sidecar_path) or {}
            except Exception as exc:  # unparseable YAML: degrade, never crash
                raw, error = {}, f"take sidecar 无法解析: {' '.join(str(exc).split())}"
            if not isinstance(raw, dict):
                raw, error = {}, "take sidecar 不是映射 (not a mapping)"
            sidecar: TakeSidecar | None = None
            if error is None:
                try:
                    sidecar = TakeSidecar.model_validate(raw)
                except ValidationError as exc:
                    # Round W (issue #22): TakeSidecar's model validator rejects an
                    # illegal source_in_ms/source_out_ms window (negative in-point,
                    # reversed/zero-length window). Re-validate WITHOUT those two
                    # fields to get a SAFE (whole-file) sidecar instead of dropping
                    # the take or crashing every caller of Project.takes()/
                    # get_take() (build/status/GUI polling all call this). The
                    # reason rides on TakeInfo.error for `manju check` / QC to
                    # surface as a real finding (core/check.py, qc/checks.py).
                    error = " ".join(str(exc).split())
            if sidecar is None:
                safe_raw = {
                    k: v for k, v in raw.items()
                    if k not in ("source_in_ms", "source_out_ms")
                }
                try:
                    sidecar = TakeSidecar.model_validate(safe_raw)
                except ValidationError:
                    # Required fields (provider/spec_hash) missing or mistyped —
                    # a truncated/hand-broken sidecar. A placeholder keeps the
                    # take VISIBLE (with the error recorded) instead of taking
                    # down `manju check`/status/build staleness/GUI polling.
                    sidecar = TakeSidecar.model_validate({
                        k: v for k, v in {
                            "provider": str(raw.get("provider") or "unknown"),
                            "spec_hash": str(raw.get("spec_hash") or "unknown"),
                            "created_at": raw.get("created_at")
                            if isinstance(raw.get("created_at"), str) else None,
                        }.items() if v is not None
                    })
            # Round Y (review #4): resolve media by MEDIA_EXTS PRIORITY ORDER,
            # deterministic across processes. If more than one media file exists
            # for this stem the pick is still stable (first by priority) AND the
            # ambiguity is recorded so `manju check` can flag it — an accident a
            # human should notice, not silently absorb.
            present = [ext for ext in MEDIA_EXTS if (tdir / (name + ext)).exists()]
            media = (tdir / (name + present[0])) if present else None
            if len(present) > 1 and error is None:
                error = (f"take 有多个媒体文件({', '.join(name + e for e in present)})"
                         f"— 已按优先级选用 {name + present[0]};请删除多余的,避免歧义")
            if skip_ghosts and media is None:
                continue
            infos.append(TakeInfo(shot_id, name, media, sidecar_path, sidecar, error=error))
        return infos

    def get_take(self, shot_id: str, take_name: str) -> TakeInfo | None:
        return next((t for t in self.takes(shot_id) if t.name == take_name), None)

    def next_take_name(self, shot_id: str) -> str:
        existing = [t.name for t in self.takes(shot_id)]
        # Also count ORPHAN media — a ``take_NN.<ext>`` with no sidecar, left by
        # a crash between the media write and the sidecar write in register_take
        # (or a hand-dropped file). ``takes()`` numbers from ``*.yaml`` sidecars
        # only, but register_take's O_EXCL reservation collides on the media
        # file; without counting it here the number never advances and
        # register_take permanently fails ("could not allocate exclusive take
        # name"). Append-only: an occupied slot is never reused. When media and
        # sidecar are paired (the normal case) this adds no new numbers.
        tdir = self.takes_dir(shot_id)
        if tdir.exists():
            existing += [
                p.stem for p in tdir.glob("take_*")
                if p.suffix.lower() in MEDIA_EXTS
            ]
        nums = [int(m.group(1)) for n in existing if (m := re.match(r"take_(\d+)$", n))]
        return f"take_{(max(nums, default=0) + 1):02d}"

    def register_take(self, shot_id: str, media_file: Path, sidecar: TakeSidecar, *,
                      move: bool = False) -> TakeInfo:
        """Append-only registration: always mints a fresh take name; never
        overwrites. `move=False` copies (imports are never consumed).

        R2-P1-7: exclusive create (``O_EXCL``) closes the check-then-copy race
        (same discipline as :meth:`register_voice_take`).
        """
        self.verify_manju_identity()  # CLI-P0-001 write-before verification
        media_file = Path(media_file)
        if not media_file.exists():
            raise ProjectError(f"take media not found: {media_file}")
        suffix = media_file.suffix.lower()
        # CORE-002: `takes()` resolves a take's media STRICTLY through
        # MEDIA_EXTS, so registering any other extension mints a take whose
        # media_path is None forever — inside the append-only namespace, where
        # it is painful to clean up. Refuse BEFORE anything is written (the
        # check belongs in core, not only in the provider doctor: a misfilled
        # local_cmd `output_ext`, a hand-passed .gif/.webp/.avi and every other
        # caller must hit the same wall).
        if suffix not in MEDIA_EXTS:
            raise ProjectError(
                f"take media 扩展名 {suffix or '(无)'} 不是可识别的媒体类型 "
                f"({', '.join(MEDIA_EXTS)}):{media_file} — 注册后 takes() 永远"
                f"解析不到它的媒体文件,已拒绝写入 media/gen(append-only,难以清理)"
            )
        tdir = self.takes_dir(shot_id)
        tdir.mkdir(parents=True, exist_ok=True)
        use_move = bool(move and self.imports_dir not in media_file.parents)
        dest: Path | None = None
        name = ""
        for _ in range(32):
            name = self.next_take_name(shot_id)
            candidate = tdir / (name + suffix)
            try:
                fd = os.open(str(candidate), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                continue
            try:
                if use_move:
                    os.close(fd)
                    # Claimed the name exclusively; move into place WITHOUT
                    # releasing the claim: unlink-then-move would reopen the
                    # exact allocation race O_EXCL closed (a concurrent
                    # register could re-mint the freed name and be silently
                    # overwritten by our move). Move to a temp sibling, then
                    # os.replace over our own placeholder — atomic on both
                    # POSIX and Windows, and the name is never unclaimed.
                    tmp_move = candidate.with_name(candidate.name + f".{os.getpid()}.moving")
                    try:
                        shutil.move(str(media_file), tmp_move)
                        os.replace(tmp_move, candidate)
                    except BaseException:
                        # never lose the source: put a completed temp move back
                        try:
                            if tmp_move.exists() and not media_file.exists():
                                shutil.move(str(tmp_move), media_file)
                            else:
                                tmp_move.unlink(missing_ok=True)
                        except OSError:
                            pass
                        raise
                else:
                    # CORE-001: STREAM the copy. Reading the source into one
                    # bytes object first made peak RSS track file size, and the
                    # copy path is the DEFAULT (imports are never consumed) —
                    # a multi-GiB camera/render take paged or OOM'd the box
                    # AFTER generation had already succeeded. The O_EXCL fd is
                    # wrapped (not re-opened), so the no-overwrite claim on the
                    # take name is exactly the one made above.
                    with os.fdopen(fd, "wb") as out:
                        with open(media_file, "rb") as src:
                            shutil.copyfileobj(src, out, 1 << 20)
                        out.flush()
                        os.fsync(out.fileno())
            except BaseException:
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            dest = candidate
            break
        if dest is None:
            raise ProjectError(
                f"could not allocate exclusive take name under {tdir}"
            )
        sidecar.created_at = sidecar.created_at or datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        sidecar_path = tdir / f"{name}.yaml"
        write_yaml(sidecar_path, sidecar.model_dump(exclude_none=True))
        return TakeInfo(shot_id, name, dest, sidecar_path, sidecar)

    # ----------------------------------------------------------- voice takes

    def _voice_takes_dir(self, shot_id: str, lang: str | None = None) -> Path:
        """Base voice dir is ``media/gen/<shot>/`` (byte-identical default).
        Locale overlay: ``media/gen/<shot>/locales/<lang>/`` (WP4).

        ``lang`` is validated via :func:`core.locale.validate_lang` so a raw
        ``..`` / path separator can never escape the shot tree (P0-3).
        """
        base = self.takes_dir(shot_id)
        if not lang:
            return base
        from .locale import validate_lang

        safe = validate_lang(str(lang))
        return base / "locales" / safe

    def voice_takes(self, shot_id: str, *,
                    lang: str | None = None
                    ) -> list[tuple[Path, "VoiceTakeSidecar | None"]]:
        """Voice takes for a shot, OLDEST FIRST (append-only numbering); the
        newest one is what the compiler uses. A media file without a sidecar
        (hand-dropped) pairs with None — manual voice, never auto-invalidated.

        ``lang=None`` (default) scans the base take dir only — projects with
        no locales stay byte-identical. ``lang=<code>`` scans the per-locale
        overlay directory (WP4)."""
        from .models import VoiceTakeSidecar

        tdir = self._voice_takes_dir(shot_id, lang)
        if not tdir.exists():
            return []
        results: list[tuple[Path, VoiceTakeSidecar | None]] = []
        audio_exts = (".wav", ".mp3", ".m4a", ".flac")

        def _num_key(p: Path) -> tuple[int, str]:
            # NUMERIC take order: "newest wins" consumers read [-1], and a
            # lexicographic sort puts voice_take_100 BEFORE voice_take_99 —
            # the same class as the final_v9-over-final_v10 round-N finding
            # (see newest_final_path). Non-matching names keep a stable tail.
            m = re.match(r"voice_take_(\d+)$", p.stem)
            return (int(m.group(1)) if m else (1 << 30), p.name)

        for media in sorted(tdir.glob("voice_take_*.*"), key=_num_key) \
                + sorted(tdir.glob("voice.*")):
            if media.suffix.lower() not in audio_exts:
                continue
            # Skip nested locales/* when scanning base (lang=None)
            if lang is None and "locales" in media.parts:
                continue
            sidecar_path = tdir / f"{media.stem}.sidecar.yaml"
            sidecar = None
            if sidecar_path.exists():
                try:
                    sidecar = VoiceTakeSidecar.model_validate(read_yaml(sidecar_path) or {})
                except Exception:
                    sidecar = None
            results.append((media, sidecar))
        return results

    def next_voice_take_name(self, shot_id: str, *, lang: str | None = None) -> str:
        nums = [
            int(m.group(1))
            for media, _ in self.voice_takes(shot_id, lang=lang)
            if (m := re.match(r"voice_take_(\d+)$", media.stem))
        ]
        return f"voice_take_{(max(nums, default=0) + 1):02d}"

    def register_voice_take(self, shot_id: str, media_file: Path,
                            sidecar: "VoiceTakeSidecar", *,
                            lang: str | None = None) -> Path:
        """Append-only registration of a generated voice take. The sidecar
        file is <name>.sidecar.yaml (NOT <name>.yaml, which would collide with
        the video-take sidecar namespace scanned by takes()).

        ``lang`` (WP4, default None): register under
        ``media/gen/<shot>/locales/<lang>/`` so base and locale voices never
        clobber each other. ``lang=None`` keeps existing paths byte-identical.
        """
        from datetime import datetime, timezone

        self.verify_manju_identity()  # CLI-P0-001 write-before verification
        media_file = Path(media_file)
        if not media_file.exists():
            raise ProjectError(f"voice media not found: {media_file}")
        tdir = self._voice_takes_dir(shot_id, lang)
        tdir.mkdir(parents=True, exist_ok=True)
        suffix = media_file.suffix.lower()
        # Exclusive create closes the check-then-copy race (P1-5): two
        # concurrent register_voice_take calls can no longer both pass
        # ``if dest.exists()`` and then overwrite via shutil.copy2.
        dest: Path | None = None
        name = ""
        for _ in range(32):
            name = self.next_voice_take_name(shot_id, lang=lang)
            candidate = tdir / (name + suffix)
            try:
                fd = os.open(str(candidate), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                continue
            try:
                # CORE-001 (same finding as register_take): stream through the
                # exclusively-created fd instead of materializing the whole
                # source in RAM — a long dubbing/ASR-sliced wav is not small.
                with os.fdopen(fd, "wb") as out:
                    with open(media_file, "rb") as src:
                        shutil.copyfileobj(src, out, 1 << 20)
                    out.flush()
                    os.fsync(out.fileno())
            except BaseException:
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            dest = candidate
            break
        if dest is None:
            raise ProjectError(
                f"could not allocate exclusive voice take name under {tdir}"
            )
        sidecar.created_at = sidecar.created_at or datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        write_yaml(tdir / f"{name}.sidecar.yaml", sidecar.model_dump(exclude_none=True))
        return dest

    # ---------------------------------------------------------------- final

    def next_final_path(self, prefix: str = "final", ext: str = ".mp4") -> Path:
        nums = [
            int(m.group(1))
            for p in self.final_dir.glob(f"{prefix}_v*{ext}")
            if (m := re.match(rf"{prefix}_v(\d+)$", p.stem))
        ]
        return self.final_dir / f"{prefix}_v{max(nums, default=0) + 1}{ext}"

    def newest_final_path(self, prefix: str = "final", ext: str = ".mp4") -> Path | None:
        """The numerically-highest final_vN, or None. The one resolver every
        reader must use: a lexicographic glob-sort picks final_v9 over
        final_v10 (round-N review finding)."""
        versions = [
            (int(m.group(1)), p)
            for p in self.final_dir.glob(f"{prefix}_v*{ext}")
            if (m := re.match(rf"{prefix}_v(\d+)$", p.stem))
        ]
        return max(versions, key=lambda t: t[0])[1] if versions else None


# --------------------------------------------------------------------- scaffold


def scaffold_shots(project: Project, n: int, *, start: int = 1) -> list[str]:
    """Scaffold ``n`` empty shot skeletons ``S{start:03d}…`` and index them.

    The cargo-new / npm-init pattern: hand the human a correctly-shaped file to
    fill in, not a blank page. Scaffolding is not authoring — the engine still
    never writes CONTENT (§2: creation belongs to the director); every field
    here is empty or a placeholder the human replaces.

    Discipline:

    - never overwrites: an id whose ``shots/<id>.yaml`` already exists is left
      exactly as-is and skipped (not returned, not re-indexed);
    - the template is written as raw text via
      :func:`~manju.core.yamlio.atomic_write_text`, NOT :func:`write_yaml` — a
      YAML dump would strip the guidance comments that tell the human what each
      field is for;
    - the skeleton PASSES ``manju check`` on a fresh, empty-Bible project:
      ``scene`` is left empty and ``characters`` empty, and
      :class:`~manju.core.models.ShotSpec` types ``scene`` as ``str | None`` —
      so an empty scene reads as ``None`` and referential-integrity checks find
      nothing dangling to complain about;
    - newly created ids are appended to ``shots/index.yaml`` order (existing
      order preserved, never duplicated).

    Returns the ids actually created — skipped pre-existing ids excluded — in
    scaffold order.
    """
    from .yamlio import atomic_write_text

    created: list[str] = []
    for i in range(start, start + max(0, n)):
        sid = f"S{i:03d}"
        path = project.shot_path(sid)
        if path.exists():
            continue  # never overwrite a human's file
        skeleton = (
            f"# {sid} — 骨架待填,引擎从不代写内容(§2:创作属于导演)\n"
            f"id: {sid}\n"
            "scene:  # bible/scenes.yaml 中的场景 id(留空=未定,先过 check)\n"
            "characters: []  # bible/characters.yaml 中的角色 id 列表\n"
            "dialogue:\n"
            '  speaker: ""  # 说话的角色 id\n'
            '  text: ""  # 台词原文,后续成为字幕/配音源\n'
            "duration: auto  # auto=引擎按节奏定时;或填秒数(如 3)\n"
        )
        atomic_write_text(path, skeleton)
        created.append(sid)

    if created:
        index = project.load_index()
        for sid in created:
            if sid not in index.order:
                index.order.append(sid)
        project.save_index(index)
    return created
