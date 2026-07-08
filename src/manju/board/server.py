"""``manju board --serve`` — the static review board turned into a local,
actionable workspace (§1-⑦, §11).

A stdlib-only HTTP server (``ThreadingHTTPServer`` + ``BaseHTTPRequestHandler``;
no starlette/uvicorn, no new dependencies) that is a THIN veneer over the same
core the CLI calls. It never shells out to ``manju``; it imports and calls the
same functions. The dangerous surface (unlock / gc / pack / unpack) is
deliberately ABSENT here, exactly as on the MCP server (§5, §11).

Routes:
  GET  /                         → the board HTML, REGENERATED per request
                                   (always-live state) with ``serve=True``.
  GET  /media/<project-rel-path> → stream a file under the project root, with
                                   HTTP Range support (``206`` / ``Accept-Ranges``)
                                   so browsers can seek a ``<video>``. The
                                   resolved path MUST stay inside the project
                                   root (``..``/absolute/symlink escapes → 403)
                                   AND inside the preview-surface allowlist
                                   (``BoardHandler._MEDIA_PREFIXES`` — media/,
                                   reports/frames/, .manju/thumbs/): truth
                                   files like project.yaml, shots/*.yaml or
                                   .manju/state.sqlite are never served here.
  POST /api/<action>             → JSON in, ``{ok: true, ...}`` / ``{ok: false,
                                   error: "one line"}`` out. Only the seven safe
                                   actions below exist; anything else is 404.

Actions: select · rollback_shot · snapshot · build · qc · package · redo · export.

Concurrency: one global ``threading.Lock`` guards every mutating action so two
clicks can't interleave. A click that arrives while another action runs gets an
immediate ``409`` ``{ok:false, error:"busy — another action is running"}``
rather than queueing — the server is threaded, so ``GET /`` and ``GET /media``
keep working during a long build.

Localhost, personal use: no auth (that is a deliberate scope decision), but path
traversal is still blocked because the media route reads arbitrary paths.
"""

from __future__ import annotations

import json
import mimetypes
import os
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from ..core.container import Project
from ..core.events import append_event
from .board import render_board

__all__ = ["make_server", "serve_board", "BoardServer", "API_ACTIONS"]

_CHUNK = 64 * 1024


def _actor() -> str:
    """Same convention as the CLI (``cli.ACTOR``): MANJU_ACTOR, default 'human'.
    Read per request so a caller can set it right before an action."""
    return os.environ.get("MANJU_ACTOR", "human")


def _one_line(text: str) -> str:
    """Collapse a message to a single clean line for the JSON error envelope."""
    return " ".join(str(text).split()) or "error"


class _ApiError(Exception):
    """A clean, one-line failure surfaced as ``{ok:false, error:...}`` (HTTP 200)."""


# ------------------------------------------------------------------- actions
# Each handler is a thin call into the same core the CLI uses. Events are
# recorded exactly where the CLI records them (some core functions log their own
# event via the actor argument — build/redo/snapshot/rollback_shot; select and
# package are logged here, mirroring cli.py).


def _api_select(project: Project, body: dict) -> dict:
    """Same checked write every other select entrance uses (round W, #39):
    lock guard, post-write check scoped to the shot, revert on regression.
    Takes the process build lock itself — `select_take_checked` deliberately
    does not (see its docstring), so build's own auto-select never
    self-deadlocks on its own already-held lock."""
    from ..core.writes import WriteRejected, select_take_checked
    from ..runtime.buildlock import BuildLocked, build_lock

    shot = body.get("shot")
    take = body.get("take")
    if not shot or not take:
        raise _ApiError("shot and take are required")
    try:
        with build_lock(project.root, actor=_actor()):
            result = select_take_checked(project, shot, take, actor=_actor(), via="board")
    except (WriteRejected, BuildLocked) as exc:
        raise _ApiError(str(exc)) from exc
    return {"shot": result["shot"], "take": result["take"]}


def _api_rollback_shot(project: Project, body: dict) -> dict:
    from ..core.history import HistoryError, rollback_shot

    shot = body.get("shot")
    if not shot:
        raise _ApiError("shot is required")
    try:
        return rollback_shot(project, shot)  # records its own event (actor from env)
    except HistoryError as exc:
        raise _ApiError(str(exc)) from exc


