"""Asset matrix (round U, goal item 5) — the productized read model over the
EXISTING bible files.

This is NOT a parallel store. Characters, scenes, props, voices and styles all
already live in ``bible/*.yaml`` (§4); the matrix is a single JSON-serializable
read view a later GUI page renders and the @mention system (``core/mentions.py``)
resolves against. Nothing here writes: the matrix is derived on every call from
the bible files plus a REUSE of the ``manju appearances`` walk
(:func:`manju.core.appearances.appearances`) — the same cross-reference of every
bible id against the shots that use it, so the "appears in" column can never
drift from ``manju appearances``.

The bible entry schema is extended additively by :class:`manju.core.models.
AssetEntry`: any entry may gain ``aliases`` / ``relations`` / ``default_position``
/ ``locked_fields``. An entry without them reads back exactly as before, and none
of these participate in a content key (see the AssetEntry note in models.py).

Per-kind rows carry: ``id``, ``name``, ``aliases``, ``description``, ``refs``
(the entry's OWN declared reference images/videos — the bible tier of
``providers/refs.py``, not the per-shot fallback resolution), ``relations``,
``default_position``, ``locked_fields`` and ``appearances`` (the shots that
reference the asset, in index order).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .appearances import _bible_by_file, appearances
from .container import Project
from .models import AssetEntry

# The five bible kinds, as (singular-kind-label, bible-file-basename). The
# singular label is what the matrix and the @mention priority use; the file name
# is the plural bible file (and the key ``appearances`` reports under). Ordered
# by @mention collision priority (goal item 6): character > scene > prop > voice
# > style.
KIND_FILES: tuple[tuple[str, str], ...] = (
    ("character", "characters"),
    ("scene", "scenes"),
    ("prop", "props"),
    ("voice", "voices"),
    ("style", "style"),
)

KIND_PRIORITY: tuple[str, ...] = tuple(kind for kind, _ in KIND_FILES)

# The three kinds ``manju appearances`` actually walks (shot fields reference
# them); voices/styles are consumed globally, never via a shot field, so their
# ``appearances`` column is always empty — the same stance appearances.py takes.
_APPEARANCE_FILES = ("characters", "scenes", "props")

# Reserved top-level keys inside a bible file that are CONFIG, not assets, so the
# matrix must not list them as rows. ``style.yaml`` carries a top-level ``look:``
# mapping (the color look, read by media/render.py) that is not a style asset.
_RESERVED_KEYS: dict[str, set[str]] = {"style": {"look"}}

_REF_IMAGE_KEYS = ("ref_image", "ref_images")
_REF_VIDEO_KEYS = ("ref_video", "ref_videos")


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v not in (None, "")]
    return [str(value)] if value != "" else []


def _entry_refs(entry: dict[str, Any]) -> dict[str, list[str]]:
    """The entry's OWN declared reference media (the ``bible`` tier of
    providers/refs.py). Authored values are passed through verbatim (paths or
    URLs) — the matrix is a read model, not a resolver."""
    images: list[str] = []
    videos: list[str] = []
    for k in _REF_IMAGE_KEYS:
        images.extend(_as_list(entry.get(k)))
    for k in _REF_VIDEO_KEYS:
        videos.extend(_as_list(entry.get(k)))
    return {"images": images, "videos": videos}


def _asset_row(kind: str, asset_id: str, entry: dict[str, Any],
               shots: list[str]) -> dict[str, Any]:
    """One JSON-serializable matrix row. The four productization fields are
    validated + normalized through :class:`AssetEntry` (shape only); name /
    description / refs come straight from the raw entry."""
    view = AssetEntry.model_validate(entry)
    return {
        "kind": kind,
        "id": asset_id,
        "name": entry.get("name"),
        "aliases": list(view.aliases),
        "description": entry.get("description"),
        "refs": _entry_refs(entry),
        "relations": dict(view.relations),
        "default_position": view.default_position,
        "locked_fields": list(view.locked_fields),
        "appearances": list(shots),
    }


def asset_matrix(project: Project) -> dict[str, Any]:
    """Build the full asset matrix — a JSON-serializable read model.

    Returns ``{"shots_total": int, "kinds": {kind: [row, …]}, "note": str}``
    with one row per bible entry in each of the five kinds (in file insertion
    order). The ``appearances`` column is filled by REUSING the
    ``manju appearances`` walk so it never disagrees with that command.
    """
    by_file = _bible_by_file(project)
    app = appearances(project)

    # (file, id) -> ordered shot list, from the reused appearances walk.
    shots_lookup: dict[tuple[str, str], list[str]] = {}
    for file in _APPEARANCE_FILES:
        for rid, present in (app.get(file) or {}).items():
            shots_lookup[(file, rid)] = list(present.get("shots") or [])

    kinds: dict[str, list[dict[str, Any]]] = {}
    for kind, file in KIND_FILES:
        reserved = _RESERVED_KEYS.get(file, set())
        rows: list[dict[str, Any]] = []
        for asset_id, entry in (by_file.get(file) or {}).items():
            if asset_id in reserved:
                continue
            shots = shots_lookup.get((file, asset_id), [])
            rows.append(_asset_row(kind, asset_id, entry, shots))
        kinds[kind] = rows

    return {
        "shots_total": app.get("shots_total", 0),
        "kinds": kinds,
        "note": (
            "资产矩阵是 bible/*.yaml 的读模型(非并行存储);出场列复用 "
            "`manju appearances` 的遍历。voice/style 不由镜头字段引用,出场恒为空。"
        ),
    }


def find_asset(matrix: dict[str, Any], asset_id: str) -> dict[str, Any] | None:
    """The row for ``asset_id`` by exact id, searched in kind-priority order
    (character > scene > prop > voice > style). ``None`` when no kind has it."""
    for kind in KIND_PRIORITY:
        for row in matrix.get("kinds", {}).get(kind, []):
            if row["id"] == asset_id:
                return row
    return None


# --------------------------------------------------------------- lookup index


@dataclass
class AssetLookup:
    """A resolution index over the matrix: id/alias token -> candidate assets,
    kind-priority aware (goal item 6). Built once by :func:`build_lookup`, reused
    by ``core/mentions.py`` and the QC mention advisories.

    ``by_key`` maps a raw token to every ``(kind, asset_id, via)`` it can name,
    where ``via`` is ``"id"``, ``"name"`` or ``"alias"``. ``all_names`` is the
    flat set of every id + display name + alias, for nearest-match hints."""

    by_key: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)
    all_names: list[str] = field(default_factory=list)

    def candidates(self, token: str) -> list[tuple[str, str, str]]:
        """Every asset ``token`` can name, priority-sorted: kind priority first,
        then id before display name before alias within the same kind."""
        order = {k: i for i, k in enumerate(KIND_PRIORITY)}
        via_order = {"id": 0, "name": 1, "alias": 2}
        cands = self.by_key.get(token, [])
        return sorted(
            cands,
            key=lambda c: (order.get(c[0], len(order)),
                           via_order.get(c[2], len(via_order))),
        )

    def resolve(self, token: str) -> tuple[str, str] | None:
        """The winning ``(kind, asset_id)`` for ``token`` under priority, or
        ``None`` if nothing matches."""
        cands = self.candidates(token)
        return (cands[0][0], cands[0][1]) if cands else None

    def is_collision(self, token: str) -> bool:
        """True when ``token`` names more than one DISTINCT asset (across kinds
        or as both an id and an alias) — the case priority silently resolves and
        QC advises on."""
        distinct = {(k, a) for k, a, _ in self.by_key.get(token, [])}
        return len(distinct) > 1


def build_lookup(matrix: dict[str, Any]) -> AssetLookup:
    """Index every id, display name and alias in the matrix for @mention
    resolution.

    The display ``name`` is indexed because it is the identity the user
    actually SEES: ``manju appearances`` prints "old_zhou 周叔", the bible file
    says ``name: 周叔``, and the GUI chips are labelled with it. Typing
    ``@周叔`` therefore has to work — indexing only id+aliases left the most
    natural handle in the project resolving to nothing, with no nearest-match
    hint either (an ASCII-only ``all_names`` gave difflib nothing to match a
    CJK token against). Same asset, extra handle: a name never manufactures a
    collision with its own id, only with a genuinely different asset."""
    by_key: dict[str, list[tuple[str, str, str]]] = {}
    names: list[str] = []
    for kind in KIND_PRIORITY:
        for row in matrix.get("kinds", {}).get(kind, []):
            aid = row["id"]
            by_key.setdefault(aid, []).append((kind, aid, "id"))
            names.append(aid)
            display = row.get("name")
            if isinstance(display, str) and display and display != aid:
                by_key.setdefault(display, []).append((kind, aid, "name"))
                names.append(display)
            for alias in row.get("aliases") or []:
                by_key.setdefault(alias, []).append((kind, aid, "alias"))
                names.append(alias)
    return AssetLookup(by_key=by_key, all_names=names)
