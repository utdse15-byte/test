"""BOARD-P0-001 regression: a file under a Board preview surface (media/, …)
is third-party PROJECT content. If ``GET /media/*`` returned it as
``text/html`` or ``image/svg+xml`` the browser would run its scripts in the
Board's own origin — able to read the per-run token off ``GET /`` and drive
the write API. So the media route must serve ONLY passive media types inline
and force everything else (HTML/SVG/XML/unknown) to an octet-stream download,
and every media response must carry ``X-Content-Type-Options: nosniff`` plus a
script-disabling sandbox CSP.
"""

from __future__ import annotations

import contextlib
import threading
from typing import Callable, Iterator

import httpx
import pytest

from manju.board.server import make_server
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
    make_take(tmp_project, "S001", "manual")  # take_01 (.mp4)
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", "take_01")
    )
    return tmp_project


def _put_media(project: Project, name: str, data: bytes) -> str:
    """Drop a file under media/imports (a served preview surface) and return its
    project-relative URL path."""
    project.imports_dir.mkdir(parents=True, exist_ok=True)
    f = project.imports_dir / name
    f.write_bytes(data)
    return project.relpath(f)


# ------------------------------------------------------- active content → download


def test_html_media_is_forced_octet_stream_download(board_project):
    rel = _put_media(board_project, "evil.html",
                     b"<script>fetch('/')</script>")
    with running(board_project) as (base, _):
        r = httpx.get(base + "/media/" + rel)
    assert r.status_code == 200
    ctype = r.headers.get("Content-Type", "")
    assert "text/html" not in ctype
    assert ctype.split(";", 1)[0].strip() == "application/octet-stream"
    assert "attachment" in r.headers.get("Content-Disposition", "").lower()
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "sandbox" in r.headers.get("Content-Security-Policy", "")
    # bytes intact — it is served (as a download), not corrupted
    assert r.content == b"<script>fetch('/')</script>"


def test_svg_media_is_forced_octet_stream_download(board_project):
    rel = _put_media(
        board_project, "evil.svg",
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    with running(board_project) as (base, _):
        r = httpx.get(base + "/media/" + rel)
    assert r.status_code == 200
    ctype = r.headers.get("Content-Type", "")
    assert "svg" not in ctype  # never image/svg+xml (scriptable)
    assert ctype.split(";", 1)[0].strip() == "application/octet-stream"
    assert "attachment" in r.headers.get("Content-Disposition", "").lower()
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "sandbox" in r.headers.get("Content-Security-Policy", "")


def test_xml_media_is_forced_download(board_project):
    rel = _put_media(board_project, "evil.xml", b"<?xml version='1.0'?><a/>")
    with running(board_project) as (base, _):
        r = httpx.get(base + "/media/" + rel)
    assert r.status_code == 200
    assert r.headers.get("Content-Type", "").split(";", 1)[0].strip() \
        == "application/octet-stream"
    assert "attachment" in r.headers.get("Content-Disposition", "").lower()


# ------------------------------------------------- passive media stays inline + guarded


def test_passive_image_still_served_inline(board_project):
    rel = _put_media(board_project, "poster.jpg", b"\xff\xd8\xff\xe0jpegbytes")
    with running(board_project) as (base, _):
        r = httpx.get(base + "/media/" + rel)
    assert r.status_code == 200
    assert r.headers.get("Content-Type", "").startswith("image/jpeg")
    # a legit image is NOT forced to download
    assert "attachment" not in r.headers.get("Content-Disposition", "").lower()
    # …but still carries the guard headers
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "sandbox" in r.headers.get("Content-Security-Policy", "")


def test_video_take_served_inline_with_guard_headers(board_project):
    take = board_project.get_take("S001", "take_01")
    rel = board_project.relpath(take.media_path)
    with running(board_project) as (base, _):
        r = httpx.get(base + "/media/" + rel)
    assert r.status_code == 200
    assert r.headers.get("Content-Type", "").startswith("video/")
    assert "attachment" not in r.headers.get("Content-Disposition", "").lower()
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "sandbox" in r.headers.get("Content-Security-Policy", "")


def test_range_response_also_carries_guard_headers(board_project):
    rel = _put_media(board_project, "clip.mp4", b"0123456789")
    with running(board_project) as (base, _):
        r = httpx.get(base + "/media/" + rel, headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "sandbox" in r.headers.get("Content-Security-Policy", "")
