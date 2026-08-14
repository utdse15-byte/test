"""Tests for the post-import confirmation layer (round AA, goal items 1+2):

  * match-state derivation on IngestRow — matched/pending/unmatched/conflict,
    including the NEW conflict-downgrade behavior (a filename that plausibly
    resolves to MORE THAN ONE owner must never land on a guess);
  * the persisted batch record (build/batches.py) apply_ingest now writes to
    reports/ingest_batches/<batch_id>.yaml — shape, ids, review states;
  * empty-shot auto-select staging on a landed take (goal item 2): stages on
    an empty shot, never overwrites an existing selection, respects a
    value-hash lock;
  * review_item confirm/flag/discard, including discard's undo-the-staged-
    selection contract (only when nobody re-selected since);
  * the CLI surface (`manju ingest-batches`/`ingest-review`/`ingest-confirm`/
    `ingest-flag`/`ingest-discard`) — happy path, --json, --all-matched,
    build-lock refusal.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from manju.build.batches import (
    BatchError,
    list_batches,
    load_batch,
    new_batch_id,
    review_item,
)
from manju.build.ingest import apply_ingest, plan_ingest
from manju.cli import app
from manju.core.events import tail_events
from manju.core.locks import seal_lock
from manju.core.writes import WriteRejected, select_take_checked
from manju.runtime.buildlock import BuildLock

runner = CliRunner()


def _drop(dirpath, name, content=b"x"):
    p = dirpath / name
    p.write_bytes(content)
    return p


# ======================================================================
# match-state derivation
# ======================================================================


def test_bare_filename_is_matched(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.match == "matched"
    assert row.candidates == []


def test_explicit_suffix_is_matched(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001_take.mp4")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "take" and row.match == "matched"


def test_leading_token_fallback_is_pending(tmp_project, add_shot, tmp_path):
    """`S001_v2_final.mov` only resolves via the generic "ignore everything
    after the first underscore" fallback — it still lands as a take (existing
    behavior, unchanged) but is flagged `pending` for a human/agent to
    confirm, with the winning id recorded in `candidates`."""
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001_v2_final.mov")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "take" and row.shot_id == "S001"
    assert row.match == "pending"
    assert row.candidates == ["S001"]


def test_voice_leading_token_fallback_is_pending(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001_alt_take.wav")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "voice" and row.shot_id == "S001"
    assert row.match == "pending"


def test_unmatched_id_stays_unmatched(tmp_project, tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "nobody_ref.png")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "import" and row.match == "unmatched"
    assert row.candidates == []


def test_shot_forced_is_always_matched(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "anything_at_all.mp4")
    row = plan_ingest(tmp_project, [batch], shot="S001").rows[0]
    assert row.action == "take" and row.match == "matched" and row.candidates == []


def test_skip_duplicate_keeps_matched(tmp_project, tmp_path):
    tmp_project.imports_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.imports_dir / "already.mp4").write_bytes(b"same-bytes")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "incoming.mp4", b"same-bytes")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "skip_duplicate" and row.match == "matched"


def test_two_shots_matching_one_filename_is_a_conflict(tmp_project, add_shot, tmp_path):
    """Today's candidate search would silently pick the leading-token match
    ("S001") and never notice the whole-stem also happens to be a real shot
    id ("S001_v2") — that is exactly the silent-resolution gap round AA
    closes: a row with more than one plausible owner must downgrade to a
    plain import, listing every candidate, never landing on a guess."""
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S001_v2")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001_v2.mov")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "import"
    assert row.match == "conflict"
    assert set(row.candidates) == {"S001", "S001_v2"}
    assert "S001" in row.reason and "S001_v2" in row.reason


def test_shot_vs_bible_ambiguity_is_a_conflict(tmp_project, add_shot, tmp_path):
    """The PRE-EXISTING ambiguous-id-falls-back-to-import behavior (a shot
    and a differently-named bible asset both plausible) is now tagged as a
    conflict too — same landing (import), richer metadata."""
    from manju.core.yamlio import read_yaml, write_yaml

    add_shot(tmp_project, "foo")
    write_yaml(
        tmp_project.root / "bible" / "characters.yaml",
        {**(read_yaml(tmp_project.root / "bible" / "characters.yaml") or {}),
         "foo_bar": {"name": "道具"}},
    )
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "foo_bar_ref.png")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "import"
    assert row.match == "conflict"
    assert set(row.candidates) == {"foo", "foo_bar"}


def test_bible_ref_matched(tmp_project, tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "linxia_ref.png")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "bible_ref" and row.match == "matched"


def test_override_at_apply_time_marks_manual(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "totally_unnamed.mp4")
    plan = plan_ingest(tmp_project, [batch])
    assert plan.rows[0].match == "unmatched"

    result = apply_ingest(
        tmp_project, plan, actor="test",
        overrides={0: {"action": "take", "shot_id": "S001"}},
    )
    assert result.results[0].row.match == "manual"
    assert result.results[0].row.candidates == []
    # the persisted batch item reflects the EFFECTIVE (overridden) row
    data = load_batch(tmp_project, result.batch_id)
    assert data["items"][0]["match"] == "manual"


# ======================================================================
# persisted batch record
# ======================================================================


def test_apply_writes_batch_record_with_expected_shape(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    _drop(batch, "unknown.bin", b"x")
    plan = plan_ingest(tmp_project, [batch])
    result = apply_ingest(tmp_project, plan, actor="tester", source=str(batch))

    path = tmp_project.reports_dir / "ingest_batches" / f"{result.batch_id}.yaml"
    assert path.exists()

    data = load_batch(tmp_project, result.batch_id)
    assert data["batch"] == result.batch_id
    assert data["actor"] == "tester"
    assert data["source"] == str(batch)
    assert isinstance(data["created"], str) and data["created"]
    assert len(data["items"]) == 2

    take_item = next(it for it in data["items"] if it["action"] == "take")
    assert take_item["index"] == data["items"].index(take_item)
    assert take_item["name"] == "S001.mp4"
    assert take_item["shot_id"] == "S001"
    assert take_item["hash"].startswith("sha256:")
    assert take_item["match"] == "matched"
    assert take_item["candidates"] == []
    assert take_item["ok"] is True
    assert take_item["error"] is None
    assert take_item["landed"]["take"] == "take_01"
    assert take_item["staged"] is False
    assert take_item["review"] == "pending"
    assert take_item["note"] == ""

    import_item = next(it for it in data["items"] if it["action"] == "import")
    assert import_item["match"] == "unmatched"
    assert import_item["review"] == "pending"


def test_skip_duplicate_item_is_review_auto(tmp_project, tmp_path):
    tmp_project.imports_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.imports_dir / "already.mp4").write_bytes(b"same-bytes")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "incoming.mp4", b"same-bytes")
    plan = plan_ingest(tmp_project, [batch])
    result = apply_ingest(tmp_project, plan, actor="tester")
    data = load_batch(tmp_project, result.batch_id)
    assert data["items"][0]["review"] == "auto"
    assert data["items"][0]["staged"] is False


def test_caller_supplied_batch_id_is_used_and_validated(tmp_project, tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "unknown.bin", b"x")
    plan = plan_ingest(tmp_project, [batch])
    result = apply_ingest(tmp_project, plan, actor="tester", batch_id="my-batch-01")
    assert result.batch_id == "my-batch-01"
    assert load_batch(tmp_project, "my-batch-01")["batch"] == "my-batch-01"

    from manju.build.ingest import IngestError

    with pytest.raises(IngestError):
        apply_ingest(tmp_project, plan, actor="tester", batch_id="../evil")


def test_clock_kwarg_controls_generated_batch_id(tmp_project, tmp_path):
    from datetime import datetime, timezone

    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "unknown.bin", b"x")
    plan = plan_ingest(tmp_project, [batch])
    fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    result = apply_ingest(tmp_project, plan, actor="tester", clock=lambda: fixed)
    assert result.batch_id == "b20260102-030405"
    assert new_batch_id(fixed) == "b20260102-030405"
    data = load_batch(tmp_project, result.batch_id)
    assert data["created"] == fixed.isoformat(timespec="seconds")


def test_list_batches_newest_first_with_counts(tmp_project, tmp_path):
    from datetime import datetime, timezone

    batch1 = tmp_path / "batch1"
    batch1.mkdir()
    _drop(batch1, "unknown1.bin", b"x")
    batch2 = tmp_path / "batch2"
    batch2.mkdir()
    _drop(batch2, "unknown2.bin", b"y")  # distinct content -> not a dedup hit
    apply_ingest(tmp_project, plan_ingest(tmp_project, [batch1]), actor="a",
                clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc), batch_id="b-old")
    apply_ingest(tmp_project, plan_ingest(tmp_project, [batch2]), actor="b",
                clock=lambda: datetime(2026, 6, 1, tzinfo=timezone.utc), batch_id="b-new")

    listed = list_batches(tmp_project)
    assert [b["batch"] for b in listed] == ["b-new", "b-old"]
    assert listed[0]["counts"] == {"pending": 1}
    assert listed[0]["items"] == 1


def test_load_batch_missing_raises(tmp_project):
    with pytest.raises(BatchError):
        load_batch(tmp_project, "nosuchbatch")


def test_list_batches_empty_project_returns_empty(tmp_project):
    assert list_batches(tmp_project) == []


# ======================================================================
# empty-shot auto-select staging (goal item 2)
# ======================================================================


def test_take_on_empty_shot_waits_for_manual_selection(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    plan = plan_ingest(tmp_project, [batch])
    result = apply_ingest(tmp_project, plan, actor="tester")

    detail = result.results[0].detail
    assert detail["staged"] is False
    assert "manual review required" in detail["staged_note"]
    assert tmp_project.load_shot("S001").status.selected_take is None

    events = tail_events(tmp_project.root, 100)
    auto = [e for e in events if e["action"] == "auto_select"]
    assert auto == []


def test_take_on_occupied_shot_never_overwrites(tmp_project, add_shot, make_take, tmp_path):
    add_shot(tmp_project, "S001")
    existing = make_take(tmp_project, "S001", "sha256:whatever")
    select_take_checked(tmp_project, "S001", existing.name, actor="human", via="cli")

    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001_take.mp4", b"brand-new")
    plan = plan_ingest(tmp_project, [batch])
    result = apply_ingest(tmp_project, plan, actor="tester")

    detail = result.results[0].detail
    assert detail["staged"] is False
    assert "manual review required" in detail["staged_note"]
    assert tmp_project.load_shot("S001").status.selected_take == existing.name


def test_take_on_locked_shot_never_auto_selects(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")

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
        "S001", lambda d: d.setdefault("locked", {}).__setitem__("status.selected_take", digest)
    )

    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    plan = plan_ingest(tmp_project, [batch])
    result = apply_ingest(tmp_project, plan, actor="tester")

    detail = result.results[0].detail
    assert result.results[0].ok is True  # the take itself still landed
    assert detail["staged"] is False
    assert "manual review required" in detail["staged_note"]
    assert tmp_project.load_shot("S001").status.selected_take is None


# ======================================================================
# review_item: confirm / flag / discard
# ======================================================================


def _apply_one_take(tmp_project, tmp_path, name="S001.mp4", content=b"v1", **kw):
    batch = tmp_path / "batch"
    batch.mkdir(exist_ok=True)
    _drop(batch, name, content)
    plan = plan_ingest(tmp_project, [batch])
    return apply_ingest(tmp_project, plan, actor="tester", **kw)


def test_review_confirm_sets_state_and_note_and_event(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    result = _apply_one_take(tmp_project, tmp_path)
    detail = review_item(tmp_project, result.batch_id, 0, decision="confirm",
                         note="looks right", actor="reviewer")
    assert detail["item"]["review"] == "confirmed"
    assert detail["item"]["note"] == "looks right"

    data = load_batch(tmp_project, result.batch_id)
    assert data["items"][0]["review"] == "confirmed"

    events = tail_events(tmp_project.root, 100)
    reviews = [e for e in events if e["action"] == "ingest_review"]
    assert reviews[-1]["detail"]["decision"] == "confirm"
    assert reviews[-1]["actor"] == "reviewer"


def test_review_flag_sets_state(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    result = _apply_one_take(tmp_project, tmp_path)
    detail = review_item(tmp_project, result.batch_id, 0, decision="flag",
                         note="double check lighting", actor="reviewer")
    assert detail["item"]["review"] == "flagged"
    assert detail["item"]["note"] == "double check lighting"


def test_review_unknown_decision_raises(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    result = _apply_one_take(tmp_project, tmp_path)
    with pytest.raises(BatchError):
        review_item(tmp_project, result.batch_id, 0, decision="bogus", actor="reviewer")


def test_review_bad_index_raises(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    result = _apply_one_take(tmp_project, tmp_path)
    with pytest.raises(BatchError):
        review_item(tmp_project, result.batch_id, 5, decision="confirm", actor="reviewer")


def test_review_unknown_batch_raises(tmp_project):
    with pytest.raises(BatchError):
        review_item(tmp_project, "nosuchbatch", 0, decision="confirm", actor="reviewer")


def test_discard_of_unselected_ingest_is_a_pure_review_decision(
        tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    result = _apply_one_take(tmp_project, tmp_path)
    assert tmp_project.load_shot("S001").status.selected_take is None

    detail = review_item(tmp_project, result.batch_id, 0, decision="discard", actor="reviewer")
    assert detail["item"]["review"] == "discarded"
    assert detail["undo"] is None
    assert tmp_project.load_shot("S001").status.selected_take is None


def test_discard_leaves_a_re_selected_take_alone(tmp_project, add_shot, make_take, tmp_path):
    """A later human selection is independent of ingest-batch review."""
    add_shot(tmp_project, "S001")
    result = _apply_one_take(tmp_project, tmp_path)
    assert tmp_project.load_shot("S001").status.selected_take is None

    other = make_take(tmp_project, "S001", "sha256:other")
    select_take_checked(tmp_project, "S001", other.name, actor="human", via="cli")

    detail = review_item(tmp_project, result.batch_id, 0, decision="discard", actor="reviewer")
    assert detail["item"]["review"] == "discarded"
    assert detail["undo"] is None
    assert tmp_project.load_shot("S001").status.selected_take == other.name


def test_discard_on_non_staged_item_is_a_pure_review_flip(tmp_project, add_shot, make_take, tmp_path):
    """Discarding an item that never auto-staged anything (shot already had
    a selection) only changes review state — no undo attempted."""
    add_shot(tmp_project, "S001")
    existing = make_take(tmp_project, "S001", "sha256:whatever")
    select_take_checked(tmp_project, "S001", existing.name, actor="human", via="cli")

    result = _apply_one_take(tmp_project, tmp_path, name="S001_take.mp4", content=b"new")
    assert result.results[0].detail["staged"] is False

    detail = review_item(tmp_project, result.batch_id, 0, decision="discard", actor="reviewer")
    assert detail["item"]["review"] == "discarded"
    assert detail["undo"] is None
    assert tmp_project.load_shot("S001").status.selected_take == existing.name


def test_discard_does_not_touch_a_locked_unselected_field(
        tmp_project, add_shot, tmp_path, monkeypatch):
    """Batch review never writes selected_take, even when it is locked."""
    add_shot(tmp_project, "S001")
    result = _apply_one_take(tmp_project, tmp_path)
    assert tmp_project.load_shot("S001").status.selected_take is None

    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("status", {}).setdefault("selected_take", None),
    )
    raw = tmp_project.load_shot_raw("S001")
    digest = seal_lock(raw, "status.selected_take")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("locked", {}).__setitem__("status.selected_take", digest)
    )

    detail = review_item(tmp_project, result.batch_id, 0, decision="discard", actor="reviewer")
    assert detail["item"]["review"] == "discarded"
    assert detail["undo"] is None
    assert tmp_project.load_shot("S001").status.selected_take is None


# ======================================================================
# CLI
# ======================================================================


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    return tmp_project


def test_cli_ingest_apply_prints_batch_id(in_project, add_shot, tmp_path):
    add_shot(in_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    result = runner.invoke(app, ["ingest", str(batch), "--apply", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["batch_id"]
    assert load_batch(in_project, data["batch_id"])["batch"] == data["batch_id"]


def test_cli_ingest_batches_lists(in_project, add_shot, tmp_path):
    add_shot(in_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    apply_result = runner.invoke(app, ["ingest", str(batch), "--apply", "--json"])
    batch_id = json.loads(apply_result.output)["batch_id"]

    result = runner.invoke(app, ["ingest-batches", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert any(b["batch"] == batch_id for b in data["batches"])

    result = runner.invoke(app, ["ingest-batches"])
    assert result.exit_code == 0
    assert batch_id in result.output


def test_cli_ingest_review_shows_items(in_project, add_shot, tmp_path):
    add_shot(in_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    apply_result = runner.invoke(app, ["ingest", str(batch), "--apply", "--json"])
    batch_id = json.loads(apply_result.output)["batch_id"]

    result = runner.invoke(app, ["ingest-review", batch_id])
    assert result.exit_code == 0, result.output
    assert "S001.mp4" in result.output
    assert "match=matched" in result.output

    result = runner.invoke(app, ["ingest-review", batch_id, "--json"])
    data = json.loads(result.output)
    assert data["items"][0]["staged"] is False


def test_cli_ingest_review_unknown_batch_fails_cleanly(in_project):
    result = runner.invoke(app, ["ingest-review", "nosuchbatch"])
    assert result.exit_code != 0


def test_cli_ingest_confirm_all_matched(in_project, add_shot, tmp_path):
    add_shot(in_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")  # matched
    apply_result = runner.invoke(app, ["ingest", str(batch), "--apply", "--json"])
    batch_id = json.loads(apply_result.output)["batch_id"]

    result = runner.invoke(app, ["ingest-confirm", batch_id, "--all-matched", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["results"][0]["item"]["review"] == "confirmed"

    assert load_batch(in_project, batch_id)["items"][0]["review"] == "confirmed"


def test_cli_ingest_flag_with_item_and_note(in_project, add_shot, tmp_path):
    add_shot(in_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    apply_result = runner.invoke(app, ["ingest", str(batch), "--apply", "--json"])
    batch_id = json.loads(apply_result.output)["batch_id"]

    result = runner.invoke(app, ["ingest-flag", batch_id, "--item", "0", "--note", "看下画质"])
    assert result.exit_code == 0, result.output
    data = load_batch(in_project, batch_id)
    assert data["items"][0]["review"] == "flagged"
    assert data["items"][0]["note"] == "看下画质"


def test_cli_ingest_discard_keeps_selection_unset(in_project, add_shot, tmp_path):
    add_shot(in_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    apply_result = runner.invoke(app, ["ingest", str(batch), "--apply", "--json"])
    batch_id = json.loads(apply_result.output)["batch_id"]
    assert in_project.load_shot("S001").status.selected_take is None

    result = runner.invoke(app, ["ingest-discard", batch_id, "--item", "0"])
    assert result.exit_code == 0, result.output
    assert in_project.load_shot("S001").status.selected_take is None


def test_cli_ingest_confirm_no_items_fails_cleanly(in_project, add_shot, tmp_path):
    add_shot(in_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    apply_result = runner.invoke(app, ["ingest", str(batch), "--apply", "--json"])
    batch_id = json.loads(apply_result.output)["batch_id"]

    result = runner.invoke(app, ["ingest-confirm", batch_id])
    assert result.exit_code != 0


def test_cli_ingest_confirm_refuses_when_build_locked(in_project, add_shot, tmp_path):
    add_shot(in_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    apply_result = runner.invoke(app, ["ingest", str(batch), "--apply", "--json"])
    batch_id = json.loads(apply_result.output)["batch_id"]

    lock = BuildLock(in_project.root, actor="human").acquire()
    try:
        result = runner.invoke(app, ["ingest-confirm", batch_id, "--item", "0"])
        assert result.exit_code != 0
        assert "占用" in result.output or "build_locked" in result.output.lower()
    finally:
        lock.release()
    # untouched while the lock was held
    assert load_batch(in_project, batch_id)["items"][0]["review"] == "pending"
