"""OpenClap format knowledge — the single place that names what we *recognize*.

This adapter treats ``.clap`` as an OPEN format: we recognize the values the
reference implementation (aitube-clap) emits, but we never reject a file over
an unknown enum value, an unknown key, or an unknown provider string. The
constants here drive three things and nothing else:

- classification (which segment category is a playable clip vs. a semantic
  annotation, which locator kind an ``assetUrl`` is),
- normalization of the two documented reference defects (the ``COMFUI``
  provider typo, the mixed-dimension category enum),
- unknown-field statistics for ``inspect`` (a key is "unknown" only relative
  to the known-key sets below — the raw store keeps it regardless).

Nothing here is authoritative over Manju's own models; it only describes the
foreign format so :mod:`.io` / :mod:`.import_plan` can read it safely.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

#: The format tag we WRITE. Reading accepts any ``format`` string (preserved).
FORMAT = "clap-0"

# ---------------------------------------------------------------- categories
# ``ClapSegmentCategory`` mixes several dimensions in one enum. We keep the
# canonical spelling UPPER-CASE and recognize case-insensitively; anything not
# here is preserved verbatim and classified as ``unknown``.

# Timed, playable media — these become clips / media roles on import.
MEDIA_CATEGORIES = frozenset({"VIDEO", "STORYBOARD", "MUSIC", "SOUND", "DIALOGUE"})

# Descriptive / semantic annotations — NEVER planned as playable clips.
ENTITY_CATEGORIES = frozenset({"CHARACTER", "LOCATION"})
SCENE_CATEGORIES = frozenset({"TIME", "LIGHTING", "WEATHER", "STYLE", "CAMERA"})
ACTION_CATEGORIES = frozenset({"ACTION", "EVENT", "PHENOMENON"})
EDIT_CATEGORIES = frozenset({"TRANSITION", "EFFECT"})
THREE_D_CATEGORIES = frozenset({"SPLAT", "MESH", "DEPTH"})
UI_CATEGORIES = frozenset({"INTERFACE"})

SEMANTIC_CATEGORIES = (
    ENTITY_CATEGORIES
    | SCENE_CATEGORIES
    | ACTION_CATEGORIES
    | EDIT_CATEGORIES
    | THREE_D_CATEGORIES
    | UI_CATEGORIES
)

KNOWN_CATEGORIES = MEDIA_CATEGORIES | SEMANTIC_CATEGORIES


def classify_category(raw: object) -> tuple[str | None, bool]:
    """``(canonical, is_known)`` for a raw category value.

    Recognition is case-insensitive; the canonical form is UPPER-CASE. An
    unknown or non-string value returns ``(None, False)`` — the caller keeps
    the raw value verbatim, this only says whether we understand it.
    """
    if not isinstance(raw, str):
        return None, False
    canon = raw.strip().upper()
    if canon in KNOWN_CATEGORIES:
        return canon, True
    return None, False


def is_media_category(raw: object) -> bool:
    canon, known = classify_category(raw)
    return known and canon in MEDIA_CATEGORIES


def is_semantic_category(raw: object) -> bool:
    canon, known = classify_category(raw)
    return known and canon in SEMANTIC_CATEGORIES


# ------------------------------------------------------------------ providers
# Reference defect #1: the ComfyUI enum value ships as ``"COMFUI"`` (missing Y).
# Accept both, normalize to a lower-case canonical in the typed view; the raw
# store always keeps the original string.
_PROVIDER_ALIASES = {
    "comfui": "comfyui",   # the documented aitube-clap typo
    "comfyui": "comfyui",
}


def normalize_provider(raw: object) -> str | None:
    """Lower-cased canonical provider name, or ``None`` for a non-string.

    Only the ``COMFUI`` typo is actively repaired; every other provider string
    is returned lower-cased as-is (never rejected — an unknown provider is
    preserved, not an error).
    """
    if not isinstance(raw, str):
        return None
    key = raw.strip().lower()
    return _PROVIDER_ALIASES.get(key, key)


# ------------------------------------------------------------------ locators
# An ``assetUrl`` is a LOCATOR, never content identity. We classify its shape;
# we never dereference a remote URL and never read an absolute/escaping path.

LocatorKind = Literal["remote_url", "data_uri", "project_path", "absolute_path", "other"]


def classify_locator(value: object) -> LocatorKind:
    """Classify a locator string by shape only (no filesystem, no network)."""
    if not isinstance(value, str) or not value:
        return "other"
    low = value.strip().lower()
    if low.startswith(("http://", "https://")):
        return "remote_url"
    if low.startswith("data:"):
        return "data_uri"
    # A Windows drive path, a POSIX absolute path, or a file:// URI is absolute.
    if value.startswith("/") or value.startswith("\\") or low.startswith("file:"):
        return "absolute_path"
    if len(value) >= 2 and value[1] == ":" and value[0].isalpha():
        return "absolute_path"
    # Anything else that looks like a relative filesystem path.
    if "/" in value or "\\" in value or value.endswith(
        (".mp4", ".mov", ".wav", ".mp3", ".png", ".jpg", ".jpeg", ".webm", ".m4a")
    ):
        return "project_path"
    return "other"


def locator_escapes_root(value: str) -> bool:
    """Cheap string-level check: does a *relative* locator traverse upward?

    This is a fast pre-filter for diagnostics; the authoritative containment
    check is still ``Project.resolve`` (which also resolves symlinks). Absolute
    paths are handled by :func:`classify_locator`; here we only catch ``..``
    escapes in an otherwise-relative locator.
    """
    try:
        parts = PurePosixPath(value.replace("\\", "/")).parts
    except Exception:
        return True
    depth = 0
    for part in parts:
        if part == "..":
            depth -= 1
            if depth < 0:
                return True
        elif part not in ("", "."):
            depth += 1
    return False


# --------------------------------------------------------------- known keys
# Used ONLY by ``inspect`` to report unknown-field statistics. A key outside
# these sets is still read and still preserved on write — "unknown" here means
# "not part of the documented reference schema", nothing more.

KNOWN_HEADER_KEYS = frozenset({
    "format", "numberOfWorkflows", "numberOfEntities", "numberOfScenes",
    "numberOfSegments",
})

KNOWN_META_KEYS = frozenset({
    "id", "title", "description", "synopsis", "licence", "license", "tags",
    "thumbnail", "thumbnailUrl", "orientation", "durationInMs", "width",
    "height", "defaultVideoModel", "extraPositivePrompt", "screenplay",
    "isLoop", "isInteractive", "bpm", "frameRate", "imageRatio", "systemPrompt",
})

KNOWN_WORKFLOW_KEYS = frozenset({
    "id", "name", "category", "provider", "engine", "data", "inputFields",
    "inputValues", "label",
})

KNOWN_ENTITY_KEYS = frozenset({
    "id", "category", "triggerName", "label", "description", "author",
    "thumbnailUrl", "seed", "imagePrompt", "imageSourceType", "imageEngine",
    "imageId", "audioPrompt", "audioSourceType", "audioEngine", "audioId",
    "age", "gender", "region", "appearance",
})

KNOWN_SCENE_KEYS = frozenset({
    "id", "scene", "line", "rawLine", "sequenceFullText", "sequenceStartAtLine",
    "sequenceEndAtLine", "startTimeInLines", "endTimeInLines", "events",
    "startAtLine", "endAtLine",
})

KNOWN_SEGMENT_KEYS = frozenset({
    "id", "track", "startTimeInMs", "endTimeInMs", "category", "entityId",
    "workflowId", "sceneId", "startTimeInLines", "endTimeInLines", "prompt",
    "label", "outputType", "renderId", "status", "assetUrl", "assetDurationInMs",
    "assetSourceType", "assetFileFormat", "createdAt", "createdBy", "revision",
    "editedBy", "outputGain", "seed",
})


def known_keys_for(section: str) -> frozenset[str]:
    return {
        "header": KNOWN_HEADER_KEYS,
        "meta": KNOWN_META_KEYS,
        "workflow": KNOWN_WORKFLOW_KEYS,
        "entity": KNOWN_ENTITY_KEYS,
        "scene": KNOWN_SCENE_KEYS,
        "segment": KNOWN_SEGMENT_KEYS,
    }.get(section, frozenset())


#: The Manju-namespaced extension key carried inside meta/entity/scene/segment
#: mappings for anything that is not a provably-standard clap field.
MANJU_EXT_KEY = "x-manju"
