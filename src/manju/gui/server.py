"""The `manju gui` HTTP server (§1-⑦ revisited): stdlib-only, localhost-first.

Position in the architecture: exactly where the CLI and MCP sit — a thin
client over the same engine core (status/stale/graph/check/explain). The GUI
adds NO state of its own: every mutation goes through the same functions and
lands in the same text files + events.jsonl. Dangerous operations (`unlock`,
`gc --hard`, `pack`/`unpack`, `import` of arbitrary paths) are absent from
this surface, mirroring the MCP decision (§5, §11).

Threat model (a localhost web server is still a web server):

- **DNS rebinding**: every request's Host header must be localhost/127.0.0.1/
  [::1] (or the explicitly bound host) — anything else is 403.
- **CSRF**: state-changing POSTs require the ``X-Manju-Token`` header carrying
  a per-run random token embedded in the served page. A custom header forces
  a CORS preflight, which we never answer — blind cross-origin POSTs die.
- **Path traversal**: /media only serves files under an allowlist of project
  subtrees, resolved through :meth:`Project.resolve`, which rejects any path
  escaping the project root. `.manju/`, `.git/`, shots/ etc. are not served.
- **XSS**: the API returns JSON; the page builds DOM via textContent. The one
  HTML document we serve carries a strict CSP (script/style self only).

Mutating engine calls are serialized through :class:`~manju.gui.jobs.JobRunner`
(one at a time, honest to the engine's single-writer design). Quick text-file
mutations (select / lock) take a small in-process mutex; cross-PROCESS
exclusion (a CLI build racing a GUI build) is the build-lock's job (§5 value
locks guard content, not processes — see runtime/buildlock).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from ..core.container import Project, ProjectError
from ..core.events import append_event, tail_events
from ..core.idents import SAFE_SEGMENT_PATTERN, is_safe_segment
from .jobs import JobRunner
from .state import build_state

__all__ = ["GuiServer", "create_server", "serve"]

# Only these project subtrees are ever served over HTTP (read-only).
MEDIA_PREFIXES = ("media/", "renders/", "reports/", "exports/", "captions/")

_CONTENT_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".webm": "video/webm",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".srt": "text/plain; charset=utf-8", ".ass": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}

_MAX_BODY = 1 << 20  # 1 MiB is plenty for any JSON action body

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def _optional_build_lock(root: Path, actor: str):
    """The per-project process build lock (§5 dual-actor race), feature-detected.

    R2's ``runtime/buildlock`` is an engine-side change that may or may not be
    present on this line yet: when it is, voice/qc jobs hold the same lock every
    other surface does; when it is absent this degrades to a no-op context
    manager, so the GUI is green standalone and lights up the moment the lock
    module lands (the ``build_lock`` status field fills in the same way)."""
    try:
        from ..runtime.buildlock import build_lock

        return build_lock(root, actor=actor)
    except Exception:  # module absent, or an unexpected signature — never block
        import contextlib

        return contextlib.nullcontext()


def _empty_spend(project: Project) -> dict[str, Any]:
    """The §8.3 事后 spend report's empty-project shape, served when the
    ``build.spend`` module (round-r/spend) has not landed yet. Matches the real
    ``spend_report`` output for a project with no runs (``source="empty"``)."""
    limit = None
    try:
        limit = project.load_config().budget.limit
    except Exception:
        pass
    return {"total": 0, "currency": None, "budget_limit": limit,
            "by_provider": [], "by_shot": [], "recent": [], "source": "empty"}


def _deep_merge_packaging(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """One-level merge of a packaging section patch onto the current spec dump.
    Nested section dicts (intro/cover/logo/…) merge key-by-key; lists
    (info_cards) and scalars replace wholesale — the §14 packaging shape is flat
    per section, so no deeper recursion is needed."""
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            merged = dict(out[k])
            merged.update(v)
            out[k] = merged
        else:
            out[k] = v
    return out


def _packaging_warnings(project: Project, spec: Any) -> list[str]:
    """Advisory-only packaging warnings the /packaging page surfaces on save —
    the cover/teaser windows against the newest final's real length, and the
    intro-card overlap advisories media/packaging.make_package would raise.
    Degrades to no warnings when there is no final or ffmpeg to probe it."""
    warnings: list[str] = []
    final_ms: int | None = None
    try:
        final = project.newest_final_path()
        if final is not None and final.exists():
            from ..media.probe import probe_duration_ms

            final_ms = probe_duration_ms(final)
    except Exception:
        final_ms = None
    if final_ms:
        if spec.teaser.enabled:
            if spec.teaser.from_ms >= final_ms:
                warnings.append(
                    f"teaser.from_ms ({spec.teaser.from_ms}ms) 已到/超过 final 末尾 "
                    f"({final_ms}ms) — 导出会失败")
            elif spec.teaser.from_ms + spec.teaser.duration_ms > final_ms:
                warnings.append(
                    f"teaser 窗口超出 final ({final_ms}ms),导出时会被裁剪")
        if spec.cover.mode == "frame" and spec.cover.frame_ms >= final_ms:
            warnings.append(
                f"cover.frame_ms ({spec.cover.frame_ms}ms) 已到/超过 final 末尾 "
                f"({final_ms}ms)")
    if spec.intro.enabled:
        intro_ms = spec.intro.duration_ms
        if spec.cover.mode == "frame" and spec.cover.frame_ms < intro_ms:
            warnings.append(
                f"cover.frame_ms 落在片头卡内 (0–{intro_ms}ms),封面会显示片头,非正片")
        if spec.teaser.enabled and spec.teaser.from_ms < intro_ms:
            warnings.append(
                f"teaser.from_ms 落在片头卡内 (0–{intro_ms}ms)")
    return warnings


class GuiServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying the project, token and job runner."""

    daemon_threads = True

    def __init__(self, project: Project, host: str, port: int, actor: str,
                 readonly: bool = False,
                 workspace: dict[str, Project] | None = None):
        self.project = project
        self.actor = actor
        self.readonly = readonly
        self.token = secrets.token_urlsafe(24)
        self.runner = JobRunner()
        # select/lock are quick read-modify-write cycles on one YAML file;
        # serialize them among themselves so two clicks can't interleave.
        self.quick_mutex = threading.Lock()
        # /api/state cache keyed by the project fingerprint: polling a large
        # project must not re-scan every shot when nothing changed
        self.state_cache: tuple[str, dict[str, Any]] | None = None
        # workspace mode (--workspace): slug -> Project; ONE active project
        # per server (self.project), switchable via POST /api/switch. Jobs
        # already running keep their closed-over project — a switch never
        # retargets in-flight work.
        self.workspace = workspace or {}
        self.active_slug = next(
            (slug for slug, p in self.workspace.items() if p.root == project.root), None)
        self.allowed_hosts = set(_LOCAL_HOSTS)
        if host not in ("", "0.0.0.0", "::"):
            self.allowed_hosts.add(host)
        super().__init__((host, port), _Handler)

    def switch_project(self, slug: str) -> Project:
        target = self.workspace.get(slug)
        if target is None:
            raise KeyError(slug)
        with self.quick_mutex:
            self.project = target
            self.active_slug = slug
            self.state_cache = None
        return target

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        host = self.server_address[0]
        shown = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
        return f"http://{shown}:{self.port}/"

    def close(self) -> None:
        self.runner.shutdown(timeout=1.0)
        self.server_close()


def discover_workspace(root: Path) -> dict[str, Project]:
    """Scan a directory for manju projects (direct children + the dir itself).
    Slugs are the directory stems, uniquified with _2/_3 on collision."""
    found: list[Project] = []
    root = Path(root).resolve()
    if (root / "project.yaml").exists():
        try:
            found.append(Project(root))
        except ProjectError:
            pass
    for child in sorted(root.iterdir()) if root.is_dir() else []:
        if child.is_dir() and (child / "project.yaml").exists():
            try:
                found.append(Project(child))
            except ProjectError:
                continue
    projects: dict[str, Project] = {}
    for p in found:
        slug = base = p.root.stem or "project"
        n = 2
        while slug in projects:
            slug = f"{base}_{n}"
            n += 1
        projects[slug] = p
    return projects


def create_server(project: Project, host: str = "127.0.0.1", port: int = 0,
                  actor: str | None = None, readonly: bool = False,
                  workspace: dict[str, Project] | None = None) -> GuiServer:
    actor = actor or os.environ.get("MANJU_ACTOR", "human")
    return GuiServer(project, host, port, actor, readonly=readonly,
                     workspace=workspace)


def serve(project: Project, host: str = "127.0.0.1", port: int = 8321,
          open_browser: bool = True) -> None:
    """Blocking entry point for the CLI."""
    server = create_server(project, host, port)
    if open_browser:
        try:
            import webbrowser

            threading.Timer(0.4, webbrowser.open, args=[server.url]).start()
        except Exception:
            pass  # a browser is a convenience, never a requirement
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