def _api_snapshot(project: Project, body: dict) -> dict:
    from ..core.history import HistoryError, snapshot

    label = body.get("label") or ""
    try:
        return snapshot(project, str(label))  # records its own event (actor from env)
    except HistoryError as exc:
        raise _ApiError(str(exc)) from exc


def _api_build(project: Project, body: dict) -> dict:
    from ..build.graph import run_build

    target = body.get("target") or "final"
    if target not in ("proxy", "final", "exports", "qc"):
        raise _ApiError(f"unknown target: {target}")
    result = run_build(project, target=target, actor=_actor())  # logs its own 'build' event
    summary = {
        "build_ok": result.ok,
        "qc_passed": result.qc_ok,
        "generated": result.generated,
        "render_path": result.render_path,
        "warnings": result.warnings,
        "errors": result.errors,
    }
    if not result.ok:
        # Surface the failure to the banner; keep the summary out of the envelope.
        raise _ApiError("build failed: " + (_one_line("; ".join(result.errors))
                                            or "see reports/qc.md"))
    return summary


def _api_qc(project: Project, body: dict) -> dict:
    from ..qc.checks import run_qc
    from ..qc.report import write_reports

    report = run_qc(project, project.load_timeline())
    write_reports(project, report)
    errors = sum(1 for it in report.items if it.level == "error")
    warns = sum(1 for it in report.items if it.level == "warn")
    infos = sum(1 for it in report.items if it.level not in ("error", "warn"))
    return {"qc_passed": report.ok, "errors": errors, "warnings": warns, "info": infos}


def _api_package(project: Project, body: dict) -> dict:
    from pydantic import ValidationError

    from ..media.ffmpeg import MediaError
    from ..media.packaging import PackagingError, make_package

    try:
        result = make_package(project)
    except (PackagingError, MediaError) as exc:
        raise _ApiError(str(exc)) from exc
    except ValidationError as exc:
        raise _ApiError(f"packaging.yaml is invalid: {exc.errors()[0].get('msg', exc)}") from exc
    append_event(project.root, _actor(), "package",
                 {k: result[k] for k in ("cover", "teaser", "skipped")})
    return {
        "cover": result.get("cover"),
        "teaser": result.get("teaser"),
        "skipped": result.get("skipped", []),
        "warnings": result.get("warnings", []),
    }


def _api_redo(project: Project, body: dict) -> dict:
    from ..build.graph import redo_shot

    shot = body.get("shot")
    if not shot:
        raise _ApiError("shot is required")
    if shot not in project.shot_ids():
        raise _ApiError(f"unknown shot '{shot}'")
    provider = body.get("provider") or None
    takes = redo_shot(project, shot, provider=provider, actor=_actor())  # logs its own event
    return {"shot": shot, "takes": takes}


# Export profiles → the SAME core `manju export` (cli.export) calls. srt/otio
# always land; the native drafts (jianying/capcut) degrade to a note when the
# optional lib isn't installed, exactly like the CLI, so one missing draft never
# fails the whole export.
_EXPORT_PROFILES = ("srt", "otio", "jianying", "capcut")


def _api_export(project: Project, body: dict) -> dict:
    from ..exporters.otio import export_otio
    from ..exporters.srt_ass import export_captions

    # Validate the request (input) before checking state, so a malformed
    # profiles list is rejected the same whether or not a timeline exists.
    profiles = body.get("profiles") or ["srt", "otio"]
    if not isinstance(profiles, list) or not profiles:
        raise _ApiError("profiles must be a non-empty array of srt|otio|jianying|capcut")
    unknown = [p for p in profiles if p not in _EXPORT_PROFILES]
    if unknown:
        raise _ApiError(f"unknown export profile(s): {', '.join(map(str, unknown))} "
                        "(use srt|otio|jianying|capcut)")
    timeline = project.load_timeline()
    if timeline is None:
        raise _ApiError("no timeline.json — run build first")

    outputs: dict[str, str] = {}
    notes: list[str] = []
    if "srt" in profiles:
        paths = export_captions(project, timeline)
        outputs["srt"] = project.relpath(paths["srt"])
        outputs["ass"] = project.relpath(paths["ass"])
    if "otio" in profiles:
        outputs["otio"] = project.relpath(export_otio(project, timeline))
    if "jianying" in profiles:
        from ..exporters.jianying import export_jianying
        from ..exporters.native_draft import ExporterUnavailable, export_jianying_native

        outputs["jianying"] = project.relpath(export_jianying(project, timeline))
        try:
            outputs["jianying_native"] = project.relpath(
                export_jianying_native(project, timeline))
        except ExporterUnavailable as exc:
            notes.append(f"jianying_native skipped: {_one_line(str(exc))}")
    if "capcut" in profiles:
        from ..exporters.native_draft import ExporterUnavailable, export_capcut_native

        try:
            outputs["capcut"] = project.relpath(export_capcut_native(project, timeline))
        except ExporterUnavailable as exc:
            notes.append(f"capcut skipped: {_one_line(str(exc))}")

    append_event(project.root, _actor(), "export", outputs)  # mirrors cli.export
    return {"outputs": outputs, "notes": notes}


