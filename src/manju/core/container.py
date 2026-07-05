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
    ProjectConfig,
    ShotIndex,
    ShotSpec,
    TakeSidecar,
    Timeline,
    TimelineRules,
)
from .yamlio import read_json, read_yaml, write_json, write_yaml

PROJECT_FILE = "project.yaml"
MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".png", ".jpg", ".jpeg", ".wav", ".mp3", ".m4a", ".flac"}

GITIGNORE = """\
# Derived and heavy artifacts stay out of git; truth text goes in (§3)
media/gen/**/*.mp4
media/gen/**/*.mov
media/gen/**/*.png
media/gen/**/*.jpg
media/gen/**/*.wav
media/gen/**/*.mp3
media/imports/
renders/
exports/
.manju/
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
        for bible_file in ("characters", "scenes", "props", "style"):
            bpath = root / "bible" / f"{bible_file}.yaml"
            if not bpath.exists():
                write_yaml(bpath, {})
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
        """Flat id -> entry map merged across characters/scenes/props/style.
        Duplicate ids across files are a check error, handled in checks."""
        merged: dict[str, dict[str, Any]] = {}
        for fname in ("characters", "scenes", "props", "style"):
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