# --------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    server: GuiServer  # narrowed type
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- plumbing

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # a polling UI would drown the terminal; errors go via responses

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        hostname = host.rsplit(":", 1)[0] if (":" in host and not host.startswith("[")) else host
        if host.startswith("[") and "]" in host:  # [::1]:8321
            hostname = host[: host.index("]") + 1]
        return hostname.lower() in self.server.allowed_hosts

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message: str, status: int) -> None:
        self._send_json({"error": message}, status)

    def _send_text(self, body: str, content_type: str, status: int = 200,
                   extra: dict[str, str] | None = None) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > _MAX_BODY:
            self._send_error_json("request body too large", 413)
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_error_json("invalid JSON body", 400)
            return None
        if not isinstance(data, dict):
            self._send_error_json("JSON body must be an object", 400)
            return None
        return data

    # --------------------------------------------------------------- routes

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_allowed():
            self._send_error_json("host not allowed (DNS-rebinding guard)", 403)
            return
        url = urlsplit(self.path)
        path = url.path
        try:
            if path == "/":
                self._page()
            elif path == "/favicon.ico":
                # a real (tiny) icon: kills the one console 404 every load
                svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
                       '<rect width="16" height="16" rx="3" fill="#1d2027"/>'
                       '<rect x="3" y="4" width="10" height="8" rx="1" fill="#6ea8fe"/>'
                       '<circle cx="6" cy="8" r="1.4" fill="#14161a"/></svg>')
                self._send_text(svg, "image/svg+xml")
            elif path == "/app.css":
                from .page import render_css

                self._send_text(render_css(), "text/css; charset=utf-8")
            elif path == "/app.js":
                from .page import render_js

                self._send_text(render_js(), "application/javascript; charset=utf-8")
            elif path == "/api/state":
                from .state import project_fingerprint

                fp = project_fingerprint(self.server.project, self.server.runner)
                cached = self.server.state_cache
                if cached is not None and cached[0] == fp:
                    payload = cached[1]
                else:
                    payload = build_state(self.server.project, self.server.runner)
                    payload["fp"] = fp
                    payload["readonly"] = self.server.readonly
                    payload["workspace"] = (
                        {"active": self.server.active_slug,
                         "count": len(self.server.workspace)}
                        if self.server.workspace else None)
                    self.server.state_cache = (fp, payload)
                self._send_json(payload)
            elif path == "/api/jobs":
                self._send_json({"jobs": [j.to_dict() for j in self.server.runner.list()]})
            elif path == "/api/check":
                from ..core.check import run_check

                self._send_json(run_check(self.server.project).to_dict())
            elif path == "/api/explain":
                from ..build.explain import explain

                self._send_json(explain(self.server.project))
            elif path == "/api/events":
                q = parse_qs(url.query)
                n = 50
                try:
                    n = max(1, min(1000, int(q.get("n", ["50"])[0])))
                except ValueError:
                    pass
                actor = q.get("actor", [None])[0]
                action = q.get("action", [None])[0]
                events = tail_events(self.server.project.root, 1000)
                if actor:
                    events = [e for e in events if e.get("actor") == actor]
                if action:
                    events = [e for e in events if e.get("action") == action]
                self._send_json({"events": events[-n:]})
            elif path == "/api/projects":
                self._projects_list()
            elif path == "/api/watch":
                self._watch(parse_qs(url.query))
            elif path == "/api/proposals":
                self._proposals()
            elif path == "/api/onboarding":
                self._onboarding_get()
            elif path == "/api/cockpit":
                from .cockpit import cockpit_data

                self._send_json(cockpit_data(self.server.project))
            elif path == "/api/tasks":
                self._tasks_get()
            elif path == "/api/presets":
                self._presets_get()
            elif path == "/api/rules":
                rules_path = self.server.project.rules_path
                self._send_json({
                    "exists": rules_path.exists(),
                    "yaml": rules_path.read_text(encoding="utf-8") if rules_path.exists() else "",
                })
            elif path == "/api/packaging":
                pkg_path = self.server.project.packaging_path
                self._send_json({
                    "exists": pkg_path.exists(),
                    "yaml": pkg_path.read_text(encoding="utf-8") if pkg_path.exists() else "",
                })
            elif path.startswith("/api/bible/"):
                self._bible_get(unquote(path[len("/api/bible/"):]))
            elif path == "/api/spend":
                try:
                    from ..build.spend import spend_report

                    self._send_json(spend_report(self.server.project))
                except ImportError:
                    # round-r/spend not landed yet — serve the empty-shape report
                    self._send_json(_empty_spend(self.server.project))
            elif path == "/api/schema":
                from ..core.models import export_json_schemas

                self._send_json({"schemas": export_json_schemas()})
            elif path == "/api/timeline":
                timeline = self.server.project.load_timeline()
                self._send_json({"timeline": timeline.model_dump() if timeline else None})
            elif path == "/api/doctor":
                from ..build.doctor import run_doctor

                self._send_json(run_doctor(self.server.project))
            elif path == "/api/git/status":
                from ..core.gitops import repo_status

                self._send_json({"git": repo_status(self.server.project.root)})
            elif path == "/api/git/diff":
                from ..core.gitops import diff_text

                q = parse_qs(url.query)
                rel = q.get("path", [None])[0]
                self._send_json({"diff": diff_text(self.server.project.root, rel)})
            elif path == "/api/git/log":
                from ..core.gitops import file_log

                q = parse_qs(url.query)
                rel = q.get("path", [None])[0]
                try:
                    n = max(1, min(200, int(q.get("n", ["20"])[0])))
                except ValueError:
                    n = 20
                self._send_json({"log": file_log(self.server.project.root, rel, n=n)})
            elif path.startswith("/api/shot/"):
                self._shot_get(unquote(path[len("/api/shot/"):]))
            elif path.startswith("/media/"):
                self._media(unquote(path[len("/media/"):]))
            elif path.startswith("/preview/"):
                self._preview(unquote(path[len("/preview/"):]))
            elif path.startswith("/thumb/"):
                self._thumb(unquote(path[len("/thumb/"):]))
            elif self._edit_get(path, url):
                pass  # round-T 剪辑 EDIT page + its frame previews — see _edit_get
            elif self._pages_get(path, url):
                pass  # round-S server-rendered pages (S8b) — see _pages_get
            elif self._pages_t_get(path, url):
                pass  # round-T finishing pages (subtitles/mixer/packaging)
            elif self._exports_get(path, url):
                pass  # round-U 导出中心 export center — see _exports_get
            elif self._director_get(path, url):
                pass  # round-U 导演助手 director loop page — see _director_get
            elif self._modes_get(path):
                pass  # round-U 新手/专业 + glossary chrome assets — see _modes_get
            elif self._storyboard_get(path, url):
                pass  # round-U 分镜工作台 storyboard table — see _storyboard_get
            elif self._lab_get(path, url):
                pass  # round-U 镜头实验室 shot lab — see _lab_get
            elif self._create_get(path, url):
                pass  # round-V 创作 creation funnel workspace — see _create_get
            elif self._ingest_get(path, url):
                pass  # round-X 批量入库 batch ingest — see _ingest_get
            else:
                self._send_error_json("not found", 404)
        except BrokenPipeError:
            pass  # client went away mid-response (video seeks do this constantly)
        except Exception as exc:
            try:
                self._send_error_json(" ".join(str(exc).split()), 500)
            except Exception:
                pass

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_allowed():
            self._send_error_json("host not allowed (DNS-rebinding guard)", 403)
            return
        if self.server.readonly and urlsplit(self.path).path != "/api/validate":
            # /api/validate is pure (no write) — readonly editors keep live checks
            self._send_error_json("readonly mode — 只读工作台,操作请回到项目机器", 403)
            return
        if self.headers.get("X-Manju-Token") != self.server.token:
            self._send_error_json("missing or invalid X-Manju-Token", 403)
            return
        url = urlsplit(self.path)
        path = url.path
        try:
            if path == "/api/upload":
                self._act_upload(parse_qs(url.query))
                return
            if path == "/api/lib/upload":  # round-S: stream upload straight into the library (S8b)
                self._act_lib_upload(parse_qs(url.query))
                return
            if path == "/api/ingest/upload":  # round-X: stream upload into the ingest staging dir
                self._act_ingest_upload(parse_qs(url.query))
                return
            body = self._read_body()
            if body is None:
                return
            if path.startswith("/api/shot/"):
                self._act_shot_save(unquote(path[len("/api/shot/"):]), body)
                return
            if path.startswith("/api/bible/"):
                self._act_bible_save(unquote(path[len("/api/bible/"):]), body)
                return
            handler = {
                "/api/select": self._act_select,
                "/api/build": self._act_build,
                "/api/redo": self._act_redo,
                "/api/voice": self._act_voice,
                "/api/redo-batch": self._act_redo_batch,
                "/api/voice-batch": self._act_voice_batch,
                "/api/plan": self._act_plan,
                "/api/qc": self._act_qc,
                "/api/lock": self._act_lock,
                "/api/git/commit": self._act_git_commit,
                "/api/git/snapshot": self._act_git_snapshot,
                "/api/git/rollback-file": self._act_git_rollback_file,
                "/api/index": self._act_index,
                "/api/rules": self._act_rules_save,
                "/api/packaging": self._act_packaging_save,
                "/api/switch": self._act_switch,
                "/api/new-project": self._act_new_project,
                "/api/onboarding/dismiss": self._act_onboarding_dismiss,
                "/api/take-note": self._act_take_note,
                "/api/validate": self._act_validate,
            }.get(path)
            if handler is None:
                if self._edit_post(path, body):  # round-T 剪辑 EDIT actions
                    return
                if self._pages_post(path, body):  # round-S page actions (S8b)
                    return
                if self._pages_t_post(path, body):  # round-T finishing actions
                    return
                if self._exports_post(path, body):  # round-U 导出中心 actions
                    return
                if self._director_post(path, body):  # round-U director loop actions
                    return
                if self._modes_post(path, body):  # round-U mode + glossary toggles
                    return
                if self._storyboard_post(path, body):  # round-U 分镜工作台 actions
                    return
                if self._lab_post(path, body):  # round-U 镜头实验室 shot lab actions
                    return
                if self._create_post(path, body):  # round-V 创作 funnel actions
                    return
                if self._ingest_post(path, body):  # round-X 批量入库 batch ingest actions
                    return
                self._send_error_json("not found", 404)
                return
            handler(body)
        except BrokenPipeError:
            pass
        except Exception as exc:
            try:
                self._send_error_json(" ".join(str(exc).split()), 500)
            except Exception:
                pass

    # ----------------------------------------------------------------- page

    def _page(self) -> None:
        from .page import render_page

        try:
            name = self.server.project.load_config().name
        except Exception:
            name = self.server.project.root.name
        html = render_page(name, self.server.token)
        self._send_text(
            html, "text/html; charset=utf-8",
            extra={
                "Content-Security-Policy": (
                    "default-src 'none'; script-src 'self'; style-src 'self'; "
                    "img-src 'self'; media-src 'self'; connect-src 'self'; "
                    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
                ),
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
            },
        )

    # ---------------------------------------------------------------- media

    def _resolve_served(self, rel: str) -> Path | None:
        """Allowlist + containment gate shared by /media and /preview."""
        project = self.server.project
        rel = rel.lstrip("/")
        if not any(rel.startswith(p) for p in MEDIA_PREFIXES):
            return None
        try:
            abspath = project.resolve(rel)  # rejects escapes above the root
            # re-check the allowlist against the RESOLVED path: "media/../x"
            # stays inside the root but must not sidestep the prefix gate,
            # and a symlink out of an allowed tree must not either
            if not any(project.relpath(abspath).startswith(p) for p in MEDIA_PREFIXES):
                return None
        except ProjectError:
            return None
        return abspath

    def _media(self, rel: str) -> None:
        abspath = self._resolve_served(rel)
        if abspath is None:
            self._send_error_json("path not served", 403)
            return
        if not abspath.is_file():
            self._send_error_json("not found", 404)
            return
        self._serve_file(abspath)

    def _preview(self, rel: str) -> None:
        """Browser-safe preview of a take that <video> cannot play natively:
        lazy ffmpeg transcode into the disposable cache (.manju/webpreview,
        §3), raw bytes as the degradation path. Same allowlist as /media."""
        abspath = self._resolve_served(rel)
        if abspath is None:
            self._send_error_json("path not served", 403)
            return
        if not abspath.is_file():
            self._send_error_json("not found", 404)
            return
        try:
            from ..media.webpreview import ensure_preview, needs_preview

            if needs_preview(abspath):
                preview = ensure_preview(self.server.project.root, abspath)
                if preview is not None:
                    self._serve_file(preview)
                    return
        except Exception:
            pass  # degrade to the original bytes below
        self._serve_file(abspath)

    def _thumb(self, rel: str) -> None:
        """Lazy cached thumbnail of a take (the wall must read at a glance
        without pressing play). 404 when no thumb can be made — the page
        just shows the plain card."""
        abspath = self._resolve_served(rel)
        if abspath is None:
            self._send_error_json("path not served", 403)
            return
        if not abspath.is_file():
            self._send_error_json("not found", 404)
            return
        try:
            from ..media.webpreview import ensure_thumb

            thumb = ensure_thumb(self.server.project.root, abspath)
        except Exception:
            thumb = None
        if thumb is None:
            self._send_error_json("no thumbnail available", 404)
            return
        self._serve_file(thumb)

    def _serve_file(self, abspath: Path) -> None:
        st = abspath.stat()
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        ctype = _CONTENT_TYPES.get(abspath.suffix.lower(), "application/octet-stream")
        size = st.st_size
        start, end = 0, size - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                else:  # suffix range: last N bytes
                    n = int(m.group(2))
                    start = max(0, size - n)
                if start >= size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(abspath, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # -------------------------------------------------------------- actions

    def _act_select(self, body: dict[str, Any]) -> None:
        """Same checked write every other select entrance uses (round W,
        #39): lock guard, post-write check scoped to the shot, revert on
        regression, plus the process build lock (in-process quick_mutex AND
        the cross-process build_lock — the GUI job runner is its own
        process, so an in-process mutex alone does not stop a concurrent CLI
        `select`)."""
        from ..core.writes import WriteRejected, select_take_checked
        from ..runtime.buildlock import BuildLocked

        project = self.server.project
        shot_id, take = str(body.get("shot") or ""), str(body.get("take") or "")
        if not shot_id or not take:
            self._send_error_json("shot and take are required", 400)
            return
        with self.server.quick_mutex:
            if project.get_take(shot_id, take) is None:
                self._send_error_json(f"{shot_id} has no take '{take}'", 404)
                return
            try:
                with _optional_build_lock(project.root, self.server.actor):
                    result = select_take_checked(
                        project, shot_id, take, actor=self.server.actor, via="gui"
                    )
            except (WriteRejected, BuildLocked) as exc:
                self._send_error_json(str(exc), 409)
                return
        self._send_json({"ok": True, "shot": result["shot"], "take": result["take"]})

    def _act_validate(self, body: dict[str, Any]) -> None:
        """Keystroke-time validation for the editors (VS Code settings.json
        pattern): parse + model-validate ONLY — no write, no full check, no
        lock verification (those stay in the gated save). Fast and pure, so
        a debounced client can call it per pause."""
        kind = str(body.get("kind") or "")
        parsed, err = self._parse_yaml_mapping(body.get("yaml"))
        if err:
            self._send_json({"ok": False, "errors": [err]})
            return
        errors: list[str] = []
        try:
            if kind == "shot":
                from ..core.models import ShotSpec

                data = dict(parsed)
                data.setdefault("id", body.get("id") or "S000")
                ShotSpec.model_validate(data)
            elif kind == "rules":
                from ..core.models import TimelineRules

                TimelineRules.model_validate(parsed)
            elif kind == "packaging":
                from ..core.models import PackagingSpec

                PackagingSpec.model_validate(parsed)
            elif kind.startswith("bible/"):
                if not all(isinstance(v, dict) for v in parsed.values()):
                    errors.append("bible entries must be mappings (id -> fields)")
            else:
                self._send_error_json(f"unknown kind: {kind}", 400)
                return
        except Exception as exc:  # pydantic ValidationError -> one-line finding
            errors = [" ".join(str(exc).split())[:500]]
        self._send_json({"ok": not errors, "errors": errors})

    def _act_take_note(self, body: dict[str, Any]) -> None:
        """Director note on a take (review annotation, Frame.io-inspired):
        one line of YAML under status.take_notes — reviewable, revertible,
        never touching media (§3). Empty text deletes the note."""
        project = self.server.project
        shot_id = str(body.get("shot") or "")
        take = str(body.get("take") or "")
        text = str(body.get("text") if body.get("text") is not None else "")
        if not shot_id or not take:
            self._send_error_json("shot and take are required", 400)
            return
        if len(text) > 2000:
            self._send_error_json("note too long (max 2000 chars)", 400)
            return
        with self.server.quick_mutex:
            if project.get_take(shot_id, take) is None:
                self._send_error_json(f"{shot_id} has no take '{take}'", 404)
                return

            def mutate(d: dict[str, Any]) -> None:
                status = d.setdefault("status", {})
                notes = status.get("take_notes")
                if not isinstance(notes, dict):
                    notes = {}
                if text.strip():
                    notes[take] = text.strip()
                else:
                    notes.pop(take, None)
                if notes:
                    status["take_notes"] = notes
                else:
                    status.pop("take_notes", None)

            project.update_shot_raw(shot_id, mutate)
            append_event(project.root, self.server.actor, "take_note",
                         {"shot": shot_id, "take": take,
                          "deleted": not text.strip(), "via": "gui"})
        self._send_json({"ok": True, "shot": shot_id, "take": take,
                         "text": text.strip()})

    def _act_lock(self, body: dict[str, Any]) -> None:
        from ..core.locks import seal_lock

        project = self.server.project
        shot_id, fieldpath = str(body.get("shot") or ""), str(body.get("field") or "")
        if not shot_id or not fieldpath:
            self._send_error_json("shot and field are required", 400)
            return
        with self.server.quick_mutex:
            try:
                raw = project.load_shot_raw(shot_id)
                digest = seal_lock(raw, fieldpath)
            except (KeyError, IndexError, ProjectError) as exc:
                self._send_error_json(f"cannot lock: {exc}", 400)
                return

            def mutate(d: dict[str, Any]) -> None:
                locked = d.get("locked")
                if not isinstance(locked, dict):
                    locked = {str(p): "" for p in locked} if isinstance(locked, list) else {}
                locked[fieldpath] = digest
                d["locked"] = locked

            project.update_shot_raw(shot_id, mutate)
            append_event(project.root, self.server.actor, "lock",
                         {"shot": shot_id, "field": fieldpath, "via": "gui"})
        self._send_json({"ok": True, "shot": shot_id, "field": fieldpath, "hash": digest})

    def _act_build(self, body: dict[str, Any]) -> None:
        from ..build.graph import GEN_MODES  # round W (issue #3): shared enum

        target = str(body.get("target") or "final")
        gen = str(body.get("gen") or "missing")
        if target not in ("proxy", "final", "exports", "qc"):
            self._send_error_json(f"unknown target: {target}", 400)
            return
        if gen not in GEN_MODES:
            self._send_error_json(f"unknown gen mode: {gen}", 400)
            return
        regen_stale = bool(body.get("regen_stale"))
        force = bool(body.get("force"))
        assume_yes = bool(body.get("assume_yes"))
        project, actor = self.server.project, self.server.actor

        if body.get("dry_run"):
            from ..build.graph import run_build

            result = run_build(project, target=target, gen=gen, regen_stale=regen_stale,
                               dry_run=True, force=force, actor=actor)
            self._send_json({"dry_run": True, "result": result.to_dict()})
            return

        params = {"target": target, "gen": gen, "regen_stale": regen_stale,
                  "force": force, "assume_yes": assume_yes}

        def fn(job) -> dict[str, Any]:
            import inspect

            from ..build.graph import run_build

            # The ask_before spend gate (assume_yes) and coarse phase progress
            # (on_phase) are R4/R8 engine extensions the spend sibling
            # (round-r/spend) carries; pass them only when run_build accepts
            # them, so this branch is green against the current core and lights
            # up the moment the gate lands. Until then, the page's dry-run
            # estimate + confirm is the "always-confirm" fallback.
            params_sig = inspect.signature(run_build).parameters
            kwargs: dict[str, Any] = dict(
                target=target, gen=gen, regen_stale=regen_stale,
                dry_run=False, force=force, actor=actor)
            if "assume_yes" in params_sig:
                kwargs["assume_yes"] = assume_yes
            if "on_phase" in params_sig:
                kwargs["on_phase"] = lambda ph: setattr(job, "progress", ph)
            return run_build(project, **kwargs).to_dict()

        job = self.server.runner.submit("build", params, fn)
        self._send_json({"job": job.to_dict()}, 202)

    def _act_redo(self, body: dict[str, Any]) -> None:
        project, actor = self.server.project, self.server.actor
        shot_id = str(body.get("shot") or "")
        if not shot_id:
            self._send_error_json("shot is required", 400)
            return
        if not project.shot_path(shot_id).exists():
            self._send_error_json(f"unknown shot: {shot_id}", 404)
            return
        provider = body.get("provider") or None
        candidates = body.get("candidates")
        seed = body.get("seed")
        assume_yes = bool(body.get("assume_yes"))
        params = {"shot": shot_id, "provider": provider,
                  "candidates": candidates, "seed": seed, "assume_yes": assume_yes}

        def fn(job) -> dict[str, Any]:
            import inspect

            from ..build.graph import redo_shot

            kwargs: dict[str, Any] = dict(
                candidates=int(candidates) if candidates else None,
                provider=str(provider) if provider else None,
                seed=int(seed) if seed is not None else None,
                actor=actor)
            # assume_yes rides the same spend gate as build (round-r/spend)
            if "assume_yes" in inspect.signature(redo_shot).parameters:
                kwargs["assume_yes"] = assume_yes
            takes = redo_shot(project, shot_id, **kwargs)
            return {"shot": shot_id, "takes": takes}

        job = self.server.runner.submit("redo", params, fn)
        self._send_json({"job": job.to_dict()}, 202)

    def _act_voice(self, body: dict[str, Any]) -> None:
        """单镜头配音 (goal 61): routes through the SAME §8.3 ask_before gate
        the CLI ``manju voice`` uses — a priced synthesis without an explicit
        ``assume_yes`` fails the job as ``waiting_user`` and spends nothing,
        exactly like ``/api/lab/generate`` (round UE) and ``/api/redo``
        already do. Previously this endpoint called the TTS provider directly
        with no gate at all — the GUI's plan modal showed the estimate (via
        ``/api/plan`` action=voice) but a confirm here never carried that
        forward into a real spend check."""
        project, actor = self.server.project, self.server.actor
        shot_id = str(body.get("shot") or "")
        provider = body.get("provider") or None
        assume_yes = bool(body.get("assume_yes"))
        if not shot_id:
            self._send_error_json("shot is required", 400)
            return
        try:
            shot = project.load_shot(shot_id)
        except ProjectError as exc:
            self._send_error_json(str(exc), 404)
            return
        if not shot.dialogue.text:
            self._send_error_json(f"{shot_id} has no dialogue.text to voice", 400)
            return

        def fn(job) -> dict[str, Any]:
            from ..build.graph import spend_gate
            from ..providers.tts import get_tts_provider

            tts = get_tts_provider(str(provider) if provider else None)
            manifest = getattr(tts, "manifest", None)
            cost = getattr(manifest, "cost", None) if manifest is not None else None
            if cost is not None:
                spend_gate(project, cost.per_call, cost.currency, assume_yes=assume_yes,
                          hint=f"确认后重试:GUI 配音带 assume_yes,"
                               f"或 CLI `manju voice {shot_id} --yes`(§8.3)")
            with _optional_build_lock(project.root, actor):
                media = tts.synthesize(project, project.load_shot(shot_id),
                                       project.load_bible())
            append_event(project.root, actor, "voice",
                         {"shot": shot_id, "take": media.stem, "provider": tts.id,
                          "via": "gui"})
            return {"shot": shot_id, "take": media.stem, "media": project.relpath(media)}

        job = self.server.runner.submit(
            "voice", {"shot": shot_id, "provider": provider, "assume_yes": assume_yes}, fn)
        self._send_json({"job": job.to_dict()}, 202)

    # ----------------------------------------------------- plan (§8.3 / §4.4)

    def _act_plan(self, body: dict[str, Any]) -> None:
        """Read-only pre-generation plan (goal item 5): the same estimators the
        CLI dry-run uses, extended to redo/voice/batch, so the page can render
        the plan modal and take an explicit confirm before ANY priced run. Never
        mutates; a bad action / unknown shot is a one-line 400, not a spend."""
        from .plan import action_plan

        action = str(body.get("action") or "")
        try:
            self._send_json(action_plan(self.server.project, action, body))
        except (ValueError, ProjectError) as exc:
            self._send_error_json(" ".join(str(exc).split()), 400)

    # --------------------------------------------------------- batch (S-E GUI)

    def _act_redo_batch(self, body: dict[str, Any]) -> None:
        """Multi-select redo through the wave-1 ``redo_batch`` — ONE build lock,
        ONE spend gate, per-shot skip/fail reasons in the BatchResult (never a
        silent exclusion, §4.3/§6)."""
        project, actor = self.server.project, self.server.actor
        shots = body.get("shots")
        if not isinstance(shots, list) or not shots or not all(isinstance(s, str) for s in shots):
            self._send_error_json("shots must be a non-empty list of shot ids", 400)
            return
        provider = body.get("provider") or None
        seed = body.get("seed")
        candidates = body.get("candidates")
        assume_yes = bool(body.get("assume_yes"))
        params = {"shots": list(shots), "provider": provider, "seed": seed,
                  "candidates": candidates, "assume_yes": assume_yes}

        def fn(job) -> dict[str, Any]:
            from ..build.graph import redo_batch

            return redo_batch(
                project, shots=list(shots),
                candidates=int(candidates) if candidates else None,
                provider=str(provider) if provider else None,
                seed=int(seed) if seed is not None else None,
                actor=actor, assume_yes=assume_yes).to_dict()

        job = self.server.runner.submit("redo_batch", params, fn)
        self._send_json({"job": job.to_dict()}, 202)

    def _act_voice_batch(self, body: dict[str, Any]) -> None:
        project, actor = self.server.project, self.server.actor
        shots = body.get("shots")
        if not isinstance(shots, list) or not shots or not all(isinstance(s, str) for s in shots):
            self._send_error_json("shots must be a non-empty list of shot ids", 400)
            return
        provider = body.get("provider") or None
        assume_yes = bool(body.get("assume_yes"))
        params = {"shots": list(shots), "provider": provider, "assume_yes": assume_yes}

        def fn(job) -> dict[str, Any]:
            from ..build.graph import voice_batch

            return voice_batch(
                project, shots=list(shots),
                provider=str(provider) if provider else None,
                actor=actor, assume_yes=assume_yes).to_dict()

        job = self.server.runner.submit("voice_batch", params, fn)
        self._send_json({"job": job.to_dict()}, 202)

    # ---------------------------------------------------- onboarding (item 2)

    def _onboarding_get(self) -> None:
        from .onboarding import build_onboarding
        from .userstate import is_onboarding_dismissed

        data = build_onboarding(self.server.project)
        dismissed = is_onboarding_dismissed(self.server.project.root)
        data["dismissed"] = dismissed
        # auto-show for a new/empty-ish project the user hasn't dismissed
        data["should_show"] = bool(data.get("empty_ish")) and not dismissed
        self._send_json(data)

    def _act_onboarding_dismiss(self, body: dict[str, Any]) -> None:
        from .userstate import set_onboarding_dismissed

        dismissed = bool(body.get("dismissed", True))
        set_onboarding_dismissed(self.server.project.root, dismissed)
        self._send_json({"ok": True, "dismissed": dismissed})

    # -------------------------------------------------------- tasks (item 4b)

    def _tasks_get(self) -> None:
        """`manju tasks` JSON — the JOB/QUEUE view over the disposable run
        ledger. Degrades to an empty-but-shaped payload when the ledger is
        missing (§3: the ledger is rebuildable, never load-bearing)."""
        project = self.server.project
        try:
            from ..runtime.state import RuntimeState

            with RuntimeState(project.root) as state:
                runs = state.run_log(50)
                pending = state.pending_jobs()
                total, currency = state.total_cost()
                by_provider = state.cost_by_provider()
        except Exception as exc:
            self._send_json({"tasks": [], "pending": [],
                             "spend": {"by_provider": [], "total": 0.0, "currency": None},
                             "available": False,
                             "note": f"run ledger unavailable ({exc})"})
            return
        tasks = [{
            "id": r.get("id"), "shot": r.get("shot"), "provider": r.get("provider"),
            "status": ("moderation-rejected"
                       if (r.get("status") == "failed"
                           and r.get("failure_kind") == "content_rejected")
                       else str(r.get("status") or "")),
            "cost": float(r.get("cost") or 0.0), "currency": r.get("currency"),
            "created": r.get("ts"), "take": r.get("take"),
            "reason": r.get("error"), "failure_id": r.get("failure_id"),
        } for r in runs]
        pending_out = [{
            "shot": j.get("shot"), "provider": j.get("provider"), "status": "polling",
            "remote_job_id": j.get("remote_job_id"),
            "created": j.get("submitted_at"), "updated": j.get("updated_at"),
        } for j in pending]
        self._send_json({
            "tasks": tasks, "pending": pending_out, "available": True,
            "spend": {"by_provider": by_provider, "total": float(total), "currency": currency},
        })

    # ------------------------------------------------------- presets (item 4)

    def _presets_get(self) -> None:
        try:
            from ..presets import list_presets

            items = [s.to_public_dict() for s in list_presets()]
        except Exception as exc:
            self._send_error_json(" ".join(str(exc).split()), 500)
            return
        self._send_json({"presets": items})

    # ------------------------------------------------------------ workspace

    def _projects_list(self) -> None:
        items = []
        for slug, project in self.server.workspace.items():
            name = project.root.stem
            shots = 0
            try:
                name = project.load_config().name
                shots = len(project.shot_ids())
            except Exception:
                pass
            items.append({"slug": slug, "name": name,
                          "root": str(project.root), "shots": shots,
                          "active": slug == self.server.active_slug})
        self._send_json({"projects": items, "workspace": bool(self.server.workspace)})

    def _act_switch(self, body: dict[str, Any]) -> None:
        """Swap the server's ACTIVE project (workspace mode). One active
        project per server — in-flight jobs keep the project they closed
        over; the page reloads its state after switching."""
        slug = str(body.get("slug") or "")
        if not self.server.workspace:
            self._send_error_json("not in workspace mode (start with --workspace)", 400)
            return
        try:
            project = self.server.switch_project(slug)
        except KeyError:
            self._send_error_json(f"unknown project: {slug}", 404)
            return
        self._send_json({"ok": True, "slug": slug, "root": str(project.root)})

    _NAME_RE = re.compile(r"^[^/\\\x00]{1,80}$")

    def _act_new_project(self, body: dict[str, Any]) -> None:
        """New-project dialog in the workspace switcher (S8a): create a sibling
        <name>.manju via the SAME core the CLI's ``manju new`` calls, optionally
        pre-filled from a preset (`manju presets`), then register it in the
        workspace and switch to it. Workspace-mode only — mirrors switch/§5:
        there is no switcher to land a new project in otherwise."""
        if not self.server.workspace:
            self._send_error_json("not in workspace mode (start with --workspace)", 400)
            return
        name = str(body.get("name") or "").strip()
        if not name or name.startswith(".") or not self._NAME_RE.fullmatch(name):
            self._send_error_json("invalid project name", 400)
            return
        preset = body.get("preset") or None
        vertical = bool(body.get("vertical", True))
        parent = self.server.project.root.parent  # new projects land beside siblings
        dest = parent / name
        try:
            spec = None
            if preset:
                from ..presets import load_preset

                spec = load_preset(str(preset))
            project = Project.create(dest, name=name, vertical=vertical, git_init=False)
            if spec is not None:
                from ..presets import apply_preset

                apply_preset(project, spec)
            append_event(project.root, self.server.actor, "new",
                         {"name": name, "preset": (spec.name if spec else None),
                          "via": "gui"})
        except Exception as exc:
            self._send_error_json(" ".join(str(exc).split()), 400)
            return
        base = project.root.stem or "project"
        with self.server.quick_mutex:
            slug = base
            n = 2
            while slug in self.server.workspace:
                slug = f"{base}_{n}"
                n += 1
            self.server.workspace[slug] = project
        self.server.switch_project(slug)
        self._send_json({"ok": True, "slug": slug, "name": name,
                         "root": str(project.root),
                         "preset": (spec.name if spec else None)}, 201)

    # ---------------------------------------------------------- watch/lists

    # at most this many watchers may hold threads; extras answer immediately
    _WATCH_SLOTS = threading.BoundedSemaphore(8)

    def _watch(self, query: dict[str, list[str]]) -> None:
        """Long-poll change detection: hold the request until the project
        fingerprint differs from the client's, or the timeout lapses — the
        page's co-presence channel (its poll loop calls this when idle).
        A small semaphore caps held threads; a saturated watcher degrades to
        an immediate answer, which the client treats as a normal re-arm."""
        import time as _time

        from .state import project_fingerprint

        client_fp = (query.get("fp", [""])[0] or "")
        try:
            timeout = min(30.0, max(1.0, float(query.get("timeout", ["25"])[0])))
        except ValueError:
            timeout = 25.0
        if not self._WATCH_SLOTS.acquire(blocking=False):
            fp = project_fingerprint(self.server.project, self.server.runner)
            self._send_json({"fp": fp, "changed": fp != client_fp})
            return
        try:
            deadline = _time.monotonic() + timeout
            while True:
                fp = project_fingerprint(self.server.project, self.server.runner)
                if fp != client_fp or _time.monotonic() >= deadline:
                    self._send_json({"fp": fp, "changed": fp != client_fp})
                    return
                _time.sleep(0.5)
        finally:
            self._WATCH_SLOTS.release()

    def _proposals(self) -> None:
        """proposals/ is the AI→human channel (§5) — list it so the human
        actually SEES requests instead of discovering them by ls."""
        items = []
        pdir = self.server.project.proposals_dir
        if pdir.exists():
            for p in sorted(pdir.glob("*.md"), reverse=True):
                try:
                    text = p.read_text(encoding="utf-8")
                except OSError:
                    continue
                truncated = len(text) > 10000
                items.append({
                    "name": p.name,
                    "path": self.server.project.relpath(p),
                    "mtime": p.stat().st_mtime,
                    "text": text[:10000],
                    "truncated": truncated,
                })
        self._send_json({"proposals": items})

    # ------------------------------------------------- check-gated writing

    def _gated_save(self, path: Path, text: str, *, label: str) -> tuple[bool, dict[str, Any], int]:
        """Shared editor core: write the text VERBATIM (atomic), run the full
        `manju check`, revert when the save introduced any new error. Returns
        (ok, payload, http_status); the caller appends its own event.
        Callers must hold quick_mutex."""
        from ..core.check import run_check
        from ..core.yamlio import atomic_write_text

        before = path.read_text(encoding="utf-8") if path.exists() else None
        baseline = set(run_check(self.server.project).errors)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, text if text.endswith("\n") else text + "\n")
        report = run_check(self.server.project)
        new_errors = [e for e in report.errors if e not in baseline]
        if new_errors:
            if before is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_text(path, before)
            # conflict-banner contract (COMPETITIVE-UX-STUDY, Figma pattern):
            # the client gets the reverted-to truth alongside the errors, so
            # it can show buffer-vs-truth and re-apply without a second fetch
            return False, {"error": f"check failed — {label} 已回滚 (reverted)",
                           "errors": new_errors,
                           "current": before if before is not None else ""}, 409
        return True, {"ok": True, "created": before is None,
                      "warnings": report.warnings}, 200

    @staticmethod
    def _parse_yaml_mapping(text: Any) -> tuple[dict[str, Any] | None, str | None]:
        import yaml as _yaml

        if not isinstance(text, str) or not text.strip():
            return None, "yaml text is required"
        try:
            parsed = _yaml.safe_load(text)
        except _yaml.YAMLError as exc:
            return None, f"YAML 解析失败: {' '.join(str(exc).split())}"
        if not isinstance(parsed, dict):
            return None, "file must be a YAML mapping"
        return parsed, None

    # --------------------------------------------------------- bible/rules

    _BIBLE_FILES = ("characters", "scenes", "props", "style")

    def _bible_get(self, name: str) -> None:
        if name not in self._BIBLE_FILES:
            self._send_error_json(f"unknown bible file: {name}", 404)
            return
        path = self.server.project.root / "bible" / f"{name}.yaml"
        self._send_json({
            "name": name,
            "exists": path.exists(),
            "yaml": path.read_text(encoding="utf-8") if path.exists() else "",
        })

    def _act_bible_save(self, name: str, body: dict[str, Any]) -> None:
        if name not in self._BIBLE_FILES:
            self._send_error_json(f"unknown bible file: {name}", 404)
            return
        _, err = self._parse_yaml_mapping(body.get("yaml"))
        if err:
            self._send_error_json(err, 400)
            return
        project = self.server.project
        with self.server.quick_mutex:
            ok, payload, status = self._gated_save(
                project.root / "bible" / f"{name}.yaml", body["yaml"],
                label=f"bible/{name}.yaml")
            if ok:
                append_event(project.root, self.server.actor, "edit_bible",
                             {"file": name, "via": "gui"})
        self._send_json(payload, status)

    def _act_rules_save(self, body: dict[str, Any]) -> None:
        _, err = self._parse_yaml_mapping(body.get("yaml"))
        if err:
            self._send_error_json(err, 400)
            return
        project = self.server.project
        with self.server.quick_mutex:
            ok, payload, status = self._gated_save(
                project.rules_path, body["yaml"], label="timeline/rules.yaml")
            if ok:
                append_event(project.root, self.server.actor, "edit_rules",
                             {"via": "gui"})
        self._send_json(payload, status)

    def _act_packaging_save(self, body: dict[str, Any]) -> None:
        """Raw-YAML packaging editor (S8a): parse, model-validate against the
        real PackagingSpec (one-line error, never save invalid), then the same
        check-gated verbatim write the other truth editors use."""
        parsed, err = self._parse_yaml_mapping(body.get("yaml"))
        if err:
            self._send_error_json(err, 400)
            return
        try:
            from ..core.models import PackagingSpec

            PackagingSpec.model_validate(parsed)
        except Exception as exc:  # pydantic ValidationError -> one-line finding
            self._send_error_json(
                "packaging 校验失败 (invalid): " + " ".join(str(exc).split())[:500], 400)
            return
        project = self.server.project
        with self.server.quick_mutex:
            ok, payload, status = self._gated_save(
                project.packaging_path, body["yaml"], label="timeline/packaging.yaml")
            if ok:
                append_event(project.root, self.server.actor, "edit_packaging",
                             {"via": "gui"})
        self._send_json(payload, status)

    # -------------------------------------------------------- index reorder

    def _act_index(self, body: dict[str, Any]) -> None:
        """Reorder shots — the cut order is one reviewable line of YAML (§3).
        The incoming order must be a permutation of the CURRENT visible order
        (index + on-disk extras); previously-unindexed shots become indexed."""
        project = self.server.project
        order = body.get("order")
        if not isinstance(order, list) or not all(isinstance(s, str) for s in order):
            self._send_error_json("order must be a list of shot ids", 400)
            return
        with self.server.quick_mutex:
            current = project.shot_ids()
            if sorted(order) != sorted(current):
                self._send_error_json(
                    "order must be a permutation of the current shots "
                    f"(expected {len(current)} ids: {', '.join(current)})", 400)
                return
            index = project.load_index()
            index.order = list(order)
            project.save_index(index)
            append_event(project.root, self.server.actor, "reorder",
                         {"order": order, "via": "gui"})
        self._send_json({"ok": True, "order": order})

    # ---------------------------------------------------------- shot editor

    # goal item 11: the ONE canonical safe-segment pattern (core/idents.py) —
    # kept as a compiled attribute here (not just calling is_safe_segment) so
    # the many `self._SHOT_ID_RE.fullmatch(...)` call sites below stay
    # unchanged; the source of truth for the pattern itself now lives in core.
    _SHOT_ID_RE = re.compile(SAFE_SEGMENT_PATTERN)

    def _shot_get(self, shot_id: str) -> None:
        """Raw YAML text of one shot file — the editor edits HUMAN TRUTH, so
        the exact bytes travel, never a model round-trip (§3: a lock sealed
        over `duration: 3` must not break because a model re-emits 3.0)."""
        if not self._SHOT_ID_RE.fullmatch(shot_id):
            self._send_error_json("invalid shot id", 400)
            return
        project = self.server.project
        path = project.shot_path(shot_id)
        if not path.exists():
            self._send_json({"id": shot_id, "exists": False, "yaml": "", "locked": []})
            return
        text = path.read_text(encoding="utf-8")
        locked: list[str] = []
        try:
            raw = project.load_shot_raw(shot_id)
            if isinstance(raw.get("locked"), dict):
                locked = sorted(raw["locked"])
        except Exception:
            pass
        self._send_json({"id": shot_id, "exists": True, "yaml": text, "locked": locked,
                         "in_index": shot_id in project.load_index().order})

    def _act_shot_save(self, shot_id: str, body: dict[str, Any]) -> None:
        """Check-gated save: write the text exactly as typed, run the full
        `manju check`, and REVERT if the save introduced any new error (lock
        violations included — §5 means the GUI cannot bypass a lock any more
        than the MCP surface can; unlock stays in the terminal)."""
        if not self._SHOT_ID_RE.fullmatch(shot_id):
            self._send_error_json("invalid shot id", 400)
            return
        _, err = self._parse_yaml_mapping(body.get("yaml"))
        if err:
            self._send_error_json(err if "mapping" not in err else
                                  "shot file must be a YAML mapping", 400)
            return
        project = self.server.project
        with self.server.quick_mutex:
            ok, payload, status = self._gated_save(
                project.shot_path(shot_id), body["yaml"],
                label=f"shots/{shot_id}.yaml")
            if ok:
                payload["shot"] = shot_id
                if payload["created"]:
                    # creating via the GUI means the user wants it in the cut:
                    # index it at the end (the engine would only APPEND it
                    # implicitly and warn on every check until someone did)
                    index = project.load_index()
                    if shot_id not in index.order:
                        index.order.append(shot_id)
                        project.save_index(index)
                        payload["warnings"] = [
                            w for w in payload["warnings"]
                            if not (shot_id in w and "index.yaml" in w)
                        ]
                append_event(project.root, self.server.actor, "edit_shot",
                             {"shot": shot_id, "created": payload["created"],
                              "via": "gui"})
        self._send_json(payload, status)

    # -------------------------------------------------------------- uploads

    _UPLOAD_MAX = 4 << 30  # 4 GiB

    def _act_upload(self, query: dict[str, list[str]]) -> None:
        """Browser upload into media/imports — the GUI twin of `manju import`.
        The MCP surface excludes import because an agent could pull arbitrary
        FILESYSTEM paths into the project; a browser upload has no such power —
        the human pushes bytes they already hold, and imports stay append-only
        (collision-safe names, never overwritten)."""
        import shutil
        import uuid as _uuid

        project = self.server.project
        raw_name = (query.get("name", [""])[0] or "").strip()
        # basename across both separators; no dotfiles; keep CJK
        raw_name = raw_name.replace("\\", "/").rsplit("/", 1)[-1]
        if not raw_name or raw_name.startswith("."):
            self._send_error_json("upload needs ?name=<filename>", 400)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._send_error_json("Content-Length required", 411)
            return
        if length > self._UPLOAD_MAX:
            self._send_error_json("file too large (max 4 GiB)", 413)
            return

        tmp_dir = project.runtime_dir / "upload-tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / f"up-{_uuid.uuid4().hex}"
        received = 0
        try:
            with open(tmp, "wb") as f:
                while received < length:
                    chunk = self.rfile.read(min(1 << 20, length - received))
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
            if received != length:
                self._send_error_json("upload truncated", 400)
                return
            with self.server.quick_mutex:
                dest = project.imports_dir / raw_name
                stem, suffix = dest.stem, dest.suffix
                n = 2
                while dest.exists():  # imports are never overwritten (§3)
                    dest = project.imports_dir / f"{stem}_{n}{suffix}"
                    n += 1
                shutil.move(str(tmp), dest)
            rel = project.relpath(dest)
            preview_rel = None
            try:
                from ..media.preview import make_preview

                preview = make_preview(dest, project.runtime_dir / "thumbs")
                if preview is not None:
                    preview_rel = project.relpath(preview)
            except Exception:
                pass
            append_event(project.root, self.server.actor, "import",
                         {"files": [rel], "via": "gui"})
            self._send_json({"ok": True, "imported": rel, "preview": preview_rel})
        finally:
            tmp.unlink(missing_ok=True)

    # ------------------------------------------------------------------ git

    def _act_git_commit(self, body: dict[str, Any]) -> None:
        from ..core.gitops import commit_all

        message = str(body.get("message") or "").strip()
        if not message:
            self._send_error_json("commit message is required", 400)
            return
        root = self.server.project.root
        from ..core.gitops import is_repo

        # the event is appended BEFORE the commit so it rides INSIDE it —
        # appending after would instantly re-dirty the tree the user just
        # cleaned (the hash is unknowable pre-commit; git history carries it)
        if is_repo(root):
            append_event(root, self.server.actor, "git_commit",
                         {"message": message, "via": "gui"})
        result = commit_all(root, message, actor=self.server.actor)
        if not result.get("ok"):
            # the audit log must not claim a commit that never happened —
            # record the failure right after the optimistic entry
            if is_repo(root):
                append_event(root, self.server.actor, "git_commit_failed",
                             {"message": message, "error": result.get("error"),
                              "via": "gui"})
            self._send_error_json(result.get("error") or "commit failed", 409)
            return
        self._send_json(result)

    def _act_git_snapshot(self, body: dict[str, Any]) -> None:
        """Labeled git checkpoint over the truth text (core/history.snapshot).
        ``clean=True`` means the tree was already checkpointed — a no-op, not an
        error. Not a repo → 409 with the same clean message the CLI shows."""
        from ..core.history import HistoryError, snapshot

        label = str(body.get("label") or "").strip()
        try:
            result = snapshot(self.server.project, label)
        except HistoryError as exc:
            self._send_error_json(str(exc), 409)
            return
        self._send_json({"ok": True, **result})

    def _act_git_rollback_file(self, body: dict[str, Any]) -> None:
        """Restore ONE truth-text file from git (core/history.rollback_file):
        story/shots/bible/timeline/captions/project.yaml only — media, renders
        and exports are refused. Runs ``check`` afterwards and reports findings
        (the CLI does the same); the rollback itself is already an event."""
        from ..core.history import HistoryError, rollback_file

        rel = str(body.get("path") or "").strip()
        ref = str(body.get("ref") or "HEAD").strip() or "HEAD"
        if not rel:
            self._send_error_json("path is required", 400)
            return
        try:
            result = rollback_file(self.server.project, rel, ref)
        except HistoryError as exc:
            self._send_error_json(str(exc), 400)
            return
        try:
            from ..core.check import run_check

            report = run_check(self.server.project)
            result["errors"] = list(report.errors)
            result["warnings"] = list(report.warnings)
        except Exception:
            pass  # findings are advisory; the restore already happened + logged
        self._send_json({"ok": True, **result})

    def _act_qc(self, body: dict[str, Any]) -> None:
        project = self.server.project
        deep = bool(body.get("deep"))

        def fn(job) -> dict[str, Any]:
            from ..qc.checks import run_qc
            from ..qc.report import write_reports

            with _optional_build_lock(project.root, self.server.actor):
                report = run_qc(project, project.load_timeline(), deep=deep)
                paths = write_reports(project, report)
            return {
                "ok": report.ok,
                "errors": sum(1 for i in report.items if i.level == "error"),
                "warnings": sum(1 for i in report.items if i.level == "warn"),
                "reports": {k: project.relpath(v) for k, v in paths.items()},
            }

        job = self.server.runner.submit("qc", {"deep": deep}, fn)
        self._send_json({"job": job.to_dict()}, 202)

    # =================================================================
    # round-S GUI PAGES (S8b): six server-rendered routes + their actions.
    # New region, isolated from the SPA endpoints above — the workbench's
    # review/compare/library/providers/routing/doctor pages, each a strict
    # client of the same engine core the CLI calls (docs/WORKBENCH.md).
    # =================================================================

    _PAGES_CSP = {
        "Content-Security-Policy": (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; media-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        ),
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }

    def _pages_get(self, path: str, url: Any) -> bool:
        """GET dispatch for the round-S pages + their static/read assets.
        Returns True when handled (response already sent), False otherwise so
        do_GET falls through to its 404."""
        from . import pages

        if path == "/pages.css":
            self._send_text(pages.render_pages_css(), "text/css; charset=utf-8")
            return True
        if path == "/pages.js":
            self._send_text(pages.render_pages_js(),
                            "application/javascript; charset=utf-8")
            return True
        if path in pages.PAGE_PATHS:
            html_doc = pages.render(path, self.server.project, self.server.token,
                                    parse_qs(url.query))
            self._send_text(html_doc, "text/html; charset=utf-8",
                            extra=self._PAGES_CSP)
            return True
        if path == "/api/route-explain":
            self._route_explain(parse_qs(url.query))
            return True
        if path.startswith("/lib-thumb/"):
            self._lib_asset(unquote(path[len("/lib-thumb/"):]), thumb=True)
            return True
        if path.startswith("/lib-blob/"):
            self._lib_asset(unquote(path[len("/lib-blob/"):]), thumb=False)
            return True
        return False

    def _pages_post(self, path: str, body: dict[str, Any]) -> bool:
        """POST dispatch for the round-S page actions. Same token/readonly
        gates as every other mutating POST (checked in do_POST before us)."""
        handler = {
            "/api/repair": self._act_repair,
            "/api/providers/toggle": self._act_providers_toggle,
            "/api/routing/strategy": self._act_routing_strategy,
            "/api/lib/use": self._act_lib_use,
            "/api/lib/tag": self._act_lib_tag,
            "/api/lib/note": self._act_lib_note,
            "/api/qc/verdict": self._act_qc_verdict,
        }.get(path)
        if handler is None:
            return False
        handler(body)
        return True

    def _act_qc_verdict(self, body: dict[str, Any]) -> None:
        """Round X (agent XB, user pain #2): a HUMAN spot-checking the /review
        page's 跨镜一致性 consistency units files a verdict through the SAME
        intake `manju qc verdict` / the `qc_verdict` MCP tool use — token-gated
        like every mutating POST, actor="human" (record_verdicts already takes
        an `actor` param; agents are not the only ones who can file verdicts).
        Accepts either `unit` (a consistency comparison-unit id) or `shot`
        (round-V per-shot) — same payload shape as the CLI/MCP verdict body."""
        from ..qc.agent_review import VerdictError, record_verdicts

        project = self.server.project
        verdict: dict[str, Any] = {
            "criterion": body.get("criterion"),
            "level": body.get("level"),
            "message": body.get("message"),
            "evidence": body.get("evidence", ""),
        }
        unit = str(body.get("unit") or "").strip()
        if unit:
            verdict["unit"] = unit
        else:
            verdict["shot"] = body.get("shot")
            if body.get("take") is not None:
                verdict["take"] = body.get("take")
        if body.get("frame_ms") is not None:
            verdict["frame_ms"] = body.get("frame_ms")

        with self.server.quick_mutex:
            try:
                result = record_verdicts(project, {"verdicts": [verdict]},
                                         actor=self.server.actor)
            except VerdictError as exc:
                self._send_error_json(str(exc), 400)
                return
        self._send_json({"ok": True, **result})

    # -------------------------------------------------- routing (read)

    def _route_explain(self, query: dict[str, list[str]]) -> None:
        """Per-shot route explain (routing view popover + review context):
        which rule fired, which were skipped and why."""
        from ..providers.routing import RoutingError, explain

        shot_id = query.get("shot", [""])[0] or ""
        if not shot_id:
            self._send_error_json("shot is required", 400)
            return
        try:
            shot = self.server.project.load_shot(shot_id)
        except ProjectError as exc:
            self._send_error_json(str(exc), 404)
            return
        try:
            self._send_json(explain(self.server.project, shot))
        except RoutingError as exc:
            self._send_error_json(" ".join(str(exc).split()), 400)

    def _act_routing_strategy(self, body: dict[str, Any]) -> None:
        """Strategy picker — writes ONLY the top-level ``strategy:`` key into
        timeline/routing.yaml, then validates on save and reverts on failure
        (never leaves the routing file naming an unknown strategy)."""
        from ..core.yamlio import atomic_write_text
        from ..providers.routing import (
            RoutingError,
            list_strategies,
            project_routing_path,
        )

        from .pages import STRATEGY_NAME_RE, set_strategy_in_text

        strat = str(body.get("strategy") or "")
        if not STRATEGY_NAME_RE.fullmatch(strat):
            self._send_error_json("invalid strategy name", 400)
            return
        project = self.server.project
        path = project_routing_path(project)
        with self.server.quick_mutex:
            before = path.read_text(encoding="utf-8") if path.exists() else None
            new_text = set_strategy_in_text(before or "", strat)
            if not new_text.endswith("\n"):
                new_text += "\n"
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, new_text)
            try:  # validate-on-save: still parses AND names a known strategy
                info = list_strategies(project)
                names = {s["name"] for s in info["strategies"]}
                if strat not in names:
                    raise RoutingError(
                        f"unknown strategy {strat!r}; available: {sorted(names)}")
            except RoutingError as exc:
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write_text(path, before)
                self._send_error_json(
                    "strategy 已回滚 (reverted): " + " ".join(str(exc).split()), 400)
                return
            append_event(project.root, self.server.actor, "route_strategy",
                         {"strategy": strat, "via": "gui"})
        self._send_json({"ok": True, "strategy": strat})

    # -------------------------------------------------- repair (review)

    def _act_repair(self, body: dict[str, Any]) -> None:
        """A per-finding repair op from the review page: builds a NEW take from
        a source take (append-only — the source is never touched), through the
        same repair_ops the CLI's `manju repair --op` calls. Serialized on the
        job runner + the build lock like every other media-writing job."""
        project, actor = self.server.project, self.server.actor
        shot_id = str(body.get("shot") or "")
        op = str(body.get("op") or "")
        take = body.get("take") or None
        if not shot_id or not project.shot_path(shot_id).exists():
            self._send_error_json(f"unknown shot: {shot_id}", 404)
            return
        if op not in ("retime", "extend", "trim", "croppad"):
            self._send_error_json(
                f"unknown repair op: {op!r} (retime|extend|trim|croppad)", 400)
            return
        if take is None:
            try:
                take = project.load_shot(shot_id).status.selected_take
            except ProjectError as exc:
                self._send_error_json(str(exc), 404)
                return
        if not take:
            self._send_error_json(
                f"{shot_id} has no selected take — pass a take or select one first", 400)
            return
        take = str(take)
        factor, ms, mode = body.get("factor"), body.get("ms"), body.get("mode")

        def fn(job) -> dict[str, Any]:
            from ..media.repair_ops import (
                crop_pad_take,
                extend_take,
                retime_take,
                trim_take,
            )

            with _optional_build_lock(project.root, actor):
                if op == "retime":
                    new = retime_take(project, shot_id, take,
                                      float(factor if factor is not None else 1.0))
                elif op == "extend":
                    new = extend_take(project, shot_id, take, int(ms or 0),
                                      mode=str(mode or "freeze"))
                elif op == "trim":
                    new = trim_take(project, shot_id, take, int(ms or 0))
                else:
                    new = crop_pad_take(project, shot_id, take,
                                        mode=str(mode or "center_crop"))
            append_event(project.root, actor, "repair",
                         {"shot": shot_id, "op": op, "source_take": take,
                          "new_take": new.name, "via": "gui"})
            return {"shot": shot_id, "op": op, "source_take": take,
                    "new_take": new.name, "params": new.sidecar.params}

        job = self.server.runner.submit("repair", {"shot": shot_id, "op": op}, fn)
        self._send_json({"job": job.to_dict()}, 202)

    # -------------------------------------------------- providers

    def _act_providers_toggle(self, body: dict[str, Any]) -> None:
        """Enable/disable a provider by flipping the manifest's ``disabled:``
        key (comment-preserving, the same policy switch the CLI makes). Secret
        values are never touched or read."""
        from ..core.yamlio import atomic_write_text
        from ..providers.manifest import providers_dir

        from .pages import set_disabled_in_text

        pid = str(body.get("id") or "")
        if not pid or "/" in pid or "\\" in pid or pid.startswith("."):
            self._send_error_json("invalid provider id", 400)
            return
        disabled = bool(body.get("disabled"))
        path = providers_dir() / pid / "provider.yaml"
        if not path.exists():
            self._send_error_json(f"no such provider: {pid}", 404)
            return
        with self.server.quick_mutex:
            text = path.read_text(encoding="utf-8")
            atomic_write_text(path, set_disabled_in_text(text, disabled))
            append_event(self.server.project.root, self.server.actor,
                         "provider_toggle",
                         {"id": pid, "enabled": not disabled, "via": "gui"})
        self._send_json({"ok": True, "id": pid, "enabled": not disabled})

    # -------------------------------------------------- library

    def _lib_asset(self, hash8: str, *, thumb: bool) -> None:
        """Serve a library thumbnail or blob by its content-hash handle. The
        library lives outside every project, so /media never reaches it; this
        route resolves through the index (hash8 → entry) and contains the
        result inside the library root (no path is ever taken from the client)."""
        from ..core.library import Library, LibraryError

        lib = Library()
        try:
            entry = lib.get(hash8)
        except LibraryError:
            self._send_error_json("not found", 404)
            return
        if thumb:
            rel = entry.get("thumb")
            target = (lib.root / rel) if rel else None
        else:
            target = lib.blob_path(entry)
        if target is None or not target.is_file():
            self._send_error_json("no such asset", 404)
            return
        try:
            resolved = target.resolve()
            if not resolved.is_relative_to(lib.root.resolve()):
                self._send_error_json("path not served", 403)
                return
        except OSError:
            self._send_error_json("not found", 404)
            return
        self._serve_file(resolved)

    def _act_lib_use(self, body: dict[str, Any]) -> None:
        """Copy a library asset INTO the current project (refs/ or imports/),
        honouring the project's no-overwrite suffixing — the same core path as
        `manju lib use`. The library stays independent; the copy is a normal
        human asset from then on."""
        import shutil
        from pathlib import Path

        from ..core.library import Library, LibraryError

        hash8 = str(body.get("hash") or body.get("hash8") or "")
        as_ = str(body.get("as") or "refs")
        if as_ not in ("refs", "imports"):
            self._send_error_json("as must be 'refs' or 'imports'", 400)
            return
        lib = Library()
        try:
            entry = lib.get(hash8)
        except LibraryError as exc:
            self._send_error_json(str(exc), 404)
            return
        blob = lib.blob_path(entry)
        if not blob.exists():
            self._send_error_json(f"library blob missing: {entry['blob']}", 400)
            return
        project, actor = self.server.project, self.server.actor
        with self.server.quick_mutex:
            dest_dir = project.imports_dir if as_ == "imports" else project.refs_dir
            dest_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(entry["blob"]).suffix
            stem = Path(entry["name"]).stem or "asset"
            dest = dest_dir / f"{stem}{ext}"
            n = 2
            while dest.exists():  # no-overwrite suffixing (§3), like `lib use`
                dest = dest_dir / f"{stem}_{n}{ext}"
                n += 1
            shutil.copy2(blob, dest)
            rel = project.relpath(dest)
            append_event(project.root, actor, "lib_use",
                         {"hash": entry["hash"], "name": entry["name"],
                          "as": as_, "dest": rel, "via": "gui"})
        self._send_json({"ok": True, "dest": rel, "as": as_, "name": entry["name"]})

    def _lib_find_entry(self, index: dict[str, Any], hash8: str) -> dict[str, Any] | None:
        from ..core.library import _hex

        needle = hash8.strip().removeprefix("sha256:").lower()
        if not needle:
            return None
        for e in index["assets"]:
            if _hex(e["hash"]).startswith(needle):
                return e
        return None

    def _act_lib_tag(self, body: dict[str, Any]) -> None:
        """Replace an asset's tags (comma string or list) in the library index."""
        from ..core.library import Library, LibraryError, _clean_tags

        hash8 = str(body.get("hash") or "")
        raw = body.get("tags")
        if isinstance(raw, str):
            tags = [t.strip() for t in raw.split(",")]
        elif isinstance(raw, list):
            tags = [str(t) for t in raw]
        else:
            tags = []
        lib = Library()
        with self.server.quick_mutex:
            try:
                index = lib.load_index()
            except LibraryError as exc:
                self._send_error_json(str(exc), 400)
                return
            entry = self._lib_find_entry(index, hash8)
            if entry is None:
                self._send_error_json(f"no asset with hash {hash8!r}", 404)
                return
            entry["tags"] = _clean_tags(tags)
            lib._save_index(index)
            append_event(self.server.project.root, self.server.actor, "lib_tag",
                         {"hash": entry["hash"], "tags": entry["tags"], "via": "gui"})
        self._send_json({"ok": True, "hash": entry["hash"], "tags": entry["tags"]})

    def _act_lib_note(self, body: dict[str, Any]) -> None:
        """Set an asset's free-text note in the library index."""
        from ..core.library import Library, LibraryError

        hash8 = str(body.get("hash") or "")
        note = str(body.get("note") or "")
        if len(note) > 2000:
            self._send_error_json("note too long (max 2000 chars)", 400)
            return
        lib = Library()
        with self.server.quick_mutex:
            try:
                index = lib.load_index()
            except LibraryError as exc:
                self._send_error_json(str(exc), 400)
                return
            entry = self._lib_find_entry(index, hash8)
            if entry is None:
                self._send_error_json(f"no asset with hash {hash8!r}", 404)
                return
            entry["note"] = note.strip()
            lib._save_index(index)
            append_event(self.server.project.root, self.server.actor, "lib_note",
                         {"hash": entry["hash"], "via": "gui"})
        self._send_json({"ok": True, "hash": entry["hash"], "note": entry["note"]})

    def _act_lib_upload(self, query: dict[str, list[str]]) -> None:
        """Stream a browser upload straight into the private library (the GUI
        twin of `manju lib add`): dedup by content hash is the library's job.
        Reuses the same bounded streaming discipline as /api/upload."""
        import uuid as _uuid

        from ..core.library import Library, LibraryError, _hex

        raw_name = (query.get("name", [""])[0] or "").strip()
        raw_name = raw_name.replace("\\", "/").rsplit("/", 1)[-1]
        if not raw_name or raw_name.startswith("."):
            self._send_error_json("upload needs ?name=<filename>", 400)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._send_error_json("Content-Length required", 411)
            return
        if length > self._UPLOAD_MAX:
            self._send_error_json("file too large (max 4 GiB)", 413)
            return
        holder = self.server.project.runtime_dir / "upload-tmp" / _uuid.uuid4().hex
        holder.mkdir(parents=True, exist_ok=True)
        tmp = holder / raw_name  # keep the real name so the library records it
        received = 0
        try:
            with open(tmp, "wb") as f:
                while received < length:
                    chunk = self.rfile.read(min(1 << 20, length - received))
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
            if received != length:
                self._send_error_json("upload truncated", 400)
                return
            lib = Library()
            try:
                res = lib.add(tmp, tags=None, note=None)
            except LibraryError as exc:
                self._send_error_json(str(exc), 400)
                return
            entry = res["entry"]
            append_event(self.server.project.root, self.server.actor, "lib_add",
                         {"hash": entry["hash"], "name": entry["name"],
                          "deduped": res["deduped"], "via": "gui"})
            self._send_json({"ok": True, "hash8": _hex(entry["hash"])[:8],
                             "name": entry["name"], "deduped": res["deduped"]})
        finally:
            tmp.unlink(missing_ok=True)
            try:
                holder.rmdir()
            except OSError:
                pass

    # =================================================================
    # round-T 剪辑 EDIT page (edit.py): the clip-level finishing surface —
    # timeline order, trim, footage audio, transitions, looks — every action a
    # strict client of the same engine core the CLI calls (set_inout_take,
    # apply_mixer, rules.transition_default, bible/style.yaml look). Isolated
    # from the round-S pages region above; frame previews stream from the
    # disposable .manju/frames cache (lazy — the page GET never runs ffmpeg).
    # =================================================================

    def _edit_get(self, path: str, url: Any) -> bool:
        """GET dispatch for the 剪辑 page, its assets, and the lazy frame
        previews. Returns True when handled, False so do_GET falls through."""
        from . import edit

        if path == "/edit.css":
            self._send_text(edit.render_edit_css(), "text/css; charset=utf-8")
            return True
        if path == "/edit.js":
            self._send_text(edit.render_edit_js(),
                            "application/javascript; charset=utf-8")
            return True
        if path in edit.EDIT_PATHS:
            html_doc = edit.render_edit(self.server.project, self.server.token,
                                        parse_qs(url.query))
            self._send_text(html_doc, "text/html; charset=utf-8",
                            extra=self._PAGES_CSP)
            return True
        if path == "/edit/frame":
            self._edit_frame(parse_qs(url.query))
            return True
        if path == "/edit/strip":
            self._edit_strip(parse_qs(url.query))
            return True
        if path == "/edit/look":
            self._edit_look(parse_qs(url.query))
            return True
        if path == "/edit/wave":
            self._edit_wave(parse_qs(url.query))
            return True
        if path == "/api/edit/synchints":
            self._edit_synchints()
            return True
        if path == "/api/edit/playback-source":
            from . import edit

            self._send_json(edit.playback_source(self.server.project))
            return True
        if path == "/api/edit/snap":
            from .userstate import is_snap_enabled

            self._send_json({"enabled": is_snap_enabled()})
            return True
        if path == "/api/edit/undo":
            from . import edit

            self._send_json({"events": edit.edit_undo_events(self.server.project),
                             "note": edit.UNDO_PANEL_NOTE})
            return True
        return False

    @staticmethod
    def _q_int(query: dict[str, list[str]], key: str, default: int) -> int:
        try:
            return int(query.get(key, [str(default)])[0])
        except (TypeError, ValueError):
            return default

    def _edit_source(self, query: dict[str, list[str]]) -> str | None:
        """Validate the ?take= media relpath through the same allowlist +
        containment gate /media uses; None (→ 404) when it isn't a served
        media path (so a probing request can never read outside the tree)."""
        rel = (query.get("take", [""])[0] or "").lstrip("/")
        return rel if (rel and self._resolve_served(rel) is not None) else None

    def _edit_frame(self, query: dict[str, list[str]]) -> None:
        rel = self._edit_source(query)
        if rel is None:
            self._send_error_json("frame source not served", 404)
            return
        from ..media.frames import extract_frame

        w = self._q_int(query, "w", 0) or None
        try:
            frame = extract_frame(self.server.project, rel,
                                  self._q_int(query, "ms", 0), width=w)
        except Exception as exc:  # no ffmpeg / bad source → broken img, not 500
            self._send_error_json(" ".join(str(exc).split()), 404)
            return
        self._serve_file(frame)

    def _edit_strip(self, query: dict[str, list[str]]) -> None:
        rel = self._edit_source(query)
        if rel is None:
            self._send_error_json("frame source not served", 404)
            return
        from ..media.frames import frame_strip

        from .edit import STRIP_COUNT

        n = max(1, min(64, self._q_int(query, "n", STRIP_COUNT)))
        i = max(0, min(n - 1, self._q_int(query, "i", 0)))
        w = self._q_int(query, "w", 0) or None
        try:
            frames = frame_strip(self.server.project, rel, count=n,
                                 width=w or 140)
        except Exception as exc:
            self._send_error_json(" ".join(str(exc).split()), 404)
            return
        self._serve_file(frames[i])

    def _edit_look(self, query: dict[str, list[str]]) -> None:
        rel = self._edit_source(query)
        if rel is None:
            self._send_error_json("frame source not served", 404)
            return
        from .edit import look_preview_frame

        w = self._q_int(query, "w", 0) or None
        preset = query.get("preset", ["none"])[0] or "none"
        try:
            intensity = float(query.get("intensity", ["1"])[0])
        except (TypeError, ValueError):
            intensity = 1.0
        try:
            frame = look_preview_frame(self.server.project, rel,
                                       self._q_int(query, "ms", 0),
                                       preset, intensity, width=w)
        except Exception as exc:
            self._send_error_json(" ".join(str(exc).split()), 404)
            return
        self._serve_file(frame)

    def _edit_wave(self, query: dict[str, list[str]]) -> None:
        """One cached waveform PNG for an audio-bearing take/source (round U,
        contract B). Lazy: the /edit page GET never calls this — the audio-lane
        <img> does. An audio-less source (or no ffmpeg) is an honest 404 + JSON
        note, not a 500. Same media allowlist as the frame endpoints."""
        rel = self._edit_source(query)
        if rel is None:
            self._send_error_json("waveform source not served", 404)
            return
        from ..media.waveform import waveform_png

        w = self._q_int(query, "w", 480)
        h = self._q_int(query, "h", 40)
        try:
            png = waveform_png(self.server.project, rel, width=w, height=h)
        except Exception as exc:  # MediaError (no audio / no ffmpeg) → broken img
            self._send_error_json(" ".join(str(exc).split()), 404)
            return
        self._serve_file(png)

    def _edit_synchints(self) -> None:
        """Caption/voice sync hints for the compiled timeline (round U, contract
        C). Runs rms analysis lazily (never the page GET); degrades to an empty
        list + note without a timeline / voice / ffmpeg. Read-only."""
        from .synchints import sync_hints_data

        project = self.server.project
        try:
            timeline = project.load_timeline()
        except Exception:
            timeline = None
        self._send_json(sync_hints_data(project, timeline))

    def _edit_post(self, path: str, body: dict[str, Any]) -> bool:
        """POST dispatch for the 剪辑 EDIT actions. Same token/readonly gates as
        every other mutating POST (checked in do_POST before us)."""
        handler = {
            "/api/edit/trim": self._act_edit_trim,
            "/api/edit/audio": self._act_edit_audio,
            "/api/edit/duration": self._act_edit_duration,
            "/api/edit/transition": self._act_edit_transition,
            "/api/edit/transition-override": self._act_edit_transition_override,
            "/api/edit/look": self._act_edit_look,
            "/api/edit/handle-rebuild-plan": self._act_edit_handle_rebuild_plan,
            "/api/edit/handle-rebuild": self._act_edit_handle_rebuild,
            "/api/edit/snap": self._act_edit_snap,
            "/api/edit/revert": self._act_edit_revert,
        }.get(path)
        if handler is None:
            return False
        handler(body)
        return True

    # ============================================================ round-T
    # 字幕 subtitles / 混音 mixer / 打包 packaging-v2: the finishing pages that
    # let a normal video's captions, sound and packaging be completed without
    # opening JianYing. GET dispatch + their read endpoints (frame/strip/card
    # preview) here; the mutating actions below. Same token/readonly/host gates
    # as every other surface (checked in do_GET/do_POST before we run).

    def _pages_t_get(self, path: str, url: Any) -> bool:
        from . import pages_t

        if path == "/pages-t.css":
            self._send_text(pages_t.render_pages_t_css(), "text/css; charset=utf-8")
            return True
        if path == "/pages-t.js":
            self._send_text(pages_t.render_pages_t_js(),
                            "application/javascript; charset=utf-8")
            return True
        if path in pages_t.PAGE_PATHS_T:
            html_doc = pages_t.render(path, self.server.project, self.server.token,
                                      parse_qs(url.query))
            self._send_text(html_doc, "text/html; charset=utf-8", extra=self._PAGES_CSP)
            return True
        if path == "/frame":
            self._t_frame(parse_qs(url.query))
            return True
        if path == "/api/strip":
            self._t_strip(parse_qs(url.query))
            return True
        if path == "/api/card-preview":
            self._t_card_preview(parse_qs(url.query))
            return True
        if path == "/api/subtitles":
            from .captions_edit import read_current_cues

            self._send_json(read_current_cues(self.server.project))
            return True
        if path == "/api/mixer":
            from ..build.mixer import read_mixer

            self._send_json(read_mixer(self.server.project))
            return True
        return False

    def _pages_t_post(self, path: str, body: dict[str, Any]) -> bool:
        handler = {
            "/api/subtitles/save": self._t_subtitles_save,
            "/api/subtitles/revert": self._t_subtitles_revert,
            "/api/mixer/apply": self._t_mixer_apply,
            "/api/packaging/apply": self._t_packaging_apply,
        }.get(path)
        if handler is None:
            return False
        handler(body)
        return True

    # =================================================================
    # round-U 导出中心 EXPORT CENTER (goal item 4): one server-rendered page
    # over build/exportstatus.deliverables — the SAME rows `manju exports`
    # prints, so CLI and GUI can never disagree. New region, isolated from the
    # SPA + round-S/T page endpoints above. Same token/readonly/host gates as
    # every mutating surface (checked in do_GET/do_POST before we run). Priced
    # deliverables (final/proxy) are NOT generable here — the page links to the
    # workbench build (plan modal → confirm); the free exporters + packaging
    # cover/teaser go through the EXISTING engine paths (§8.3 no silent spend).
    # =================================================================

    # exporters/packaging draft kinds → whether generation is a build (never) or
    # a free/local export (always here). final/proxy are deliberately absent.
    _EXPORT_GEN_KINDS = ("srt", "ass", "otio", "jianying", "capcut", "cover", "teaser")

    def _exports_get(self, path: str, url: Any) -> bool:
        """GET dispatch for the 导出中心 page + its static/read assets. Returns
        True when handled (response already sent)."""
        from . import exports_page

        if path == "/exports.css":
            self._send_text(exports_page.render_exports_css(), "text/css; charset=utf-8")
            return True
        if path == "/exports.js":
            self._send_text(exports_page.render_exports_js(),
                            "application/javascript; charset=utf-8")
            return True
        if path == exports_page.PAGE_PATH:
            html_doc = exports_page.render(self.server.project, self.server.token)
            self._send_text(html_doc, "text/html; charset=utf-8", extra=self._PAGES_CSP)
            return True
        if path == "/api/exports":
            from ..build.exportstatus import deliverables_data

            self._send_json(deliverables_data(self.server.project))
            return True
        return False

    def _exports_post(self, path: str, body: dict[str, Any]) -> bool:
        """POST dispatch for the 导出中心 actions (generate/update + verify)."""
        handler = {
            "/api/exports/generate": self._act_exports_generate,
            "/api/exports/verify": self._act_exports_verify,
        }.get(path)
        if handler is None:
            return False
        handler(body)
        return True

    # ============================================================ round-U
    # 新手/专业 view switch (goal item 16) + plain-language glossary toggle (item
    # 18). Both are per-USER preferences in ~/.manju/gui_state.json — NO project
    # data is read or written, so switching mode never mutates the film. The
    # mode only shapes which nav links + panels a page renders; every page stays
    # reachable by URL (a mode never 403s a page). The two static chrome assets
    # (/glossary.css, /glossary.js) drive the tooltip look, the mode switch and
    # the 显示专业术语 toggle. Same GET host + POST token/readonly gates as every
    # other surface (checked in do_GET/do_POST before we run).

    def _modes_get(self, path: str) -> bool:
        """GET dispatch for the shared glossary + mode chrome assets. Returns
        True when handled, False so do_GET falls through to its 404."""
        from . import glossary

        if path == "/glossary.css":
            self._send_text(glossary.render_glossary_css(), "text/css; charset=utf-8")
            return True
        if path == "/glossary.js":
            self._send_text(glossary.render_glossary_js(),
                            "application/javascript; charset=utf-8")
            return True
        return False

    def _modes_post(self, path: str, body: dict[str, Any]) -> bool:
        """POST dispatch for the view-mode + glossary toggles. Same token/readonly
        gates as every other mutating POST (checked in do_POST before us)."""
        handler = {
            "/api/mode": self._act_set_mode,
            "/api/pro-terms": self._act_set_pro_terms,
        }.get(path)
        if handler is None:
            return False
        handler(body)
        return True

    def _act_exports_generate(self, body: dict[str, Any]) -> None:
        """Generate/update ONE deliverable through the existing engine path.

        - srt/ass  → exporters.srt_ass.export_captions (writes both)
        - otio     → exporters.otio.export_otio
        - jianying → exporters.jianying.export_jianying (+ native attempt)
        - capcut   → exporters.native_draft.export_capcut_native (pycapcut)
        - cover/teaser → media.packaging.make_package (local ffmpeg cut)

        final/proxy are refused with a pointer to the workbench build so a paid
        render always goes through the plan modal (§8.3 no silent spend). Runs on
        the job runner (serialized against builds), returns 202 with the job."""
        project, actor = self.server.project, self.server.actor
        kind = str(body.get("kind") or "")
        if kind in ("final", "proxy"):
            self._send_error_json(
                "成片/预览版请在工作台构建(计划弹窗确认花费),导出中心不直接消费", 400)
            return
        if kind not in self._EXPORT_GEN_KINDS:
            self._send_error_json(
                f"unknown deliverable: {kind!r} ({', '.join(self._EXPORT_GEN_KINDS)})", 400)
            return

        def fn(job) -> dict[str, Any]:
            from ..core.events import append_event

            if kind in ("srt", "ass", "otio", "jianying", "capcut"):
                timeline = project.load_timeline()
                if timeline is None:
                    raise RuntimeError("no timeline.json — run `manju build` first")
            if kind in ("srt", "ass"):
                from ..exporters.srt_ass import export_captions

                out = export_captions(project, timeline)
                result = {k: project.relpath(v) for k, v in out.items()}
                append_event(project.root, actor, "export",
                             {**result, "via": "gui"})
            elif kind == "otio":
                from ..exporters.otio import export_otio

                out = export_otio(project, timeline)
                result = {"otio": project.relpath(out)}
                append_event(project.root, actor, "export", {**result, "via": "gui"})
            elif kind == "jianying":
                from ..exporters.jianying import export_jianying
                from ..exporters.native_draft import (
                    ExporterUnavailable,
                    export_jianying_native,
                )

                out = export_jianying(project, timeline)  # skeleton + lint
                result = {"jianying": project.relpath(out)}
                try:
                    nat = export_jianying_native(project, timeline)
                    result["jianying_native"] = project.relpath(nat)
                except ExporterUnavailable as exc:
                    result["note"] = " ".join(str(exc).split())
                append_event(project.root, actor, "export",
                             {"jianying": result["jianying"], "via": "gui"})
            elif kind == "capcut":
                from ..exporters.native_draft import (
                    ExporterUnavailable,
                    export_capcut_native,
                )

                try:
                    out = export_capcut_native(project, timeline)
                except ExporterUnavailable as exc:
                    raise RuntimeError(" ".join(str(exc).split())) from exc
                result = {"capcut": project.relpath(out)}
                append_event(project.root, actor, "export", {**result, "via": "gui"})
            else:  # cover / teaser
                from ..media.packaging import make_package

                result = make_package(project)
                append_event(project.root, actor, "package",
                             {k: result[k] for k in ("cover", "teaser", "skipped")
                              if k in result} | {"via": "gui"})
            return result

        job = self.server.runner.submit("export", {"kind": kind}, fn)
        self._send_json({"job": job.to_dict()}, 202)

    def _act_exports_verify(self, body: dict[str, Any]) -> None:
        """标记已人工确认 for a desktop draft — appends to
        reports/verifications.jsonl (truth-is-text, §3). A draft shows 已人工确认
        only while its content hash still matches; regenerating flips it back."""
        from ..build.exportstatus import ExportStatusError, mark_verified
        from ..core.events import append_event

        project, actor = self.server.project, self.server.actor
        kind = str(body.get("kind") or "")
        note = str(body.get("note") or "")[:200]
        try:
            record = mark_verified(project, kind, actor, note)
        except ExportStatusError as exc:
            self._send_error_json(" ".join(str(exc).split()), 400)
            return
        append_event(project.root, actor, "verify_draft",
                     {"kind": kind, "note": note, "via": "gui"})
        with self.server.quick_mutex:
            self.server.state_cache = None  # a new verification changes the page
        self._send_json({"ok": True, "kind": kind, "record": record})

    def _act_set_mode(self, body: dict[str, Any]) -> None:
        """Switch 新手/专业, or dismiss the fresh-user hint. Per-user only — no
        project write, no event, no state-cache invalidation."""
        from .userstate import (
            MODES,
            resolve_mode,
            set_mode,
            set_mode_hint_dismissed,
        )

        if body.get("dismiss_hint"):
            set_mode_hint_dismissed(True)
            self._send_json({"ok": True, "mode": resolve_mode(),
                             "hint_dismissed": True})
            return
        mode = str(body.get("mode") or "")
        if mode not in MODES:
            self._send_error_json(
                f"invalid mode {mode!r}; expected one of {list(MODES)}", 400)
            return
        set_mode(mode)  # choosing a mode also dismisses the hint
        self._send_json({"ok": True, "mode": mode})

    def _act_set_pro_terms(self, body: dict[str, Any]) -> None:
        """The §10 显示专业术语 toggle — grey the English original beside each
        Chinese word. Per-user preference; no project write."""
        from .userstate import set_show_pro_terms

        show = bool(body.get("show"))
        set_show_pro_terms(show)
        self._send_json({"ok": True, "show": show})

    # =================================================================
    # round-U 分镜工作台 STORYBOARD (goal item 3): one server-rendered table
    # over project.shot_ids() + the asset matrix + build/stale + routing. The
    # REPORTS §2 columns, two orthogonal chips (状态 take-state / 审批 review).
    # New region, isolated from the SPA + round-S/T/U page endpoints above. Same
    # token/readonly/host gates as every mutating surface (checked in
    # do_GET/do_POST before we run). Every edit is lock-respecting — a locked
    # path is NEVER written (409 naming the lock, 中文); LOCK reuses the
    # `manju lock` seal, UNLOCK is deliberately absent (CLI-only containment).
    # =================================================================

    def _storyboard_get(self, path: str, url: Any) -> bool:
        """GET dispatch for the 分镜工作台 page + its static assets. Returns True
        when handled (response already sent)."""
        from . import storyboard

        if path == "/storyboard.css":
            self._send_text(storyboard.render_storyboard_css(), "text/css; charset=utf-8")
            return True
        if path == "/storyboard.js":
            self._send_text(storyboard.render_storyboard_js(),
                            "application/javascript; charset=utf-8")
            return True
        if path == storyboard.PAGE_PATH:
            html_doc = storyboard.render(self.server.project, self.server.token)
            self._send_text(html_doc, "text/html; charset=utf-8", extra=self._PAGES_CSP)
            return True
        return False

    def _storyboard_post(self, path: str, body: dict[str, Any]) -> bool:
        """POST dispatch for the 分镜工作台 actions (inline edit / approve /
        batch lock). Single-field lock reuses the shared ``/api/lock``."""
        handler = {
            "/api/storyboard/edit": self._act_sb_edit,
            "/api/storyboard/approve": self._act_sb_approve,
            "/api/storyboard/lock-batch": self._act_sb_lock_batch,
        }.get(path)
        if handler is None:
            return False
        handler(body)
        return True

    def _act_sb_edit(self, body: dict[str, Any]) -> None:
        """Inline edit of one shot free-text field (动作/台词/must_show/avoid)
        through ``update_shot_raw`` — LOCK-RESPECTING: a write that would touch a
        locked path is refused 409 naming the lock, never written (§5)."""
        from .storyboard import EDITABLE_FIELDS, LIST_FIELDS, lock_conflict

        project = self.server.project
        shot_id = str(body.get("shot") or "")
        field = str(body.get("field") or "")
        if not self._SHOT_ID_RE.fullmatch(shot_id):
            self._send_error_json("invalid shot id", 400)
            return
        if not project.shot_path(shot_id).exists():
            self._send_error_json(f"unknown shot: {shot_id}", 404)
            return
        if field not in EDITABLE_FIELDS:
            self._send_error_json(
                f"field not inline-editable: {field!r} ({', '.join(EDITABLE_FIELDS)})", 400)
            return
        value = body.get("value")
        value = "" if value is None else value
        with self.server.quick_mutex:
            try:
                raw = project.load_shot_raw(shot_id)
            except ProjectError as exc:
                self._send_error_json(str(exc), 404)
                return
            locked = raw.get("locked") or {}
            if isinstance(locked, list):
                locked = {str(p): "" for p in locked}
            conflict = lock_conflict(locked, field)
            if conflict is not None:
                self._send_error_json(
                    f"字段 {field} 被锁定({conflict})— 已封印,不写入锁定路径"
                    "(解锁请用命令行 manju unlock)", 409)
                return
            is_list = field in LIST_FIELDS
            if is_list:
                new_val: Any = [ln.strip() for ln in str(value).splitlines() if ln.strip()]
            else:
                new_val = str(value)
            parent, child = field.split(".", 1)

            def mutate(d: dict[str, Any]) -> None:
                sub = d.get(parent)
                if not isinstance(sub, dict):
                    sub = {}
                sub[child] = new_val
                d[parent] = sub

            project.update_shot_raw(shot_id, mutate)
            append_event(project.root, self.server.actor, "edit_shot",
                         {"shot": shot_id, "field": field, "via": "gui"})
        self._send_json({"ok": True, "shot": shot_id, "field": field})

    def _act_sb_approve(self, body: dict[str, Any]) -> None:
        """Set the three-state review on one shot (chip cycle) or a batch
        (multi-select). Writing ``review`` SYNCS the legacy ``approved`` bool so
        every existing reader stays correct. Never touches a locked/creative
        field — approval is status, orthogonal to the picture spec."""
        from ..core.models import REVIEW_STATES

        project = self.server.project
        review = str(body.get("review") or "")
        if review not in REVIEW_STATES:
            self._send_error_json(
                f"review must be one of {REVIEW_STATES}", 400)
            return
        shots = body.get("shots")
        single = body.get("shot")
        if isinstance(shots, list):
            ids = [s for s in shots if isinstance(s, str)]
            batch = True
        elif single:
            ids = [str(single)]
            batch = False
        else:
            self._send_error_json("shot or shots is required", 400)
            return
        if not ids:
            self._send_error_json("no shots given", 400)
            return
        changed: list[str] = []
        skipped: list[dict[str, str]] = []
        with self.server.quick_mutex:
            for sid in ids:
                if not self._SHOT_ID_RE.fullmatch(sid) or not project.shot_path(sid).exists():
                    skipped.append({"shot": sid, "reason": "no such shot"})
                    continue

                def mutate(d: dict[str, Any]) -> None:
                    status = d.get("status")
                    if not isinstance(status, dict):
                        status = {}
                    status["review"] = review
                    status["approved"] = review == "approved"
                    d["status"] = status

                project.update_shot_raw(sid, mutate)
                changed.append(sid)
            if changed:
                detail: dict[str, Any] = {"review": review, "via": "gui"}
                if batch:
                    detail["shots"] = changed
                else:
                    detail["shot"] = changed[0]
                append_event(project.root, self.server.actor, "approve", detail)
        self._send_json({"ok": True, "review": review, "changed": len(changed),
                         "shots": changed, "skipped": skipped})

    def _act_sb_lock_batch(self, body: dict[str, Any]) -> None:
        """Batch LOCK one field across the selected shots — the same seal
        ``manju lock`` uses (per-shot isolation: a missing field / already-locked
        shot is skipped with a reason, never a silent failure). UNLOCK stays
        CLI-only."""
        from ..core.locks import seal_lock

        project = self.server.project
        field = str(body.get("field") or "")
        shots = body.get("shots")
        if not field:
            self._send_error_json("field is required", 400)
            return
        if not isinstance(shots, list) or not shots or not all(isinstance(s, str) for s in shots):
            self._send_error_json("shots must be a non-empty list of shot ids", 400)
            return
        locked_ids: list[str] = []
        skipped: list[dict[str, str]] = []
        with self.server.quick_mutex:
            for sid in shots:
                if not self._SHOT_ID_RE.fullmatch(sid) or not project.shot_path(sid).exists():
                    skipped.append({"shot": sid, "reason": "no such shot"})
                    continue
                try:
                    raw = project.load_shot_raw(sid)
                    if field in (raw.get("locked") or {}):
                        skipped.append({"shot": sid, "reason": f"{field} 已锁定"})
                        continue
                    digest = seal_lock(raw, field)
                except (KeyError, IndexError, ValueError, ProjectError) as exc:
                    skipped.append({"shot": sid, "reason": f"cannot lock: {exc}"})
                    continue

                def mutate(d: dict[str, Any], digest: str = digest, field: str = field) -> None:
                    locked = d.get("locked")
                    if not isinstance(locked, dict):
                        locked = {str(p): "" for p in locked} if isinstance(locked, list) else {}
                    locked[field] = digest
                    d["locked"] = locked

                project.update_shot_raw(sid, mutate)
                append_event(project.root, self.server.actor, "lock",
                             {"shot": sid, "field": field, "via": "gui"})
                locked_ids.append(sid)
        self._send_json({"ok": True, "field": field, "locked": len(locked_ids),
                         "shots": locked_ids, "skipped": skipped})

    # =================================================================
    # round-U 镜头实验室 SHOT LAB (goal item 13): the single-shot experiment
    # page. Own dispatch region — the /lab three-panel page (参考 / 提示词 /
    # 候选), its read-only data + cost-delta endpoints, and the five mutating
    # actions (refs / prompt_override / scaffold / generate / save-ref). Every
    # priced action goes through the EXISTING redo/jobs path; every spec write
    # respects locks (§5); the panels read the SAME code the CLI (prompt/refs)
    # and the build use (src/manju/gui/lab_page.py).
    # =================================================================

    def _lab_get(self, path: str, url: Any) -> bool:
        """GET dispatch for the 镜头实验室 page + its static/read endpoints.
        Returns True when handled (response already sent)."""
        from . import lab_page

        if path == "/lab.css":
            self._send_text(lab_page.render_lab_css(), "text/css; charset=utf-8")
            return True
        if path == "/lab.js":
            self._send_text(lab_page.render_lab_js(),
                            "application/javascript; charset=utf-8")
            return True
        if path == lab_page.PAGE_PATH:
            html_doc = lab_page.render(self.server.project, self.server.token,
                                       parse_qs(url.query))
            self._send_text(html_doc, "text/html; charset=utf-8", extra=self._PAGES_CSP)
            return True
        if path == "/api/lab/data":
            shot_id = (parse_qs(url.query).get("shot") or [""])[0]
            try:
                self._send_json(lab_page.lab_data(self.server.project, shot_id))
            except ProjectError as exc:
                self._send_error_json(" ".join(str(exc).split()), 404)
            return True
        if path == "/api/lab/generate-plan":
            q = parse_qs(url.query)
            shot_id = (q.get("shot") or [""])[0]
            provider = (q.get("provider") or [None])[0]
            try:
                self._send_json(lab_page.generate_plan(
                    self.server.project, shot_id, provider))
            except ProjectError as exc:
                self._send_error_json(" ".join(str(exc).split()), 404)
            except (ValueError, KeyError) as exc:
                self._send_error_json(" ".join(str(exc).split()), 400)
            return True
        return False

    def _lab_post(self, path: str, body: dict[str, Any]) -> bool:
        """POST dispatch for the 镜头实验室 mutating actions. Same token/readonly
        gates as every other mutating POST (checked in do_POST before us)."""
        handler = {
            "/api/lab/refs": self._act_lab_refs,
            "/api/lab/prompt-override": self._act_lab_prompt_override,
            "/api/lab/scaffold": self._act_lab_scaffold,
            "/api/lab/generate": self._act_lab_generate,
            "/api/lab/save-ref": self._act_lab_save_ref,
        }.get(path)
        if handler is None:
            return False
        handler(body)
        return True

    @staticmethod
    def _coerce_dict(parent: dict[str, Any], key: str) -> dict[str, Any]:
        """Get-or-create ``parent[key]`` as a dict (defensive against a malformed
        truth file where the field is a scalar/list — never crash a write)."""
        val = parent.get(key)
        if not isinstance(val, dict):
            val = {}
            parent[key] = val
        return val

    def _act_lab_refs(self, body: dict[str, Any]) -> None:
        """Reorder / pick the shot's OWN refs — writes ``generation.params.refs``
        through the normal spec path, lock-respecting (409 + 中文 on a sealed
        field, §5). The panel shows the full resolved lineage read-only; this
        action edits only the explicit param slot the director controls."""
        from . import lab_page

        project, actor = self.server.project, self.server.actor
        shot_id = str(body.get("shot") or "")
        refs = body.get("refs")
        if not shot_id:
            self._send_error_json("shot is required", 400)
            return
        if not project.shot_path(shot_id).exists():
            self._send_error_json(f"unknown shot: {shot_id}", 404)
            return
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            self._send_error_json("refs must be a list of strings", 400)
            return
        refs = [r.strip() for r in refs if r.strip()]
        with self.server.quick_mutex:
            raw = project.load_shot_raw(shot_id)
            locker = lab_page.field_locked(raw, "generation.params.refs")
            if locker:
                self._send_error_json(
                    f"generation.params.refs 已锁定(§5):字段 {locker} 已封存,"
                    "GUI 不会覆盖锁定;先在终端 `manju unlock` 再改", 409)
                return

            def mutate(d: dict[str, Any]) -> None:
                params = self._coerce_dict(self._coerce_dict(d, "generation"), "params")
                if refs:
                    params["refs"] = refs
                else:
                    params.pop("refs", None)

            project.update_shot_raw(shot_id, mutate)
            append_event(project.root, actor, "edit_shot",
                         {"shot": shot_id, "field": "generation.params.refs",
                          "via": "gui"})
        self._send_json({"ok": True, "shot": shot_id, "refs": refs})

    def _act_lab_prompt_override(self, body: dict[str, Any]) -> None:
        """Edit ``generation.prompt_override`` inline — the exact string the model
        receives. Lock-respecting (409 + 中文 on a sealed field, §5). Empty text
        clears the override (falls back to the compiled prompt)."""
        from . import lab_page

        project, actor = self.server.project, self.server.actor
        shot_id = str(body.get("shot") or "")
        text = "" if body.get("text") is None else str(body.get("text"))
        if not shot_id:
            self._send_error_json("shot is required", 400)
            return
        if not project.shot_path(shot_id).exists():
            self._send_error_json(f"unknown shot: {shot_id}", 404)
            return
        if len(text) > 5000:
            self._send_error_json("prompt_override too long (max 5000 chars)", 400)
            return
        with self.server.quick_mutex:
            raw = project.load_shot_raw(shot_id)
            locker = lab_page.field_locked(raw, "generation.prompt_override")
            if locker:
                self._send_error_json(
                    f"generation.prompt_override 已锁定(§5):字段 {locker} 已封存,"
                    "GUI 不会覆盖锁定;先在终端 `manju unlock` 再改", 409)
                return

            def mutate(d: dict[str, Any]) -> None:
                gen = self._coerce_dict(d, "generation")
                if text.strip():
                    gen["prompt_override"] = text
                else:
                    gen.pop("prompt_override", None)

            project.update_shot_raw(shot_id, mutate)
            append_event(project.root, actor, "edit_shot",
                         {"shot": shot_id, "field": "generation.prompt_override",
                          "via": "gui"})
        self._send_json({"ok": True, "shot": shot_id, "chars": len(text.strip())})

    def _act_lab_scaffold(self, body: dict[str, Any]) -> None:
        """按关键帧拆分 — apply a deterministic action breakdown into the shot's
        ``keyframes`` (the SAME scaffold path `manju board keyframes --scaffold`
        uses). Only this explicit apply writes; the page's preview never does.
        Refuses (409) when ``keyframes`` is sealed (§5)."""
        from ..media.boards import (
            KeyframeScaffoldError,
            breakdown_action,
            scaffold_keyframes,
        )

        project, actor = self.server.project, self.server.actor
        shot_id = str(body.get("shot") or "")
        if not shot_id:
            self._send_error_json("shot is required", 400)
            return
        if not project.shot_path(shot_id).exists():
            self._send_error_json(f"unknown shot: {shot_id}", 404)
            return
        try:
            n = int(body.get("n", 4))
        except (TypeError, ValueError):
            self._send_error_json("n must be an integer (2..9)", 400)
            return
        if n < 2 or n > 9:
            self._send_error_json("n must be between 2 and 9", 400)
            return
        try:
            shot = project.load_shot(shot_id)
        except ProjectError as exc:
            self._send_error_json(str(exc), 404)
            return
        action = (shot.action.main or "").strip() or (shot.generation.prompt_override or "")
        beats = breakdown_action(action, n)
        with self.server.quick_mutex:
            try:
                kfs = scaffold_keyframes(project, shot_id, beats)
            except KeyframeScaffoldError as exc:
                self._send_error_json(" ".join(str(exc).split()), 409)
                return
            append_event(project.root, actor, "board_keyframes",
                         {"shot": shot_id, "beats": n, "via": "gui"})
        self._send_json({"ok": True, "shot": shot_id, "keyframes": kfs})

    def _act_lab_generate(self, body: dict[str, Any]) -> None:
        """生成候选 — run the EXISTING single-shot redo on the jobs runner with the
        quality-resolved 生成来源 and the same §8.3 spend gate (assume_yes). The
        草稿/成片 toggle resolves to a provider via the UE build-mode else-bias, so
        the priced provider (generate-plan) and the run provider are identical.
        A priced run without ``assume_yes`` fails the job as ``waiting_user`` —
        never a silent spend."""
        from . import lab_page

        project, actor = self.server.project, self.server.actor
        shot_id = str(body.get("shot") or "")
        quality = str(body.get("quality") or "draft")
        explicit = body.get("provider") or None
        assume_yes = bool(body.get("assume_yes"))
        if not shot_id:
            self._send_error_json("shot is required", 400)
            return
        if not project.shot_path(shot_id).exists():
            self._send_error_json(f"unknown shot: {shot_id}", 404)
            return
        if quality not in lab_page.QUALITY_MODES:
            self._send_error_json("quality must be 'draft' or 'final'", 400)
            return
        try:
            shot = project.load_shot(shot_id)
        except ProjectError as exc:
            self._send_error_json(str(exc), 404)
            return
        provider = lab_page.resolve_quality_provider(
            project, shot, quality, str(explicit) if explicit else None)
        params = {"shot": shot_id, "quality": quality, "provider": provider,
                  "assume_yes": assume_yes}

        def fn(job) -> dict[str, Any]:
            import inspect

            from ..build.graph import redo_shot

            kwargs: dict[str, Any] = dict(
                provider=str(provider) if provider else None, actor=actor)
            if "assume_yes" in inspect.signature(redo_shot).parameters:
                kwargs["assume_yes"] = assume_yes
            takes = redo_shot(project, shot_id, **kwargs)
            return {"shot": shot_id, "quality": quality, "provider": provider,
                    "takes": takes}

        job = self.server.runner.submit("redo", params, fn)
        self._send_json({"job": job.to_dict()}, 202)

    def _act_lab_save_ref(self, body: dict[str, Any]) -> None:
        """存为参考 — extract a frame of a chosen take (frames.extract_frame) and
        save it either into the shot's refs (durable copy into media/refs +
        ``generation.params.refs`` spec write, lock-respecting) or into the user
        asset library (core.library). The user picks which via ``dest``."""
        import shutil

        from . import lab_page
        from ..media.ffmpeg import MediaError
        from ..media.frames import extract_frame

        project, actor = self.server.project, self.server.actor
        shot_id = str(body.get("shot") or "")
        take = str(body.get("take") or "")
        dest = str(body.get("dest") or "refs")
        if not shot_id or not take:
            self._send_error_json("shot and take are required", 400)
            return
        if dest not in ("refs", "library"):
            self._send_error_json("dest must be 'refs' or 'library'", 400)
            return
        try:
            at_ms = max(0, int(body.get("at_ms", 0)))
        except (TypeError, ValueError):
            self._send_error_json("at_ms must be an integer (ms)", 400)
            return
        if not project.shot_path(shot_id).exists():
            self._send_error_json(f"unknown shot: {shot_id}", 404)
            return
        t = project.get_take(shot_id, take)
        if t is None or t.media_path is None:
            self._send_error_json(f"{shot_id} has no take '{take}' with media", 404)
            return
        try:
            frame = extract_frame(project, project.relpath(t.media_path), at_ms)
        except MediaError as exc:
            self._send_error_json(f"截帧失败: {' '.join(str(exc).split())}", 400)
            return

        if dest == "library":
            from ..core.library import Library, LibraryError

            try:
                result = Library().add(
                    frame, tags=[shot_id, take, "frame"],
                    note=f"{shot_id}/{take} @ {at_ms}ms")
            except LibraryError as exc:
                self._send_error_json(" ".join(str(exc).split()), 400)
                return
            entry = result["entry"]
            append_event(project.root, actor, "lib_add",
                         {"shot": shot_id, "take": take, "hash": entry["hash"],
                          "via": "gui"})
            self._send_json({"ok": True, "dest": "library",
                             "hash": entry["hash"], "name": entry["name"],
                             "deduped": result["deduped"]})
            return

        # dest == "refs": the frame cache is disposable (§3, `manju gc` wipes it),
        # so copy it into media/refs (a durable project asset) before declaring it.
        with self.server.quick_mutex:
            raw = project.load_shot_raw(shot_id)
            locker = lab_page.field_locked(raw, "generation.params.refs")
            if locker:
                self._send_error_json(
                    f"generation.params.refs 已锁定(§5):字段 {locker} 已封存,"
                    "GUI 不会覆盖锁定;先在终端 `manju unlock` 再改", 409)
                return
            refs_dir = project.refs_dir
            refs_dir.mkdir(parents=True, exist_ok=True)
            base = f"{shot_id}_{take}_{at_ms}ms"
            dest_path = refs_dir / f"{base}.jpg"
            k = 2
            while dest_path.exists():  # never overwrite an existing ref (§3)
                dest_path = refs_dir / f"{base}_{k}.jpg"
                k += 1
            shutil.copy2(frame, dest_path)
            ref_rel = project.relpath(dest_path)

            def mutate(d: dict[str, Any]) -> None:
                params = self._coerce_dict(self._coerce_dict(d, "generation"), "params")
                cur = params.get("refs")
                lst = list(cur) if isinstance(cur, list) else (
                    [cur] if isinstance(cur, str) and cur else [])
                if ref_rel not in lst:
                    lst.append(ref_rel)
                params["refs"] = lst

            project.update_shot_raw(shot_id, mutate)
            append_event(project.root, actor, "save_ref",
                         {"shot": shot_id, "take": take, "ref": ref_rel,
                          "dest": "refs", "via": "gui"})
        self._send_json({"ok": True, "dest": "refs", "ref": ref_rel})

    def _act_edit_trim(self, body: dict[str, Any]) -> None:
        """Trim a take's in/out — the `manju repair --op inout` engine op run as
        a job (append-only: a NEW take is minted, the source is never touched and
        the new take is NOT auto-selected; the page offers select afterward, so
        human selection judgment is preserved)."""
        project, actor = self.server.project, self.server.actor
        shot_id = str(body.get("shot") or "")
        if not shot_id or not project.shot_path(shot_id).exists():
            self._send_error_json(f"unknown shot: {shot_id}", 404)
            return
        take = body.get("take") or None
        if take is None:
            try:
                take = project.load_shot(shot_id).status.selected_take
            except ProjectError as exc:
                self._send_error_json(str(exc), 404)
                return
        if not take:
            self._send_error_json(
                f"{shot_id} has no selected take — pass a take or select one first", 400)
            return
        take = str(take)
        if project.get_take(shot_id, take) is None:
            self._send_error_json(f"{shot_id} has no take '{take}'", 404)
            return
        in_ms, out_ms = body.get("in_ms"), body.get("out_ms")
        if in_ms is None or out_ms is None:
            self._send_error_json("in_ms and out_ms are required", 400)
            return
        try:
            in_ms, out_ms = int(in_ms), int(out_ms)
        except (TypeError, ValueError):
            self._send_error_json("in_ms and out_ms must be integers (ms)", 400)
            return
        if in_ms < 0 or out_ms <= in_ms:
            self._send_error_json("require 0 <= in_ms < out_ms", 400)
            return

        def fn(job) -> dict[str, Any]:
            from ..media.repair_ops import set_inout_take

            with _optional_build_lock(project.root, actor):
                new = set_inout_take(project, shot_id, take, in_ms, out_ms)
            append_event(project.root, actor, "repair",
                         {"shot": shot_id, "op": "inout", "source_take": take,
                          "new_take": new.name, "in_ms": in_ms, "out_ms": out_ms,
                          "via": "gui"})
            return {"shot": shot_id, "op": "inout", "source_take": take,
                    "new_take": new.name, "params": new.sidecar.params}

        job = self.server.runner.submit("repair", {"shot": shot_id, "op": "inout"}, fn)
        self._send_json({"job": job.to_dict()}, 202)

    def _act_edit_audio(self, body: dict[str, Any]) -> None:
        """Footage-audio (per-shot source_audio) gain + mute → the SAME
        apply_mixer shots[] path the CLI mixer uses (writes source_audio into the
        shot file, lands one `mixer` event)."""
        from ..build.mixer import MixerError, apply_mixer

        project, actor = self.server.project, self.server.actor
        shot_id = str(body.get("shot") or "")
        if not shot_id:
            self._send_error_json("shot is required", 400)
            return
        if not project.shot_path(shot_id).exists():
            self._send_error_json(f"unknown shot: {shot_id}", 404)
            return
        change: dict[str, Any] = {"shot": shot_id}
        if "gain_db" in body:
            change["gain_db"] = body["gain_db"]
        if "mute" in body:
            change["mute"] = bool(body["mute"])
        with self.server.quick_mutex:
            try:
                report = apply_mixer(project, {"shots": [change]}, actor=actor)
            except MixerError as exc:
                self._send_error_json(" ".join(str(exc).split()), 400)
                return
            except ProjectError as exc:
                self._send_error_json(str(exc), 404)
                return
        self._send_json({"ok": True, "shot": shot_id, "report": report})

    def _act_edit_duration(self, body: dict[str, Any]) -> None:
        """Shot duration override (auto | seconds) via the existing check-gated
        shot-edit path — a top-level `duration:` text edit (comments + locks left
        verbatim), reverted if it introduces any new check error (lock included)."""
        project = self.server.project
        shot_id = str(body.get("shot") or "")
        if not self._SHOT_ID_RE.fullmatch(shot_id):
            self._send_error_json("invalid shot id", 400)
            return
        if not project.shot_path(shot_id).exists():
            self._send_error_json(f"unknown shot: {shot_id}", 404)
            return
        dur = body.get("duration")
        if dur == "auto":
            val = "auto"
        else:
            try:
                f = float(dur)
            except (TypeError, ValueError):
                self._send_error_json("duration must be 'auto' or a positive number", 400)
                return
            if not (f > 0):
                self._send_error_json("duration seconds must be > 0 (or 'auto')", 400)
                return
            val = f"{f:g}"
        from .edit import set_duration_in_text

        with self.server.quick_mutex:
            text = project.shot_path(shot_id).read_text(encoding="utf-8")
            ok, payload, status = self._gated_save(
                project.shot_path(shot_id), set_duration_in_text(text, val),
                label=f"shots/{shot_id}.yaml")
            if ok:
                payload["shot"] = shot_id
                append_event(project.root, self.server.actor, "edit_shot",
                             {"shot": shot_id, "field": "duration",
                              "duration": val, "via": "gui"})
        self._send_json(payload, status)

    def _act_edit_transition(self, body: dict[str, Any]) -> None:
        """Default transition picker → rules.transition_default, validate-on-save
        (rejects an unknown type / out-of-range duration, then the same
        check-gated write the rules editor uses so a bad save is reverted)."""
        from ..core.models import TRANSITION_TYPES, TransitionSpec
        from ..core.yamlio import dump_yaml

        project = self.server.project
        ttype = str(body.get("type") or "")
        if ttype not in TRANSITION_TYPES:
            self._send_error_json(
                f"unknown transition type {ttype!r}; one of {list(TRANSITION_TYPES)}", 400)
            return
        dur = body.get("duration_ms", 300)
        try:
            dur = int(dur)
        except (TypeError, ValueError):
            self._send_error_json("duration_ms must be an integer (ms)", 400)
            return
        if not (0 <= dur <= 5000):
            self._send_error_json("duration_ms must be in [0, 5000]", 400)
            return
        try:
            spec = TransitionSpec(type=ttype, duration_ms=dur)
        except Exception as exc:
            self._send_error_json("invalid transition: " + " ".join(str(exc).split()), 400)
            return
        with self.server.quick_mutex:
            rules = project.load_rules()
            rules.transition_default = spec
            ok, payload, status = self._gated_save(
                project.rules_path, dump_yaml(rules.model_dump()),
                label="timeline/rules.yaml")
            if ok:
                append_event(project.root, self.server.actor, "edit_rules",
                             {"transition_default": {"type": ttype, "duration_ms": dur},
                              "via": "gui"})
        self._send_json(payload, status)

    def _act_edit_transition_override(self, body: dict[str, Any]) -> None:
        """Per-boundary transition override (round U) → rules.transition_overrides
        keyed by the OUT-edge shot id, through the SAME check-gated rules save the
        default picker uses.

        - ``{shot, type, duration_ms}`` sets an xfade/fade override — the type is
          validated against TRANSITION_TYPES and the duration against the ½-clip
          cap (REPORTS §1: ≤ half the shorter neighbour) → 400 with a 中文 reason
          otherwise;
        - ``{shot, cut: true}`` writes ``null`` (硬切);
        - ``{shot, action: "reset"}`` removes the key (恢复默认).

        Round W (issue #69): ``shot`` is used verbatim as a rules key, so it is
        now checked against the same ``known`` set `qc/checks.py
        _transition_override_advisories` uses (every real shot id, plus the
        ``__intro__``/``__outro__`` packaging sentinels) — an unknown key is a
        404 with a 中文 reason instead of silently writing an inert override.
        A KNOWN key that is the film's true last segment (no out-edge — the
        override could never fire) still writes, but the response carries a
        warning explaining why, mirroring the QC advisory's wording.
        Records an ``edit_rules`` event with the change."""
        from ..core.models import TRANSITION_TYPES, TransitionSpec
        from ..core.yamlio import dump_yaml
        from .edit_engine import max_override_duration_ms, neighbour_durations

        project = self.server.project
        shot_id = str(body.get("shot") or "")
        if not shot_id:
            self._send_error_json("shot (out-edge id) is required", 400)
            return
        known = set(project.shot_ids()) | {"__intro__", "__outro__"}
        if shot_id not in known:
            self._send_error_json(
                f"未知镜头/边界键 \"{shot_id}\":不是任何镜头 id,也不是 __intro__/__outro__"
                "(transition_overrides 的键必须是镜头 id 或 __intro__/__outro__)", 404)
            return
        try:
            timeline = project.load_timeline()
        except Exception:
            timeline = None
        no_out_edge = neighbour_durations(project, timeline, shot_id) is None
        action = str(body.get("action") or "")
        cut = bool(body.get("cut"))
        detail: dict[str, Any]
        new_value: Any
        if action == "reset":
            new_value = "__remove__"
            detail = {"transition_override": {"shot": shot_id, "reset": True}}
        elif cut:
            new_value = None  # explicit null = hard cut
            detail = {"transition_override": {"shot": shot_id, "type": "cut"}}
        else:
            ttype = str(body.get("type") or "")
            if ttype not in TRANSITION_TYPES:
                self._send_error_json(
                    f"unknown transition type {ttype!r}; one of {list(TRANSITION_TYPES)}", 400)
                return
            dur = body.get("duration_ms", 300)
            try:
                dur = int(dur)
            except (TypeError, ValueError):
                self._send_error_json("duration_ms must be an integer (ms)", 400)
                return
            if not (0 <= dur <= 5000):
                self._send_error_json("duration_ms must be in [0, 5000]", 400)
                return
            # REPORTS §1 validity: ≤ half the shorter neighbour clip.
            cap = max_override_duration_ms(project, timeline, shot_id)
            if cap is not None and dur > cap:
                self._send_error_json(
                    f"转场时长 {dur}ms 超过上限:相邻较短片段一半为 {cap}ms(剪映规则)", 400)
                return
            try:
                spec = TransitionSpec(type=ttype, duration_ms=dur)
            except Exception as exc:
                self._send_error_json(
                    "invalid transition: " + " ".join(str(exc).split()), 400)
                return
            new_value = spec.model_dump()
            detail = {"transition_override": {"shot": shot_id, "type": ttype,
                                              "duration_ms": dur}}
        with self.server.quick_mutex:
            rules = project.load_rules()
            ov = dict(rules.transition_overrides or {})
            if new_value == "__remove__":
                if shot_id not in ov:
                    self._send_error_json(f"{shot_id} 没有逐切换点覆盖可恢复", 400)
                    return
                ov.pop(shot_id, None)
            elif new_value is None:
                ov[shot_id] = None
            else:
                ov[shot_id] = new_value
            rules.transition_overrides = ov
            ok, payload, status = self._gated_save(
                project.rules_path, dump_yaml(rules.model_dump()),
                label="timeline/rules.yaml")
            if ok:
                append_event(project.root, self.server.actor, "edit_rules",
                             {**detail, "via": "gui"})
                # issue #69: writing an override on the true last segment is
                # legal (the key is real) but inert (there is no out-edge for
                # it to render on) — warn instead of pretending it will fire.
                if no_out_edge and action != "reset":
                    payload.setdefault("warnings", [])
                    payload["warnings"].append(
                        f"\"{shot_id}\" 是当前时间线的最后一段,没有出边(out-edge),"
                        "这个转场覆盖不会生效"
                    )
        self._send_json(payload, status)

    def _act_edit_handle_rebuild_plan(self, body: dict[str, Any]) -> None:
        """Read-only 补拍手柄 proposal (round U, contract F): the extended
        generate duration + centred trim window + est cost, OR the honest
        'no duration control' advisory. Never mutates."""
        from .edit_engine import handle_rebuild_proposal

        project = self.server.project
        shot_id = str(body.get("shot") or "")
        if not shot_id or not project.shot_path(shot_id).exists():
            self._send_error_json(f"unknown shot: {shot_id}", 404)
            return
        tm = body.get("transition_ms")
        try:
            prop = handle_rebuild_proposal(
                project, shot_id,
                transition_ms=int(tm) if tm is not None else None)
        except ProjectError as exc:
            self._send_error_json(str(exc), 404)
            return
        except Exception as exc:
            self._send_error_json(" ".join(str(exc).split()), 400)
            return
        self._send_json(prop)

    def _act_edit_handle_rebuild(self, body: dict[str, Any]) -> None:
        """补拍手柄 (round U, contract F): regenerate the shot LONGER through the
        existing redo path, then virtual-trim to the centred content span so a
        degraded xfade earns real handles. Runs on the job runner (serialized
        against builds), same §8.3 spend gate as redo (assume_yes). A provider
        without duration control is refused synchronously with the advisory.

        Goal 63: the client sends back ``provider`` — the value its EARLIER
        ``/api/edit/handle-rebuild-plan`` call showed in the confirm popover.
        When present it is compared against a FRESH resolution here (routing
        may have changed in the time the human spent looking at the popover)
        and again inside the job right before spending; either mismatch
        refuses with a 已重新报价 message rather than silently running a
        provider the human never confirmed. Omitting it (an older client)
        falls back to resolving fresh, as before."""
        from .edit_engine import (
            NO_DURATION_ADVISORY,
            handle_rebuild_proposal,
        )

        project, actor = self.server.project, self.server.actor
        shot_id = str(body.get("shot") or "")
        if not shot_id or not project.shot_path(shot_id).exists():
            self._send_error_json(f"unknown shot: {shot_id}", 404)
            return
        tm = body.get("transition_ms")
        transition_ms = int(tm) if tm is not None else None
        assume_yes = bool(body.get("assume_yes"))
        proposed_provider = body.get("provider") or None
        # refuse up front (no job) when the provider can't be told a duration.
        try:
            prop = handle_rebuild_proposal(project, shot_id, transition_ms=transition_ms)
        except ProjectError as exc:
            self._send_error_json(str(exc), 404)
            return
        except Exception as exc:
            self._send_error_json(" ".join(str(exc).split()), 400)
            return
        if not prop.get("supported"):
            self._send_error_json(NO_DURATION_ADVISORY, 400)
            return
        if proposed_provider and proposed_provider != prop.get("provider"):
            self._send_error_json(
                f"已重新报价:供应商从提案的 {proposed_provider} 变为 "
                f"{prop.get('provider')}(routing 配置发生变化)— "
                "请重新查看补拍手柄提案后再确认", 409)
            return

        def fn(job) -> dict[str, Any]:
            from .edit_engine import run_handle_rebuild

            return run_handle_rebuild(project, shot_id, transition_ms=transition_ms,
                                      provider=proposed_provider,
                                      actor=actor, assume_yes=assume_yes)

        job = self.server.runner.submit(
            "handle_rebuild", {"shot": shot_id}, fn)
        self._send_json({"job": job.to_dict()}, 202)

    def _act_edit_look(self, body: dict[str, Any]) -> None:
        """Look preset + intensity → bible/style.yaml `look:`, validate-on-save
        (LookSpec bounds reject an unknown preset / out-of-range intensity), other
        style keys preserved, reverted if the check regresses."""
        from ..core.models import LookSpec
        from ..core.yamlio import dump_yaml, read_yaml

        project = self.server.project
        preset = str(body.get("preset") or "none")
        intensity = body.get("intensity", 1.0)
        try:
            look = LookSpec(preset=preset, intensity=float(intensity))
        except Exception as exc:
            self._send_error_json("invalid look: " + " ".join(str(exc).split()), 400)
            return
        style_path = project.root / "bible" / "style.yaml"
        with self.server.quick_mutex:
            data: Any = {}
            if style_path.exists():
                try:
                    data = read_yaml(style_path) or {}
                except Exception:
                    data = {}
            if not isinstance(data, dict):
                data = {}
            data["look"] = {"preset": look.preset, "intensity": look.intensity}
            ok, payload, status = self._gated_save(
                style_path, dump_yaml(data), label="bible/style.yaml")
            if ok:
                append_event(project.root, self.server.actor, "edit_bible",
                             {"file": "style",
                              "look": {"preset": look.preset, "intensity": look.intensity},
                              "via": "gui"})
        self._send_json(payload, status)

    def _act_edit_snap(self, body: dict[str, Any]) -> None:
        """Persist the /edit snapping (magnet) toggle — a per-USER preference in
        gui_state.json (Native Cut v2 §B, additive key). No project write, no
        event: switching snap never mutates the film."""
        from .userstate import set_snap_enabled

        enabled = bool(body.get("enabled"))
        set_snap_enabled(enabled)
        self._send_json({"ok": True, "enabled": enabled})

    def _act_edit_revert(self, body: dict[str, Any]) -> None:
        """Honest, git-backed tier-1 revert (Native Cut v2 §C): re-apply the value
        in effect BEFORE the event at ``index`` through the SAME engine endpoint
        the original edit used. The revert is ITSELF a normal event (undo is
        visible history, not erased history). File/history tiers are surfaced to
        the client (which routes them to the guarded /api/git/rollback-file).
        Media/takes are append-only and never reach here (plan_revert refuses)."""
        from . import edit
        from ..core.models import LookSpec, TransitionSpec
        from ..core.yamlio import dump_yaml

        project, actor = self.server.project, self.server.actor
        idx = body.get("index")
        try:
            index = int(idx)
        except (TypeError, ValueError):
            self._send_error_json("index is required (int)", 400)
            return
        try:
            plan = edit.plan_revert(project, index)
        except IndexError as exc:
            self._send_error_json(str(exc), 404)
            return
        except ValueError as exc:
            self._send_error_json(str(exc), 400)
            return
        ts = body.get("ts")
        if ts and plan.get("ts") and str(ts) != str(plan.get("ts")):
            self._send_error_json("撤销目标已过期(有新的改动),请刷新后重试", 409)
            return
        if plan.get("tier") != 1:
            # file / history tiers: the client confirms + calls rollback-file.
            self._send_json({"ok": True, **plan})
            return

        endpoint = plan["endpoint"]
        ap = plan["apply"]
        with self.server.quick_mutex:
            if endpoint in ("transition", "transition-override"):
                rules = project.load_rules()
                if endpoint == "transition":
                    rules.transition_default = TransitionSpec(
                        type=ap["type"], duration_ms=int(ap["duration_ms"]))
                    detail: dict[str, Any] = {"transition_default": {
                        "type": ap["type"], "duration_ms": int(ap["duration_ms"])}}
                else:
                    ov = dict(rules.transition_overrides or {})
                    shot = ap["shot"]
                    if ap.get("action") == "reset":
                        ov.pop(shot, None)
                        detail = {"transition_override": {"shot": shot, "reset": True}}
                    elif ap.get("cut"):
                        ov[shot] = None
                        detail = {"transition_override": {"shot": shot, "type": "cut"}}
                    else:
                        ov[shot] = TransitionSpec(
                            type=ap["type"], duration_ms=int(ap["duration_ms"])).model_dump()
                        detail = {"transition_override": {
                            "shot": shot, "type": ap["type"],
                            "duration_ms": int(ap["duration_ms"])}}
                    rules.transition_overrides = ov
                ok, payload, status = self._gated_save(
                    project.rules_path, dump_yaml(rules.model_dump()),
                    label="timeline/rules.yaml")
                if ok:
                    append_event(project.root, actor, "edit_rules",
                                 {**detail, "revert_of": index, "via": "gui"})
            elif endpoint == "look":
                from ..core.yamlio import read_yaml

                look = LookSpec(preset=ap["preset"], intensity=float(ap["intensity"]))
                style_path = project.root / "bible" / "style.yaml"
                data: Any = {}
                if style_path.exists():
                    try:
                        data = read_yaml(style_path) or {}
                    except Exception:
                        data = {}
                if not isinstance(data, dict):
                    data = {}
                data["look"] = {"preset": look.preset, "intensity": look.intensity}
                ok, payload, status = self._gated_save(
                    style_path, dump_yaml(data), label="bible/style.yaml")
                if ok:
                    append_event(project.root, actor, "edit_bible",
                                 {"file": "style",
                                  "look": {"preset": look.preset, "intensity": look.intensity},
                                  "revert_of": index, "via": "gui"})
            elif endpoint == "duration":
                from .edit import set_duration_in_text

                shot = ap["shot"]
                raw = ap.get("duration", "auto")
                valstr = "auto" if (raw == "auto" or raw is None) else str(raw)
                spath = project.shot_path(shot)
                if not spath.exists():
                    self._send_error_json(f"unknown shot: {shot}", 404)
                    return
                text = spath.read_text(encoding="utf-8")
                ok, payload, status = self._gated_save(
                    spath, set_duration_in_text(text, valstr), label=f"shots/{shot}.yaml")
                if ok:
                    append_event(project.root, actor, "edit_shot",
                                 {"shot": shot, "field": "duration", "duration": valstr,
                                  "revert_of": index, "via": "gui"})
            else:
                self._send_error_json(f"unsupported revert endpoint: {endpoint}", 400)
                return
        if isinstance(payload, dict) and status == 200:
            payload = {**payload, "reverted": plan.get("label", ""), "tier": 1}
        self._send_json(payload, status)

    # ---------------------------------------------------- frame / strip / card

    def _t_frame(self, query: dict[str, list[str]]) -> None:
        """One cached frame — either a strip file by basename (``?file=``) or a
        fresh single-frame grab of a served media at a timestamp
        (``?src=&ms=``). Backs the safe-area preview and the cover fine-tune."""
        from ..media.frames import extract_frame, frames_cache_dir

        project = self.server.project
        fname = query.get("file", [None])[0]
        if fname:
            if "/" in fname or "\\" in fname or ".." in fname or not fname.endswith(".jpg"):
                self._send_error_json("bad frame file", 400)
                return
            target = frames_cache_dir(project.root) / fname
            if not target.is_file():
                self._send_error_json("not found", 404)
                return
            self._serve_file(target)
            return
        src = query.get("src", [None])[0]
        if not src:
            self._send_error_json("src or file is required", 400)
            return
        abspath = self._resolve_served(src.lstrip("/"))
        if abspath is None:
            self._send_error_json("frame source not served", 403)
            return
        if not abspath.is_file():
            self._send_error_json("not found", 404)
            return
        try:
            at_ms = max(0, int(query.get("ms", ["0"])[0]))
        except ValueError:
            at_ms = 0
        try:
            width = int(query.get("w", ["0"])[0]) or None
        except ValueError:
            width = None
        from ..media.ffmpeg import MediaError

        try:
            dest = extract_frame(project, project.relpath(abspath), at_ms, width=width)
        except MediaError as exc:
            self._send_error_json(" ".join(str(exc).split()), 500)
            return
        self._serve_file(dest)

    def _t_strip(self, query: dict[str, list[str]]) -> None:
        """A scrub strip of the served media in ONE ffmpeg pass (frames.frame_strip,
        strip-preferred). Returns each thumbnail's approximate timestamp + a
        ``/frame?file=`` url so clicks map back to a frame_ms."""
        from urllib.parse import quote

        from ..media.frames import frame_strip
        from ..media.probe import probe_duration_ms

        project = self.server.project
        src = query.get("src", [None])[0]
        abspath = self._resolve_served(src.lstrip("/")) if src else None
        if abspath is None or not abspath.is_file():
            self._send_error_json("strip source not served", 404)
            return
        try:
            count = max(2, min(40, int(query.get("count", ["12"])[0])))
        except ValueError:
            count = 12
        from ..media.ffmpeg import MediaError

        try:
            paths = frame_strip(project, project.relpath(abspath), count=count)
        except MediaError as exc:
            self._send_error_json(" ".join(str(exc).split()), 500)
            return
        dur = probe_duration_ms(abspath) or 0
        frames = [
            {"ms": int(round(i * dur / count)) if dur else 0,
             "url": "/frame?file=" + quote(p.name)}
            for i, p in enumerate(paths)
        ]
        self._send_json({"duration_ms": dur, "count": count, "frames": frames})

    def _t_card_preview(self, query: dict[str, list[str]]) -> None:
        """Live card-preview PNG through the real card chain (cardprev.render_card_preview),
        cached in .manju/frames. Always 200s: a placeholder SVG when NEITHER
        renderer (Chromium / ffmpeg) is available."""
        from .cardprev import render_card_preview

        project = self.server.project
        text = (query.get("text", [""])[0] or "")[:400]
        subtext = (query.get("subtext", [""])[0] or "")[:400]
        template = query.get("template", ["chapter"])[0]
        if template not in ("chapter", "caption"):
            template = "chapter"
        dest, _hit = render_card_preview(project, text=text, subtext=subtext,
                                         template=template)
        if dest is None:
            import html as _html

            label = _html.escape(text or "card")
            svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="240" height="135">'
                   '<rect width="240" height="135" fill="#14161a"/>'
                   '<text x="120" y="68" fill="#8b93a3" font-size="12" '
                   f'text-anchor="middle">{label}</text></svg>')
            self._send_text(svg, "image/svg+xml")
            return
        self._serve_file(dest)

    # --------------------------------------------------------- subtitles write

    def _t_subtitles_save(self, body: dict[str, Any]) -> None:
        """Save the cue table into captions/captions.srt (§3 human truth).

        First edit in compiled mode requires an explicit confirm (needs_confirm)
        and then flips rules.captions.mode → manual, snapshotting the compiled
        captions into captions.generated.srt for comparison. Empty/inverted cues
        are rejected before any write; overlaps beyond the QC tolerance warn."""
        from ..core.yamlio import atomic_write_text
        from .captions_edit import (
            CaptionEditError,
            _timeline_cues,
            cues_to_srt,
            normalize_cues,
            validate_cues,
        )

        project, actor = self.server.project, self.server.actor
        try:
            cues = normalize_cues(body.get("cues"))
        except CaptionEditError as exc:
            self._send_error_json(str(exc), 400)
            return
        errors, warnings = validate_cues(cues)
        if errors:
            self._send_json({"ok": False, "errors": errors, "warnings": warnings}, 400)
            return
        confirm = bool(body.get("confirm_manual"))
        with self.server.quick_mutex:
            rules = project.load_rules()
            was_manual = rules.captions.mode == "manual"
            if not was_manual and not confirm:
                self._send_json({
                    "ok": False, "needs_confirm": True, "warnings": warnings,
                    "message": ("接管字幕为 manual:captions.srt 将成为人工truth,编译版本转存 "
                                "captions.generated.srt 用于对比;重建时 ASS 从人工字幕逐字重烧 "
                                "(= final 重渲染)。"),
                })
                return
            project.captions_dir.mkdir(parents=True, exist_ok=True)
            srt_path = project.captions_dir / "captions.srt"
            gen_path = project.captions_dir / "captions.generated.srt"
            flipped = False
            if not was_manual:
                # snapshot the compiled comparison BEFORE overwriting captions.srt
                if not gen_path.exists():
                    if srt_path.exists():
                        atomic_write_text(gen_path, srt_path.read_text(encoding="utf-8"))
                    else:
                        tcues = _timeline_cues(project)
                        if tcues:
                            atomic_write_text(gen_path, cues_to_srt(tcues))
                rules.captions.mode = "manual"
                project.save_rules(rules)
                flipped = True
            atomic_write_text(srt_path, cues_to_srt(cues))
            append_event(project.root, actor, "captions_edit",
                         {"cues": len(cues), "mode_flip": flipped, "mode": "manual",
                          "via": "gui"})
        self._send_json({
            "ok": True, "cues": len(cues), "flipped": flipped, "warnings": warnings,
            "rebuild": "captions.srt 已更新 — 运行 build 后 ASS 逐字重烧 = final 重渲染",
        })

    def _t_subtitles_revert(self, body: dict[str, Any]) -> None:
        """还原自动字幕: flip rules.captions.mode back to compiled and restore the
        compiled captions (from captions.generated.srt, else regenerate from the
        timeline, else drop captions.srt so the next build regenerates)."""
        from ..core.yamlio import atomic_write_text
        from .captions_edit import _timeline_cues, cues_to_srt

        project, actor = self.server.project, self.server.actor
        with self.server.quick_mutex:
            rules = project.load_rules()
            if rules.captions.mode != "manual":
                self._send_json({"ok": True, "restored": False,
                                 "message": "已是自动字幕 (already compiled)"})
                return
            rules.captions.mode = "compiled"
            project.save_rules(rules)
            srt_path = project.captions_dir / "captions.srt"
            gen_path = project.captions_dir / "captions.generated.srt"
            restored = False
            if gen_path.exists():
                atomic_write_text(srt_path, gen_path.read_text(encoding="utf-8"))
                restored = True
            else:
                tcues = _timeline_cues(project)
                if tcues:
                    atomic_write_text(srt_path, cues_to_srt(tcues))
                    restored = True
                else:
                    srt_path.unlink(missing_ok=True)  # next build regenerates
            append_event(project.root, actor, "captions_revert",
                         {"restored": restored, "mode": "compiled", "via": "gui"})
        self._send_json({
            "ok": True, "restored": restored,
            "message": "已还原自动字幕 — 运行 build 重新生成并重烧 (final 重渲染)",
        })

    # ------------------------------------------------------------- mixer write

    def _t_mixer_apply(self, body: dict[str, Any]) -> None:
        """apply_mixer verbatim: validate-through-models, write via the atomic
        save paths, ONE mixer event, and return the rebuild verdict
        (timeline/final/segments_restale)."""
        from ..build.mixer import MixerError, apply_mixer

        project, actor = self.server.project, self.server.actor
        changes = body.get("changes")
        if not isinstance(changes, dict):
            self._send_error_json("changes object is required", 400)
            return
        with self.server.quick_mutex:
            try:
                changes = self._resolve_mixer_sources(changes)
                result = apply_mixer(project, changes, actor=actor)
            except MixerError as exc:
                self._send_error_json(
                    "mixer 校验失败 (invalid): " + " ".join(str(exc).split()), 400)
                return
        self._send_json({"ok": True, **result})

    def _resolve_mixer_sources(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Resolve any ``lib:<hash8>`` source token to a real project path by
        copying the library asset into imports (reusing the lib-use copy), so a
        BGM/ambient/sfx source picked from the library becomes a normal human
        asset before apply_mixer sees it."""
        changes = dict(changes)
        for bed in ("music", "ambient"):
            b = changes.get(bed)
            if isinstance(b, dict) and "source" in b:
                b = dict(b)
                b["source"] = self._resolve_source_token(b["source"])
                changes[bed] = b
        if isinstance(changes.get("sfx"), list):
            new_sfx = []
            for s in changes["sfx"]:
                if isinstance(s, dict) and "source" in s:
                    s = dict(s)
                    s["source"] = self._resolve_source_token(s["source"])
                new_sfx.append(s)
            changes["sfx"] = new_sfx
        return changes

    def _resolve_source_token(self, token: Any) -> Any:
        import shutil

        from ..build.mixer import MixerError
        from ..core.library import Library, LibraryError

        if not isinstance(token, str) or not token.startswith("lib:"):
            return token
        hash8 = token[len("lib:"):]
        lib = Library()
        try:
            entry = lib.get(hash8)
        except LibraryError as exc:
            raise MixerError(f"library asset {hash8!r} not found") from exc
        blob = lib.blob_path(entry)
        if not blob.exists():
            raise MixerError(f"library blob missing: {entry['blob']}")
        project, actor = self.server.project, self.server.actor
        dest_dir = project.imports_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(entry["blob"]).suffix
        stem = Path(entry["name"]).stem or "asset"
        dest = dest_dir / f"{stem}{ext}"
        n = 2
        while dest.exists():
            dest = dest_dir / f"{stem}_{n}{ext}"
            n += 1
        shutil.copy2(blob, dest)
        rel = project.relpath(dest)
        append_event(project.root, actor, "lib_use",
                     {"hash": entry["hash"], "name": entry["name"],
                      "as": "imports", "dest": rel, "via": "gui-mixer"})
        return rel

    # --------------------------------------------------------- packaging write

    def _t_packaging_apply(self, body: dict[str, Any]) -> None:
        """Structured packaging edit: deep-merge the section patch onto the
        current spec, model-validate (surfacing the model validators inline),
        then the same check-gated verbatim write the S8a editor uses. Cover
        frame_ms + teaser window get final-length advisories."""
        from pydantic import ValidationError

        from ..core.models import PackagingSpec

        project, actor = self.server.project, self.server.actor
        patch = body.get("patch")
        if not isinstance(patch, dict):
            self._send_error_json("patch object is required", 400)
            return
        with self.server.quick_mutex:
            merged = _deep_merge_packaging(project.load_packaging().model_dump(), patch)
            try:
                spec = PackagingSpec.model_validate(merged)
            except ValidationError as exc:
                self._send_error_json(
                    "packaging 校验失败 (invalid): "
                    + " ".join(str(exc).split())[:500], 400)
                return
            import yaml as _yaml

            text = _yaml.safe_dump(spec.model_dump(), allow_unicode=True, sort_keys=False)
            ok, payload, status = self._gated_save(
                project.packaging_path, text, label="timeline/packaging.yaml")
            if not ok:
                self._send_json(payload, status)
                return
            warnings = _packaging_warnings(project, spec)
            append_event(project.root, actor, "edit_packaging",
                         {"keys": sorted(patch.keys()), "via": "gui"})
        self._send_json({
            "ok": True, "changed": sorted(patch.keys()), "warnings": warnings,
            "rebuild": "packaging.yaml 已更新 — 卡片/封面在 build/package 时生成",
        })

    # =================================================================
    # round-U 导演助手 /director (director_page.py): the six-step AI-director
    # loop as one page — propose → impact/cost(试跑) → 确认 → 执行 → diff →
    # 下一步建议. Every button calls the SAME build/director core the CLI/MCP
    # drive; 确认 and 执行 are DELIBERATELY separate POSTs, never one (§8.3
    # approve-before-execute). Isolated GET/POST region, same token/readonly/
    # host gates as every other surface (checked in do_GET/do_POST before us).
    # =================================================================

    def _director_get(self, path: str, url: Any) -> bool:
        """GET dispatch for the director page + its read endpoints. Returns True
        when handled, False so do_GET falls through to its 404."""
        from . import director_page

        if path == "/director.css":
            self._send_text(director_page.render_director_css(),
                            "text/css; charset=utf-8")
            return True
        if path == "/director.js":
            self._send_text(director_page.render_director_js(),
                            "application/javascript; charset=utf-8")
            return True
        if path in director_page.PAGE_PATHS_DIRECTOR:
            html_doc = director_page.render(path, self.server.project,
                                            self.server.token, parse_qs(url.query))
            self._send_text(html_doc, "text/html; charset=utf-8",
                            extra=self._PAGES_CSP)
            return True
        if path == "/api/director/state":
            self._send_json(director_page.proposals_payload(self.server.project))
            return True
        if path == "/api/director/suggest":
            from ..build.director import suggest_next

            self._send_json({"suggestions": [
                s.to_dict() for s in suggest_next(self.server.project)]})
            return True
        return False

    def _director_post(self, path: str, body: dict[str, Any]) -> bool:
        """POST dispatch for the director loop actions. Same token/readonly gates
        as every other mutating POST (checked in do_POST before us). propose /
        confirm / run are separate endpoints — the page never fuses confirm+run."""
        handler = {
            "/api/director/propose": self._act_director_propose,
            "/api/director/confirm": self._act_director_confirm,
            "/api/director/run": self._act_director_run,
            "/api/director/reject": self._act_director_reject,
        }.get(path)
        if handler is None:
            return False
        handler(body)
        return True

    def _act_director_propose(self, body: dict[str, Any]) -> None:
        from ..build.director import DirectorError, propose

        actions = body.get("actions")
        if not isinstance(actions, list) or not actions:
            self._send_error_json("actions must be a non-empty array", 400)
            return
        try:
            proposal = propose(self.server.project, actions,
                               why=str(body.get("why") or ""), actor=self.server.actor)
        except DirectorError as exc:
            self._send_error_json(" ".join(str(exc).split()), 400)
            return
        self._send_json({"ok": True, **proposal.model_dump()})

    def _act_director_confirm(self, body: dict[str, Any]) -> None:
        from ..build.director import DirectorError, confirm

        pid = str(body.get("id") or "")
        try:
            proposal = confirm(self.server.project, pid, actor=self.server.actor)
        except DirectorError as exc:
            self._send_error_json(" ".join(str(exc).split()), 409)
            return
        self._send_json({"ok": True, **proposal.model_dump()})

    def _act_director_run(self, body: dict[str, Any]) -> None:
        """Execute a confirmed proposal. Runs synchronously (the engine's own
        build/value locks serialize against any concurrent job); a phase callback
        would need a job handle, so the page shows a done-state on reload."""
        from ..build.director import DirectorError, execute

        pid = str(body.get("id") or "")
        try:
            outcome = execute(self.server.project, pid, actor=self.server.actor)
        except DirectorError as exc:
            self._send_error_json(" ".join(str(exc).split()), 409)
            return
        self._send_json(outcome.to_dict())

    def _act_director_reject(self, body: dict[str, Any]) -> None:
        from ..build.director import DirectorError, reject

        pid = str(body.get("id") or "")
        try:
            proposal = reject(self.server.project, pid, actor=self.server.actor)
        except DirectorError as exc:
            self._send_error_json(" ".join(str(exc).split()), 409)
            return
        self._send_json({"ok": True, **proposal.model_dump()})

    # =================================================================
    # round-V 创作 CREATE (goal item 2 GUI half): the creation funnel as a
    # product surface — the stage rail (build/funnel.funnel_status), the
    # current stage's textarea workspace over story/*.md, the skill modal, and
    # a read-only echo of the director's proposals (approve stays on /director).
    # Isolated GET/POST region, same token/readonly/host gates as every other
    # surface (checked in do_GET/do_POST before us). This page NEVER calls an
    # LLM and adds no engine machinery — a strict client of build/funnel.py +
    # the same story writes the CLI/agent make.
    # =================================================================

    def _create_get(self, path: str, url: Any) -> bool:
        """GET dispatch for /create + its static/read assets. Returns True when
        handled (response already sent), False so do_GET falls through to 404."""
        from . import create_page

        if path == "/create.css":
            self._send_text(create_page.render_create_css(),
                            "text/css; charset=utf-8")
            return True
        if path == "/create.js":
            self._send_text(create_page.render_create_js(),
                            "application/javascript; charset=utf-8")
            return True
        if path in create_page.PAGE_PATHS_CREATE:
            html_doc = create_page.render(path, self.server.project,
                                          self.server.token, parse_qs(url.query))
            self._send_text(html_doc, "text/html; charset=utf-8",
                            extra=self._PAGES_CSP)
            return True
        if path == "/api/create/state":
            self._send_json(create_page.create_payload(self.server.project))
            return True
        if path == "/api/create/skill":
            sid = (parse_qs(url.query).get("id") or [""])[0]
            try:
                self._send_json(
                    create_page.skill_payload(self.server.project, sid))
            except KeyError as exc:
                self._send_error_json(" ".join(str(exc).split()), 404)
            return True
        return False

    def _create_post(self, path: str, body: dict[str, Any]) -> bool:
        """POST dispatch for the 创作 funnel actions. Same token/readonly gates
        as every other mutating POST (checked in do_POST before us)."""
        handler = {
            "/api/create/save": self._act_create_save,
            "/api/create/scaffold": self._act_create_scaffold,
        }.get(path)
        if handler is None:
            return False
        handler(body)
        return True

    def _act_create_save(self, body: dict[str, Any]) -> None:
        """Write a pre-storyboard story file VERBATIM (atomic) + record an event
        — the same path-checked, allow-listed write captions_edit/bible editors
        use. The stage id keys a FIXED story-relpath map (from the funnel), so a
        path-escape attempt is simply an unknown stage; ``project.resolve`` is a
        second containment guard."""
        from ..core.yamlio import atomic_write_text
        from .create_page import stage_files

        stage = str(body.get("stage") or "")
        text = body.get("text")
        if not isinstance(text, str):
            self._send_error_json("text must be a string", 400)
            return
        relpath = stage_files().get(stage)
        if relpath is None:
            self._send_error_json(
                f"unknown stage {stage!r} — 只能编辑 {'/'.join(stage_files())}", 400)
            return
        project = self.server.project
        try:
            path = project.resolve(relpath)  # defense in depth: refuse escapes
        except ProjectError:
            self._send_error_json("path not served", 403)
            return
        with self.server.quick_mutex:
            created = not path.exists()
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, text if text.endswith("\n") else text + "\n")
            append_event(project.root, self.server.actor, "funnel_edit",
                         {"stage": stage, "path": relpath, "created": created,
                          "via": "gui"})
        self._send_json({"ok": True, "stage": stage, "path": relpath,
                         "created": created})

    def _act_create_scaffold(self, body: dict[str, Any]) -> None:
        """Scaffold a stage template — the SAME :func:`funnel.scaffold_stage`
        the CLI ``manju create <stage>`` calls (never a forked template)."""
        from ..build.funnel import FunnelError, scaffold_stage

        stage = str(body.get("stage") or "")
        force = bool(body.get("force"))
        try:
            result = scaffold_stage(self.server.project, stage, force=force,
                                    actor=self.server.actor)
        except FunnelError as exc:
            self._send_error_json(" ".join(str(exc).split()), 400)
            return
        self._send_json({"ok": True, **result})

    # =================================================================
    # round-X 批量入库 BATCH INGEST (workflow smoothness goal #1): drop a batch
    # of externally-produced files, classify them by filename convention onto
    # specific project steps (take/voice/ref/import), review, confirm. Own
    # dispatch region — the page + its static assets, a raw-byte upload route
    # (mirrors /api/upload's streaming discipline, landing in a disposable
    # per-batch staging dir since a browser cannot hand the engine a real
    # directory path), and the plan/apply actions. Every mutating action runs
    # through build/ingest.py — the SAME plan_ingest/apply_ingest `manju
    # ingest` uses, so the CLI and the GUI can never disagree on a
    # classification. apply runs on the jobs runner (serialized against
    # builds) since it can touch ffmpeg previews.
    # =================================================================

    def _ingest_stage_dir(self, batch: str) -> Path:
        return self.server.project.runtime_dir / "ingest-tmp" / batch

    def _ingest_get(self, path: str, url: Any) -> bool:
        """GET dispatch for the 批量入库 page + its static assets. Returns True
        when handled (response already sent), False so do_GET falls through."""
        from . import ingest_page

        if path == "/ingest.css":
            self._send_text(ingest_page.render_ingest_css(), "text/css; charset=utf-8")
            return True
        if path == "/ingest.js":
            self._send_text(ingest_page.render_ingest_js(),
                            "application/javascript; charset=utf-8")
            return True
        if path == ingest_page.PAGE_PATH:
            html_doc = ingest_page.render(self.server.project, self.server.token)
            self._send_text(html_doc, "text/html; charset=utf-8", extra=self._PAGES_CSP)
            return True
        return False

    def _ingest_post(self, path: str, body: dict[str, Any]) -> bool:
        """POST dispatch for the 批量入库 plan/apply actions. Same token/readonly
        gates as every other mutating POST (checked in do_POST before us) — the
        plan step is read-only on the PROJECT (it only reads the disposable
        staging dir) but stays behind the same gate as everything else here."""
        handler = {
            "/api/ingest/plan": self._act_ingest_plan,
            "/api/ingest/apply": self._act_ingest_apply,
        }.get(path)
        if handler is None:
            return False
        handler(body)
        return True

    def _act_ingest_upload(self, query: dict[str, list[str]]) -> None:
        """Stream a browser upload into ``batch``'s disposable staging dir —
        the ingest twin of ``_act_upload``/``_act_lib_upload``. Landed files
        are NOT project truth (nothing under media/ or bible/ moves here);
        they only become an ``IngestRow``'s source once ``/api/ingest/plan``
        classifies them, and only land for real on ``/api/ingest/apply``."""
        import uuid as _uuid

        batch = (query.get("batch", [""])[0] or "").strip()
        if not is_safe_segment(batch):
            self._send_error_json("upload needs a valid ?batch=<id>", 400)
            return
        raw_name = (query.get("name", [""])[0] or "").strip()
        raw_name = raw_name.replace("\\", "/").rsplit("/", 1)[-1]
        if not raw_name or raw_name.startswith("."):
            self._send_error_json("upload needs ?name=<filename>", 400)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._send_error_json("Content-Length required", 411)
            return
        if length > self._UPLOAD_MAX:
            self._send_error_json("file too large (max 4 GiB)", 413)
            return

        stage = self._ingest_stage_dir(batch)
        stage.mkdir(parents=True, exist_ok=True)
        dest = stage / raw_name  # staging is disposable scratch — re-selecting a
                                  # file just replaces its own staged copy
        tmp = stage / f".up-{_uuid.uuid4().hex}"
        received = 0
        try:
            with open(tmp, "wb") as f:
                while received < length:
                    chunk = self.rfile.read(min(1 << 20, length - received))
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
            if received != length:
                self._send_error_json("upload truncated", 400)
                return
            os.replace(tmp, dest)
            self._send_json({"ok": True, "batch": batch, "name": raw_name})
        finally:
            tmp.unlink(missing_ok=True)

    def _act_ingest_plan(self, body: dict[str, Any]) -> None:
        """Classify every file staged under ``batch`` — READ-ONLY, exactly
        ``build.ingest.plan_ingest`` run against the staging dir as its one
        directory argument (the same shape a CLI ``manju ingest <dir>`` call
        takes). Nothing moves; this only returns the plan."""
        from ..build.ingest import IngestError, plan_ingest

        project = self.server.project
        batch = str(body.get("batch") or "")
        if not is_safe_segment(batch):
            self._send_error_json("batch 不合法(仅字母数字下划线连字符)", 400)
            return
        stage = self._ingest_stage_dir(batch)
        if not stage.exists() or not any(stage.iterdir()):
            self._send_error_json("还没有已上传的文件 (batch 为空或不存在)", 400)
            return
        role = str(body.get("role") or "auto")
        shot = body.get("shot") or None
        try:
            plan = plan_ingest(project, [stage], role=role, shot=str(shot) if shot else None)
        except IngestError as exc:
            self._send_error_json(" ".join(str(exc).split()), 400)
            return
        self._send_json({"batch": batch, **plan.to_dict()})

    @staticmethod
    def _ingest_overrides_from_body(raw: Any, rows: list[Any]) -> dict[int, dict[str, Any]]:
        """``{"<row index>": {"action": "...", "id": "..."}}`` (the shape the
        per-row dropdown + id text box in ``/ingest.js`` posts) -> the
        ``build.ingest.apply_ingest`` override shape (``shot_id``/``asset_id``
        split by the EFFECTIVE action, so one text box works for both)."""
        overrides: dict[int, dict[str, Any]] = {}
        if not isinstance(raw, dict):
            return overrides
        for k, v in raw.items():
            try:
                idx = int(k)
            except (TypeError, ValueError):
                continue
            if not isinstance(v, dict) or not (0 <= idx < len(rows)):
                continue
            ov: dict[str, Any] = {}
            action = str(v["action"]) if v.get("action") else rows[idx].action
            if v.get("action"):
                ov["action"] = action
            row_id = v.get("id")
            if row_id:
                if action == "bible_ref":
                    ov["asset_id"] = str(row_id)
                elif action in ("take", "voice", "shot_ref"):
                    ov["shot_id"] = str(row_id)
            if ov:
                overrides[idx] = ov
        return overrides

    def _act_ingest_apply(self, body: dict[str, Any]) -> None:
        """Re-derive the plan from ``batch``'s staging dir (unchanged since
        ``/api/ingest/plan`` — planning is read-only), apply any per-row
        overrides from the reviewer, then execute through
        ``build.ingest.apply_ingest`` on the jobs runner (serialized against
        builds; an import row's preview generation shells out to ffmpeg).
        The staging dir is removed once the job finishes — its bytes are
        only ever a copy source, never itself project truth."""
        import shutil

        from ..build.ingest import IngestError, apply_ingest, plan_ingest

        project, actor = self.server.project, self.server.actor
        batch = str(body.get("batch") or "")
        if not is_safe_segment(batch):
            self._send_error_json("batch 不合法(仅字母数字下划线连字符)", 400)
            return
        stage = self._ingest_stage_dir(batch)
        if not stage.exists() or not any(stage.iterdir()):
            self._send_error_json("还没有已上传的文件 (batch 为空或不存在)", 400)
            return
        role = str(body.get("role") or "auto")
        shot = body.get("shot") or None
        raw_overrides = body.get("overrides")
        if raw_overrides is not None and not isinstance(raw_overrides, dict):
            self._send_error_json("overrides must be an object", 400)
            return
        try:
            plan = plan_ingest(project, [stage], role=role, shot=str(shot) if shot else None)
        except IngestError as exc:
            self._send_error_json(" ".join(str(exc).split()), 400)
            return
        overrides = self._ingest_overrides_from_body(raw_overrides, plan.rows)

        def fn(job) -> dict[str, Any]:
            result = apply_ingest(project, plan, actor=actor, overrides=overrides)
            shutil.rmtree(stage, ignore_errors=True)
            return {**plan.to_dict(), **result.to_dict()}

        job = self.server.runner.submit("ingest", {"batch": batch, "rows": len(plan.rows)}, fn)
        self._send_json({"job": job.to_dict()}, 202)
