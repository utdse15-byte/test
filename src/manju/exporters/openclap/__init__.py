"""OpenClap (``.clap``) interchange adapter — an ISOLATED, optional exit.

OpenClap lives ONLY here. It never replaces or leaks into Manju's Project /
ShotSpec / Bible / Timeline models or sidecars, and it is read/export/plan
only: ``import-plan`` never writes into a project, never downloads remote
media, and never auto-selects takes. The export is a derived snapshot — nothing
in the build pipeline reads it back as authoritative input.

Public surface (re-exported for callers and the CLI sub-app):

- :func:`export_openclap` — Manju compiled timeline → ``.clap`` (deterministic,
  atomic).
- :func:`read_clap` / :func:`write_clap` — raw-preserving parse / round-trip.
- :func:`inspect_clap` — never-writing summary of a parsed document.
- :func:`build_import_plan` — read-only staged-import plan.
"""

from __future__ import annotations

from .exporter import export_openclap
from .import_plan import PLAN_SCHEMA, build_import_plan
from .io import inspect_clap, read_clap, write_clap
from .model import (
    ClapDocument,
    ClapLimits,
    ClapReadError,
    DEFAULT_LIMITS,
    Diagnostic,
)

__all__ = [
    "export_openclap",
    "read_clap",
    "write_clap",
    "inspect_clap",
    "build_import_plan",
    "PLAN_SCHEMA",
    "ClapDocument",
    "ClapLimits",
    "ClapReadError",
    "DEFAULT_LIMITS",
    "Diagnostic",
]
