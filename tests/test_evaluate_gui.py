"""GUI surfacing of core/evaluate.py (round AA goal item 8, GUI half).

The engine layer (core/evaluate.py: evaluate()) is frozen — this suite only
pins the new read-only ``GET /api/evaluate`` the cockpit's evaluate block
calls: no lock (a pure read, exactly like the existing ``GET /api/cockpit``),
same shape as ``manju evaluate --json``, and it must round-trip byte-for-byte
with a direct ``evaluate(project)`` call on both an empty and a seeded
project (evaluate() itself is documented to degrade to an all-zeros report
rather than raise — this suite just confirms the HTTP layer doesn't disturb
that).
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from manju.core.events import append_event
from manju.core.evaluate import SMALL_N_THRESHOLD, evaluate
from manju.gui.server import create_server


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _get(server, path):
    url = f"http://127.0.0.1:{server.port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = {}
        return exc.code, parsed


def test_api_evaluate_roundtrips_empty_project(gui, tmp_project):
    status, data = _get(gui, "/api/evaluate")
    assert status == 200
    assert data == evaluate(tmp_project)

    # zero-data project degrades gracefully — never an error, always the
    # all-zeros shape (module docstring), honesty section always present.
    assert data["events_total"] == 0
    assert data["workflow"]["redo"] == {"total": 0, "hotspots": []}
    assert data["qc"]["verdicts_total"] == 0
    assert data["actors"] == {}
    honesty = data["honesty"]
    assert honesty["summary"]
    assert honesty["cannot_claim"]
    assert honesty["small_n_threshold"] == SMALL_N_THRESHOLD


def test_api_evaluate_roundtrips_seeded_project(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    root = tmp_project.root
    append_event(root, "human", "skill_used", {"skill": "creation-funnel", "via": "cli"})
    append_event(root, "ai", "redo", {"shot": "S001"})
    append_event(root, "ai", "redo", {"shot": "S001"})

    status, data = _get(gui, "/api/evaluate")
    assert status == 200
    assert data == evaluate(tmp_project)
    assert data["events_total"] == 3
    assert data["workflow"]["redo"]["total"] == 2
    assert data["workflow"]["redo"]["hotspots"][0] == {
        "shot": "S001", "count": 2,
        "last_used": data["workflow"]["redo"]["hotspots"][0]["last_used"],
        "low_n": True,  # below SMALL_N_THRESHOLD
    }
    skills = data["skills"]
    used = next(r for r in skills["usage"] if r["id"] == "creation-funnel")
    assert used["count"] == 1
