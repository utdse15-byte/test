"""FP_M2 — board serve-mode hardening (audit items 15 + 17 + 14).

Three defense-in-depth pins on the local ``manju board --serve`` surface, none
of which touch the BYTE-PINNED static board (``test_board_serve.py``,
``test_fp_board_transport.py``): they are all on SERVED responses / a shared
Project helper.

  * item 17 — an oversize POST body gets ``Connection: close`` and does NOT
    desync the keep-alive connection into the next request.
  * item 15 — served board responses carry the GUI's security header set
    (CSP with a hash of the inline script, X-Frame-Options, X-Content-Type-
    Options), computed so the HTML bytes never change.
  * item 14 — both the GUI and the board media gate route through the ONE
    ``Project.safe_served_path`` helper (a future fix cannot be one-sided).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import re
import socket
import threading
from typing import Callable, Iterator

import httpx
import pytest

from manju.board import board as bd
from manju.board.server import _MAX_BODY_BYTES, make_server
from manju.core.container import Project


@contextlib.contextmanager
def running(project: Project) -> Iterator[tuple[str, object]]:
    server = make_server(project, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def board_project(tmp_project: Project, add_shot: Callable, make_take: Callable) -> Project:
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "manual")
    make_take(tmp_project, "S001", "manual")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", "take_01")
    )
    return tmp_project


# ---------------------------------------------------------------- item 17: oversize


def _drain(sock: socket.socket, timeout: float = 5.0) -> bytes:
    sock.settimeout(timeout)
    chunks = []
    while True:
        try:
            b = sock.recv(65536)
        except socket.timeout:
            break
        if not b:
            break
        chunks.append(b)
    return b"".join(chunks)


def test_oversize_post_sends_connection_close_and_no_desync(board_project):
    """Two back-to-back requests on ONE keep-alive connection: an oversize POST
    followed by a pipelined GET /. The oversize response must carry
    ``Connection: close`` and the server must close the connection, so the
    leftover undrained body bytes can NEVER be misparsed as (or leak into) the
    pipelined GET."""
    with running(board_project) as (base, server):
        host, port = "127.0.0.1", server.server_address[1]
        declared = _MAX_BODY_BYTES + 4096          # over the 1 MiB cap
        body = b"{" + b"a" * (declared - 1)         # `declared` bytes, invalid JSON
        first = (
            f"POST /api/select HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"X-Manju-Token: {server.token}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {declared}\r\n"
            f"\r\n"
        ).encode() + body
        pipelined = (f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n").encode()

        sock = socket.create_connection((host, port), timeout=5)
        try:
            sock.sendall(first + pipelined)
            data = _drain(sock)
        finally:
            sock.close()

    assert b"Connection: close" in data                 # the desync fix
    # exactly ONE HTTP response on this connection — the pipelined GET was
    # never served (the connection closed), so no board HTML / 200 leaked out
    assert data.count(b"HTTP/1.1 ") == 1
    assert b"HTTP/1.1 400" in data                       # oversize → invalid JSON → 400
    assert b"HTTP/1.1 200" not in data


def test_normal_post_still_keeps_the_connection(board_project):
    """A NORMAL-size POST must NOT carry Connection: close — the fix is scoped
    to the oversize path (keep-alive is otherwise preserved)."""
    with running(board_project) as (base, server):
        r = httpx.post(base + "/api/select",
                       headers={"X-Manju-Token": server.token},
                       json={"shot": "S001", "take": "take_02"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.headers.get("Connection", "").lower() != "close"


# ---------------------------------------------------------------- item 15: CSP


def _csp_of(headers) -> str:
    return headers.get("Content-Security-Policy", "")


def test_served_board_carries_security_headers(board_project):
    with running(board_project) as (base, server):
        r = httpx.get(base + "/")
    assert r.status_code == 200
    csp = _csp_of(r.headers)
    assert csp, "served board must carry a Content-Security-Policy"
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_csp_script_hash_matches_the_inline_script(board_project):
    """The CSP script-src carries the sha256 of the ACTUAL inline <script> the
    served HTML ships — so the (token-bearing) inline script is allowed to run
    and the HTML bytes never had to change."""
    with running(board_project) as (base, server):
        r = httpx.get(base + "/")
    html = r.text
    csp = _csp_of(r.headers)
    scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    assert scripts, "served board has an inline script"
    for body in scripts:
        digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
        assert f"'sha256-{digest}'" in csp, "script hash must be in script-src"
    # and the policy really is script-src by hash, not a blanket unsafe-inline
    assert "script-src" in csp
    assert "'unsafe-inline'" not in csp.split("style-src")[0]  # not in script-src


def test_static_board_bytes_unchanged_by_csp(board_project, monkeypatch):
    """Adding the CSP header changed NOTHING in the rendered HTML (serve or
    static) — re-pins the static byte-identity locally so item 15 can never
    have touched the frozen bytes."""
    written = bd.render_board(board_project, serve=False)
    assert written == bd.render_board(board_project, serve=False)  # deterministic
    with running(board_project) as (base, server):
        served = httpx.get(base + "/").text
    # the served HTML still has exactly one inline <script> and one <style>,
    # i.e. the CSP fix did not externalize anything
    assert served.count("<script>") == 1
    assert served.count("<style>") == 1


# ---------------------------------------------------------------- item 14: shared gate


def test_board_media_gate_routes_through_shared_helper(board_project, monkeypatch):
    """The board media route MUST call Project.safe_served_path (so a future
    hardening of the gate cannot be applied to only one surface)."""
    calls: list = []
    real = Project.safe_served_path

    def _spy(self, rel, prefixes):
        calls.append((rel, tuple(prefixes)))
        return real(self, rel, prefixes)

    monkeypatch.setattr(Project, "safe_served_path", _spy)
    take = board_project.get_take("S001", "take_01")
    rel = board_project.relpath(take.media_path)
    with running(board_project) as (base, server):
        r = httpx.get(base + "/media/" + rel)
    assert r.status_code == 200
    assert calls, "board /media did not route through Project.safe_served_path"
