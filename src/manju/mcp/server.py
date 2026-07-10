"""Manju One MCP server over stdio (§11) — a thin wrapper over the same core.

Transport: newline-delimited JSON-RPC 2.0, UTF-8, exactly one JSON object per
line on stdout. **stdout is protocol-pure** — every diagnostic goes to stderr.

Methods:
  initialize                 -> {protocolVersion, capabilities:{tools:{}}, serverInfo}
  notifications/initialized  -> (notification, no response)
  ping                       -> {}
  tools/list                 -> {tools:[{name, description, inputSchema}...]}
  tools/call                 -> {content:[{type:"text", text:<json>}], isError}

JSON-RPC error responses (code -32700/-32600/-32601) are reserved for malformed
requests and unknown methods. A tool that fails is NOT a protocol error: it
returns a normal result with ``isError: true`` and a ``{"error": ...}`` payload.

Run it with ``python -m manju.mcp.server [--project PATH]`` (or ``-m manju.mcp``).
EOF on stdin exits cleanly with status 0.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

import manju

from ..core.container import Project, ProjectError
from . import policy as _policy
from . import tools

DEFAULT_PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601


def _log(message: str) -> None:
    """Diagnostics go to stderr only — stdout must stay protocol-pure."""
    print(message, file=sys.stderr, flush=True)


class _MethodNotFound(Exception):
    """Raised for an unknown JSON-RPC method (-> -32601)."""


class MCPServer:
    def __init__(self, project_start: Path, agent_profile: str = _policy.COLLABORATIVE):
        self._project_start = project_start
        self._project: Project | None = None
        # The agent profile is set ONCE, here, from the serve-mcp flag — it can
        # NEVER come from project.yaml / skills / shots / tool arguments (§ DR05
        # ruling 3). An unknown value is a hard config error.
        if agent_profile not in _policy.PROFILES:
            raise ValueError(
                f"unknown agent profile {agent_profile!r} "
                f"(expected one of {sorted(_policy.PROFILES)})"
            )
        self._profile = agent_profile

    # ---- lazy project resolution (initialize / tools/list need no project)

    def project(self) -> Project:
        if self._project is None:
            self._project = Project.find(self._project_start)
            # round X (agent XE): MCP server startup touches recents exactly
            # once — `self._project` is cached above, so this branch only
            # ever runs on the FIRST successful resolution per process.
            try:
                from ..core.recents import touch_recent

                touch_recent(self._project)
            except Exception:
                pass  # recents is a convenience shelf, never load-bearing (§3)
        return self._project

    # ---- main loop

    def serve(self, stdin: TextIO, stdout: TextIO) -> int:
        while True:
            line = stdin.readline()
            if line == "":  # EOF -> clean exit
                return 0
            line = line.strip()
            if not line:
                continue
            self._handle_line(line, stdout)

    # ---- one line = one JSON-RPC message

    def _handle_line(self, line: str, stdout: TextIO) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            self._send(stdout, self._error(None, PARSE_ERROR, "parse error"))
            return
        if not isinstance(message, dict):
            self._send(stdout, self._error(None, INVALID_REQUEST, "invalid request"))
            return

        is_notification = "id" not in message
        msg_id = message.get("id")
        method = message.get("method")

        if not isinstance(method, str):
            if is_notification:
                return  # never respond to a notification
            self._send(stdout, self._error(msg_id, INVALID_REQUEST, "missing/invalid method"))
            return

        if is_notification:
            # notifications/initialized and any other notification: ignore silently
            return

        params = message.get("params")
        if not isinstance(params, dict):
            params = {}
        try:
            result = self._dispatch(method, params)
        except _MethodNotFound:
            self._send(stdout, self._error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}"))
            return
        self._send(stdout, {"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _dispatch(self, method: str, params: dict) -> dict:
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": tools.list_tools(self._profile)}
        if method == "tools/call":
            return self._tools_call(params)
        raise _MethodNotFound(method)

    def _initialize(self, params: dict) -> dict:
        requested = params.get("protocolVersion")
        version = requested if isinstance(requested, str) and requested else DEFAULT_PROTOCOL_VERSION
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "manju", "version": manju.__version__},
        }

    def _tools_call(self, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            result = tools.call_tool(self.project(), name, arguments, self._profile)
            text = json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except tools.AgentProfileDenied as denied:
            # a profile refusal is a STRUCTURED isError result (agent_profile_denied)
            # — never a JSON-RPC error, never disguised as unknown-tool (§ ruling 4).
            _log(f"agent_profile_denied [{name}] under {self._profile}")
            text = json.dumps(denied.payload, ensure_ascii=False, separators=(",", ":"))
            return {"content": [{"type": "text", "text": text}], "isError": True}
        except Exception as exc:  # tool failure is an isError result, not a protocol error
            _log(f"tool error [{name}]: {exc}")
            text = json.dumps({"error": str(exc)}, ensure_ascii=False, separators=(",", ":"))
            return {"content": [{"type": "text", "text": text}], "isError": True}

    # ---- wire helpers

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    @staticmethod
    def _send(stdout: TextIO, obj: dict) -> None:
        stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="manju-mcp",
        description="Manju One MCP server (stdio, newline-delimited JSON-RPC 2.0).",
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=None,
        help="project path (default: discover a .manju project from the cwd upward).",
    )
    parser.add_argument(
        "--agent-profile",
        choices=sorted(_policy.PROFILES),
        default=_policy.COLLABORATIVE,
        help="agent surface profile (default: collaborative — byte-identical to "
        "today). 'unattended' is the opt-in boundary for a self-driving agent: "
        "reads/proposals stay, self-confirming spend and paid redo are hidden + "
        "refused, shot writes require an expected_rev CAS token, build is "
        "dry-run-only. Settable ONLY here — never from project content.",
    )
    args = parser.parse_args(argv)

    # UTF-8 on the wire regardless of locale (§14: Chinese text is first-class).
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass

    start = args.project if args.project is not None else Path.cwd()
    server = MCPServer(start, agent_profile=args.agent_profile)
    try:  # surface a bad path early on stderr; tool calls also report it
        server.project()
    except ProjectError as exc:
        _log(f"warning: {exc}")
    _log(f"manju MCP server ready (stdio); project start = {start}; "
         f"agent profile = {args.agent_profile}")

    return server.serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
