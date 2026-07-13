"""Wave 3 (MANJU_WINDOWS_ONLY_LEAN_V3 §5.5) — structured, media-bound review
annotations: the ONE new public schema the plan budgets
(``manju.review.annotation/v1``, owner ``manju.core.models``).

Covered here, test-first:

  * the :class:`Annotation` model's validation surface (severity enum, text
    caps, range ordering, safe id, media-hash forms, geometry kind);
  * OLD-SHOT BYTE-IDENTITY — a shot yaml written before annotations existed,
    loaded and re-saved through the model, is byte-for-byte identical (the
    ``annotations`` field truly disappears when empty);
  * the board server's ``annotate`` action over the real HTTP harness
    (test_board_serve's fixture pattern): happy path lands in the shot yaml +
    events.jsonl, 403 without the token, 400 on bad input, CAS refusal on a
    stale ``expected_rev``;
  * serve-mode rendering: the annotation list + click-to-seek ``data-seek`` +
    per-take add form, the STALE badge when the stored media_sha256 no longer
    matches the take file (takes are append-only ⇒ the take was replaced), and
    the EXISTING static-board byte pin (no serve veneer leaks into it);
  * the CONTRACTS.yaml registration (experimental, read_older).
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import threading
from typing import Callable, Iterator

import httpx
import pytest
from pydantic import ValidationError

from manju.board import board as bd
from manju.board.server import API_ACTIONS, make_server
from manju.core import contracts
from manju.core.container import Project
from manju.core.events import tail_events
from manju.core.hashing import hash_file
from manju.core.models import ANNOTATION_SCHEMA, Annotation, ShotSpec

# ------------------------------------------------------------- helpers/fixtures
# (the test_board_serve harness pattern: a live server on an ephemeral port,
# every mutating POST carrying the per-run token)


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


def _post(server, url, **kw):
    headers = kw.pop("headers", {})
    headers.setdefault("X-Manju-Token", server.token)
    return httpx.post(url, headers=headers, **kw)


class _FrozenDatetime:
    """Deterministic ``now`` so static-board timestamps match across renders."""

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        return _dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=_dt.timezone.utc)


@pytest.fixture
def ann_project(tmp_project: Project, add_shot: Callable, make_take: Callable) -> Project:
    """A project with one shot, two takes, take_01 selected."""
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "manual")  # take_01
    make_take(tmp_project, "S001", "manual")  # take_02
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", "take_01")
    )
    return tmp_project


def _ann(**overrides) -> dict:
    """A valid annotation payload; overrides flip single fields per test."""
    data = {
        "id": "ann_0001",
        "take": "take_01",
        "media_sha256": "sha256:" + "ab" * 32,
        "frame_rate": "24",
        "text": "构图偏了 framing is off",
        "created_at": "2026-07-13T00:00:00+00:00",
    }
    data.update(overrides)
    return data


# ------------------------------------------------------------------- the model


def test_annotation_model_happy_path_and_defaults():
    ann = Annotation.model_validate(_ann(frame=12, subject="手部 hands"))
    assert ann.severity == "note" and ann.actor == "human"
    assert ann.frame == 12 and ann.range_frames is None
    # None-valued optionals disappear from the stored dict form
    dumped = ann.model_dump(exclude_none=True)
    assert "range_frames" not in dumped and "geometry" not in dumped
    assert "repair_variable" not in dumped
    assert ANNOTATION_SCHEMA == "manju.review.annotation/v1"


def test_annotation_bad_severity_rejected():
    with pytest.raises(ValidationError):
        Annotation.model_validate(_ann(severity="nitpick"))
    for ok in ("note", "issue", "blocker"):
        assert Annotation.model_validate(_ann(severity=ok)).severity == ok


def test_annotation_text_caps():
    with pytest.raises(ValidationError):
        Annotation.model_validate(_ann(text=""))
    with pytest.raises(ValidationError):
        Annotation.model_validate(_ann(text="   "))
    with pytest.raises(ValidationError):
        Annotation.model_validate(_ann(text="x" * 2001))
    assert Annotation.model_validate(_ann(text="x" * 2000)).text == "x" * 2000


def test_annotation_subject_cap():
    with pytest.raises(ValidationError):
        Annotation.model_validate(_ann(subject="s" * 201))
    assert Annotation.model_validate(_ann(subject="s" * 200)).subject == "s" * 200


def test_annotation_range_rules():
    with pytest.raises(ValidationError):  # reversed
        Annotation.model_validate(_ann(range_frames=[9, 3]))
    with pytest.raises(ValidationError):  # not exactly two
        Annotation.model_validate(_ann(range_frames=[3]))
    with pytest.raises(ValidationError):  # negative
        Annotation.model_validate(_ann(range_frames=[-1, 3]))
    assert Annotation.model_validate(_ann(range_frames=[3, 9])).range_frames == [3, 9]
    assert Annotation.model_validate(_ann(range_frames=[4, 4])).range_frames == [4, 4]


def test_annotation_frame_non_negative():
    with pytest.raises(ValidationError):
        Annotation.model_validate(_ann(frame=-1))
    assert Annotation.model_validate(_ann(frame=0)).frame == 0


def test_annotation_unsafe_id_rejected():
    for bad in ("../etc", "a/b", "", "x" * 65, "a b"):
        with pytest.raises(ValidationError):
            Annotation.model_validate(_ann(id=bad))
    assert Annotation.model_validate(_ann(id="ann_ok-1")).id == "ann_ok-1"


def test_annotation_media_hash_forms():
    hexd = "0f" * 32
    assert Annotation.model_validate(_ann(media_sha256=hexd)).media_sha256 == hexd
    prefixed = "sha256:" + hexd
    assert Annotation.model_validate(_ann(media_sha256=prefixed)).media_sha256 == prefixed
    for bad in ("", "manual", "sha256:zz", "sha256:" + "0f" * 16):
        with pytest.raises(ValidationError):
            Annotation.model_validate(_ann(media_sha256=bad))
    # bare vs prefixed forms of the SAME digest match each other
    ann = Annotation.model_validate(_ann(media_sha256=hexd))
    assert ann.matches_media(prefixed) and ann.matches_media(hexd)
    assert not ann.matches_media("sha256:" + "ee" * 32)
    assert not ann.matches_media(None)  # media gone ⇒ never a match


def test_annotation_frame_rate_string_forms():
    assert Annotation.model_validate(_ann(frame_rate="24000/1001")).frame_rate == "24000/1001"
    for bad in ("", "24.0", "0", "24/0", "fast"):
        with pytest.raises(ValidationError):
            Annotation.model_validate(_ann(frame_rate=bad))


def test_annotation_geometry_kind_validated_when_present():
    ok = Annotation.model_validate(_ann(geometry={"kind": "rect", "x": 0.1, "y": 0.2,
                                                  "w": 0.3, "h": 0.4}))
    assert ok.geometry["kind"] == "rect"
    assert Annotation.model_validate(_ann(geometry={"note": "free"})).geometry == {"note": "free"}
    with pytest.raises(ValidationError):
        Annotation.model_validate(_ann(geometry={"kind": "circle"}))


def test_annotation_carries_repair_variable_verbatim():
    ann = Annotation.model_validate(_ann(repair_variable="post_grade"))
    assert ann.repair_variable == "post_grade"
    assert ann.model_dump(exclude_none=True)["repair_variable"] == "post_grade"


# --------------------------------------------------------- old-shot byte identity


def test_old_shot_without_annotations_round_trips_byte_identical(tmp_project, add_shot):
    """A shot saved WITHOUT annotations (incl. populated review/take_notes —
    the exclude-when-empty neighbours) must reload + re-save byte-identically:
    the annotations field truly disappears when empty."""
    add_shot(tmp_project, "S001", status={
        "selected_take": "take_01", "approved": False,
        "review": "in_progress", "take_notes": {"take_01": "好"},
    })
    before = tmp_project.shot_path("S001").read_text(encoding="utf-8")
    assert "annotations" not in before

    loaded = tmp_project.load_shot("S001")
    assert loaded.status.annotations == []  # the field exists, empty
    tmp_project.save_shot(loaded)
    after = tmp_project.shot_path("S001").read_text(encoding="utf-8")
    assert after == before
    # and the dict form drops the key entirely, not just the YAML writer
    assert "annotations" not in loaded.status.model_dump(exclude_none=True)
    assert "annotations" not in loaded.model_dump(exclude_none=True)["status"]


def test_non_empty_annotations_do_serialize(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    shot = tmp_project.load_shot("S001")
    shot.status.annotations = [Annotation.model_validate(_ann())]
    dumped = shot.status.model_dump(exclude_none=True)
    assert dumped["annotations"][0]["id"] == "ann_0001"
    tmp_project.save_shot(shot)
    assert "annotations" in tmp_project.shot_path("S001").read_text(encoding="utf-8")


# ------------------------------------------------------------ the registry row


def test_annotation_schema_registered_experimental():
    entry = contracts.entry(ANNOTATION_SCHEMA)
    assert entry["status"] == "experimental"
    assert entry["owner"] == "manju.core.models"
    assert entry["read_older"] is True and entry["write_older"] is False
    assert entry["latest_version"] == 1


# --------------------------------------------------------- POST /api/annotate


def test_annotate_happy_path_writes_shot_and_event(ann_project, monkeypatch):
    monkeypatch.setenv("MANJU_ACTOR", "director")
    with running(ann_project) as (base, server):
        r = _post(server, base + "/api/annotate", json={
            "shot": "S001", "take": "take_02", "text": " 手抖了 blur ",
            "severity": "issue", "frame": 12, "subject": "手部 hands",
            "repair_variable": "post_grade",
        })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["stale"] is False and body["id"]

    raw = ann_project.load_shot_raw("S001")
    anns = raw["status"]["annotations"]
    assert len(anns) == 1
    a = anns[0]
    take = ann_project.get_take("S001", "take_02")
    assert a["media_sha256"] == hash_file(take.media_path)          # media-bound
    assert a["frame_rate"] == str(ann_project.edit_rate())          # exact rational string
    assert a["take"] == "take_02" and a["frame"] == 12
    assert a["severity"] == "issue" and a["text"] == "手抖了 blur"   # stripped
    assert a["subject"] == "手部 hands"
    assert a["repair_variable"] == "post_grade"
    assert a["actor"] == "director" and a["created_at"]
    assert a["id"] == body["id"]
    # the whole shot still validates through the model (round-trips)
    shot = ann_project.load_shot("S001")
    assert shot.status.annotations[0].id == body["id"]

    last = tail_events(ann_project.root, n=1)[0]
    assert last["action"] == "annotate" and last["actor"] == "director"
    assert last["detail"] == {"shot": "S001", "take": "take_02", "id": body["id"],
                              "severity": "issue", "via": "board"}


def test_annotate_range_form(ann_project):
    with running(ann_project) as (base, server):
        r = _post(server, base + "/api/annotate", json={
            "shot": "S001", "take": "take_01", "text": "整段偏暗 too dark",
            "range": [3, 9],
        })
    assert r.status_code == 200 and r.json()["ok"] is True
    a = ann_project.load_shot_raw("S001")["status"]["annotations"][0]
    assert a["range_frames"] == [3, 9] and "frame" not in a


def test_annotate_without_token_is_403_and_writes_nothing(ann_project):
    with running(ann_project) as (base, _server):
        r = httpx.post(base + "/api/annotate",
                       json={"shot": "S001", "take": "take_01", "text": "x"})
    assert r.status_code == 403
    assert "annotations" not in (ann_project.load_shot_raw("S001").get("status") or {})


@pytest.mark.parametrize("payload,needle", [
    ({"shot": "S001", "take": "nope", "text": "x"}, "nope"),            # unknown take
    ({"shot": "NOPE", "take": "take_01", "text": "x"}, "NOPE"),         # unknown shot
    ({"shot": "S001", "take": "take_01", "text": "x" * 2001}, ""),      # oversize text
    ({"shot": "S001", "take": "take_01", "text": ""}, "text"),          # empty text
    ({"shot": "S001", "take": "take_01", "text": "x",
      "severity": "nitpick"}, ""),                                      # bad severity
    ({"shot": "S001", "take": "take_01", "text": "x",
      "range": [9, 3]}, ""),                                            # reversed range
    ({"shot": "S001", "take": "take_01", "text": "x",
      "frame": -2}, ""),                                                # negative frame
])
def test_annotate_bad_input_is_400(ann_project, payload, needle):
    with running(ann_project) as (base, server):
        r = _post(server, base + "/api/annotate", json=payload)
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    if needle:
        assert needle in body["error"]
    assert "annotations" not in (ann_project.load_shot_raw("S001").get("status") or {})


def test_annotate_cas_refusal_on_stale_expected_rev(ann_project):
    """A stale expected_rev (the CAS token some other entrance invalidated)
    refuses the write BEFORE anything lands — same checked_shot_write refusal
    shape as every other board business-rule rejection."""
    with running(ann_project) as (base, server):
        r = _post(server, base + "/api/annotate", json={
            "shot": "S001", "take": "take_01", "text": "过期的表单 stale form",
            "expected_rev": "sha256:" + "0" * 64,
        })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["error"]
    assert "annotations" not in (ann_project.load_shot_raw("S001").get("status") or {})


def test_annotate_current_rev_passes_cas(ann_project):
    from manju.core.writes import shot_text_hash

    rev = shot_text_hash(ann_project, "S001")
    with running(ann_project) as (base, server):
        r = _post(server, base + "/api/annotate", json={
            "shot": "S001", "take": "take_01", "text": "新鲜的表单 fresh form",
            "expected_rev": rev,
        })
    assert r.status_code == 200 and r.json()["ok"] is True
    assert len(ann_project.load_shot_raw("S001")["status"]["annotations"]) == 1


def test_annotate_is_registered_and_dangerous_surface_unchanged():
    assert "annotate" in API_ACTIONS
    for danger in ("unlock", "gc", "pack", "unpack"):
        assert danger not in API_ACTIONS


# ----------------------------------------------------------- serve-mode render


def test_serve_html_lists_annotation_with_seek_and_form(ann_project):
    with running(ann_project) as (base, server):
        r = _post(server, base + "/api/annotate", json={
            "shot": "S001", "take": "take_01", "text": "看这里 <b>look</b>",
            "severity": "blocker", "frame": 6, "subject": "灯光 <i>light</i>",
        })
        assert r.json()["ok"] is True
        body = httpx.get(base + "/").text
    # the list row: severity chip + escaped text/subject + actor/created meta
    assert "ann-blocker" in body
    assert "看这里 &lt;b&gt;look&lt;/b&gt;" in body
    assert "<b>look</b>" not in body
    assert "灯光 &lt;i&gt;light&lt;/i&gt;" in body
    # click-to-seek: frame 6 @ 24fps = 0.250s
    assert 'data-seek="0.250"' in body
    # per-take add form, POSTing through the delegated handler
    assert 'class="ann-form"' in body and 'data-annsubmit="1"' in body
    assert 'data-take="take_01"' in body
    # a FRESH annotation (bound to the current media) shows no stale badge
    assert "STALE" not in body


def test_stale_annotation_renders_stale_badge(ann_project):
    """An annotation whose stored media_sha256 no longer matches the take's
    current file hash (written directly into the shot yaml, as if the take had
    been replaced) renders the STALE badge at serve time."""
    stale = _ann(id="ann_stale01", take="take_01",
                 media_sha256="sha256:" + "ee" * 32, frame=3,
                 text="旧媒体上的批注 note on superseded media")
    ann_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("annotations", [stale]))
    with running(ann_project) as (base, _server):
        body = httpx.get(base + "/").text
    assert "旧媒体上的批注" in body
    assert "STALE" in body


def test_static_board_pin_still_holds_and_carries_no_annotation_ui(ann_project, monkeypatch):
    """The EXISTING static byte pin: generate_board == render_board(serve=False),
    and none of the serve-only annotation veneer leaks into the static bytes."""
    monkeypatch.setattr(bd, "datetime", _FrozenDatetime)
    written = bd.generate_board(ann_project).read_text(encoding="utf-8")
    rendered = bd.render_board(ann_project, serve=False)
    assert written == rendered
    for marker in ('fetch("/api/', "/media/", "mj-overlay",
                   "ann-form", "data-seek", "data-annsubmit", "annotate"):
        assert marker not in written
