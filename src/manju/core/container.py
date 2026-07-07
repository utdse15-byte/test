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

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    PackagingSpec,
    ProjectConfig,
    ShotIndex,
    ShotSpec,
    TakeSidecar,
    Timeline,
    TimelineRules,
)
from .yamlio import dump_yaml, read_json, read_yaml, write_json, write_yaml

PROJECT_FILE = "project.yaml"
MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".png", ".jpg", ".jpeg", ".wav", ".mp3", ".m4a", ".flac"}

# The bible is a generic dict-of-files keyed by id (§4). This is the ONE list
# of first-class bible files — scaffolded at `manju new`, merged by load_bible,
# and lock-verified by `manju check`. Adding a file here makes it first-class
# everywhere at once (goal item 11: props.yaml and voices.yaml).
BIBLE_FILES = ("characters", "scenes", "props", "style", "voices")

GITIGNORE = """\
# Derived and heavy artifacts stay out of git; truth text goes in (§3)
media/gen/**/*.mp4
media/gen/**/*.mov
media/gen/**/*.png
media/gen/**/*.jpg
media/gen/**/*.wav
media/gen/**/*.mp3
media/generated/
media/imports/
renders/
exports/
.manju/
"""

# packaging.yaml scaffold header — commented hints for a file that turns nothing
# on by default (round-N). Enabling any section changes build/package output.
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


class ProjectError(RuntimeError):
    pass


