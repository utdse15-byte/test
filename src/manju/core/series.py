"""Series — the multi-episode UMBRELLA over ordinary episode projects (goal V-3).

A *series* is scaffolding, not a new engine. Its layout is::

    <series>/
      series.yaml            # the umbrella marker (SeriesConfig)
      bible/                 # the GLOBAL series bible (same file set as a project)
      script/                # the long-script home (split-script reads from here)
      reports/               # series-level aggregation outputs
      events.jsonl           # the series collaboration log (§10, reused verbatim)
      episodes/
        E01.manju/           # a COMPLETELY NORMAL .manju project (Project.create'd)
        E02.manju/
        …

The architecture is deliberately fixed (round V hard rule 2): the single-project
engine is untouched. An episode is a plain project that works standalone with
EVERY existing command (`check` / `build` / `status` / …). This module only adds

  1. scaffolding      — `Series.create`, `new_episode` (delegates to Project.create),
  2. read-mostly aggregation — `series_status`, `series_characters`,
  3. explicit sync    — `sync_bible` (never silently overwrites a divergence).

Two distinct markers keep the two layers from ever being confused:
``series.yaml`` names a series root, ``project.yaml`` names a project. So an
episode nested at ``<series>/episodes/E01.manju`` resolves BOTH — ``Project.find``
stops at the episode, ``Series.find`` walks past it (no ``series.yaml`` there) to
the series root.

Note on the ``.manju`` suffix: ``Project.create`` forces a ``.manju`` suffix on a
project dir. So the on-disk episode directory for episode id ``E01`` is
``episodes/E01.manju`` — the id stays ``E01`` (what ``series.yaml`` records and the
CLI takes), the directory carries the engine's project suffix.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .container import BIBLE_FILES, PROJECT_FILE, Project, ProjectError
from .events import append_event
from .hashing import get_by_path
from .yamlio import read_yaml, write_yaml

SERIES_FILE = "series.yaml"

# SERIES-P0-001: the non-defaultable format marker `Series.create` seeds so a
# series root is told apart from any unrelated `series.yaml` (a MORE likely
# plain-business filename than project.yaml). Mirrors PROJECT_FORMAT.
SERIES_FORMAT = "manju.series" + "/v1"  # value: manju.series slash v1; split like PROJECT_FORMAT (schema-registry grep pin)

# The umbrella sub-directories `Series.create` scaffolds (bible/ is filled with
# the same file set as a project bible below). Also the on-disk structure
# signals that mark a real series (SERIES-P0-001) — an old series without the
# format marker still resolves via any of these, so discovery never forces a
# migration; a bare foreign series.yaml matches neither and is refused.
SERIES_DIRS = ("bible", "episodes", "script", "reports")


def _looks_like_manju_series(root: Path) -> bool:
    """True iff ``root`` is a real Manju series (SERIES-P0-001): its series.yaml
    declares ``format: manju.series/v1`` OR one of the :data:`SERIES_DIRS`
    structure directories exists. A plain `series.yaml` from another tool has
    neither, so it is never mistaken for a series nor written into."""
    sf = root / SERIES_FILE
    if not sf.exists():
        return False
    if any((root / sig).is_dir() for sig in SERIES_DIRS):
        return True
    try:
        data = read_yaml(sf)
    except Exception:
        data = None
    return (
        isinstance(data, dict)
        and isinstance(data.get("format"), str)
        and data["format"].startswith("manju.series/")
    )

# An episode id is either the E-number convention (E01, E02, …) or a lowercase
# slug (a-z0-9 with -/_ inside). Rejecting everything else keeps directory names
# portable (§14) and the id ↔ dir mapping (`<eid>.manju`) unambiguous.
_EID_RE = re.compile(r"^(E\d+|[a-z0-9][a-z0-9_-]*)$")

# `sync_bible` reports diverged entries by their canonical (plural, bible-file)
# kind — ``characters:linxia``. A `--force` flag is tolerant of both the plural
# form (copy-pasted from the report) and the singular asset-matrix/@mention form
# (``character:linxia``); both normalize to the bible-file kind below.
_KIND_ALIASES = {
    "character": "characters", "characters": "characters",
    "scene": "scenes", "scenes": "scenes",
    "prop": "props", "props": "props",
    "voice": "voices", "voices": "voices",
    "style": "style", "styles": "style",
}


def _normalize_force(force: list[str] | set[str] | None) -> set[str]:
    """Normalize ``kind:id`` force tokens to the canonical bible-file kind, so a
    user may pass either ``character:linxia`` or ``characters:linxia``."""
    out: set[str] = set()
    for token in force or []:
        if ":" not in token:
            continue  # a bare id is ambiguous across kinds — ignored (unused_force)
        kind, _, entry_id = token.partition(":")
        canonical = _KIND_ALIASES.get(kind.strip(), kind.strip())
        out.add(f"{canonical}:{entry_id.strip()}")
    return out

# Long-script episode markers: `# E01 <title>` or `## E01` style headings.
# `#{1,2} (E\d+)[ :—-]*(title)` — one or two hashes, an E-number, then an
# optional separator (space / colon (ASCII or CJK) / dash / em/en dash) and the
# rest of the line as the title. A sentinel-only heading (`## E01`) yields "".
_HEADING_RE = re.compile(r"^\s{0,3}#{1,2}\s+(E\d+)\b[ \t:：—–-]*(.*?)\s*$")


class SeriesError(RuntimeError):
    pass


class EpisodeRef(BaseModel):
    """One registered episode in ``series.yaml``. Extra-tolerant so a human may
    annotate an episode entry (status, notes, …) without the model rejecting it."""

    model_config = ConfigDict(extra="allow")

    id: str
    title: str = ""

    # Round W (issue #48): ``new_episode`` already checks a FRESH id against
    # this same ``_EID_RE`` before scaffolding (kept below, unchanged — it
    # still fails fast before any directory is created). But LOADING an
    # existing series.yaml never re-checked it, so a hand-edited id could
    # smuggle a path-escaping value (e.g. ``../../etc``) past `manju check` and
    # into ``Series.episode_project_dir`` (``episodes/<eid>.manju``) and every
    # downstream path built from it. Validating here closes that gap: every
    # episode id — new or loaded — now satisfies the same E01/E02… or
    # lowercase-slug rule. Every id ever accepted by ``new_episode`` already
    # matches, so this is byte-identical for every existing series.yaml.
    @field_validator("id")
    @classmethod
    def _known_id_shape(cls, v: str) -> str:
        if not _EID_RE.match(v):
            raise ValueError(
                f"episode id {v!r} 不合法 — 必须是 E01/E02… 或小写 slug(a-z0-9_-);"
                "非法 id(比如带 / 或 ..)可能拼出越出 series 目录的路径"
            )
        return v


class SeriesConfig(BaseModel):
    """``series.yaml`` — the umbrella manifest. Extra-tolerant (pydantic v2
    ``extra='allow'``): forward/extra keys round-trip untouched, mirroring the
    truth-is-text stance of the project models."""

    model_config = ConfigDict(extra="allow")

    name: str
    # SERIES-P0-001 identity marker — see SERIES_FORMAT. Optional/default-None so
    # an old series without it round-trips unchanged (dropped by exclude_none on
    # save); recognized on discovery via the structure signal instead.
    format: str | None = None
    description: str = ""
    episodes: list[EpisodeRef] = Field(default_factory=list)
    created_at: str | None = None


class Series:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        if not (self.root / SERIES_FILE).exists():
            raise SeriesError(f"not a manju series (no {SERIES_FILE}): {self.root}")

    # ------------------------------------------------------------ discovery

    @classmethod
    def find(cls, start: Path | str = ".") -> "Series":
        """Walk up from ``start`` to the nearest ``series.yaml`` — like
        ``Project.find`` but for the umbrella marker.

        Crucially this keeps walking PAST an episode project: an episode carries
        ``project.yaml`` (which ``Project.find`` stops at) but no ``series.yaml``,
        so from inside ``episodes/E01.manju`` this climbs to the series root.

        SERIES-P0-001: a `series.yaml` is only a stopping point when the
        directory is REALLY a manju series (format marker or structure signal).
        A bare foreign series.yaml is walked past and, if it is the only
        candidate, reported with a migration hint — never silently taken over."""
        p = Path(start).resolve()
        bare: Path | None = None
        for candidate in [p, *p.parents]:
            if not (candidate / SERIES_FILE).exists():
                continue
            if _looks_like_manju_series(candidate):
                return cls(candidate)
            if bare is None:
                bare = candidate
        if bare is not None:
            raise SeriesError(
                f"{bare} 有 {SERIES_FILE},但它不像 Manju series:既没有 "
                f"`format: {SERIES_FORMAT}` 标识,也没有结构目录"
                f"({'/、'.join(SERIES_DIRS)}/)。为避免误接管并污染无关目录,这里不当作 "
                f"series 处理。若这确是老的 Manju series,给 {SERIES_FILE} 加一行 "
                f"`format: {SERIES_FORMAT}`;否则请用 `manju series new` 新建。"
            )
        raise SeriesError(f"no manju series found from {p} upward")

    def verify_manju_identity(self) -> None:
        """SERIES-P0-001 write-before verification: refuse a series WRITE against
        a directory that merely holds a foreign `series.yaml`. In the Series
        layer (not only the CLI) so every write entry point is covered."""
        if not _looks_like_manju_series(self.root):
            raise SeriesError(
                f"拒绝写入 {self.root}:该目录有 {SERIES_FILE} 但不像 Manju series"
                f"(缺少 `format: {SERIES_FORMAT}` 标识与结构目录)。为避免污染无关目录,"
                f"写命令不在此执行。老 series 可在 {SERIES_FILE} 补 `format: {SERIES_FORMAT}`。"
            )

    @classmethod
    def find_or_none(cls, start: Path | str = ".") -> "Series | None":
        try:
            return cls.find(start)
        except SeriesError:
            return None

    # ------------------------------------------------------------- creation

    @classmethod
    def create(cls, path: Path | str, name: str | None = None, *,
               description: str = "", git_init: bool = True) -> "Series":
        root = Path(path).resolve()
        if (root / SERIES_FILE).exists():
            raise SeriesError(f"series already exists: {root}")
        name = name or root.name

        bible_paths = [root / "bible" / f"{f}.yaml" for f in BIBLE_FILES]

        # Precheck EVERY planned path before the first write, so a blocked
        # scaffold never leaves a half-built series behind. All blockers are
        # reported at once (a per-path abort would only reveal the first).
        blockers: list[str] = []
        if root.exists() and not root.is_dir():
            blockers.append(f"{root} 不是目录")
        for sub in SERIES_DIRS:
            p = root / sub
            if p.exists() and not p.is_dir():
                blockers.append(f"{sub} 已存在且不是目录")
        for bpath in bible_paths:
            if bpath.exists() and not bpath.is_file():
                blockers.append(f"bible/{bpath.name} 已存在且不是普通文件")
        events_path = root / "events.jsonl"
        if events_path.exists() and not events_path.is_file():
            blockers.append("events.jsonl 已存在且不是普通文件")
        if blockers:
            raise SeriesError(
                f"无法在 {root} 建立剧集(未做任何写入): " + "; ".join(blockers))

        # Everything this call creates, in creation order — an error rolls the
        # list back in reverse so a failure leaves ZERO residue.
        created: list[Path] = []

        def _mkdir(p: Path) -> None:
            if not p.exists():
                p.mkdir(parents=True)
                created.append(p)

        try:
            _mkdir(root)
            for sub in SERIES_DIRS:
                _mkdir(root / sub)
            # The GLOBAL series bible: the SAME file set as a project bible (§4),
            # empty scaffolds. new_episode seeds an episode's bible from these.
            for bpath in bible_paths:
                if not bpath.exists():
                    write_yaml(bpath, {})
                    created.append(bpath)

            config = SeriesConfig(
                name=name,
                format=SERIES_FORMAT,  # SERIES-P0-001 identity marker
                description=description,
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            series_path = root / SERIES_FILE
            write_yaml(series_path, config.model_dump(exclude_none=True))
            created.append(series_path)
            if not events_path.exists():
                events_path.touch()
                created.append(events_path)
        except (OSError, ValueError, ValidationError) as exc:
            for p in reversed(created):
                try:
                    if p.is_dir():
                        p.rmdir()
                    else:
                        p.unlink()
                except OSError:
                    pass
            raise SeriesError(f"建立剧集失败,已回滚 {root}: {exc}") from exc

        if git_init and shutil.which("git") and not (root / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=root, check=False, capture_output=True)

        return cls(root)

    # ----------------------------------------------------------------- paths

    @property
    def episodes_dir(self) -> Path:
        return self.root / "episodes"

    @property
    def bible_dir(self) -> Path:
        return self.root / "bible"

    @property
    def script_dir(self) -> Path:
        return self.root / "script"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    def episode_project_dir(self, eid: str) -> Path:
        """On-disk project dir for episode ``eid`` — always ``<eid>.manju``
        (Project.create's suffix convention)."""
        return self.episodes_dir / f"{eid}.manju"

    def relpath(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.root).as_posix()

    # ---------------------------------------------------------------- config

    def load_config(self) -> SeriesConfig:
        """Round W (issue #48): a hand-edited series.yaml with an illegal
        episode id (EpisodeRef._known_id_shape) now fails HERE, as a clean
        :class:`SeriesError` — the one exception type every series CLI command
        already knows how to catch and report — instead of a raw pydantic
        ValidationError bubbling out of whichever read-only command happened
        to call this first (status/episodes/characters/sync-bible all funnel
        through here)."""
        try:
            return SeriesConfig.model_validate(read_yaml(self.root / SERIES_FILE) or {})
        except ValidationError as exc:
            issues = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
            )
            raise SeriesError(f"series.yaml: schema invalid — {issues}") from exc

    def save_config(self, config: SeriesConfig) -> None:
        write_yaml(self.root / SERIES_FILE, config.model_dump(exclude_none=True))

    # -------------------------------------------------------------- episodes

    def episode_ids(self) -> list[str]:
        """Registered episode ids in series.yaml order, then any on-disk
        ``*.manju`` under episodes/ not yet registered (robustness — mirrors how
        ``Project.shot_ids`` folds in unindexed shot files)."""
        registered = [e.id for e in self.load_config().episodes]
        on_disk = sorted(
            p.name[: -len(".manju")]
            for p in self.episodes_dir.glob("*.manju")
            if p.is_dir()
        ) if self.episodes_dir.exists() else []
        return registered + [e for e in on_disk if e not in registered]

    def open_episode(self, eid: str) -> Project:
        """Open episode ``eid`` as an ordinary Project (raises ProjectError when
        its dir is missing or not a project — the caller degrades per-episode)."""
        return Project(self.episode_project_dir(eid))


