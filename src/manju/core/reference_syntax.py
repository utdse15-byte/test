"""Pure authored-reference syntax shared by resolution and picture specs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator


@dataclass(frozen=True)
class AuthoredReferenceBinding:
    """One un-resolved binding with its authored tier and forced media kind."""

    value: Any
    kind: str | None
    tier: str
    inferred_scope: str | None = None


def _entries(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [item for item in value if item not in (None, "")]
    return [value]


def _mapping_bindings(value: dict, *, tier: str) -> Iterator[AuthoredReferenceBinding]:
    for key in ("image", "images"):
        for entry in _entries(value.get(key)):
            yield AuthoredReferenceBinding(entry, "image", tier)
    for key in ("video", "videos"):
        for entry in _entries(value.get(key)):
            yield AuthoredReferenceBinding(entry, "video", tier)
    for entry in _entries(value.get("refs")):
        yield AuthoredReferenceBinding(entry, None, tier)


def iter_authored_reference_bindings(
    shot: Any,
    bible: dict[str, dict[str, Any]] | None = None,
    *,
    params: dict[str, Any] | None = None,
) -> Iterator[AuthoredReferenceBinding]:
    """Yield every authored binding in provider resolution order.

    The function only expands syntax. It does no file I/O, ownership validation,
    deduplication, provider selection, or delivery budgeting.
    """

    if params is None:
        generation = getattr(shot, "generation", None)
        params = dict(getattr(generation, "params", None) or {})
    yield from _mapping_bindings(dict(params or {}), tier="params")

    refs = getattr(shot, "refs", None)
    if isinstance(refs, dict):
        yield from _mapping_bindings(refs, tier="shot_refs")
    else:
        for entry in _entries(refs):
            yield AuthoredReferenceBinding(entry, None, "shot_refs")

    bible = bible or {}
    subjects: list[tuple[str, str]] = [
        (str(character_id), f"character:{character_id}")
        for character_id in (getattr(shot, "characters", None) or [])
    ]
    scene = getattr(shot, "scene", None)
    if scene:
        subjects.append((str(scene), f"scene:{scene}"))
    subjects.extend(
        (str(prop_id), f"prop:{prop_id}")
        for prop_id in (getattr(shot, "props", None) or [])
        if prop_id
    )
    for bible_id, scope in subjects:
        entry = bible.get(bible_id)
        if not isinstance(entry, dict):
            continue
        for key in ("ref_image", "ref_images"):
            for value in _entries(entry.get(key)):
                yield AuthoredReferenceBinding(value, "image", "bible", scope)
        for key in ("ref_video", "ref_videos"):
            for value in _entries(entry.get(key)):
                yield AuthoredReferenceBinding(value, "video", "bible", scope)


__all__ = ["AuthoredReferenceBinding", "iter_authored_reference_bindings"]
