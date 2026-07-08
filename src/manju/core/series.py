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

# The umbrella sub-directories `Series.create` scaffolds (bible/ is filled with
# the same file set as a project bible below).
SERIES_DIRS = ("bible", "episodes", "script", "reports")

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
        so from inside ``episodes/E01.manju`` this climbs to the series root."""
        p = Path(start).resolve()
        for candidate in [p, *p.parents]:
            if (candidate / SERIES_FILE).exists():
                return cls(candidate)
        raise SeriesError(f"no manju series found from {p} upward")

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

        for sub in SERIES_DIRS:
            (root / sub).mkdir(parents=True, exist_ok=True)
        # The GLOBAL series bible: the SAME file set as a project bible (§4),
        # empty scaffolds. new_episode seeds an episode's bible from these.
        for bible_file in BIBLE_FILES:
            bpath = root / "bible" / f"{bible_file}.yaml"
            if not bpath.exists():
                write_yaml(bpath, {})

        config = SeriesConfig(
            name=name,
            description=description,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        write_yaml(root / SERIES_FILE, config.model_dump(exclude_none=True))
        (root / "events.jsonl").touch()

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
    update). The NEW episode directory itself needs no lock (``Project.create``
    + the bible seed write into a path nothing else can know about until this
    call returns), so only the register step — load config, append, save,
    all INSIDE the series root's own ``BuildLock`` — is locked; that is the
    one write here that touches something already known to other processes.
    A busy series lock leaves the new project directory ON DISK but
    UNREGISTERED — honestly reported (never silently dropped): ``Series.
    episode_ids()`` already folds in on-disk-but-unregistered ``*.manju``
    dirs, so a retry (or ``manju series episodes``) finds it either way.
    """
    if not _EID_RE.match(eid):
        raise SeriesError(
            f"invalid episode id {eid!r}: use E01/E02… or a lowercase slug (a-z0-9_-)"
        )
    ep_dir = series.episode_project_dir(eid)
    config = series.load_config()
    if any(e.id == eid for e in config.episodes) or (ep_dir / PROJECT_FILE).exists():
        raise SeriesError(f"episode already exists: {eid}")

    # Resolve the preset up-front so a bad name fails before we scaffold anything.
    spec = None
    if preset is not None:
        from ..presets import PresetError, load_preset

        try:
            spec = load_preset(preset)
        except PresetError as exc:
            raise SeriesError(str(exc)) from exc

    project = Project.create(ep_dir, name=title or eid, git_init=git_init)
    if spec is not None:
        from ..presets import apply_preset

        apply_preset(project, spec)

    _seed_episode_bible(series, project)

    from ..runtime.buildlock import BuildLocked, build_lock

    try:
        with build_lock(series.root, actor=actor):
            # re-read INSIDE the lock: another new_episode may have landed
            # between our pre-check above and here — never silently clobber
            # a concurrent registration.
            config = series.load_config()
            if not any(e.id == eid for e in config.episodes):
                config.episodes.append(EpisodeRef(id=eid, title=title))
                series.save_config(config)
    except BuildLocked as exc:
        # NOTE: the project directory + its seeded bible are ALREADY on disk
        # and safe (never rolled back — nothing else could see them yet
        # anyway); only the series.yaml registration step failed. Re-running
        # new_episode(eid) would now hit the "episode already exists" guard
        # above (the directory is there), so recovery is either wait for the
        # lock to free and hand-append {id, title} to series.yaml, or just
        # use the project — `Series.episode_ids()` already folds in an
        # on-disk-but-unregistered `*.manju` dir, so `manju series episodes`
        # / `series-status` see it either way.
        raise SeriesError(
            f"{eid} 的项目目录与 bible 已创建,但 series.yaml 正被其它进程占用"
            f"(build_lock),未能登记 —— 等锁释放后手动在 series.yaml 里补一条 "
            f"{{id: {eid}, title: ...}} 即可(目录已存在,重跑 new-episode 会报"
            "\"already exists\");`manju series episodes` 已能看到这个未登记的目录。"
            f"{' '.join(str(exc).split())}"
        ) from exc

    append_event(series.root, actor, "series_new_episode",
                 {"episode": eid, "title": title, "preset": preset})
    return project


# ------------------------------------------------------------- series_status


