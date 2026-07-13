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
                                   reports/frames/, .manju/thumbs/,
                                   .manju/frames/): truth files like
                                   project.yaml, shots/*.yaml or
                                   .manju/state.sqlite are never served here.
  POST /api/<action>             → JSON in, ``{ok: true, ...}`` / ``{ok: false,
                                   error: "one line"}`` out. Only the safe
                                   actions below exist; anything else is 404.

Actions: select · rollback_shot · snapshot · build · qc · package · redo ·
export · annotate.

Concurrency: one global ``threading.Lock`` guards every mutating action so two
clicks can't interleave. A click that arrives while another action runs gets an
immediate ``409`` ``{ok:false, error:"busy — another action is running"}``
rather than queueing — the server is threaded, so ``GET /`` and ``GET /media``
keep working during a long build.

Localhost, personal use: no auth (that is a deliberate scope decision), but path
traversal is still blocked because the media route reads arbitrary paths.
"""

from __future__ import annotations

import secrets

# Hostnames a browser is allowed to send in the Host header — the
# DNS-rebinding guard: a foreign name that resolves to 127.0.0.1 is refused
# (Round Y, review #12; mirrors gui/server.py's allowlist).
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
# Board POST bodies are tiny JSON control messages; cap to stop a hostile
# large body from tying the handler up.
_MAX_BODY_BYTES = 1 << 20  # 1 MiB

import base64
import hashlib
import json
import mimetypes
import os
import re
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from ..core.container import Project
from ..core.events import append_event
from ..runtime.buildlock import BuildLocked
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


class _BadRequest(_ApiError):
    """A malformed REQUEST (missing/invalid input fields) — the same one-line
    ``{ok:false, error:...}`` envelope, but as a real HTTP 400: the client sent
    something the server refuses to interpret, distinct from a well-formed
    request whose business rule said no (plain :class:`_ApiError`, HTTP 200)."""


# ---- Audit 15: served-board security headers (mirror gui/server.py's set) ----
# The board is a SINGLE-FILE inline-everything document, so — unlike the GUI,
# which serves an external /app.js and /app.css — script-src can't be 'self'.
# Instead it is HASH-based over the ACTUAL inline <script> the served bytes
# carry (that block includes the per-run token, so the hash is computed at serve
# time, never baked in): the script runs WITHOUT changing one HTML byte, so the
# static byte-pin (test_board_serve.py / test_fp_board_transport.py) stays
# frozen — these are response HEADERS only. style-src is 'unsafe-inline' because
# the board inlines its <style> block + a few style="" attributes; style
# injection is not a script-execution vector, so script-src carries the real
# XSS protection while the board stays styled.
_INLINE_SCRIPT_RE = re.compile(r"<script(?:\s[^>]*)?>(.*?)</script>", re.DOTALL)
_INLINE_STYLE_RE = re.compile(r"<style(?:\s[^>]*)?>.*?</style>", re.DOTALL)
_INLINE_HANDLER_RE = re.compile(r"""\son[a-z]+\s*=\s*(?:"([^"]*)"|'([^']*)')""",
                                re.IGNORECASE)


def _csp_sha256(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


def _board_security_headers(html: str) -> list[tuple[str, str]]:
    """The CSP + framing/sniff headers for a served board, derived from its own
    bytes so the inline script is allowed without any HTML change."""
    script_hashes = list(dict.fromkeys(_csp_sha256(m) for m in _INLINE_SCRIPT_RE.findall(html)))
    # inline event handlers, if any — scan MARKUP only (strip <script>/<style>
    # bodies so JS/CSS text can't false-match an on*= handler). The served board
    # has none today (the one onclick is static-mode only), so 'unsafe-hashes'
    # is omitted; this self-heals if a serve-mode handler is ever added.
    markup = _INLINE_STYLE_RE.sub("", _INLINE_SCRIPT_RE.sub("", html))
    handlers = list(dict.fromkeys(a or b for a, b in _INLINE_HANDLER_RE.findall(markup)))
    script_src_parts = ["script-src", *script_hashes]
    if handlers:
        script_src_parts.append("'unsafe-hashes'")
        script_src_parts += [_csp_sha256(h) for h in handlers]
    csp = (
        "default-src 'none'; "
        + " ".join(script_src_parts) + "; "
        "style-src 'unsafe-inline'; img-src 'self'; media-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'none'"
    )
    return [
        ("Content-Security-Policy", csp),
        ("X-Frame-Options", "DENY"),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "no-referrer"),
    ]


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
    self-deadlocks on its own already-held lock.

    Round Z (agent ZA): ``BuildLocked`` is deliberately NOT caught here
    anymore — it now propagates to ``_handle_api``'s own 409 branch (the
    same "busy" shape ``mutation_lock`` contention already returns), instead
    of downgrading to a 200 ``{ok:false}`` the way a content-lock
    (``WriteRejected``) rejection does. The two are different things: a
    ``WriteRejected`` is a clean business-rule refusal; a ``BuildLocked`` is
    "try again shortly", worth a distinct status."""
    from ..core.writes import WriteRejected, select_take_checked
    from ..runtime.buildlock import build_lock

    shot = body.get("shot")
    take = body.get("take")
    if not shot or not take:
        raise _ApiError("shot and take are required")
    try:
        with build_lock(project.root, actor=_actor()):
            result = select_take_checked(project, shot, take, actor=_actor(), via="board")
    except WriteRejected as exc:
        raise _ApiError(str(exc)) from exc
    return {"shot": result["shot"], "take": result["take"]}


def _api_rollback_shot(project: Project, body: dict) -> dict:
    """Round Z (agent ZA): ``rollback_shot`` writes ``status.selected_take``
    straight to the shot file with no lock of its own (§9 gap — its own
    docstring explains why it skips the VALUE-lock guard in
    ``select_take_checked``, but that is a different lock from the
    cross-process build lock guarded here). ``BuildLocked`` propagates to
    ``_handle_api``'s own 409 branch, same shape as ``mutation_lock`` busy."""
    from ..core.history import HistoryError, rollback_shot
    from ..runtime.buildlock import build_lock

    shot = body.get("shot")
    if not shot:
        raise _ApiError("shot is required")
    try:
        with build_lock(project.root, actor=_actor()):
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
    # Round Z (agent ZA): qc writes qc.json/qc.md/repair_plan.yaml (a
    # mutation, §9) — the board path held no process lock for it before,
    # unlike the GUI/MCP qc surfaces which already do.
    from ..qc.checks import run_qc
    from ..qc.report import write_reports
    from ..runtime.buildlock import build_lock

    with build_lock(project.root, actor=_actor()):
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
    from ..runtime.buildlock import build_lock

    try:
        with build_lock(project.root, actor=_actor()):
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
    from ..runtime.buildlock import build_lock

    # Validate the request (input) before checking state, so a malformed
    # profiles list is rejected the same whether or not a timeline exists.
    profiles = body.get("profiles") or ["srt", "otio"]
    if not isinstance(profiles, list) or not profiles:
        raise _ApiError("profiles must be a non-empty array of srt|otio|jianying|capcut")
    unknown = [p for p in profiles if p not in _EXPORT_PROFILES]
    if unknown:
        raise _ApiError(f"unknown export profile(s): {', '.join(map(str, unknown))} "
                        "(use srt|otio|jianying|capcut)")

    # Round Z (agent ZA): srt/ass land in captions/ — the same files a
    # concurrent build's captions phase writes (§9) — build lock guards it.
    with build_lock(project.root, actor=_actor()):
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


def _api_annotate(project: Project, body: dict) -> dict:
    """Wave 3 (§5.5): append ONE structured, media-bound review annotation
    (``manju.review.annotation/v1``, core.models.Annotation) to the shot's
    ``status.annotations``.

    Validation is strictly SERVER-SIDE (the board form is a convenience, never
    a guard): the shot must exist, the take must exist AND its media file must
    be on disk — its sha256 (core.hashing.hash_file) becomes the annotation's
    media binding; text/severity/frame/range/subject are checked here + by the
    Annotation model itself. Bad input → :class:`_BadRequest` (HTTP 400, same
    envelope).

    Write path: the SAME build-lock discipline as the other mutating actions,
    then ``core.writes.checked_shot_write`` (post-write check + revert;
    ``expected_rev`` → the CAS token, refused with the standard WriteRejected
    envelope when stale), then one ``annotate`` event. ``frame_rate`` is the
    exact rational string of ``ProjectConfig.frame_rate`` (the R2 typed
    resolver — the raw rational field never leaves core, per the ratemig1
    grep-pin) rendered by the ONE formatter (core.timebase.Rate.__str__);
    no second formatter is invented here."""
    from datetime import datetime, timezone

    from pydantic import ValidationError

    from ..core.hashing import hash_file
    from ..core.models import Annotation
    from ..core.writes import WriteRejected, checked_shot_write
    from ..runtime.buildlock import build_lock

    shot = body.get("shot")
    take = body.get("take")
    if not shot or not isinstance(shot, str) or not take or not isinstance(take, str):
        raise _BadRequest("shot and take are required")
    if shot not in project.shot_ids():
        raise _BadRequest(f"unknown shot '{shot}'")
    take_info = project.get_take(shot, take)
    if take_info is None:
        raise _BadRequest(f"{shot} has no take '{take}'")
    if take_info.media_path is None or not take_info.media_path.is_file():
        raise _BadRequest(
            f"{shot}/{take} has no media file on disk — 批注必须绑定真实媒体哈希")

    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise _BadRequest("text is required (1..2000 chars)")
    frame = body.get("frame")
    if frame is not None and (isinstance(frame, bool) or not isinstance(frame, int)):
        raise _BadRequest("frame must be a non-negative integer")
    range_frames = body.get("range")
    if range_frames is not None and not isinstance(range_frames, list):
        raise _BadRequest("range must be [start_frame, end_frame]")
    subject = body.get("subject", "")
    if not isinstance(subject, str):
        raise _BadRequest("subject must be a string")
    geometry = body.get("geometry")
    if geometry is not None and not isinstance(geometry, dict):
        raise _BadRequest("geometry must be an object")
    repair_variable = body.get("repair_variable")
    if repair_variable is not None and not isinstance(repair_variable, str):
        raise _BadRequest("repair_variable must be a string")
    expected_rev = body.get("expected_rev")
    if expected_rev is not None and not isinstance(expected_rev, str):
        raise _BadRequest("expected_rev must be a string")

    ann_id = "ann_" + secrets.token_hex(6)
    try:
        annotation = Annotation(
            id=ann_id,
            take=take,
            media_sha256=hash_file(take_info.media_path),
            frame_rate=str(project.load_config().frame_rate),
            frame=frame,
            range_frames=range_frames,
            subject=subject,
            severity=body.get("severity", "note"),
            text=text.strip(),
            geometry=geometry,
            repair_variable=repair_variable,
            actor=_actor(),
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    except ValidationError as exc:
        first = exc.errors()[0]
        raise _BadRequest(str(first.get("msg", exc))) from exc

    record = annotation.model_dump(exclude_none=True)

    def mutate(d: dict) -> None:
        status = d.setdefault("status", {})
        anns = status.get("annotations")
        if not isinstance(anns, list):
            anns = []
        anns.append(record)
        status["annotations"] = anns

    try:
        with build_lock(project.root, actor=_actor()):
            checked_shot_write(project, shot, mutate, guard_paths=(),
                               expected_text_hash=expected_rev)
    except WriteRejected as exc:
        raise _ApiError(str(exc)) from exc
    append_event(project.root, _actor(), "annotate",
                 {"shot": shot, "take": take, "id": ann_id,
                  "severity": annotation.severity, "via": "board"})
    return {"id": ann_id, "stale": False}


API_ACTIONS: dict[str, Callable[[Project, dict], dict]] = {
    "select": _api_select,
    "rollback_shot": _api_rollback_shot,
    "snapshot": _api_snapshot,
    "build": _api_build,
    "qc": _api_qc,
    "package": _api_package,
    "redo": _api_redo,
    "export": _api_export,
    "annotate": _api_annotate,
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
        # Round Y (review #12): the board exposes MUTATING POST actions
        # (select/rollback/snapshot/build/qc/package/redo/export). Bound to
        # localhost is NOT sufficient — a page in the user's browser can POST
        # to 127.0.0.1 (CSRF), and CORS only blocks READING the reply, not the
        # side effect. So the board now carries the SAME control-plane guards
        # as the main GUI (gui/server.py): a per-run token every mutating POST
        # must echo, a Host allowlist (DNS-rebinding guard), a JSON
        # Content-Type gate, and a request-body size cap. The token is minted
        # here and embedded into the served page so the board's own JS sends
        # it; a foreign page cannot read it.
        self.token = secrets.token_urlsafe(24)
        self.allowed_hosts = set(_LOCAL_HOSTS)
        host = server_address[0]
        if host and host not in ("0.0.0.0", "::"):
            self.allowed_hosts.add(host.lower())


class BoardHandler(BaseHTTPRequestHandler):
    server_version = "manju-board/1.0"
    protocol_version = "HTTP/1.1"

    # ---- convenience

    @property
    def _project(self) -> Project:
        return self.server.project  # type: ignore[attr-defined]

    def log_message(self, *args: Any) -> None:  # keep the console quiet
        pass

    # ---- control-plane guards (Round Y, review #12)

    def _host_ok(self) -> bool:
        """DNS-rebinding guard: the Host header's hostname must be a known local
        name. A foreign name pointing at 127.0.0.1 is refused."""
        raw = self.headers.get("Host", "")
        hostname = raw.rsplit(":", 1)[0].strip().lower() if raw else ""
        # bracketed IPv6 keeps its brackets in the allowlist
        if raw.startswith("[") and "]" in raw:
            hostname = raw[: raw.index("]") + 1].lower()
        return hostname in self.server.allowed_hosts  # type: ignore[attr-defined]

    def _reject_bad_host(self) -> bool:
        if self._host_ok():
            return False
        self._read_body_raw()  # drain so keep-alive stays sane
        self._send_json(HTTPStatus.FORBIDDEN,
                        {"ok": False, "error": "bad Host header (DNS-rebinding guard)"})
        return True

    def _token_ok(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-Manju-Token", ""), self.server.token)  # type: ignore[attr-defined]

    # ---- routing

    def do_GET(self) -> None:
        if self._reject_bad_host():
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_board()
        elif path.startswith("/media/"):
            self._serve_media(path[len("/media/"):])
        elif "text/html" in (self.headers.get("Accept") or ""):
            # UX audit F21: browser navigations get a page with a way home;
            # API fetches (no text/html Accept) keep the JSON envelope.
            body = ('<!doctype html><html lang="zh"><meta charset="utf-8">'
                    "<title>404</title><body style=\"font-family:system-ui;"
                    'padding:2rem\"><p>页面不存在 (not found)。</p>'
                    '<p><a href="/">返回看板</a></p></body></html>')
            data = body.encode("utf-8")
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_HEAD(self) -> None:
        if self._reject_bad_host():
            return
        # Mirror GET for media (browsers may probe) without a body.
        path = urlparse(self.path).path
        if path.startswith("/media/"):
            self._serve_media(path[len("/media/"):])
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if self._reject_bad_host():
            return
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            self._read_body_raw()  # drain so keep-alive stays sane
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        # Round Y (review #12): every mutating POST must echo the per-run token
        # and be a JSON request — a cross-site form POST carries neither.
        if not self._token_ok():
            self._read_body_raw()
            self._send_json(HTTPStatus.FORBIDDEN,
                            {"ok": False, "error": "missing or invalid X-Manju-Token"})
            return
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype and ctype != "application/json":
            self._read_body_raw()
            self._send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                            {"ok": False, "error": "Content-Type must be application/json"})
            return
        self._handle_api(path[len("/api/"):])

    # ---- GET /

    def _serve_board(self) -> None:
        try:
            html = render_board(self._project, serve=True, token=self.server.token)
        except Exception as exc:  # a broken project should not kill the thread
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                            {"ok": False, "error": _one_line(str(exc))})
            return
        data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        # Audit 15: the GUI's security header set, computed from the served
        # bytes so the inline (token-bearing) script runs with the HTML unchanged.
        for name, value in _board_security_headers(html):
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    # ---- GET /media/<relpath>

    # goal item 40: project-root containment alone let `/media/<relpath>` read
    # ANY project file — project.yaml, shots/*.yaml, story/*, events.jsonl,
    # .manju/state.sqlite — not just the media/preview surfaces the board
    # actually links to. Allowlisted to what board.py actually renders as an
    # image/video/poster src: take media + imports thumbnails under media/,
    # keyframe posters under reports/frames/, the thumb/waveform cache under
    # .manju/thumbs/, and (FP T1) the boundary-view stills in the EXISTING
    # frame-preview cache .manju/frames/ — another §3 disposable preview
    # surface, precedent: the GUI's .manju/webpreview/ entry (NOT the rest of
    # .manju — state.sqlite/events stay unreachable). Mirrors the GUI's
    # MEDIA_PREFIXES allowlist (gui/server.py).
    _MEDIA_PREFIXES = ("media/", "reports/frames/", ".manju/thumbs/",
                       ".manju/frames/")

    def _safe_media_path(self, rel: str) -> Path:
        """Resolve a project-relative media path, refusing any escape above the
        project root AND anything outside the preview-surface allowlist above.
        Delegates to the ONE shared containment gate (Audit 14:
        :meth:`Project.safe_served_path`) so the board and GUI media gates can
        never be hardened one-sidedly — ``resolve()`` there collapses ``..`` AND
        follows symlinks, so a symlink pointing outside the tree (or outside the
        allowlist) is rejected too. ``unquote`` first (an absolute-looking path
        is treated as project-relative). Raises :class:`ValueError` on a
        refusal (→ 403)."""
        target = self._project.safe_served_path(unquote(rel), self._MEDIA_PREFIXES)
        if target is None:
            raise ValueError("path is not a served preview surface")
        return target

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
        except _BadRequest as exc:
            # malformed INPUT (Wave 3 annotate validation) — a real 400, same
            # one-line envelope as everything else.
            self._send_json(HTTPStatus.BAD_REQUEST,
                            {"ok": False, "error": _one_line(str(exc))})
        except _ApiError as exc:
            self._send_json(HTTPStatus.OK, {"ok": False, "error": _one_line(str(exc))})
        except BuildLocked as exc:
            # Round Z (agent ZA): a SEPARATE process (CLI `manju build`, or
            # another manju surface) holds the cross-process build lock — the
            # SAME "busy" shape mutation_lock's own contention returns above,
            # so the board's client-side handling needs no new branch.
            self._send_json(HTTPStatus.CONFLICT,
                            {"ok": False, "error": _one_line(str(exc))})
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
        # Round Y (review #12): cap the body — read at most the limit + 1 so an
        # over-limit request is detected without pulling the whole thing into
        # memory; the JSON parse then fails and _handle_api returns 400.
        if n > _MAX_BODY_BYTES:
            # Audit 17: the rest of the declared body is left UNDRAINED, so the
            # keep-alive connection must be closed after we respond — otherwise
            # those leftover bytes desync into the next request on this socket.
            # _send_json emits `Connection: close` when this flag is set.
            self.close_connection = True
            self.rfile.read(min(n, _MAX_BODY_BYTES + 1))
            return b"__oversize__"
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
        if self.close_connection:  # Audit 17: honour an oversize-body close
            self.send_header("Connection", "close")
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
