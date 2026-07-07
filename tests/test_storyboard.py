"""分镜工作台 Storyboard workspace — the approval model + the table page (round-U).

Three layers, ffmpeg-free (fake take media, no probing):

- APPROVAL MODEL (:class:`manju.core.models.ShotStatus`): the additive three-state
  ``review`` is byte-stable — setting review/approved changes NO spec_hash and
  never restages a FRESH take (status is excluded from ``spec_payload``); writing
  ``review`` syncs the legacy ``approved`` bool.
- PAGE (``/storyboard``): every REPORTS §2 column rendered over real HTTP,
  alias-aware character chips, the CSP header, unlock deliberately absent.
- ACTIONS: inline edit (writes + event), the lock 409 refusal, the approval
  cycle, batch approve, batch lock, and the token/readonly POST guards.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from manju.build.stale import ShotState, evaluate_all
from manju.core.events import tail_events
from manju.core.models import ShotStatus
from manju.core.spec import compute_spec_hash, spec_payload
from manju.core.yamlio import write_yaml
from manju.gui.server import create_server
from manju.gui.storyboard import lock_conflict


# =============================================================== APPROVAL MODEL


def test_review_state_property():
    assert ShotStatus().review_state == "needs_review"          # legacy default
    assert ShotStatus(approved=True).review_state == "approved"  # legacy bool
    assert ShotStatus(review="in_progress").review_state == "in_progress"
    # explicit review wins over a (stale) legacy bool
    assert ShotStatus(review="needs_review", approved=True).review_state == "needs_review"


def test_review_validator_rejects_unknown():
    with pytest.raises(ValueError):
        ShotStatus(review="bogus")


def test_review_absent_is_byte_stable(tmp_project, add_shot):
    """A shot that never sets review dumps byte-identically (exclude_none)."""
    add_shot(tmp_project, "S001")
    before = tmp_project.shot_path("S001").read_text(encoding="utf-8")
    # a model round-trip must not introduce a review: null line
    shot = tmp_project.load_shot("S001")
    tmp_project.save_shot(shot)
    assert "review" not in tmp_project.shot_path("S001").read_text(encoding="utf-8")
    assert before  # sanity


def test_approval_does_not_change_spec_hash(tmp_project, add_shot):
    """PIN: status (review + approved) is not in spec_payload, so approving a
    shot cannot restage its picture take."""
    add_shot(tmp_project, "S001")
    bible = tmp_project.load_bible()
    h0 = compute_spec_hash(tmp_project.load_shot("S001"), bible)
    payload0 = spec_payload(tmp_project.load_shot("S001"), bible)
    assert "status" not in payload0  # status is excluded, full stop

    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("status", {}).update({"review": "approved", "approved": True}))
    h1 = compute_spec_hash(tmp_project.load_shot("S001"), bible)
    assert h1 == h0
    # cycling review again is still hash-inert
    tmp_project.update_shot_raw(
        "S001", lambda d: d["status"].update({"review": "in_progress", "approved": False}))
    assert compute_spec_hash(tmp_project.load_shot("S001"), bible) == h0


def test_review_does_not_restage_fresh_take(tmp_project, add_shot, make_take):
    """A FRESH selected take stays FRESH after the shot is approved — approval
    never marks a take stale."""
    add_shot(tmp_project, "S001")
    h = compute_spec_hash(tmp_project.load_shot("S001"), tmp_project.load_bible())
    take = make_take(tmp_project, "S001", h)
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name))
    assert {s.shot_id: s for s in evaluate_all(tmp_project)}["S001"].state is ShotState.FRESH

    tmp_project.update_shot_raw(
        "S001", lambda d: d["status"].update({"review": "approved", "approved": True}))
    st = {s.shot_id: s for s in evaluate_all(tmp_project)}["S001"]
    assert st.state is ShotState.FRESH  # NOT stale


# =============================================================== lock helper


def test_lock_conflict_prefix_rules():
    assert lock_conflict({"action.main": "h"}, "action.main") == "action.main"
    assert lock_conflict({"action": "h"}, "action.main") == "action"          # parent
    assert lock_conflict({"quality.must_show": "h"}, "quality.must_show") == "quality.must_show"
    assert lock_conflict({"camera": "h"}, "action.main") is None              # unrelated
    assert lock_conflict({}, "dialogue.text") is None


# =============================================================== HTTP harness


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _req(server, path, *, method="GET", body=None, headers=None, raw=False):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            return resp.status, dict(resp.headers), (payload if raw else json.loads(payload or b"{}"))
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = payload if raw else json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = payload
        return exc.code, dict(exc.headers), parsed


def _post(server, path, body, token="__use__"):
    tok = server.token if token == "__use__" else token
    headers = {} if tok is None else {"X-Manju-Token": tok}
    return _req(server, path, method="POST", body=body, headers=headers)


# =============================================================== PAGE render


def test_page_renders_all_columns(gui, add_shot):
    add_shot(gui.project, "S001",
             camera={"shot_size": "close_up", "movement": "static"},
             quality={"must_show": ["旧手表"], "avoid": ["字幕穿帮"]})
    status, headers, html = _req(gui, "/storyboard", raw=True)
    assert status == 200
    assert "Content-Security-Policy" in headers
    text = html.decode("utf-8")
    for col in ("分镜工作台", "Shot#", "场景", "角色", "动作/描述", "台词",
                "镜头", "必须出现", "避免", "来源", "状态", "锁", "审批"):
        assert col in text, col
    assert 'href="/storyboard"' in text  # nav link, active
    assert "S001" in text and "旧手表" in text and "字幕穿帮" in text
    assert "close_up" in text and "static" in text
    assert "便利店" in text  # scene name resolved from the bible
    # the detail drawer carries the full spec + is toggleable
    assert "sb-detail" in text and "完整规格" in text
    # static assets serve
    assert _req(gui, "/storyboard.css", raw=True)[0] == 200
    assert _req(gui, "/storyboard.js", raw=True)[0] == 200


def test_empty_project_page(gui):
    text = _req(gui, "/storyboard", raw=True)[2].decode("utf-8")
    assert "分镜工作台" in text and "还没有镜头" in text


def test_char_chips_resolve_aliases(gui, add_shot):
    write_yaml(gui.project.root / "bible" / "characters.yaml",
               {"linxia": {"name": "林夏", "aliases": ["阿夏", "夏"]},
                "qiao": {"name": "老乔"}})
    # the shot registers the character by an ALIAS, and @mentions an unregistered one
    add_shot(gui.project, "S001", characters=["阿夏"],
             action={"main": "林夏推门,@qiao 在柜台后"})
    text = _req(gui, "/storyboard", raw=True)[2].decode("utf-8")
    assert "林夏" in text                 # alias 阿夏 resolved to the canonical name
    assert "别名 阿夏" in text            # tooltip explains the alias resolution
    assert "sb-hollow" in text           # @qiao → hollow (unregistered) chip
    assert "老乔" in text                # its canonical name
    assert "manju mentions --apply" in text  # the registration hint


def test_state_and_approval_chips_orthogonal(gui, add_shot):
    add_shot(gui.project, "S001", status={"review": "in_progress"})
    text = _req(gui, "/storyboard", raw=True)[2].decode("utf-8")
    assert "无版本" in text   # take-state (no take yet) chip
    assert "进行中" in text   # approval chip, separate word


# =============================================================== unlock absent


def test_unlock_absent_from_page(gui, add_shot):
    add_shot(gui.project, "S001")
    assert _post(gui, "/api/lock", {"shot": "S001", "field": "dialogue.text"})[0] == 200
    text = _req(gui, "/storyboard", raw=True)[2].decode("utf-8")
    assert "🔒" in text                              # the lock is shown
    assert "解锁请用命令行 manju unlock" in text      # containment tooltip
    assert "sb-unlock" not in text                    # NO unlock control class
    assert "storyboard/unlock" not in text            # NO unlock endpoint referenced
    # and there is no unlock endpoint at all
    assert _post(gui, "/api/storyboard/unlock", {"shot": "S001", "field": "dialogue.text"})[0] == 404
    assert _post(gui, "/api/unlock", {"shot": "S001", "field": "dialogue.text"})[0] == 404


# =============================================================== inline edit


def test_inline_edit_writes_and_events(gui, add_shot):
    add_shot(gui.project, "S001", action={"main": "旧动作"})
    status, _, data = _post(gui, "/api/storyboard/edit",
                            {"shot": "S001", "field": "action.main", "value": "林夏冲进门"})
    assert status == 200 and data["ok"]
    assert gui.project.load_shot("S001").action.main == "林夏冲进门"
    ev = [e for e in tail_events(gui.project.root, 20) if e["action"] == "edit_shot"]
    assert ev and ev[-1]["actor"] == "human"
    assert ev[-1]["detail"]["field"] == "action.main" and ev[-1]["detail"]["via"] == "gui"


def test_inline_edit_list_field(gui, add_shot):
    add_shot(gui.project, "S001")
    status, _, data = _post(gui, "/api/storyboard/edit",
                            {"shot": "S001", "field": "quality.must_show",
                             "value": "红伞\n旧手表\n  \n霓虹灯"})
    assert status == 200 and data["ok"]
    # blank lines dropped, entries stripped
    assert gui.project.load_shot("S001").quality.must_show == ["红伞", "旧手表", "霓虹灯"]


def test_inline_edit_dialogue(gui, add_shot):
    add_shot(gui.project, "S001")
    _post(gui, "/api/storyboard/edit",
          {"shot": "S001", "field": "dialogue.text", "value": "你终于来了。"})
    shot = gui.project.load_shot("S001")
    assert shot.dialogue.text == "你终于来了。"
    assert shot.dialogue.speaker == "linxia"  # sibling field preserved


def test_inline_edit_rejects_unknown_field(gui, add_shot):
    add_shot(gui.project, "S001")
    status, _, data = _post(gui, "/api/storyboard/edit",
                            {"shot": "S001", "field": "camera.shot_size", "value": "wide"})
    assert status == 400 and "inline-editable" in data["error"]


def test_inline_edit_locked_field_409(gui, add_shot):
    add_shot(gui.project, "S001", action={"main": "封印动作"})
    assert _post(gui, "/api/lock", {"shot": "S001", "field": "action.main"})[0] == 200
    status, _, data = _post(gui, "/api/storyboard/edit",
                            {"shot": "S001", "field": "action.main", "value": "改一改"})
    assert status == 409
    assert "锁定" in data["error"] and "action.main" in data["error"]
    # the locked value is untouched
    assert gui.project.load_shot("S001").action.main == "封印动作"


def test_inline_edit_parent_lock_409(gui, add_shot):
    """Locking the whole ``action`` block also refuses an ``action.main`` edit."""
    add_shot(gui.project, "S001", action={"main": "a", "emotion": "冷"})
    assert _post(gui, "/api/lock", {"shot": "S001", "field": "action"})[0] == 200
    status, _, data = _post(gui, "/api/storyboard/edit",
                            {"shot": "S001", "field": "action.main", "value": "b"})
    assert status == 409 and "action" in data["error"]


# =============================================================== approval cycle


def test_approve_syncs_legacy_bool(gui, add_shot):
    add_shot(gui.project, "S001")
    status, _, data = _post(gui, "/api/storyboard/approve",
                            {"shot": "S001", "review": "approved"})
    assert status == 200 and data["ok"] and data["changed"] == 1
    shot = gui.project.load_shot("S001")
    assert shot.status.review == "approved" and shot.status.approved is True

    # cycling back to needs_review clears the legacy bool
    _post(gui, "/api/storyboard/approve", {"shot": "S001", "review": "needs_review"})
    shot = gui.project.load_shot("S001")
    assert shot.status.review == "needs_review" and shot.status.approved is False

    ev = [e for e in tail_events(gui.project.root, 20) if e["action"] == "approve"]
    assert ev and ev[-1]["detail"].get("via") == "gui" and ev[-1]["detail"]["shot"] == "S001"


def test_approve_rejects_bad_state(gui, add_shot):
    add_shot(gui.project, "S001")
    status, _, data = _post(gui, "/api/storyboard/approve",
                            {"shot": "S001", "review": "loved_it"})
    assert status == 400 and "review" in data["error"]


# =============================================================== batch ops


def test_batch_approve(gui, add_shot):
    for sid in ("S001", "S002", "S003"):
        add_shot(gui.project, sid)
    status, _, data = _post(gui, "/api/storyboard/approve",
                            {"shots": ["S001", "S002", "S003"], "review": "approved"})
    assert status == 200 and data["changed"] == 3
    for sid in ("S001", "S002", "S003"):
        s = gui.project.load_shot(sid).status
        assert s.review == "approved" and s.approved is True
    ev = [e for e in tail_events(gui.project.root, 20) if e["action"] == "approve"]
    assert ev and set(ev[-1]["detail"]["shots"]) == {"S001", "S002", "S003"}


def test_batch_approve_skips_missing(gui, add_shot):
    add_shot(gui.project, "S001")
    status, _, data = _post(gui, "/api/storyboard/approve",
                            {"shots": ["S001", "NOPE"], "review": "approved"})
    assert status == 200 and data["changed"] == 1
    assert data["skipped"] and data["skipped"][0]["shot"] == "NOPE"


def test_batch_lock(gui, add_shot):
    add_shot(gui.project, "S001", dialogue={"speaker": "linxia", "text": "一"})
    add_shot(gui.project, "S002", dialogue={"speaker": "linxia", "text": "二"})
    status, _, data = _post(gui, "/api/storyboard/lock-batch",
                            {"shots": ["S001", "S002"], "field": "dialogue.text"})
    assert status == 200 and data["locked"] == 2
    for sid in ("S001", "S002"):
        assert "dialogue.text" in gui.project.load_shot(sid).locked
    ev = [e for e in tail_events(gui.project.root, 20) if e["action"] == "lock"]
    assert ev and ev[-1]["detail"]["via"] == "gui"
    # re-locking an already-locked field is a per-shot skip, never an error
    status, _, data = _post(gui, "/api/storyboard/lock-batch",
                            {"shots": ["S001"], "field": "dialogue.text"})
    assert status == 200 and data["locked"] == 0 and data["skipped"]


def test_batch_lock_seal_verifies(gui, add_shot):
    """A batch-sealed field is a REAL value-hash lock: tampering trips check."""
    from manju.core.check import run_check

    add_shot(gui.project, "S001", dialogue={"speaker": "linxia", "text": "原句"})
    _post(gui, "/api/storyboard/lock-batch", {"shots": ["S001"], "field": "dialogue.text"})
    assert run_check(gui.project).ok  # sealed with the current value → clean
    # tamper directly on disk → the lock must fire
    gui.project.update_shot_raw(
        "S001", lambda d: d["dialogue"].__setitem__("text", "被人偷改了"))
    report = run_check(gui.project)
    assert not report.ok


# =============================================================== guards


def test_post_requires_token(gui, add_shot):
    add_shot(gui.project, "S001")
    for path, body in (
        ("/api/storyboard/edit", {"shot": "S001", "field": "action.main", "value": "x"}),
        ("/api/storyboard/approve", {"shot": "S001", "review": "approved"}),
        ("/api/storyboard/lock-batch", {"shots": ["S001"], "field": "dialogue.text"}),
    ):
        status, _, data = _post(gui, path, body, token=None)
        assert status == 403 and "Token" in data["error"]
        # and the wrong token is refused too
        assert _post(gui, path, body, token="wrong")[0] == 403


def test_post_readonly_forbidden(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    server = create_server(tmp_project, host="127.0.0.1", port=0,
                           actor="human", readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for path, body in (
            ("/api/storyboard/edit", {"shot": "S001", "field": "action.main", "value": "x"}),
            ("/api/storyboard/approve", {"shot": "S001", "review": "approved"}),
        ):
            status, _, data = _post(server, path, body)
            assert status == 403 and "readonly" in data["error"]
        # readonly does not write
        assert tmp_project.load_shot("S001").status.review is None
        assert tmp_project.load_shot("S001").action.main != "x"
    finally:
        server.shutdown()
        server.close()