# ------------------------------------------------------------- bible helpers


def _load_series_bible_by_file(series: Series) -> dict[str, dict[str, dict[str, Any]]]:
    """Per-file id -> entry maps for the GLOBAL series bible (only dict entries,
    so a stray scalar never poses as an asset). Mirrors appearances._bible_by_file
    but reads the series root instead of a project."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for fname in BIBLE_FILES:
        path = series.bible_dir / f"{fname}.yaml"
        data = read_yaml(path) or {} if path.exists() else {}
        out[fname] = (
            {k: v for k, v in data.items() if isinstance(v, dict)}
            if isinstance(data, dict)
            else {}
        )
    return out


def _load_episode_bible_file(project: Project, kind: str) -> dict[str, Any]:
    path = project.root / "bible" / f"{kind}.yaml"
    if not path.exists():
        return {}
    data = read_yaml(path) or {}
    return data if isinstance(data, dict) else {}


def _seed_episode_bible(series: Series, project: Project) -> None:
    """Plain copy of the series bible into a freshly created episode (creation
    time only — after that the episode OWNS its copy and diverges freely)."""
    for kind in BIBLE_FILES:
        s_path = series.bible_dir / f"{kind}.yaml"
        if not s_path.exists():
            continue
        data = read_yaml(s_path) or {}
        if isinstance(data, dict) and data:
            write_yaml(project.root / "bible" / f"{kind}.yaml", deepcopy(data))


def _locked_conflicts(ep_entry: dict[str, Any], series_entry: dict[str, Any]) -> list[str]:
    """Which value-locked fields of the EPISODE's entry would change if it were
    overwritten with the series entry (core/locks machinery, bible tier).

    A bible entry may carry a ``locked`` mapping (path -> sealed hash), verified
    by `manju check` exactly like a shot lock. Overwriting a field that is sealed
    would silently break that seal, so `--force` refuses when any sealed path's
    value differs between the episode copy and the series copy. An UNSEALED lock
    (a bare list, already a check error) is treated as a conflict too — we never
    overwrite through it. Returns the conflicting dotted paths (empty = safe)."""
    locked = ep_entry.get("locked")
    if isinstance(locked, list):  # unsealed hand-written form — refuse to be safe
        return [str(p) for p in locked]
    if not isinstance(locked, dict) or not locked:
        return []
    conflicts: list[str] = []
    sentinel = object()
    for path in locked:
        try:
            ev: Any = get_by_path(ep_entry, path)
        except (KeyError, IndexError, ValueError):
            ev = sentinel
        try:
            sv: Any = get_by_path(series_entry, path)
        except (KeyError, IndexError, ValueError):
            sv = sentinel
        if ev is sentinel or sv is sentinel or ev != sv:
            conflicts.append(str(path))
    return conflicts


# ------------------------------------------------------------- new_episode


def new_episode(series: Series, eid: str, *, title: str = "", preset: str | None = None,
                actor: str = "human", git_init: bool = False) -> Project:
    """Create episode ``eid`` under the series as a COMPLETELY NORMAL project.

    Delegates to the EXISTING ``Project.create`` (with ``preset`` applied exactly
    as ``manju new --preset`` does), seeds the episode bible from the series bible
    (a plain copy — the episode owns it afterwards), registers ``{id, title}`` in
    series.yaml, and writes a series-level event. ``git_init`` defaults to False so
    the episode's truth text is tracked by the SERIES repo instead of a nested one.

    Round AA (residual A, DECISIONS §9): ``series.yaml`` is a file every
    concurrent ``new_episode``/``split_script`` call reads-modifies-writes —
    two racing calls could each load it before either saves, and whichever
    saves LAST would silently drop the other's registration (a classic lost
    update).

    Audit 2026-07-20 (findings 21/22): the OLD comment claimed the episode
    directory "needs no lock (a path nothing else can know about until this
    call returns)". That is FALSE when two callers pass the SAME public ``eid``
    — ``episode_project_dir(eid)`` is the same path for both, so both raced the
    pre-check and both scaffolded it, clobbering (series.yaml title vs
    project.yaml name diverge). And a series-lock-busy-after-scaffold left an
    unregistered directory that a retry could never complete (it hit "already
    exists"). Both are closed here:

    * a PER-EID reservation lock (``newep-<eid>``) wraps the check + scaffold,
      so two same-eid callers can never both scaffold (the loser gets an
      immediate clean ``BuildLocked``); DIFFERENT eids use different lock files
      and never contend.
    * registration is RESUMABLE: an on-disk-but-unregistered directory (a prior
      attempt whose series-lock register step failed) is COMPLETED on retry —
      the scaffold is never re-run (it would clobber a bible the owner may have
      started editing), only the series.yaml row is appended.
    """
    if not _EID_RE.match(eid):
        raise SeriesError(
            f"invalid episode id {eid!r}: use E01/E02… or a lowercase slug (a-z0-9_-)"
        )
    series.verify_manju_identity()  # SERIES-P0-001 write-before verification
    ep_dir = series.episode_project_dir(eid)

    # Resolve the preset up-front so a bad name fails before we scaffold anything.
    spec = None
    if preset is not None:
        from ..presets import PresetError, load_preset

        try:
            spec = load_preset(preset)
        except PresetError as exc:
            raise SeriesError(str(exc)) from exc

    from ..runtime.buildlock import BuildLocked, build_lock

    # Per-eid reservation: only ONE creator of THIS eid at a time. eid already
    # matched _EID_RE (filename-safe), so it is a safe lock-file name segment.
    try:
        reservation = build_lock(series.root, actor=actor, name=f"newep-{eid}")
        reservation.__enter__()
    except BuildLocked as exc:
        raise SeriesError(
            f"{eid} 正在被另一个进程创建(newep 锁被占用)—— 稍后重试即可"
            f"({' '.join(str(exc).split())})"
        ) from exc
    try:
        config = series.load_config()
        if any(e.id == eid for e in config.episodes):
            raise SeriesError(f"episode already exists: {eid}")
        if (ep_dir / PROJECT_FILE).exists():
            # RESUME (finding 22): scaffolded by a prior attempt whose register
            # step failed. Complete registration ONLY — never re-scaffold /
            # re-seed the bible (the owner may already be editing it).
            project = Project(ep_dir)
            if not title:
                # a retry with no --title must not register an empty title over
                # the name the first attempt already wrote to project.yaml
                try:
                    title = project.load_config().name or ""
                except Exception:
                    title = ""
        else:
            project = Project.create(ep_dir, name=title or eid, git_init=git_init)
            if spec is not None:
                from ..presets import apply_preset

                apply_preset(project, spec)
            _seed_episode_bible(series, project)

        try:
            with build_lock(series.root, actor=actor):
                # re-read INSIDE the lock: another writer (split_script,
                # sync) may have touched series.yaml — never clobber it.
                config = series.load_config()
                if not any(e.id == eid for e in config.episodes):
                    config.episodes.append(EpisodeRef(id=eid, title=title))
                    series.save_config(config)
        except BuildLocked as exc:
            # The directory + seeded bible are ALREADY on disk and safe; only
            # the series.yaml register step failed. Retry now COMPLETES the
            # registration (the resume branch above), so the guidance finally
            # matches reality: just re-run new-episode once the lock frees.
            raise SeriesError(
                f"{eid} 的目录与 bible 已就绪,但 series.yaml 正被其它进程占用"
                f"(build_lock),未能登记 —— 等锁释放后重跑 new-episode 即可"
                "自动补登记(不会重建目录);`manju series episodes` 也已能看到"
                f"这个未登记的目录。{' '.join(str(exc).split())}"
            ) from exc
    finally:
        reservation.__exit__(None, None, None)

    append_event(series.root, actor, "series_new_episode",
                 {"episode": eid, "title": title, "preset": preset})
    return project


# ------------------------------------------------------------- series_status


def _episode_build_summary(project: Project) -> dict[str, Any]:
    """Per-episode shot/build/spend probing — ``build.stale.evaluate_all``
    (the same state summary `manju status` prints), ``Project.
    newest_final_path`` (the one numeric final resolver), and ``build.spend.
    spend_report`` (ledger-or-sidecar total, §3 fallback).

    Round AA7 (goal item 7): extracted out of :func:`series_status` so
    :func:`series_continuity` reuses the EXACT same probing instead of
    re-walking ``evaluate_all`` a second way — one engine, two read models.
    """
    from ..build.spend import spend_report
    from ..build.stale import evaluate_all

    statuses = evaluate_all(project)
    by_state: dict[str, int] = {}
    for st in statuses:
        by_state[st.state.value] = by_state.get(st.state.value, 0) + 1
    final = project.newest_final_path()
    spend = spend_report(project)
    cost = float(spend.get("total") or 0.0)
    return {
        "shots_total": len(statuses),
        "shots_by_state": by_state,
        "latest_final": project.relpath(final) if final else None,
        "cost": cost,
        "currency": spend.get("currency"),
        "spend_source": spend.get("source"),
    }


def series_status(series: Series) -> dict[str, Any]:
    """Per-episode aggregation over the EXISTING per-project machinery — read-only.

    Each episode reuses :func:`_episode_build_summary` (``build.stale.
    evaluate_all`` + ``Project.newest_final_path`` + ``build.spend.
    spend_report``). An unreadable episode degrades to ``{"error": …}`` instead
    of killing the whole table. A totals row sums shots / finals / cost across
    the readable episodes.
    """
    config = series.load_config()
    episodes: list[dict[str, Any]] = []
    totals = {"episodes": 0, "ok": 0, "errors": 0, "shots": 0, "finals": 0, "cost": 0.0}
    currency: str | None = None

    for ref in config.episodes:
        entry: dict[str, Any] = {"id": ref.id, "title": ref.title}
        totals["episodes"] += 1
        try:
            project = series.open_episode(ref.id)
            summary = _episode_build_summary(project)
            entry.update(summary)
            totals["ok"] += 1
            totals["shots"] += summary["shots_total"]
            totals["finals"] += 1 if summary["latest_final"] else 0
            totals["cost"] += summary["cost"]
            if summary["currency"]:
                currency = summary["currency"]
        except Exception as exc:  # degrade per-episode, never kill the table
            entry["error"] = str(exc)
            totals["errors"] += 1
        episodes.append(entry)

    totals["currency"] = currency
    return {"series": config.name, "episodes": episodes, "totals": totals}


# ---------------------------------------------------------- series_characters


def _episode_asset_context(
    series: Series, config: SeriesConfig,
    kinds: tuple[str, ...] = ("characters", "scenes", "props"),
) -> dict[str, dict[str, Any]]:
    """Open every registered episode ONCE and collect what the asset-continuity
    matrices need: each episode's own bible-by-kind maps (for ``kinds``) and its
    ``appearances()`` walk (which reports characters/scenes/props together in
    ONE pass — see :mod:`core.appearances`). Shared by :func:`series_characters`
    and :func:`series_continuity` so N episode projects are opened once per
    caller, not once per asset kind.
    """
    from .appearances import appearances

    ep_ctx: dict[str, dict[str, Any]] = {}
    for ref in config.episodes:
        try:
            project = series.open_episode(ref.id)
            ep_ctx[ref.id] = {
                "bible_by_kind": {k: _load_episode_bible_file(project, k) for k in kinds},
                "app": appearances(project),
                "error": None,
            }
        except Exception as exc:
            ep_ctx[ref.id] = {"error": str(exc)}
    return ep_ctx


def _asset_continuity_matrix(
    series: Series, kind: str, config: SeriesConfig, ep_ctx: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Generic present/diverged/appearances matrix for ONE addressable bible
    kind (``characters``/``scenes``/``props`` — the kinds :mod:`core.
    appearances` cross-references against shot fields) over ``ep_ctx`` (see
    :func:`_episode_asset_context`).

    Keyed on the SERIES bible's entries of ``kind``: for each, its per-episode
    presence (in that episode's own bible copy? diverged from the series
    entry?) and its per-episode appearances (the ``manju appearances`` walk).
    Entries present only in an episode's bible (never promoted to the series
    bible) are returned separately as ``episode_only`` so the view stays honest
    about drift.

    This is the ONE engine behind :func:`series_characters` (``kind=
    "characters"``) and :func:`series_continuity`'s scenes/props panels — the
    present/diverged/appearances logic used to live only inside
    ``series_characters``; round AA7 (goal item 7) generalized it here because
    ``appearances()`` already reports scenes and props in the exact same shape,
    so duplicating the walk per kind would just be copy-paste.
    """
    series_entries = _load_series_bible_by_file(series).get(kind, {})
    ep_ids = [e.id for e in config.episodes]

    rows: list[dict[str, Any]] = []
    for aid, s_entry in series_entries.items():
        row: dict[str, Any] = {"id": aid, "name": s_entry.get("name"), "episodes": []}
        for eid in ep_ids:
            ctx = ep_ctx[eid]
            cell: dict[str, Any] = {"id": eid}
            if ctx.get("error"):
                cell["error"] = ctx["error"]
            else:
                ep_entry = ctx["bible_by_kind"].get(kind, {}).get(aid)
                cell["present"] = ep_entry is not None
                cell["diverged"] = bool(ep_entry is not None and ep_entry != s_entry)
                cell["appearances"] = list(
                    (ctx["app"].get(kind, {}).get(aid) or {}).get("shots") or [])
            row["episodes"].append(cell)
        rows.append(row)

    # Episode-only entries: in some episode's bible but never in the series one.
    episode_only: dict[str, list[str]] = {}
    for eid in ep_ids:
        ctx = ep_ctx[eid]
        if ctx.get("error"):
            continue
        for aid in ctx["bible_by_kind"].get(kind, {}):
            if aid not in series_entries:
                episode_only.setdefault(aid, []).append(eid)

    return {
        "episodes": ep_ids,
        "rows": rows,
        "episode_only": [{"id": aid, "episodes": eps} for aid, eps in episode_only.items()],
    }


def series_characters(series: Series) -> dict[str, Any]:
    """The GLOBAL character view (the "global character management" read model).

    A thin wrapper over :func:`_asset_continuity_matrix` (``kind="characters"``)
    — see that function for the present/diverged/appearances logic itself.
    """
    config = series.load_config()
    ep_ctx = _episode_asset_context(series, config, kinds=("characters",))
    matrix = _asset_continuity_matrix(series, "characters", config, ep_ctx)

    return {
        "series": config.name,
        "episodes": matrix["episodes"],
        "characters": matrix["rows"],
        "episode_only": matrix["episode_only"],
        "note": (
            "全局角色视图以剧集总 bible 为准;present/diverged 描述每集自有副本,"
            "出场列复用 `manju appearances`。episode_only 为仅存在于分集 bible 的角色。"
        ),
    }


# --------------------------------------------------------------- sync_bible


def sync_bible(series: Series, *, apply: bool = False,
               force: list[str] | set[str] | None = None,
               actor: str = "human",
               should_cancel: "Callable[[], bool] | None" = None) -> dict[str, Any]:
    """Conservative, explicit series-bible → episode-bible sync (truth-is-text).

    For every series-bible entry, per episode:

    - MISSING in the episode  → a candidate ADD (written on ``apply=True``);
    - present but DIFFERENT    → DIVERGED — reported, NEVER auto-overwritten;
    - present and IDENTICAL    → in sync (counted, untouched).

    A divergence is overwritten only when its ``<kind>:<id>`` is named in
    ``force`` AND no value-locked field of the episode's entry would change
    (core/locks bible tier — see :func:`_locked_conflicts`); otherwise the force
    is REFUSED and reported. ``apply=False`` (default) is a pure report — nothing
    is written. Every applied change (add or forced overwrite) lands an event.

    ``should_cancel`` (goal: honest job cancellation, GUI-jobs-runner only —
    ``None``, the default, keeps every CLI call byte-identical) is checked
    BETWEEN two episodes — a many-episode series syncing/writing several
    bible files per episode is genuinely multi-second. A trip never touches
    the episode it is currently on; every episode already appended to
    ``episodes`` (and, on ``apply=True``, already written) stays as-is.

    Round AA (residual A, DECISIONS §9): this call writes into MULTIPLE
    episode PROJECTS in one pass, each of which is also an independent CLI/
    GUI/MCP mutation target on its own — a straight per-file write (the
    pre-round-AA shape) had no cross-process guard, so a `manju build`
    running against episode E03 could interleave with sync-bible mid-write
    into E03's bible/*.yaml. Every episode's WHOLE write section below (all
    its bible-kind files, one hold) is now wrapped in THAT episode's own
    ``runtime.buildlock.BuildLock`` — the SAME lock a CLI/GUI/MCP build
    against that lone episode project would take. ``apply=False`` (a pure
    report) never acquires anything — nothing is written, so there is
    nothing to protect. A BUSY episode STOPS the whole sync run right there
    (fail-fast, mirroring ``build.ingest.apply_ingest``'s ``stopped_at``):
    every episode processed before it is already synced and STAYS synced;
    ``stopped_at`` names the id of the episode we could not get into, and
    that episode's own report entry carries a 中文 ``error`` naming it — the
    SAME field an unreadable/broken episode already reports through, so
    every existing CLI/GUI renderer needs no changes to show it.
    """
    from ..runtime.buildlock import BuildLocked, build_lock

    force_set = _normalize_force(force)
    forced_used: set[str] = set()
    config = series.load_config()
    series_bible = _load_series_bible_by_file(series)

    episodes: list[dict[str, Any]] = []
    totals = {"added": 0, "diverged": 0, "overwritten": 0, "refused": 0, "in_sync": 0}
    canceled = False
    stopped_at: str | None = None

    for ref in config.episodes:
        if should_cancel is not None and should_cancel():
            canceled = True
            break
        ep_report: dict[str, Any] = {
            "id": ref.id, "title": ref.title, "error": None,
            "added": [], "diverged": [], "overwritten": [], "refused": [], "in_sync": 0,
        }
        try:
            project = series.open_episode(ref.id)
        except Exception as exc:
            ep_report["error"] = str(exc)
            episodes.append(ep_report)
            continue

        lock_ctx = build_lock(project.root, actor=actor) if apply else contextlib.nullcontext()
        try:
            with lock_ctx:
                for kind in BIBLE_FILES:
                    s_entries = series_bible.get(kind, {})
                    if not s_entries:
                        continue
                    ep_data = _load_episode_bible_file(project, kind)
                    file_changed = False
                    for eid, s_entry in s_entries.items():
                        key = f"{kind}:{eid}"
                        if eid not in ep_data:
                            ep_report["added"].append(key)
                            totals["added"] += 1
                            if apply:
                                ep_data[eid] = deepcopy(s_entry)
                                file_changed = True
                                append_event(series.root, actor, "series_sync_bible",
                                             {"episode": ref.id, "entry": key, "action": "add"})
                            continue
                        if ep_data[eid] == s_entry:
                            ep_report["in_sync"] += 1
                            totals["in_sync"] += 1
                            continue
                        # diverged
                        if key in force_set:
                            forced_used.add(key)
                            conflicts = _locked_conflicts(ep_data[eid], s_entry)
                            if conflicts:
                                ep_report["refused"].append({"entry": key, "locked": conflicts})
                                totals["refused"] += 1
                            else:
                                ep_report["overwritten"].append(key)
                                totals["overwritten"] += 1
                                if apply:
                                    ep_data[eid] = deepcopy(s_entry)
                                    file_changed = True
                                    append_event(series.root, actor, "series_sync_bible",
                                                 {"episode": ref.id, "entry": key,
                                                  "action": "overwrite"})
                        else:
                            ep_report["diverged"].append(key)
                            totals["diverged"] += 1
                    if apply and file_changed:
                        write_yaml(project.root / "bible" / f"{kind}.yaml", ep_data)
        except BuildLocked as exc:
            ep_report["error"] = (
                f"{ref.id} 正被其它进程构建占用(build_lock)——已在此处停止同步,"
                f"{ref.id} 之前的分集已完成同步。{' '.join(str(exc).split())}"
            )
            episodes.append(ep_report)
            stopped_at = ref.id
            break

        episodes.append(ep_report)

    errors: list[str] = []
    if canceled:
        remaining = len(config.episodes) - len(episodes)
        errors.append(
            f"已取消:{len(episodes)}/{len(config.episodes)} 集已检查"
            + ("并写入" if apply else "")
            + f"(已处理的分集不受影响),剩余 {remaining} 集未处理"
        )
    return {
        "apply": apply,
        "totals": totals,
        "episodes": episodes,
        "stopped_at": stopped_at,
        "unused_force": sorted(force_set - forced_used),
        # goal: honest job cancellation — mirrors BuildResult/BatchResult's
        # canceled/errors shape (see build/graph.py) so gui/jobs.py's
        # JobRunner can generically recognize this dict result as "canceled".
        "canceled": canceled,
        "errors": errors,
        "note": (
            "保守同步:缺失→可新增;不同→只报告 DIVERGED(绝不自动覆盖),需 "
            "--force kind:id 显式覆盖;分集条目若有 core/locks 值锁且被覆盖字段会变则拒绝。"
            + ("" if stopped_at is None else
               f" 本次同步在分集 {stopped_at} 处停止(该分集正被占用)——之前的分集已同步,"
               "稍后对该分集重跑 sync-bible 即可继续。")
        ),
    }


# ----------------------------------------------------------- series_continuity


# Packaging fields worth a cross-episode consistency glance — intro/outro/cover
# are the "does this still feel like the same show" knobs (core/models.py's
# PackagingCard/CoverSpec). Branding overlays (logo/watermark/badge/cta,
# round-Q) are deliberately excluded: those commonly vary ON PURPOSE per
# episode (a one-off sponsor badge, …), so flagging them would just be noise.
_PACKAGING_FIELDS: dict[str, "Callable[[Any], Any]"] = {
    "intro.enabled": lambda p: p.intro.enabled,
    "intro.style_preset": lambda p: p.intro.style_preset,
    "intro.template": lambda p: p.intro.template,
    "outro.enabled": lambda p: p.outro.enabled,
    "outro.style_preset": lambda p: p.outro.style_preset,
    "outro.template": lambda p: p.outro.template,
    "cover.mode": lambda p: p.cover.mode,
    "cover.template": lambda p: p.cover.template,
}


def _packaging_outliers(pkg_by_episode: dict[str, Any]) -> list[dict[str, Any]]:
    """Mode-vs-outliers over each readable episode's ``timeline/packaging.yaml``
    (intro/outro/cover knobs only — see :data:`_PACKAGING_FIELDS`).

    PURELY REFERENTIAL (参考性提示, never a verdict input): a series is free to
    vary its packaging per episode on purpose (a finale's extra-long outro, a
    pilot's different intro card, …). This only surfaces "most episodes use X,
    these N don't" for a human to glance at — nothing here is "wrong" by
    construction.
    """
    from collections import Counter

    out: list[dict[str, Any]] = []
    if len(pkg_by_episode) < 2:
        return out
    for field, getter in _PACKAGING_FIELDS.items():
        values = {eid: getter(pkg) for eid, pkg in pkg_by_episode.items()}
        counts = Counter(values.values())
        if len(counts) <= 1:
            continue  # every episode agrees — nothing to flag
        majority_value, _ = counts.most_common(1)[0]
        outliers = [{"episode": eid, "value": v} for eid, v in values.items()
                   if v != majority_value]
        out.append({"field": field, "majority": majority_value, "outliers": outliers})
    return out


def _voice_divergence(series: Series, config: SeriesConfig) -> list[dict[str, Any]]:
    """Which VOICE-SHAPING bible fields (``core.spec.VOICE_BIBLE_KEYS`` —
    voice/voice_ref/voice_sample/voice_id/tone, exactly the fields
    ``core.spec.voice_payload`` hashes into TTS staleness) differ between the
    series bible and an episode's own copy, for every character present in
    both.

    A generic ``diverged`` flag (as :func:`sync_bible`/:func:`series_characters`
    report it) only says SOMETHING about the entry differs — it could be an
    unrelated note field. Series continuity cares specifically about voice: a
    character whose ``voice_id`` silently drifts between episodes is exactly
    the kind of thing that makes a series sound inconsistent, so it gets its
    own report (round AA7, goal item 7).
    """
    from .spec import VOICE_BIBLE_KEYS

    series_chars = _load_series_bible_by_file(series).get("characters", {})
    out: list[dict[str, Any]] = []
    for ref in config.episodes:
        try:
            project = series.open_episode(ref.id)
        except Exception:
            continue  # already reported as a broken episode by series_continuity
        ep_chars = _load_episode_bible_file(project, "characters")
        for cid, s_entry in series_chars.items():
            ep_entry = ep_chars.get(cid)
            if not isinstance(ep_entry, dict):
                continue
            changed = [k for k in VOICE_BIBLE_KEYS if s_entry.get(k) != ep_entry.get(k)]
            if changed:
                out.append({"id": cid, "episode": ref.id, "fields": changed})
    return out


_VERDICT_TOTALS_KEY = {
    "完整": "complete", "缺素材": "missing_assets",
    "有问题": "problem", "待同步": "needs_sync",
}


def series_continuity(series: Series) -> dict[str, Any]:
    """The GLOBAL continuity dashboard (round AA, goal item 7) — everything
    below is DERIVED from existing truth in one aggregation pass; nothing new
    is stored.

    Per episode:

    - shot totals/selected/built — :func:`_episode_build_summary`, the SAME
      probing :func:`series_status` already does (``selected`` = a take is
      chosen — states fresh/stale/manual/broken; ``built`` = that take is
      actually usable — states fresh/stale/manual, mirrors ``build.stale.
      ShotBuildStatus.usable``);
    - check error/warning counts — ``core.check.run_check`` verbatim;
    - bible-sync state against the series bible — ONE :func:`sync_bible`
      (``apply=False``) call up front, redistributed per episode from its
      report (never re-implements the diff);
    - reference orphan/missing counts — ``core.refs.refs_report`` verbatim;
    - a derived VERDICT (see precedence below).

    Cross-episode:

    - a characters/scenes/props continuity matrix — :func:`_asset_continuity_matrix`,
      the SAME engine :func:`series_characters` is built on, generalized because
      ``appearances()`` already reports scenes/props in the identical shape;
    - voice-field divergence — :func:`_voice_divergence`, narrowed to
      ``core.spec.VOICE_BIBLE_KEYS`` (the fields that actually drive TTS
      staleness), surfaced both as a flat list AND stamped onto the matching
      characters-matrix cell (``voice_diverged``) so a diverged cell can say
      WHETHER the divergence is voice-shaped or something else;
    - a REFERENTIAL intro/outro/cover packaging-outlier report —
      :func:`_packaging_outliers` (majority vs minority value per field);
      honestly labelled 参考性提示 — it is never a verdict input, a series may
      vary packaging per episode on purpose.

    VERDICT PRECEDENCE (documented here — the ONE place it is decided). For a
    readable episode, in order, the FIRST condition that applies wins:

      1. 有问题 (problem)  — ``run_check`` reports at least one ERROR, or a
         shot is in the ``broken`` state (selected take has no media on disk —
         in practice already flagged by ``check`` too; kept here as an
         explicit belt-and-braces signal in case a future check regresses).
         The project's own truth is internally inconsistent — worse than
         merely incomplete, so it outranks everything below.
      2. 缺素材 (missing assets) — no errors, but a shot has no take at all
         (state ``missing``), has takes but none SELECTED (state
         ``needs_selection``), or a bible entry's ``ref_image``/``ref_video``
         points at a file that no longer exists (``refs_report``'s
         ``missing``). The episode is incomplete but not internally broken.
      3. 待同步 (needs sync) — no errors and nothing missing, but the
         episode's bible has a candidate ADD or a DIVERGED entry against the
         series bible.
      4. 完整 (complete) — none of the above.

    ``check`` WARNINGS and ``refs_report``'s ORPHAN count (an unowned file
    under media/refs) are reported as data but never move the verdict — they
    are hygiene notes, not "this episode is unfinished or wrong".

    An episode whose project cannot even be opened degrades to
    ``{"id", "title", "error": …}`` exactly like :func:`series_status` — it
    carries no verdict, and the cross-episode matrices surface the SAME error
    string on its column rather than a silent gap (see
    :func:`_asset_continuity_matrix`).

    Read-only, no lock, no job: this walks each episode once (a few cheap
    reads + one already-existing report function each) — over the round's
    test fixtures (a handful of episodes) this is sub-second, so unlike
    ``sync_bible``'s ``apply=True`` write path it is never routed through the
    GUI job runner.
    """
    from .check import run_check
    from .refs import refs_report

    config = series.load_config()
    sync_report = sync_bible(series, apply=False)
    sync_by_ep = {e["id"]: e for e in sync_report["episodes"]}

    episodes: list[dict[str, Any]] = []
    totals = {
        "episodes": 0, "ok": 0, "errors": 0,
        "complete": 0, "missing_assets": 0, "problem": 0, "needs_sync": 0,
        "check_errors": 0, "check_warnings": 0,
        "ref_orphans": 0, "ref_missing": 0,
        "bible_added": 0, "bible_diverged": 0,
    }
    needs_sync: list[str] = []
    pkg_by_episode: dict[str, Any] = {}

    for ref in config.episodes:
        entry: dict[str, Any] = {"id": ref.id, "title": ref.title}
        totals["episodes"] += 1
        try:
            project = series.open_episode(ref.id)
            build = _episode_build_summary(project)
            check = run_check(project)
            refs = refs_report(project)
            sync_ep = sync_by_ep.get(ref.id) or {}

            by_state = build["shots_by_state"]
            shots_missing = by_state.get("missing", 0)
            shots_needs_selection = by_state.get("needs_selection", 0)
            shots_broken = by_state.get("broken", 0)
            check_errors = len(check.errors)
            check_warnings = len(check.warnings)
            ref_missing = len(refs["missing"])
            ref_orphans = refs["orphan_count"]
            bible_added = len(sync_ep.get("added") or [])
            bible_diverged = len(sync_ep.get("diverged") or [])

            if check_errors or shots_broken:
                verdict = "有问题"
            elif shots_missing or shots_needs_selection or ref_missing:
                verdict = "缺素材"
            elif bible_added or bible_diverged:
                verdict = "待同步"
            else:
                verdict = "完整"

            entry.update({
                "shots_total": build["shots_total"],
                "shots_by_state": by_state,
                "shots_selected": (by_state.get("fresh", 0) + by_state.get("stale", 0)
                                   + by_state.get("manual", 0) + shots_broken),
                "shots_built": (by_state.get("fresh", 0) + by_state.get("stale", 0)
                               + by_state.get("manual", 0)),
                "latest_final": build["latest_final"],
                "check_errors": check_errors,
                "check_warnings": check_warnings,
                "refs": {"total": refs["total"], "orphan": ref_orphans, "missing": ref_missing},
                "bible_sync": {"added": bible_added, "diverged": bible_diverged,
                               "refused": len(sync_ep.get("refused") or []),
                               "in_sync": sync_ep.get("in_sync") or 0},
                "verdict": verdict,
            })
            try:
                pkg_by_episode[ref.id] = project.load_packaging()
            except Exception:
                pass  # a malformed packaging.yaml is already a check error above

            totals["ok"] += 1
            totals[_VERDICT_TOTALS_KEY[verdict]] += 1
            totals["check_errors"] += check_errors
            totals["check_warnings"] += check_warnings
            totals["ref_orphans"] += ref_orphans
            totals["ref_missing"] += ref_missing
            totals["bible_added"] += bible_added
            totals["bible_diverged"] += bible_diverged
            if bible_added or bible_diverged:
                needs_sync.append(ref.id)
        except Exception as exc:  # degrade per-episode, never kill the table
            entry["error"] = str(exc)
            totals["errors"] += 1
        episodes.append(entry)

    # cross-episode continuity — the SAME asset-continuity engine
    # series_characters is built on, generalized to scenes/props.
    ep_ctx = _episode_asset_context(series, config)
    char_matrix = _asset_continuity_matrix(series, "characters", config, ep_ctx)
    scene_matrix = _asset_continuity_matrix(series, "scenes", config, ep_ctx)
    prop_matrix = _asset_continuity_matrix(series, "props", config, ep_ctx)

    voice_divergences = _voice_divergence(series, config)
    voice_map: dict[tuple[str, str], list[str]] = {
        (v["id"], v["episode"]): v["fields"] for v in voice_divergences
    }
    for row in char_matrix["rows"]:
        for cell in row["episodes"]:
            fields = voice_map.get((row["id"], cell["id"]))
            if fields:
                cell["voice_diverged"] = fields

    packaging_outliers = _packaging_outliers(pkg_by_episode)

    return {
        "series": config.name,
        "episodes": episodes,
        "totals": totals,
        "needs_sync": needs_sync,
        "characters": char_matrix["rows"],
        "scenes": scene_matrix["rows"],
        "props": prop_matrix["rows"],
        "episode_only": {
            "characters": char_matrix["episode_only"],
            "scenes": scene_matrix["episode_only"],
            "props": prop_matrix["episode_only"],
        },
        "voice_divergences": voice_divergences,
        "packaging_outliers": packaging_outliers,
        "note": (
            "跨集连续性看板:每集结论按 有问题(检查错误/镜头 broken)> 缺素材(镜头缺失/未选定,"
            "或 bible 引用文件缺失)> 待同步(bible 有新增/分歧待同步)> 完整 的优先级推导"
            "(详见函数 docstring);角色/场景/道具矩阵复用 series_characters 的同一引擎;"
            "语音字段(voice/voice_ref/voice_sample/voice_id/tone)分歧单独标出(TTS 一致性"
            "关键字段);片头/片尾/封面偏差(packaging_outliers)只是多数-少数参考性提示,"
            "不同分集完全可以有意使用不同包装,不作为结论依据。"
        ),
    }


# -------------------------------------------------------------- split_script


# The EXACT scaffold `Project.create` writes into a fresh episode's
# story/script.md (core/container.py, ``story_templates["script.md"]``) — a
# script.md still holding this text has never been split into OR hand-edited,
# so overwriting it needs no --force (round-W #75). Duplicated here on
# purpose (not imported) the same way this module already avoids reaching
# into container.py internals for a one-line literal.
_SCRIPT_SCAFFOLD = "# 剧本\n\n<!-- 分场与对白;对白会成为 shots/*.yaml 的 dialogue.text -->\n"


def split_script(series: Series, script_path: Path | str, *, apply: bool = False,
                 force: list[str] | set[str] | None = None,
                 actor: str = "human") -> dict[str, Any]:
    """DETERMINISTIC long-script splitting at EXPLICIT markers only.

    Splits a markdown file whose episodes are marked by ``# E01 <title>`` /
    ``## E01`` headings (``#{1,2} (E\\d+)[sep](title)``) into per-episode script
    files at ``episodes/<eid>.manju/story/script.md``. On ``apply=True`` an
    episode that does not exist yet is created via :func:`new_episode`. On
    ``apply=False`` the report lists what WOULD be created/written; nothing is
    touched. No heading markers → :class:`SeriesError` — creative splitting is the
    AI's job, the engine only cuts on explicit markers (see the skills library).

    Round-W (#75): an episode's ``story/script.md`` that ALREADY holds content
    — and that content is neither the untouched scaffold NOR an exact repeat
    of this split's own output — is refused, per episode, unless that
    episode's id is named in ``force``. There is no marker/hash-sidecar to
    distinguish "an earlier, now-superseded split" from "a human's hand-edit"
    — the simplest honest rule treats them the same: something already lives
    there that this run would silently replace, so it requires an explicit
    ``--force <eid>``.
    """
    force_set = {str(f).strip() for f in (force or []) if str(f).strip()}
    forced_used: set[str] = set()
    path = Path(script_path)
    if not path.exists():
        raise SeriesError(f"script not found: {path}")
    text = path.read_text(encoding="utf-8")

    sections: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            eid, title = m.group(1), m.group(2).strip()
            if eid in seen:
                raise SeriesError(
                    f"duplicate episode marker {eid} in {path.name} — each episode "
                    "heading must be unique"
                )
            seen.add(eid)
            current = {"eid": eid, "title": title, "body": []}
            sections.append(current)
        elif current is not None:
            current["body"].append(line)
        # lines before the first marker are preamble and ignored

    if not sections:
        raise SeriesError(
            "未找到分集标记(# E01 <标题> 或 ## E01)。创作性拆分是 AI 的活,"
            "引擎只按显式标记切分 —— 先让 AI 在长稿里标注 `# E0N` 分集标题,"
            "或查阅技能库(manju skills)里的长片拆分工作流,再运行 split-script。"
        )

    results: list[dict[str, Any]] = []
    for sec in sections:
        eid, title = sec["eid"], sec["title"]
        body = "\n".join(sec["body"]).strip()
        content = f"# {title or eid}\n\n" + (body + "\n" if body else "")
        ep_dir = series.episode_project_dir(eid)
        exists = (ep_dir / PROJECT_FILE).exists()
        item: dict[str, Any] = {
            "eid": eid,
            "title": title,
            "exists": exists,
            "created": False,
            "written": False,
            "refused": False,
            "script_path": series.relpath(ep_dir / "story" / "script.md"),
            "chars": len(content),
        }
        if apply:
            if not exists:
                project = new_episode(series, eid, title=title, actor=actor)
                item["created"] = True
            else:
                project = series.open_episode(eid)
            from .yamlio import atomic_write_text

            script_dest = project.root / "story" / "script.md"
            script_dest.parent.mkdir(parents=True, exist_ok=True)
            on_disk = script_dest.read_text(encoding="utf-8") if script_dest.exists() else None
            # round-W #75: something already there, and it is neither the
            # untouched scaffold nor an exact repeat of THIS split's output →
            # refuse without an explicit --force <eid>.
            edited = (on_disk is not None and on_disk != content
                     and on_disk != _SCRIPT_SCAFFOLD)
            if edited and eid not in force_set:
                item["refused"] = True
                item["note"] = (
                    f"{eid} 的 story/script.md 已存在且内容与本次拆分结果不同"
                    "(可能已人工改过,或来自不同版本的原稿)—— 为避免覆盖人工精修,"
                    f"已跳过,未写入。确要用本次拆分结果覆盖,加 --force {eid}。"
                )
            else:
                if edited:
                    forced_used.add(eid)
                atomic_write_text(script_dest, content)
                item["written"] = True
                append_event(series.root, actor, "series_split_script",
                             {"episode": eid, "created": item["created"],
                              "chars": item["chars"], "forced": edited})
        results.append(item)

    return {
        "apply": apply,
        "source": str(path),
        "episodes": results,
        "unused_force": sorted(force_set - forced_used),
        "note": (
            "确定性拆分:只按 # E0N / ## E0N 显式标记切分(创作性拆分是 AI 的活)。"
            "--apply 会创建缺失的分集并写入 story/script.md(覆盖脚手架占位);"
            "已存在且内容不同(人工改过或来自不同原稿)的分集脚本不会被覆盖,"
            "需 --force <eid> 显式确认。"
        ),
    }
