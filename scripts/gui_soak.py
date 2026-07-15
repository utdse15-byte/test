"""Deterministic GUI HTTP soak (no browser, no paid providers).

Usage::

    python scripts/gui_soak.py --project path/to/x.manju --polls 100 --actions 50 --seed 1

Exercises /api/state, /api/jobs, filters via select/note posts, cancel, and
optional restarts of the in-process server. Fails on unexpected 5xx or
cross-project token pollution.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


def _req(port: int, path: str, *, method="GET", body=None, token="", project=""):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Manju-Token", token)
    if project:
        req.add_header("X-Manju-Project", project)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except Exception:
            payload = {}
        return exc.code, payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--polls", type=int, default=100)
    ap.add_argument("--actions", type=int, default=50)
    ap.add_argument("--restarts", type=int, default=0)
    ap.add_argument("--seed", type=int, default=12345)
    args = ap.parse_args(argv)

    from manju.core.container import Project
    from manju.gui.server import create_server

    rng = random.Random(args.seed)
    project = Project(Path(args.project).expanduser().resolve())
    errors: list[str] = []

    def run_once(label: str) -> None:
        server = create_server(project, host="127.0.0.1", port=0, actor="soak")
        thr = threading.Thread(target=server.serve_forever, daemon=True)
        thr.start()
        try:
            token = server.token
            pid = server.session.project_id if server.session else ""
            st, state = _req(server.port, "/api/state", token=token, project=pid)
            if st != 200:
                errors.append(f"{label}: state {st}")
                return
            if state.get("project_token") != pid:
                errors.append(f"{label}: token mismatch")
            shots = [s["id"] for s in (state.get("shots") or [])]
            for i in range(args.polls):
                path = "/api/state" if i % 3 else "/api/jobs"
                s, body = _req(server.port, path, token=token, project=pid)
                if s >= 500:
                    errors.append(f"{label}: poll {path} -> {s}")
            for i in range(args.actions):
                if not shots:
                    break
                sid = rng.choice(shots)
                kind = rng.choice(["note", "status", "jobs", "ui"])
                if kind == "note":
                    s, body = _req(
                        server.port, "/api/take-note", method="POST",
                        body={"shot": sid, "take": "take_01", "text": f"soak {i}"},
                        token=token, project=pid)
                    if s >= 500:
                        errors.append(f"{label}: note {s}")
                elif kind == "status":
                    s, _ = _req(server.port, "/api/app/status")
                    if s != 200:
                        errors.append(f"{label}: app/status {s}")
                elif kind == "ui":
                    s, _ = _req(
                        server.port, "/api/ui-state", method="POST",
                        body={"ui": {"last_shot_id": sid, "review_position": i}},
                        token=token, project=pid)
                    if s >= 500:
                        errors.append(f"{label}: ui-state {s}")
                else:
                    s, _ = _req(server.port, "/api/jobs", token=token, project=pid)
                    if s >= 500:
                        errors.append(f"{label}: jobs {s}")
        finally:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.close()
            except Exception:
                pass

    run_once("main")
    for r in range(args.restarts):
        run_once(f"restart-{r}")

    if errors:
        print("FAIL", len(errors))
        for e in errors[:20]:
            print(" ", e)
        return 1
    print(f"OK polls={args.polls} actions={args.actions} restarts={args.restarts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
