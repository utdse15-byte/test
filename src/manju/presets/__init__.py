"""Preset kits — pre-filled project skeletons for `manju new --preset` (P3).

A preset is *just a pre-filled project skeleton* (DESIGN_v2.2 §12 / decision 6):
it pre-fills project.yaml / rules.yaml / packaging.yaml / story scaffolds at
`manju new` time and then NEVER binds the project afterwards. Everything a
preset writes is plain, hand-editable YAML/markdown — the project format stays
model- and preset-independent. No preset ⇒ today's generic scaffold, unchanged.

The kit that scaffolded a project is recorded only as `project.yaml: preset:
<name>` (a label, ProjectConfig already carries it); nothing downstream reads
that field to change behaviour. Presets are data (``data/*.yaml``), loaded via
``importlib.resources`` so they ship with the package.
"""

from __future__ import annotations

import unicodedata
from importlib import resources
from math import gcd
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..core.models import PackagingSpec, ProjectConfig, TimelineRules

# Deterministic display/enumeration order. Every file in data/ must appear here;
# list_presets() falls back to appending any stragglers sorted, but the invariant
# (and a test) is that these three are exactly the shipped kits. The kits are
# deliberately content-type-neutral (blank plus a vertical/horizontal frame) —
# the agent infers the genre from the input at `manju new` time.
PRESET_ORDER: tuple[str, ...] = (
    "blank",
    "vertical_ai_video",
    "horizontal_ai_video",
)


class PresetError(ValueError):
    """Unknown preset name / malformed preset data — carries a clean message
    (the CLI relays it verbatim via `_fail`)."""


# --------------------------------------------------------------- display helpers


def display_width(text: str) -> int:
    """Visual column width of ``text`` in a monospaced terminal: East-Asian
    Wide (W) and Fullwidth (F) characters take two columns, everything else one.

    ``str.ljust`` counts code points, so a CJK title (each char is one ``len``
    unit but two display columns) drifts under a naive pad — the bilingual
    ``manju presets`` table needs this to line up."""
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text
    )


def pad(text: str, width: int) -> str:
    """Left-justify ``text`` to a visual ``width`` (the CJK-aware cousin of
    ``str.ljust``). Pads with trailing spaces to reach ``width`` display
    columns; never truncates when already at/over ``width``."""
    gap = width - display_width(text)
    return text + " " * gap if gap > 0 else text


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``over`` onto a copy of ``base``. Nested dicts merge;
    every other value (scalars, lists) is replaced wholesale — a preset that
    sets ``fallback: [...]`` means exactly that list, not an append."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class ProjectOverrides(BaseModel):
    """The subset of project.yaml a preset may pre-fill (all optional/partial).
    Anything else about the project stays the plain `manju new` default."""

    model_config = ConfigDict(extra="forbid")

    width: int | None = None
    height: int | None = None
    fps: int | None = None
    export_profiles: list[str] | None = None