def series_status(series: Series) -> dict[str, Any]:
    """Per-episode aggregation over the EXISTING per-project machinery — read-only.

    Each episode reuses ``build.stale.evaluate_all`` (the same state summary
    `manju status` prints), ``Project.newest_final_path`` (the one numeric final
    resolver), and ``build.spend.spend_report`` (ledger-or-sidecar total, §3
    fallback). An unreadable episode degrades to ``{"error": …}`` instead of
    killing the whole table. A totals row sums shots / finals / cost across the
    readable episodes.
    """
    from ..build.spend import spend_report
    from ..build.stale import evaluate_all

    config = series.load_config()
    episodes: list[dict[str, Any]] = []
    totals = {"episodes": 0, "ok": 0, "errors": 0, "shots": 0, "finals": 0, "cost": 0.0}
    currency: str | None = None

    for ref in config.episodes:
        entry: dict[str, Any] = {"id": ref.id, "title": ref.title}
        totals["episodes"] += 1
        try:
            project = series.open_episode(ref.id)
            statuses = evaluate_all(project)
            by_state: dict[str, int] = {}
            for st in statuses:
                by_state[st.state.value] = by_state.get(st.state.value, 0) + 1
            final = project.newest_final_path()
            spend = spend_report(project)
            cost = float(spend.get("total") or 0.0)
            entry.update({
                "shots_total": len(statuses),
                "shots_by_state": by_state,
                "latest_final": project.relpath(final) if final else None,
                "cost": cost,
                "currency": spend.get("currency"),
                "spend_source": spend.get("source"),
            })
            totals["ok"] += 1
            totals["shots"] += len(statuses)
            totals["finals"] += 1 if final else 0
            totals["cost"] += cost
            if spend.get("currency"):
                currency = spend["currency"]
        except Exception as exc:  # degrade per-episode, never kill the table
            entry["error"] = str(exc)
            totals["errors"] += 1
        episodes.append(entry)

    totals["currency"] = currency
    return {"series": config.name, "episodes": episodes, "totals": totals}


# ---------------------------------------------------------- series_characters


def series_characters(series: Series) -> dict[str, Any]:
    """The GLOBAL character view (the "global character management" read model).

    Keyed on the series bible's characters: for each, its per-episode presence
    (in that episode's bible? diverged from the series entry?) and its per-episode
    appearances (reusing the ``manju appearances`` walk per episode). Characters
    present only in an episode bible (never promoted to the series bible) are
    listed under ``episode_only`` so the global view is honest about drift.
    """
    from .appearances import appearances

    config = series.load_config()
    series_chars = _load_series_bible_by_file(series).get("characters", {})
    ep_ids = [e.id for e in config.episodes]

    # Open each episode once: its appearances walk + its own characters bible.
    ep_ctx: dict[str, dict[str, Any]] = {}
    for ref in config.episodes:
        try:
            project = series.open_episode(ref.id)
            ep_ctx[ref.id] = {
                "chars": _load_episode_bible_file(project, "characters"),
                "app": appearances(project).get("characters", {}),
                "error": None,
            }
        except Exception as exc:
            ep_ctx[ref.id] = {"error": str(exc)}

    characters: list[dict[str, Any]] = []
    for cid, s_entry in series_chars.items():
        row: dict[str, Any] = {
            "id": cid,
            "name": s_entry.get("name"),
            "episodes": [],
        }
        for eid in ep_ids:
            ctx = ep_ctx[eid]
            cell: dict[str, Any] = {"id": eid}
            if ctx.get("error"):
                cell["error"] = ctx["error"]
            else:
                ep_entry = ctx["chars"].get(cid)
                cell["present"] = ep_entry is not None
                cell["diverged"] = bool(ep_entry is not None and ep_entry != s_entry)
                cell["appearances"] = list((ctx["app"].get(cid) or {}).get("shots") or [])
            row["episodes"].append(cell)
        characters.append(row)

    # Episode-only characters: in some episode's bible but never in the series one.
    episode_only: dict[str, list[str]] = {}
    for eid in ep_ids:
        ctx = ep_ctx[eid]
        if ctx.get("error"):
            continue
        for cid in ctx["chars"]:
            if cid not in series_chars:
                episode_only.setdefault(cid, []).append(eid)

    return {
        "series": config.name,
        "episodes": ep_ids,
        "characters": characters,
        "episode_only": [{"id": cid, "episodes": eps} for cid, eps in episode_only.items()],
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
