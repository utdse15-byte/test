"""End-to-end tests for the Manju One MCP server (§5, §10, §11).

These drive the REAL server as a subprocess (``python -m manju.mcp.server
--project <tmp project>``) over the wire protocol: newline-delimited JSON-RPC
2.0 on stdio. No ffmpeg is needed anywhere — the tools exercised here read/write
text truth only. Most cases share one long-lived server session; the EOF test
spawns its own so it can close stdin and observe a clean exit.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml

from manju.core.container import Project
from manju.core.locks import seal_lock
from manju.core.models import Dialogue, ShotSpec, ShotStatus, TakeSidecar
from manju.core.yamlio import write_yaml

# A CJK project name — Chinese/Windows paths are first-class (§14).
PROJECT_NAME = "雨夜便利店"


# --------------------------------------------------------------- fixtures


def _register_fake_take(project: Project, shot_id: str, tmp: Path, n: int) -> str:
    """Register a tiny FAKE-media take (never probed) and return its name."""
    src = tmp / f"_fake_{shot_id}_{n}.mp4"
    src.write_bytes(b"fakevideo-" + str(n).encode("ascii"))
    return project.register_take(
        shot_id, src, TakeSidecar(provider="test", spec_hash="manual")
    ).name


def _build_project(root_parent: Path) -> Project:
    """A valid project (mirrors tests/conftest.py style) with:

    - S001: UNLOCKED, two takes (take_01/take_02), take_01 selected.
    - S002: dialogue.text sealed with a value-hash lock; no takes (MISSING).
    """
    project = Project.create(root_parent / PROJECT_NAME, name=PROJECT_NAME, git_init=False)
    write_yaml(
        project.root / "bible" / "scenes.yaml",
        {"convenience_store": {"name": "便利店", "description": "雨夜街角的便利店。"}},
    )
    write_yaml(
        project.root / "bible" / "characters.yaml",
        {"linxia": {"name": "林夏", "appearance": "短发,黑色风衣"}},
    )

    take_01 = _register_fake_take(project, "S001", root_parent, 1)
    _register_fake_take(project, "S001", root_parent, 2)  # take_02
    project.save_shot(
        ShotSpec(
            id="S001",
            scene="convenience_store",
            characters=["linxia"],
            dialogue=Dialogue(speaker="linxia", text="初始台词。"),
            status=ShotStatus(selected_take=take_01),
        )
    )
    project.save_shot(
        ShotSpec(
            id="S002",
            scene="convenience_store",
            characters=["linxia"],
            dialogue=Dialogue(speaker="linxia", text="这不可能。"),
        )
    )
    index = project.load_index()
    index.order = ["S001", "S002"]
    project.save_index(index)

    # Seal a value-hash lock on S002.dialogue.text via seal_lock + update_shot_raw
    # (exactly how cli.py `lock` does it).
    raw = project.load_shot_raw("S002")
    digest = seal_lock(raw, "dialogue.text")

    def _seal(d: dict) -> None:
        locked = d.get("locked")
        locked = dict(locked) if isinstance(locked, dict) else {}
        locked["dialogue.text"] = digest
        d["locked"] = locked

    project.update_shot_raw("S002", _seal)
    return project


class MCPClient:
    """A minimal JSON-RPC-over-stdio client with a timeout on reads (a reader
    thread pushes each stdout line into a queue)."""

    def __init__(self, root: Path):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "manju.mcp.server", "--project", str(root)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._id = 0
        self._lines: "queue.Queue[str]" = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        # drain stderr so a chatty diagnostic can never block the pipe
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                self._lines.put(line)

    def _drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        for _ in self.proc.stderr:
            pass

    def _send(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def notify(self, method: str, params: dict | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def request(self, method: str, params: dict | None = None, timeout: float = 10.0) -> dict:
        self._id += 1
        rid = self._id
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        # responses arrive in order and no server-initiated messages exist, but
        # match on id to be defensive.
        while True:
            resp = json.loads(self._lines.get(timeout=timeout))
            if resp.get("id") == rid:
                return resp

    def call_tool(self, name: str, arguments: dict | None = None) -> tuple[dict, dict]:
        """Return (result, parsed_content) for a tools/call."""
        resp = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        result = resp["result"]
        payload = json.loads(result["content"][0]["text"])
        return result, payload

    def close(self) -> int:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        return self.proc.wait(timeout=10)


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Project:
    return _build_project(tmp_path_factory.mktemp("mcp_proj"))


@pytest.fixture(scope="module")
def client(project: Project):
    c = MCPClient(project.root)
    # handshake once for the shared session
    init = c.request("initialize", {"protocolVersion": "2025-06-18"})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    c.notify("notifications/initialized")
    yield c
    try:
        c.close()
    except Exception:
        c.proc.kill()


# --------------------------------------------------------------- tests


def test_initialize_echoes_protocol_version(client: MCPClient):
    resp = client.request("initialize", {"protocolVersion": "2024-11-05"})
    result = resp["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["capabilities"] == {"tools": {}}
    assert result["serverInfo"]["name"] == "manju"
    assert result["serverInfo"]["version"]


def test_initialize_defaults_protocol_version_when_absent(client: MCPClient):
    resp = client.request("initialize", {})
    assert resp["result"]["protocolVersion"] == "2024-11-05"


def test_ping(client: MCPClient):
    resp = client.request("ping")
    assert resp["result"] == {}


def test_tools_list_surface(client: MCPClient):
    resp = client.request("tools/list")
    tools = resp["result"]["tools"]
    names = {t["name"] for t in tools}
    # the safe surface is present …
    for expected in ("status", "check", "update_shot", "propose"):
        assert expected in names
    # … and the dangerous / off-surface commands are ABSENT by design (§5, §11)
    for forbidden in ("unlock", "gc", "pack", "import"):
        assert forbidden not in names
    # every tool advertises a JSON Schema object
    for t in tools:
        assert t["inputSchema"]["type"] == "object"
        assert "description" in t and t["description"]


def test_tool_status_returns_shots_total(client: MCPClient):
    result, payload = client.call_tool("status")
    assert result["isError"] is False
    assert payload["shots_total"] == 2


def test_get_shot_roundtrip(client: MCPClient):
    result, payload = client.call_tool("get_shot", {"shot_id": "S001"})
    assert result["isError"] is False
    assert payload["id"] == "S001"
    assert payload["data"]["id"] == "S001"
    assert payload["data"]["dialogue"]["speaker"] == "linxia"
    # `yaml` is the raw file text, and it round-trips back to the parsed `data`
    assert "dialogue" in payload["yaml"]
    assert yaml.safe_load(payload["yaml"]) == payload["data"]


def test_update_shot_happy_path_changes_file(client: MCPClient, project: Project):
    _, before = client.call_tool("get_shot", {"shot_id": "S001"})
    data = before["data"]
    on_disk_before = project.shot_path("S001").read_text(encoding="utf-8")
    data["dialogue"]["text"] = "改写后的新台词。"
    new_yaml = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)

    result, payload = client.call_tool(
        "update_shot", {"shot_id": "S001", "yaml_content": new_yaml}
    )
    assert result["isError"] is False, payload
    assert payload.get("ok") is True
    assert "check_warnings" in payload

    on_disk_after = project.shot_path("S001").read_text(encoding="utf-8")
    assert on_disk_after != on_disk_before
    reloaded = yaml.safe_load(on_disk_after)
    assert reloaded["dialogue"]["text"] == "改写后的新台词。"


def test_update_shot_locked_field_rejected_and_file_unchanged(
    client: MCPClient, project: Project
):
    _, before = client.call_tool("get_shot", {"shot_id": "S002"})
    data = before["data"]
    assert "dialogue.text" in data["locked"]  # a sealed lock is present
    on_disk_before = project.shot_path("S002").read_text(encoding="utf-8")

    # try to change the LOCKED field (locked map kept identical)
    data["dialogue"]["text"] = "被篡改的台词。"
    new_yaml = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    result, payload = client.call_tool(
        "update_shot", {"shot_id": "S002", "yaml_content": new_yaml}
    )

    assert result["isError"] is True or "error" in payload
    # the file must be untouched on disk
    assert project.shot_path("S002").read_text(encoding="utf-8") == on_disk_before


def test_update_shot_adding_a_lock_is_rejected(client: MCPClient, project: Project):
    _, before = client.call_tool("get_shot", {"shot_id": "S001"})
    data = before["data"]
    on_disk_before = project.shot_path("S001").read_text(encoding="utf-8")

    # attempt to ADD a lock entry over MCP — forbidden (§5)
    data["locked"] = {"camera.movement": "sha256:deadbeef"}
    new_yaml = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    result, payload = client.call_tool(
        "update_shot", {"shot_id": "S001", "yaml_content": new_yaml}
    )

    assert result["isError"] is True or "error" in payload
    assert project.shot_path("S001").read_text(encoding="utf-8") == on_disk_before


def test_select_take_then_visible_in_list_shots(client: MCPClient):
    result, payload = client.call_tool("select_take", {"shot_id": "S001", "take": "take_02"})
    assert result["isError"] is False, payload
    assert payload["ok"] is True

    _, listing = client.call_tool("list_shots")
    by_id = {s["id"]: s for s in listing["shots"]}
    assert by_id["S001"]["selected_take"] == "take_02"


def test_select_take_unknown_take_is_error(client: MCPClient):
    result, payload = client.call_tool("select_take", {"shot_id": "S001", "take": "take_99"})
    assert result["isError"] is True
    assert "error" in payload


def test_propose_creates_numbered_markdown(client: MCPClient, project: Project):
    result, payload = client.call_tool(
        "propose",
        {"title": "改 S002 对白 dialogue", "body": "建议把台词改得更含蓄。"},
    )
    assert result["isError"] is False, payload
    rel = payload["path"]
    assert rel.startswith("proposals/0001_")
    assert rel.endswith(".md")
    created = project.root / rel
    assert created.exists()
    text = created.read_text(encoding="utf-8")
    assert text.startswith("# 改 S002 对白 dialogue\n\n")
    assert "建议把台词改得更含蓄。" in text


def test_events_reflect_ai_actions(client: MCPClient):
    """SELF-CONTAINED (ubuntu run #226): this used to assert on events that
    "earlier tests in this module" had written — but xdist's default ``load``
    distribution may hand any single test of a module to a fresh worker,
    where the module-scoped project is newly scaffolded and the event log is
    EMPTY (observed 1/4311 the first time the W4/W5 test files shifted the
    chunk boundaries). The actions are performed HERE; on a worker that
    already ran the select/propose tests this is a harmless repeat (select
    is idempotent, propose appends a new numbered file)."""
    r1, p1 = client.call_tool("select_take", {"shot_id": "S001", "take": "take_02"})
    assert r1["isError"] is False, p1
    r2, p2 = client.call_tool("propose", {"title": "events 自证", "body": "为事件断言自备动作。"})
    assert r2["isError"] is False, p2

    _, payload = client.call_tool("events", {"n": 50})
    actions = {(e.get("actor"), e.get("action")) for e in payload["events"]}
    assert ("ai", "select") in actions
    assert ("ai", "propose") in actions


def test_unknown_tool_is_error(client: MCPClient):
    result, payload = client.call_tool("does_not_exist")
    assert result["isError"] is True
    assert "error" in payload


def test_unknown_method_is_jsonrpc_error(client: MCPClient):
    resp = client.request("no/such/method")
    assert "result" not in resp
    assert resp["error"]["code"] == -32601


def test_server_exits_cleanly_on_stdin_eof(project: Project):
    """A dedicated spawn: after the handshake, closing stdin must exit 0."""
    c = MCPClient(project.root)
    init = c.request("initialize", {"protocolVersion": "2024-11-05"})
    assert init["result"]["serverInfo"]["name"] == "manju"
    assert c.close() == 0


def test_mcp_qc_respects_build_lock(tmp_project, add_shot):
    """SKILL.md §5.5 promises the lock covers qc from EVERY surface — the MCP
    path bypassed it (review finding P1-2)."""
    import pytest as _pytest

    from manju.mcp.tools import TOOL_DEFS
    from manju.runtime.buildlock import BuildLock, BuildLocked

    add_shot(tmp_project, "S001")
    handler = next(t["handler"] for t in TOOL_DEFS if t["name"] == "qc")
    lock = BuildLock(tmp_project.root, actor="human").acquire()
    try:
        with _pytest.raises(BuildLocked):
            handler(tmp_project, {})
    finally:
        lock.release()
    result = handler(tmp_project, {})  # released -> runs normally
    assert "ok" in result and "reports" in result


# ============================================================ round W (#39/#9)


def test_mcp_select_take_respects_build_lock(tmp_project, add_shot, make_take):
    """select_take is one of the entrances round W (#9) adds process-lock
    coverage to — a held lock must refuse it, not race a concurrent build."""
    import pytest as _pytest

    from manju.mcp.tools import TOOL_DEFS
    from manju.runtime.buildlock import BuildLock, BuildLocked

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    handler = next(t["handler"] for t in TOOL_DEFS if t["name"] == "select_take")
    lock = BuildLock(tmp_project.root, actor="human").acquire()
    try:
        with _pytest.raises(BuildLocked):
            handler(tmp_project, {"shot_id": "S001", "take": take.name})
    finally:
        lock.release()
    assert tmp_project.load_shot("S001").status.selected_take is None
    result = handler(tmp_project, {"shot_id": "S001", "take": take.name})
    assert result["ok"] is True
    assert tmp_project.load_shot("S001").status.selected_take == take.name


def test_mcp_select_take_refuses_when_selected_take_locked(tmp_project, add_shot, make_take):
    """Round W (#39/#19): MCP select_take used to write status.selected_take
    directly, bypassing the SAME value-hash lock check `update_shot` honors.
    A shot whose status.selected_take is sealed must refuse here too."""
    from manju.mcp.tools import TOOL_DEFS, ToolError

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")

    def ensure_field(d):
        status = d.get("status")
        if not isinstance(status, dict):
            status = {}
        status.setdefault("selected_take", None)
        d["status"] = status

    tmp_project.update_shot_raw("S001", ensure_field)
    raw = tmp_project.load_shot_raw("S001")
    digest = seal_lock(raw, "status.selected_take")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("locked", {}).__setitem__(
            "status.selected_take", digest)
    )

    handler = next(t["handler"] for t in TOOL_DEFS if t["name"] == "select_take")
    with pytest.raises(ToolError):
        handler(tmp_project, {"shot_id": "S001", "take": take.name})
    assert tmp_project.load_shot("S001").status.selected_take is None


# ---------------------------------------------------------------------------
# Round-2 UX audit (2026-07-13): the MCP surface's error contract, live-driven.


def test_missing_required_argument_is_a_structured_refusal(client: MCPClient):
    """A missing required arg used to surface as the bare KeyError text
    ({"error": "'shot_id'"}) — the declared `required` arrays were never
    enforced at dispatch. Now: a named, branchable refusal."""
    result, payload = client.call_tool("get_shot", {})
    assert result["isError"] is True
    assert "missing required argument" in payload["error"]
    assert "shot_id" in payload["error"]
    assert payload.get("code") == "invalid_argument"


def test_nonstring_tool_name_is_refused_cleanly(client: MCPClient):
    resp = client.request("tools/call", {"name": {"evil": 1}, "arguments": {}})
    content = resp["result"]
    assert content["isError"] is True
    body = json.loads(content["content"][0]["text"])
    assert "string" in body["error"]  # not "unhashable type: 'dict'"


def test_update_shot_rollback_is_isError_true(client: MCPClient, project: Project):
    """The rollback path RETURNED its error dict, so the server stamped
    isError:false — an agent branching on the MCP-spec signal concluded the
    write landed when it was rolled back."""
    bad = (project.root / "shots" / "S001.yaml").read_text(encoding="utf-8") \
        .replace("characters:", "characters:\n- ghost_who_does_not_exist\n_old:")
    # simpler: append an unknown character reference
    result, payload = client.call_tool("update_shot", {
        "shot_id": "S001",
        "yaml_content": (project.root / "shots" / "S001.yaml")
        .read_text(encoding="utf-8")
        .replace("- linxia", "- ghost_who_does_not_exist"),
    })
    assert result["isError"] is True, payload
    assert payload.get("check_errors"), payload
    assert payload.get("code") == "check_rejected"


def test_build_refuses_an_unknown_target(client: MCPClient):
    """Paid-safety: a typo'd target fell through every phase branch — the
    generation phase (spend=POSSIBLE) still ran and the requested phase was
    silently skipped. The CLI has guarded this exact hole forever; MCP was
    the one unguarded entrance."""
    result, payload = client.call_tool("build", {"target": "bogus", "dry_run": True})
    assert result["isError"] is True
    assert "unknown target" in payload["error"]
    assert payload.get("code") == "invalid_argument"


def test_events_n_is_clamped(client: MCPClient):
    """n=0 dumped the ENTIRE unbounded log into one content text; negative n
    hit the tail-quirk. The MCP handler clamps (core tail_events semantics
    stay untouched — they are pinned where they live)."""
    for i in range(25):
        client.call_tool("propose", {"title": f"e{i}", "body": "…"})
    _, p0 = client.call_tool("events", {"n": 0})
    assert len(p0["events"]) == 20  # the documented default, not everything
    _, pneg = client.call_tool("events", {"n": -1})
    assert len(pneg["events"]) == 20


def test_skill_show_error_is_not_double_quoted(client: MCPClient):
    result, payload = client.call_tool("skill_show", {"id": "nope"})
    assert result["isError"] is True
    assert not payload["error"].startswith('"')


def test_spending_tools_say_so_in_their_descriptions(client: MCPClient):
    """tools/list is a cold agent's ONLY surface — build/redo may spend real
    money and their descriptions must say so (director_execute already did)."""
    resp = client.request("tools/list", {})
    by_name = {t["name"]: t for t in resp["result"]["tools"]}
    for name in ("build", "redo"):
        desc = by_name[name]["description"]
        assert "花" in desc or "spend" in desc.lower(), (name, desc)