class PresetSpec(BaseModel):
    """One preset kit. Local to this module — deliberately NOT in core.models:
    presets are an authoring convenience layered on top of the frozen project
    format, not part of it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    title: str  # bilingual is fine, e.g. "漫剧 / Comic drama"
    description: str = ""
    project: ProjectOverrides = Field(default_factory=ProjectOverrides)
    # Partial rules, deep-merged over TimelineRules() defaults at apply time.
    rules: dict[str, Any] = Field(default_factory=dict)
    # Optional packaging (intro/outro/cover/teaser/info cards).
    packaging: dict[str, Any] | None = None
    # Advisory strings surfaced by `manju status` and recorded in project.yaml.
    qc_focus: list[str] = Field(default_factory=list)
    # Project-relative path -> seed text (markdown scaffolds, e.g. story/brief.md).
    scaffold: dict[str, str] = Field(default_factory=dict)

    # -- derived -----------------------------------------------------------

    def resolution(self) -> tuple[int, int]:
        return (self.project.width or 1080, self.project.height or 1920)

    def aspect(self) -> str:
        """Aspect ratio label, e.g. '9:16' (vertical) or '16:9' (landscape)."""
        w, h = self.resolution()
        g = gcd(w, h) or 1
        return f"{w // g}:{h // g}"

    def merged_rules(self) -> TimelineRules:
        """The preset's rules deep-merged over TimelineRules defaults, validated.
        This is exactly what gets written to timeline/rules.yaml."""
        merged = _deep_merge(TimelineRules().model_dump(), self.rules)
        return TimelineRules.model_validate(merged)

    def packaging_spec(self) -> PackagingSpec | None:
        if self.packaging is None:
            return None
        return PackagingSpec.model_validate(self.packaging)

    @model_validator(mode="after")
    def _validate_overrides(self) -> "PresetSpec":
        # Fail at load time if the merged rules / packaging don't validate
        # against the real engine models — a preset that can't produce a valid
        # project is a bug in the kit, not a runtime surprise.
        self.merged_rules()
        self.packaging_spec()
        return self

    def to_public_dict(self) -> dict[str, Any]:
        """Stable shape for `manju presets --json` and the local GUI."""
        w, h = self.resolution()
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "aspect": self.aspect(),
            "resolution": f"{w}x{h}",
            "fps": self.project.fps,
            "project": self.project.model_dump(exclude_none=True),
            "rules": self.merged_rules().model_dump(),
            "packaging": (self.packaging_spec().model_dump()
                          if self.packaging is not None else None),
            "qc_focus": list(self.qc_focus),
            "scaffold": sorted(self.scaffold.keys()),
        }


# --------------------------------------------------------------------- loading


def _data_dir():
    return resources.files(__package__) / "data"


def _available_names() -> list[str]:
    names = sorted(
        p.name[: -len(".yaml")]
        for p in _data_dir().iterdir()
        if p.name.endswith(".yaml")
    )
    # Canonical order first, any extras appended (defensive — normally empty).
    ordered = [n for n in PRESET_ORDER if n in names]
    return ordered + [n for n in names if n not in PRESET_ORDER]


def load_preset(name: str) -> PresetSpec:
    """Load one preset by name. Unknown name → PresetError listing the
    available kits (§: a clean error, never a traceback)."""
    available = _available_names()
    if name not in available:
        raise PresetError(
            f"unknown preset '{name}'. available: {', '.join(available)}"
        )
    text = (_data_dir() / f"{name}.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    data.setdefault("name", name)
    return PresetSpec.model_validate(data)


def list_presets() -> list[PresetSpec]:
    """All shipped presets in deterministic order."""
    return [load_preset(name) for name in _available_names()]


# --------------------------------------------------------------------- applying


def apply_preset(project, spec: PresetSpec) -> None:
    """Pre-fill a freshly created project from a preset. Idempotent-ish writes:
    project.yaml overrides + preset label + qc_focus, timeline/rules.yaml (the
    merged rules), timeline/packaging.yaml (when the preset carries one), and
    the scaffold seeds. Nothing here binds the project — it's all plain files
    the human/agent edits from here on."""
    # 1. project.yaml — width/height/fps/export_profiles overrides, plus the
    #    preset label and advisory qc_focus (an extra field: ProjectConfig is
    #    extra="allow", so it round-trips as plain YAML).
    config = project.load_config()
    data = config.model_dump()
    ov = spec.project
    if ov.width is not None:
        data["width"] = ov.width
    if ov.height is not None:
        data["height"] = ov.height
    if ov.fps is not None:
        data["fps"] = ov.fps
    if ov.export_profiles is not None:
        data["export_profiles"] = ov.export_profiles
    data["preset"] = spec.name
    if spec.qc_focus:
        data["qc_focus"] = list(spec.qc_focus)
    project.save_config(ProjectConfig.model_validate(data))

    # 2. timeline/rules.yaml — the merged, validated TimelineRules.
    project.save_rules(spec.merged_rules())

    # 3. timeline/packaging.yaml — only when the preset ships one.
    pkg = spec.packaging_spec()
    if pkg is not None:
        project.save_packaging(pkg)

    # 4. scaffold seeds — additive/replacing the default story scaffolds. `new`
    #    just created the project, so there's nothing of the human's to clobber.
    from ..core.yamlio import atomic_write_text

    for relpath, seed in spec.scaffold.items():
        dest = project.resolve(relpath)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # atomic_write_text, not Path.write_text: the latter uses newline=None,
        # which on WINDOWS turns every "\n" into CRLF — so the same preset would
        # seed different bytes (and different content hashes) there than
        # anywhere else. A scaffold has to be byte-identical across platforms.
        atomic_write_text(dest, seed)
