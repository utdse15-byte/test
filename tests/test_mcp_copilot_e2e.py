"""Round F: the M2 copilot acceptance shape, driven over the real MCP wire —
status → write a shot → check → build → explain → select → propose. One
server process, one project, the whole loop, no human editing any JSON."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from manju.core.container import Project
from manju.core.yamlio import write_yaml
from tests.test_mcp import MCPClient

pytestmark = [pytest.mark.ffmpeg,  # F43: the fast-loop deselector
              pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg required for the build step"
)]

SHOT_YAML = """\
id: S001
scene: convenience_store
characters: [linxia]
duration: 1.5
camera:
  shot_size: close_up
  movement: slow_push_in
action:
  main: 林夏接过硬币,镜头推进到硬币年份
  emotion: 震惊、克制
dialogue:
  speaker: linxia
  text: 这不可能。
"""


@pytest.fixture(scope="module")
def copilot(tmp_path_factory):
    root_parent = tmp_path_factory.mktemp("copilot")
    project = Project.create(root_parent / "雨夜便利店", git_init=False)
    write_yaml(project.root / "bible" / "characters.yaml",
               {"linxia": {"name": "林夏", "voice": "低沉"}})
    write_yaml(project.root / "bible" / "scenes.yaml",
               {"convenience_store": {"name": "便利店", "mood": "雨夜"}})
    write_yaml(project.shots_dir / "S001.yaml", {"id": "S001"})  # placeholder file
    write_yaml(project.shots_dir / "index.yaml", {"order": ["S001"], "defaults": {}})

    client = MCPClient(project.root)
    init = client.request("initialize", {"protocolVersion": "2025-06-18"})
    assert init["result"]["serverInfo"]["name"] == "manju"
    client.notify("notifications/initialized")
    yield project, client
    client.close()


def test_full_copilot_loop_over_the_wire(copilot):
    project, client = copilot

    # 1. takeover entry: status
    _, status = client.call_tool("status")
    assert status["shots_total"] == 1

    # 2. AI writes the shot (the ONLY write path for shot files over MCP)
    result, payload = client.call_tool(
        "update_shot", {"shot_id": "S001", "yaml_content": SHOT_YAML}
    )
    assert not result.get("isError"), payload
    assert payload["ok"] is True

    # 3. safety net
    _, check = client.call_tool("check")
    assert check["ok"] is True, check

    # 4. build to qc: no takes and no cloud provider -> the fallback chain
    #    ends at caption_card, so the shot self-fills offline (§8.4)
    result, build = client.call_tool("build", {"target": "qc"})
    assert not result.get("isError"), build
    assert build["ok"] is True, build
    assert any("S001" in g for g in build["generated"])

    # 5. explain: everything the build just did is justified read-only
    _, explain = client.call_tool("explain")
    [shot] = explain["shots"]
    assert shot["video"]["state"] in ("fresh", "needs_selection", "manual")
    assert explain["timeline"]["exists"] is True

    # 6. the human's decision surface: list takes via list_shots + select
    _, shots = client.call_tool("list_shots")
    take = shots["shots"][0]["selected_take"]
    assert take  # build auto-selected the generated card (no human decision existed)
    _, sel = client.call_tool("select_take", {"shot_id": "S001", "take": take})
    assert sel["ok"] is True

    # 7. the agent wants a locked change -> proposal channel, never unlock
    _, proposal = client.call_tool(
        "propose", {"title": "S001 台词修改", "body": "建议把台词改为『这……不可能。』"}
    )
    assert proposal["path"].startswith("proposals/")
    assert (project.root / proposal["path"]).exists()

    # 8. events recorded the whole session as actor=ai
    _, events = client.call_tool("events", {"n": 50})
    actions = [e["action"] for e in events["events"]]
    assert "mcp_update_shot" in actions and "propose" in actions
    assert all(e["actor"] == "ai" for e in events["events"]
               if e["action"] in ("mcp_update_shot", "propose"))


def test_dangerous_surface_still_absent(copilot):
    _, client = copilot
    tools = client.request("tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"status", "update_shot", "explain", "build", "propose"} <= names
    assert not {"unlock", "gc", "pack", "import"} & names