class Project:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        if not (self.root / PROJECT_FILE).exists():
            raise ProjectError(f"not a manju project (no {PROJECT_FILE}): {self.root}")

    # ------------------------------------------------------------ discovery

    @classmethod
    def find(cls, start: Path | str = ".") -> "Project":
        p = Path(start).resolve()
        for candidate in [p, *p.parents]:
            if (candidate / PROJECT_FILE).exists():
                return cls(candidate)
        raise ProjectError(f"no manju project found from {p} upward")

    # ------------------------------------------------------------- creation

    @classmethod
    def create(cls, path: Path | str, name: str | None = None, *, vertical: bool = True,
               git_init: bool = True) -> "Project":
        root = Path(path).resolve()
        if root.suffix != ".manju":
            root = root.with_name(root.name + ".manju")
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
        config = ProjectConfig(name=name, width=width, height=height)
        write_yaml(root / PROJECT_FILE, config.model_dump(exclude_none=True))
        write_yaml(root / "shots" / "index.yaml", ShotIndex().model_dump())
        write_yaml(root / "timeline" / "rules.yaml", TimelineRules().model_dump())
        # packaging.yaml sits next to rules.yaml, everything disabled + hints (§13-14)
        (root / "timeline" / "packaging.yaml").write_text(
            PACKAGING_SCAFFOLD_HEADER + dump_yaml(PackagingSpec().model_dump()),
            encoding="utf-8",
        )
        for bible_file in BIBLE_FILES:
            bpath = root / "bible" / f"{bible_file}.yaml"
            if not bpath.exists():
                write_yaml(bpath, {})
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
                spath.write_text(template, encoding="utf-8")
        (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
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

    # ---------------------------------------------------------------- config

    def load_config(self) -> ProjectConfig:
        return ProjectConfig.model_validate(read_yaml(self.root / PROJECT_FILE) or {})

    def save_config(self, config: ProjectConfig) -> None:
        write_yaml(self.root / PROJECT_FILE, config.model_dump(exclude_none=True))

    # ----------------------------------------------------------------- shots

    def load_index(self) -> ShotIndex:
        path = self.shots_dir / "index.yaml"
        return ShotIndex.model_validate(read_yaml(path) or {}) if path.exists() else ShotIndex()

    def save_index(self, index: ShotIndex) -> None:
        write_yaml(self.shots_dir / "index.yaml", index.model_dump())

    def shot_ids(self) -> list[str]:
        """Order from index.yaml, then any shot files not yet in the index."""
        ordered = list(self.load_index().order)
        on_disk = sorted(
            p.stem for p in self.shots_dir.glob("*.yaml") if p.name != "index.yaml"
        )
        return ordered + [s for s in on_disk if s not in ordered]

    def shot_path(self, shot_id: str) -> Path:
        return self.shots_dir / f"{shot_id}.yaml"

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
        write_yaml(self.shot_path(shot.id), shot.model_dump(exclude_none=True))

    def update_shot_raw(self, shot_id: str, mutate) -> dict[str, Any]:
        """Edit a shot file as a raw dict and write it back.

        Engine-side writes (select, lock, approve) go through here instead of
        the model so a round-trip never normalizes values a human wrote —
        a lock sealed over `duration: 3` must not break because the model
        would re-emit `3.0`.
        """
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
        write_yaml(self.rules_path, rules.model_dump())

    # ------------------------------------------------------------ packaging

    def load_packaging(self) -> PackagingSpec:
        """Packaging kit spec (round-N). Defaults to an all-disabled spec when
        packaging.yaml is absent — the compiler treats that as no-op (§13-14)."""
        if not self.packaging_path.exists():
            return PackagingSpec()
        return PackagingSpec.model_validate(read_yaml(self.packaging_path) or {})

    def save_packaging(self, packaging: PackagingSpec) -> None:
        write_yaml(self.packaging_path, packaging.model_dump())

    # -------------------------------------------------------------- timeline

    def load_timeline(self) -> Timeline | None:
        if not self.timeline_path.exists():
            return None
        return Timeline.model_validate(read_json(self.timeline_path))

    def save_timeline(self, timeline: Timeline, *, generated_only: bool = False) -> Path:
        target = self.timeline_generated_path if generated_only else self.timeline_path
        write_json(target, timeline.model_dump())
        return target

    # ----------------------------------------------------------------- takes

    def takes_dir(self, shot_id: str) -> Path:
        return self.gen_dir / shot_id

    def takes(self, shot_id: str) -> list[TakeInfo]:
        tdir = self.takes_dir(shot_id)
        if not tdir.exists():
            return []
        infos: list[TakeInfo] = []
        for sidecar_path in sorted(tdir.glob("take_*.yaml")):
            name = sidecar_path.stem
            sidecar = TakeSidecar.model_validate(read_yaml(sidecar_path) or {})
            media = next(
                (tdir / (name + ext) for ext in MEDIA_EXTS if (tdir / (name + ext)).exists()),
                None,
            )
            infos.append(TakeInfo(shot_id, name, media, sidecar_path, sidecar))
        return infos

    def get_take(self, shot_id: str, take_name: str) -> TakeInfo | None:
        return next((t for t in self.takes(shot_id) if t.name == take_name), None)

    def next_take_name(self, shot_id: str) -> str:
        existing = [t.name for t in self.takes(shot_id)]
        nums = [int(m.group(1)) for n in existing if (m := re.match(r"take_(\d+)$", n))]
        return f"take_{(max(nums, default=0) + 1):02d}"

    def register_take(self, shot_id: str, media_file: Path, sidecar: TakeSidecar, *,
                      move: bool = False) -> TakeInfo:
        """Append-only registration: always mints a fresh take name; never
        overwrites. `move=False` copies (imports are never consumed)."""
        media_file = Path(media_file)
        if not media_file.exists():
            raise ProjectError(f"take media not found: {media_file}")
        tdir = self.takes_dir(shot_id)
        tdir.mkdir(parents=True, exist_ok=True)
        name = self.next_take_name(shot_id)
        dest = tdir / (name + media_file.suffix.lower())
        if dest.exists():  # paranoia: append-only means never clobber
            raise ProjectError(f"refusing to overwrite existing take media: {dest}")
        if move and self.imports_dir not in media_file.parents:
            shutil.move(str(media_file), dest)
        else:
            shutil.copy2(media_file, dest)
        sidecar.created_at = sidecar.created_at or datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        sidecar_path = tdir / f"{name}.yaml"
        write_yaml(sidecar_path, sidecar.model_dump(exclude_none=True))
        return TakeInfo(shot_id, name, dest, sidecar_path, sidecar)

    # ----------------------------------------------------------- voice takes

    def voice_takes(self, shot_id: str) -> list[tuple[Path, "VoiceTakeSidecar | None"]]:
        """Voice takes for a shot, OLDEST FIRST (append-only numbering); the
        newest one is what the compiler uses. A media file without a sidecar
        (hand-dropped) pairs with None — manual voice, never auto-invalidated."""
        from .models import VoiceTakeSidecar

        tdir = self.takes_dir(shot_id)
        if not tdir.exists():
            return []
        results: list[tuple[Path, VoiceTakeSidecar | None]] = []
        audio_exts = (".wav", ".mp3", ".m4a", ".flac")
        for media in sorted(tdir.glob("voice_take_*.*")) + sorted(tdir.glob("voice.*")):
            if media.suffix.lower() not in audio_exts:
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

    def next_voice_take_name(self, shot_id: str) -> str:
        nums = [
            int(m.group(1))
            for media, _ in self.voice_takes(shot_id)
            if (m := re.match(r"voice_take_(\d+)$", media.stem))
        ]
        return f"voice_take_{(max(nums, default=0) + 1):02d}"

    def register_voice_take(self, shot_id: str, media_file: Path,
                            sidecar: "VoiceTakeSidecar") -> Path:
        """Append-only registration of a generated voice take. The sidecar
        file is <name>.sidecar.yaml (NOT <name>.yaml, which would collide with
        the video-take sidecar namespace scanned by takes())."""
        from datetime import datetime, timezone

        media_file = Path(media_file)
        if not media_file.exists():
            raise ProjectError(f"voice media not found: {media_file}")
        tdir = self.takes_dir(shot_id)
        tdir.mkdir(parents=True, exist_ok=True)
        name = self.next_voice_take_name(shot_id)
        dest = tdir / (name + media_file.suffix.lower())
        if dest.exists():
            raise ProjectError(f"refusing to overwrite existing voice take: {dest}")
        shutil.copy2(media_file, dest)
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
