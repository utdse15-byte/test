"""Frozen GUI project session — one project, one JobRunner, one lifetime.

A bound GUI process must never hot-swap its project while jobs, logs, and
HTTP handlers share process state. The session is created exactly once
(:meth:`~manju.gui.server.GuiServer.bind_project_once` or server construction
with a project) and is immutable thereafter. Workspace open of a different
project returns ``open_in_new_window`` instead of rebinding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.container import Project
    from .jobs import JobRunner

__all__ = ["ProjectSession"]


@dataclass(frozen=True, slots=True)
class ProjectSession:
    """Immutable binding of a project to its dedicated job runner."""

    project: "Project"
    project_id: str
    runner: "JobRunner"