API_ACTIONS: dict[str, Callable[[Project, dict], dict]] = {
    "select": _api_select,
    "rollback_shot": _api_rollback_shot,
    "snapshot": _api_snapshot,
    "build": _api_build,
    "qc": _api_qc,
    "package": _api_package,
    "redo": _api_redo,
    "export": _api_export,
}


# -------------------------------------------------------------------- server


class BoardServer(ThreadingHTTPServer):
    """Threaded so a long build (which holds ``mutation_lock``) never blocks the
    board/media reads. ``daemon_threads`` keeps Ctrl-C responsive."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], project: Project):
        super().__init__(server_address, BoardHandler)
        self.project = project
        self.mutation_lock = threading.Lock()


class BoardHandler(BaseHTTPRequestHandler):
    server_version = "manju-board/1.0"
    protocol_version = "HTTP/1.1"

    # ---- convenience

    @property
    def _project(self) -> Project:
        return self.server.project  # type: ignore[attr-defined]

    def log_message(self, *args: Any) -> None:  # keep the console quiet
        pass

    # ---- routing

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_board()
        elif path.startswith("/media/"):
            self._serve_media(path[len("/media/"):])
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_HEAD(self) -> None:
        # Mirror GET for media (browsers may probe) without a body.
        path = urlparse(self.path).path
        if path.startswith("/media/"):
            self._serve_media(path[len("/media/"):])
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            self._read_body_raw()  # drain so keep-alive stays sane
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        self._handle_api(path[len("/api/"):])

    # ---- GET /

    def _serve_board(self) -> None:
        try:
            html = render_board(self._project, serve=True)
        except Exception as exc:  # a broken project should not kill the thread
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                            {"ok": False, "error": _one_line(str(exc))})
            return
        data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---- GET /media/<relpath>

    # goal item 40: project-root containment alone let `/media/<relpath>` read
    # ANY project file — project.yaml, shots/*.yaml, story/*, events.jsonl,
    # .manju/state.sqlite — not just the media/preview surfaces the board
    # actually links to. Allowlisted to what board.py actually renders as an
    # image/video/poster src: take media + imports thumbnails under media/,
    # keyframe posters under reports/frames/, and the thumb/waveform cache
    # under .manju/thumbs/ (NOT the rest of .manju — state.sqlite/events stay
    # unreachable). Mirrors the GUI's MEDIA_PREFIXES allowlist (gui/server.py).
    _MEDIA_PREFIXES = ("media/", "reports/frames/", ".manju/thumbs/")

    def _safe_media_path(self, rel: str) -> Path:
        """Resolve a project-relative media path, refusing any escape above the
        project root AND anything outside the preview-surface allowlist above.
        ``Path.resolve()`` collapses ``..`` AND follows symlinks, so a symlink
        pointing outside the tree — or outside the allowlist — is rejected
        too. Raises :class:`ValueError` on a refusal (→ 403)."""
        root = self._project.root  # already absolute + resolved (Project.__init__)
        rel = unquote(rel).lstrip("/")  # an absolute-looking path is treated as project-relative
        if not any(rel.startswith(p) for p in self._MEDIA_PREFIXES):
            raise ValueError("path is not a served preview surface")
        candidate = (root / rel).resolve()
        if candidate != root and not candidate.is_relative_to(root):
            raise ValueError("path escapes the project root")
        # re-check the allowlist against the RESOLVED path: "media/../x" stays
        # inside the root but must not sidestep the prefix gate, and neither
        # must a symlink that resolves out of an allowed tree into another.
        rel_resolved = candidate.relative_to(root).as_posix()
        if not any(rel_resolved.startswith(p) for p in self._MEDIA_PREFIXES):
            raise ValueError("path is not a served preview surface")
        return candidate

    def _serve_media(self, rel: str) -> None:
        try:
            target = self._safe_media_path(rel)
        except ValueError:
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "forbidden"})
            return
        if not target.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        self._stream_file(target)

    def _stream_file(self, path: Path) -> None:
        size = path.stat().st_size
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        start, end, is_range = self._parse_range(size)

        if is_range and (start > end or start >= size or start < 0):
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        length = end - start + 1
        if is_range:
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(HTTPStatus.OK)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remaining -= len(chunk)

    def _parse_range(self, size: int) -> tuple[int, int, bool]:
        """Parse a single ``Range: bytes=`` header. Returns (start, end, is_range);
        multi-range and malformed headers degrade to a full 200 response."""
        header = self.headers.get("Range")
        if not header or not header.startswith("bytes="):
            return 0, size - 1, False
        spec = header[len("bytes="):].split(",")[0].strip()  # single range only
        if "-" not in spec:
            return 0, size - 1, False
        s, _, e = spec.partition("-")
        try:
            if s == "":  # suffix range: last N bytes
                n = int(e)
                if n <= 0:
                    return 0, size - 1, False
                return max(0, size - n), size - 1, True
            start = int(s)
            end = int(e) if e else size - 1
            return start, min(end, size - 1), True
        except ValueError:
            return 0, size - 1, False

    # ---- POST /api/<action>

    def _handle_api(self, action: str) -> None:
        body = self._read_json_body()
        handler = API_ACTIONS.get(action)
        if handler is None:  # unlock / gc / pack / anything else → 404 (never exposed)
            self._send_json(HTTPStatus.NOT_FOUND,
                            {"ok": False, "error": f"unknown action: {action!r}"})
            return
        if body is None:
            self._send_json(HTTPStatus.BAD_REQUEST,
                            {"ok": False, "error": "request body must be a JSON object"})
            return
        lock = self.server.mutation_lock  # type: ignore[attr-defined]
        if not lock.acquire(blocking=False):  # never queue: fail fast so clicks don't stack
            self._send_json(HTTPStatus.CONFLICT,
                            {"ok": False, "error": "busy — another action is running"})
            return
        try:
            result = handler(self._project, body)
            payload: dict[str, Any] = {"ok": True}
            if result:
                payload.update(result)
            self._send_json(HTTPStatus.OK, payload)
        except _ApiError as exc:
            self._send_json(HTTPStatus.OK, {"ok": False, "error": _one_line(str(exc))})
        except Exception as exc:  # any core failure → clean one-line envelope, never a 500 stack
            self._send_json(HTTPStatus.OK, {"ok": False, "error": _one_line(str(exc))})
        finally:
            lock.release()

    # ---- body / response helpers

    def _read_body_raw(self) -> bytes:
        length = self.headers.get("Content-Length")
        if length is None:
            return b""
        try:
            n = int(length)
        except ValueError:
            return b""
        return self.rfile.read(n) if n > 0 else b""

    def _read_json_body(self) -> dict | None:
        raw = self._read_body_raw()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _send_json(self, status: HTTPStatus | int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


# ------------------------------------------------------------------- factory


def make_server(project: Project | Path | str, host: str = "127.0.0.1",
                port: int = 8787) -> BoardServer:
    """Bind and return a :class:`BoardServer` (not yet serving). ``port=0`` binds
    an ephemeral port — the actual one is ``server.server_address[1]`` (tests)."""
    if not isinstance(project, Project):
        project = Project.find(project)
    return BoardServer((host, port), project)


def serve_board(project: Project | Path | str, host: str = "127.0.0.1",
                port: int = 8787) -> None:
    """Serve the actionable board forever (blocks; Ctrl-C to stop)."""
    server = make_server(project, host, port)
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"manju board serving at {url} (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
