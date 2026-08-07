"""Shared pytest fixtures for the Manju One core test suite.

These fixtures import ONLY from the frozen core surface
(``manju.core.*``, ``manju.build.stale``, ``manju.timeline.compiler``) so the
suite stays green while sibling modules (media/providers/qc/exporters/board)
are still being written concurrently.

Fake media is used everywhere: the tests exercised here never probe media, so
a few bytes on disk is enough to make ``register_take`` and the take-lookup
machinery happy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from manju.core.container import Project, TakeInfo
from manju.core.models import ShotSpec, TakeSidecar
from manju.core.yamlio import write_yaml

# A project name with CJK characters — Chinese/Windows paths are first-class (§14).
PROJECT_NAME = "雨夜便利店"


@pytest.fixture(autouse=True)
def _isolate_gui_state(monkeypatch, tmp_path):
    """Round U: the per-user GUI mode/glossary prefs live in
    ``~/.manju/gui_state.json``, which the mode-aware GUI nav resolves AND
    persists server-side on first render. Redirect it to a hermetic tmp path for
    every test so no test ever reads or writes a real home; a fresh (absent) file
    means the round-U default (新手/beginner) applies unless a test sets one.
    Suites that need their own path (test_gui_core, ...) just override it."""
    monkeypatch.setenv("MANJU_GUI_STATE", str(tmp_path / "_gui_state_conftest.json"))


@pytest.fixture(autouse=True)
def _isolate_recents(monkeypatch, tmp_path):
    """Round X (agent XE): ``cli._project()``/GUI-server-startup/
    startup all touch ``~/.manju/recents.json`` on every real invocation —
    redirect it to a hermetic tmp path for every test so the suite never reads
    or writes a real home (mirrors ``_isolate_gui_state`` above)."""
    monkeypatch.setenv("MANJU_RECENTS", str(tmp_path / "_recents_conftest.json"))


@pytest.fixture(autouse=True)
def _isolate_providers(monkeypatch, tmp_path):
    """UX round-2 incident: a scratch provider manifest left in the REAL
    ``~/.manju/providers`` turned doctor's gating provider check red across
    the suite — the tests had only ever been green because that directory
    happened to be empty on every host so far. ``MANJU_PROVIDERS_DIR`` is the
    documented test seam (providers/manifest.py); default every test onto a
    hermetic tmp dir. Suites that set their own dir override this (their
    monkeypatch.setenv runs after this autouse fixture)."""
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "_providers_conftest"))


@pytest.fixture(autouse=True)
def _isolate_http_proxy(monkeypatch):
    """GUI HTTP tests hit 127.0.0.1; system HTTP_PROXY (e.g. 127.0.0.1:10090)
    hijacks urllib and returns 502. Clear proxy env for every test and install
    a no-proxy opener so localhost never routes through a broken corporate
    proxy (continuous-goal skeptic failure on merge_blockers/job_cancel)."""
    import urllib.request

    for key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    urllib.request.install_opener(opener)
    yield
    # Restore default opener after the test so nothing bleeds into other tools.
    urllib.request.install_opener(urllib.request.build_opener())


@pytest.fixture
def tmp_project(tmp_path: Path) -> Project:
    """A freshly scaffolded project with a minimal, valid Bible.

    - Chinese directory name (雨夜便利店.manju), no git (fast + hermetic).
    - one scene ``convenience_store`` and one character ``linxia`` so that a
      shot built by :func:`add_shot` passes referential-integrity checks.
    """
    project = Project.create(tmp_path / PROJECT_NAME, git_init=False)
    write_yaml(
        project.root / "bible" / "scenes.yaml",
        {
            "convenience_store": {
                "name": "便利店",
                "description": "雨夜街角的二十四小时便利店,霓虹灯在水洼里融化。",
                "lighting": "冷白灯管,窗外霓虹泛蓝",
            }
        },
    )
    write_yaml(
        project.root / "bible" / "characters.yaml",
        {
            "linxia": {
                "name": "林夏",
                "appearance": "短发,黑色风衣,左手戴旧手表",
                "voice": "冷静、克制、略带沙哑",
                "personality": "警觉,不轻易相信人",
            }
        },
    )
    return project


@pytest.fixture
def make_take(tmp_path: Path) -> Callable[..., TakeInfo]:
    """Factory: fabricate a take for a shot from a tiny FAKE media file.

    ``register_take`` copies from whatever temp file we hand it, so a few
    distinct bytes are enough — nothing here ever runs ffprobe.
    """
    counter = {"n": 0}

    def _make(project: Project, shot_id: str, spec_hash: str, *, suffix: str = ".mp4") -> TakeInfo:
        counter["n"] += 1
        tmp = tmp_path / f"_faketake_{counter['n']}{suffix}"
        tmp.write_bytes(b"fakevideo-" + str(counter["n"]).encode("ascii"))
        return project.register_take(
            shot_id, tmp, TakeSidecar(provider="test", spec_hash=spec_hash)
        )

    return _make


@pytest.fixture
def add_shot() -> Callable[..., ShotSpec]:
    """Factory: build a valid ShotSpec, save it, and append it to the index.

    Defaults reference the Bible entries created by :func:`tmp_project`.
    ``overrides`` are merged in before validation, so a test can flip any
    field (``scene=...``, ``characters=[...]``, ``status={...}``, ...).
    """

    def _add(project: Project, shot_id: str, **overrides: Any) -> ShotSpec:
        data: dict[str, Any] = {
            "id": shot_id,
            "scene": "convenience_store",
            "characters": ["linxia"],
            "dialogue": {"speaker": "linxia", "text": "这不可能。"},
            "duration": "auto",
        }
        data.update(overrides)
        shot = ShotSpec.model_validate(data)
        project.save_shot(shot)

        index = project.load_index()
        if shot_id not in index.order:
            index.order.append(shot_id)
            project.save_index(index)
        return shot

    return _add
