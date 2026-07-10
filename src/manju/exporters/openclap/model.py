"""Typed, raw-preserving views over a parsed ``.clap`` document.

The reference sanitizers rebuild fixed-shape objects and drop everything they
do not recognize. We do the opposite: :func:`.io.read_clap` keeps every
original mapping verbatim in :attr:`ClapDocument.raw_items` (unknown keys,
unknown enum values, original order all intact), and the dataclasses here are
thin VIEWS that hold a reference to their raw dict plus normalized accessors.

Two rules make this safe:

- A view NEVER copies-and-drops: ``self.raw`` *is* the dict stored in
  ``raw_items``, so a patch (``view.patch(key, value)``) mutates the very
  object :func:`.io.write_clap` re-serializes — known fields change, unknown
  keys stay.
- Normalization lives on read-only properties (``.category``, ``.provider``);
  the raw value is always reachable via ``.raw``. The typed view is a lens,
  never a destructive sanitizer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import profile

# --------------------------------------------------------------- diagnostics


@dataclass(frozen=True)
class Diagnostic:
    """One structured note accumulated during read/verify.

    ``severity`` is ``error`` | ``warning`` | ``info``; ``code`` is a stable
    machine token; ``path`` locates the offending item (e.g. ``items[7]`` or
    ``header.numberOfSegments``). Errors abort the read (raised inside a
    :class:`ClapReadError`); warnings and info ride along on success.
    """

    severity: str
    code: str
    message: str
    path: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }


class ClapError(RuntimeError):
    """Base class for adapter faults (subclasses :class:`RuntimeError` so the
    CLI's existing ``(OSError, RuntimeError)`` export guard covers it)."""


class ClapReadError(ClapError):
    """A fail-closed read: carries the diagnostics that explain why.

    The CLI turns :attr:`diagnostics` into a structured JSON error envelope;
    a caller can branch on the first error's ``code``.
    """

    def __init__(self, message: str, diagnostics: list[Diagnostic] | None = None):
        super().__init__(message)
        self.diagnostics: list[Diagnostic] = list(diagnostics or [])


@dataclass(frozen=True)
class ClapLimits:
    """Resource caps enforced by :func:`.io.read_clap` (fail closed on breach).

    Defaults are generous for real projects and tiny for the guard tests, which
    construct their own :class:`ClapLimits` to trip a specific ceiling.
    """

    max_compressed_bytes: int = 64 * 1024 * 1024      # 64 MiB — pre-read size check
    max_decompressed_bytes: int = 256 * 1024 * 1024   # 256 MiB — streamed, abort mid-way
    max_items: int = 100_000                          # top-level array length
    max_nesting_depth: int = 32                       # cheap recursive-walk cap


DEFAULT_LIMITS = ClapLimits()


# ---------------------------------------------------------------- base view


@dataclass
class _RawView:
    """A view whose identity is its (shared, mutable) raw mapping."""

    raw: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def patch(self, key: str, value: Any) -> None:
        """Write a KNOWN field into the original raw dict (export write path).

        Mutating ``self.raw`` mutates the object :func:`.io.write_clap`
        re-serializes, so unknown keys are untouched and this one field moves.
        """
        self.raw[key] = value

    def unknown_keys(self, section: str) -> list[str]:
        """Keys on this mapping outside the documented schema for ``section``.

        The Manju extension key (``x-manju``) is intentionally NOT counted as
        unknown — it is our own namespaced escape hatch, not foreign drift.
        """
        known = profile.known_keys_for(section)
        return sorted(
            k for k in self.raw
            if k not in known and k != profile.MANJU_EXT_KEY
        )


# ------------------------------------------------------------------- header


@dataclass
class ClapHeader(_RawView):
    @property
    def format(self) -> str | None:
        v = self.raw.get("format")
        return v if isinstance(v, str) else None

    def _count(self, key: str) -> int | None:
        v = self.raw.get(key)
        if isinstance(v, bool):  # bool is an int subclass — a count it is not
            return None
        if isinstance(v, int):
            return v
        return None

    @property
    def declared_workflows(self) -> int | None:
        return self._count("numberOfWorkflows")

    @property
    def declared_entities(self) -> int | None:
        return self._count("numberOfEntities")

    @property
    def declared_scenes(self) -> int | None:
        return self._count("numberOfScenes")

    @property
    def declared_segments(self) -> int | None:
        return self._count("numberOfSegments")


# --------------------------------------------------------------------- meta


@dataclass
class ClapMeta(_RawView):
    @property
    def title(self) -> str | None:
        v = self.raw.get("title")
        return v if isinstance(v, str) else None

    @property
    def width(self) -> int | None:
        v = self.raw.get("width")
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    @property
    def height(self) -> int | None:
        v = self.raw.get("height")
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    @property
    def duration_in_ms(self) -> int | None:
        v = self.raw.get("durationInMs")
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    @property
    def orientation(self) -> str | None:
        v = self.raw.get("orientation")
        return v if isinstance(v, str) else None


# ----------------------------------------------------------------- workflow


@dataclass
class ClapWorkflow(_RawView):
    @property
    def id(self) -> Any:
        return self.raw.get("id")

    @property
    def raw_provider(self) -> Any:
        """The provider string exactly as parsed (``COMFUI`` stays ``COMFUI``)."""
        return self.raw.get("provider")

    @property
    def provider(self) -> str | None:
        """Normalized provider (``COMFUI`` → ``comfyui``); raw kept on ``.raw``."""
        return profile.normalize_provider(self.raw.get("provider"))


# ------------------------------------------------------------------- entity


@dataclass
class ClapEntity(_RawView):
    @property
    def id(self) -> Any:
        return self.raw.get("id")

    @property
    def raw_category(self) -> Any:
        return self.raw.get("category")

    @property
    def category(self) -> str | None:
        canon, _known = profile.classify_category(self.raw.get("category"))
        return canon

    @property
    def label(self) -> str | None:
        v = self.raw.get("label")
        return v if isinstance(v, str) else None


# -------------------------------------------------------------------- scene


@dataclass
class ClapScene(_RawView):
    @property
    def id(self) -> Any:
        return self.raw.get("id")


# ------------------------------------------------------------------ segment


@dataclass
class ClapSegment(_RawView):
    @property
    def id(self) -> Any:
        return self.raw.get("id")

    @property
    def track(self) -> Any:
        return self.raw.get("track")

    @property
    def raw_category(self) -> Any:
        return self.raw.get("category")

    @property
    def category(self) -> str | None:
        """Canonical UPPER category when recognized, else ``None`` (raw kept)."""
        canon, _known = profile.classify_category(self.raw.get("category"))
        return canon

    @property
    def category_is_known(self) -> bool:
        _canon, known = profile.classify_category(self.raw.get("category"))
        return known

    @property
    def start_time_in_ms(self) -> int | float | None:
        v = self.raw.get("startTimeInMs")
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    @property
    def end_time_in_ms(self) -> int | float | None:
        """``endTimeInMs`` validated ON ITS OWN MERITS (reference defect #2).

        The reference sanitizer checks ``assetDurationInMs`` instead; we do
        not. A numeric ``endTimeInMs`` is returned even when
        ``assetDurationInMs`` is absent; a missing/invalid one returns ``None``
        (and :func:`.io.read_clap` records a per-segment warning) — never
        silently zeroed, and the raw value is preserved regardless.
        """
        v = self.raw.get("endTimeInMs")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        if v < 0:
            return None
        return v

    @property
    def has_valid_end_time(self) -> bool:
        return self.end_time_in_ms is not None

    @property
    def asset_url(self) -> str | None:
        v = self.raw.get("assetUrl")
        return v if isinstance(v, str) and v else None

    @property
    def locator_kind(self) -> str:
        return profile.classify_locator(self.raw.get("assetUrl"))


# ----------------------------------------------------------------- document


@dataclass
class ClapDocument:
    """A fully-parsed, raw-preserving ``.clap`` document.

    ``raw_items`` is the untouched top-level array (original order); the typed
    views below each hold a reference INTO that list, so patching a view and
    calling :func:`.io.write_clap` round-trips unknown fields and item order.
    """

    raw_items: list[dict[str, Any]]
    header: ClapHeader
    meta: ClapMeta
    workflows: list[ClapWorkflow] = field(default_factory=list)
    entities: list[ClapEntity] = field(default_factory=list)
    scenes: list[ClapScene] = field(default_factory=list)
    segments: list[ClapSegment] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def actual_counts(self) -> dict[str, int]:
        return {
            "workflows": len(self.workflows),
            "entities": len(self.entities),
            "scenes": len(self.scenes),
            "segments": len(self.segments),
        }
