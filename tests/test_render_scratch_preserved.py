"""When a render fails, it deleted the evidence on the way out.

Measured, this session. A boundary crossfade failed intermittently during a full
suite run. The failure record was good — ffmpeg's stderr tail plus the argv, and
the argv said exactly which two files were fed in:

    ffmpeg -i .../renders/segments/tmpXXXX/a.mp4 -i .../tmpXXXX/b.mp4 \\
           -filter_complex '[0:v][1:v]xfade=...[v];[0:a][1:a]acrossfade=d=0.334[a]' ...
    [aost#0:1/aac] Could not open encoder before EOF
    [aost#0:1/aac] Task finished with error code: -22 (Invalid argument)

The named question — did the AUDIO leg of one of those two layers come out
empty? — is answerable in one ffprobe. It was not answerable, because
``tempfile.TemporaryDirectory`` had already removed ``tmpXXXX`` by the time the
error surfaced. The argv points at paths that no longer exist.

That is the whole defect, and it is a user-experience defect for both audiences:

- for the owner, a failed build leaves an error and nothing to look at;
- for an agent, the one artifact that would turn "ffmpeg failed" into a
  diagnosis is destroyed at the exact moment it became interesting.

So the scratch directory is now kept — but only on failure, only under
``.manju/`` (disposable by contract, never a build input, already gitignored),
only the latest set per stage, and only within a size budget whose exclusions
are NAMED rather than silently applied.

Success behaviour is untouched: the scratch is deleted exactly as before.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from manju.media.ffmpeg import MediaCanceled, MediaError
from manju.media.render import (
    _SCRATCH_FILE_CAP_BYTES,
    _render_scratch,
    _scratch_keep_root,
)


def _keep(project, label: str) -> Path:
    return _scratch_keep_root(project) / label


def _manifest(project, label: str) -> str:
    return (_keep(project, label) / "MANIFEST.txt").read_text(encoding="utf-8")


def _fail_inside(project, label: str, files: dict[str, bytes],
                 exc: BaseException | None = None) -> BaseException:
    """Run a scratch scope that writes ``files`` and then blows up."""
    boom = exc if exc is not None else MediaError("ffmpeg exited 1")
    with pytest.raises(type(boom)) as ei:
        with _render_scratch(project, label, dir=project.segments_dir) as tmp:
            for name, data in files.items():
                (Path(tmp) / name).write_bytes(data)
            raise boom
    return ei.value


@pytest.fixture
def project(tmp_project):
    tmp_project.segments_dir.mkdir(parents=True, exist_ok=True)
    return tmp_project


# --------------------------------------------------------------- success path


def test_a_successful_scope_deletes_the_scratch_exactly_as_before(project) -> None:
    with _render_scratch(project, "segment", dir=project.segments_dir) as tmp:
        inside = Path(tmp)
        (inside / "base.mp4").write_bytes(b"x")
        assert inside.is_dir()
    assert not inside.exists()


def test_a_successful_scope_preserves_nothing(project) -> None:
    with _render_scratch(project, "segment", dir=project.segments_dir) as tmp:
        (Path(tmp) / "base.mp4").write_bytes(b"x")
    assert not _scratch_keep_root(project).exists(), "kept debris on a clean run"


# --------------------------------------------------------------- failure path


def test_the_inputs_survive_the_failure(project) -> None:
    _fail_inside(project, "boundary", {"a.mp4": b"AAAA", "b.mp4": b"BBBBBB"})
    kept = _keep(project, "boundary")
    assert (kept / "a.mp4").read_bytes() == b"AAAA"
    assert (kept / "b.mp4").read_bytes() == b"BBBBBB"


def test_the_scratch_itself_is_still_cleaned_up(project) -> None:
    """Preserving is a copy aside, not a leak: no tmp* dir is left behind in
    renders/segments, which is a cache directory the build scans."""
    _fail_inside(project, "boundary", {"a.mp4": b"AAAA"})
    leftovers = [p for p in project.segments_dir.iterdir() if p.name.startswith("tmp")]
    assert leftovers == [], leftovers


def test_it_lands_under_the_disposable_runtime_dir(project) -> None:
    """.manju/ is disposable by contract and already gitignored — debug debris
    must never appear anywhere the owner would mistake it for an artifact."""
    _fail_inside(project, "boundary", {"a.mp4": b"A"})
    kept = _keep(project, "boundary")
    assert kept.is_relative_to(project.runtime_dir), kept


def test_the_original_exception_is_re_raised_unchanged(project) -> None:
    err = MediaError("ffmpeg exited 1: acrossfade")
    raised = _fail_inside(project, "boundary", {"a.mp4": b"A"}, exc=err)
    assert raised is err


def test_nested_stages_do_not_clobber_each_other(project) -> None:
    """A boundary failure happens INSIDE the compose scope, so both scopes
    unwind. The inner set is the useful one; it must not be overwritten by the
    outer one's concat.txt."""
    with pytest.raises(MediaError):
        with _render_scratch(project, "compose-final", dir=project.segments_dir) as outer:
            (Path(outer) / "concat.txt").write_text("file 'x'\n", encoding="utf-8")
            with _render_scratch(project, "boundary", dir=project.segments_dir) as inner:
                (Path(inner) / "a.mp4").write_bytes(b"A")
                raise MediaError("boundary blew up")
    assert (_keep(project, "boundary") / "a.mp4").exists()
    assert (_keep(project, "compose-final") / "concat.txt").exists()


def test_a_new_failure_replaces_the_previous_one_for_that_stage(project) -> None:
    """Bounded disk, and no ambiguity about which failure you are looking at."""
    _fail_inside(project, "boundary", {"old.mp4": b"OLD"})
    _fail_inside(project, "boundary", {"new.mp4": b"NEW"})
    kept = _keep(project, "boundary")
    assert (kept / "new.mp4").exists()
    assert not (kept / "old.mp4").exists(), "stale evidence from an older failure"


# ------------------------------------------------------- cancel is not failure


def test_a_cancellation_preserves_nothing(project) -> None:
    """`manju build` can be cancelled on purpose; that is not a defect and must
    not leave debris behind."""
    _fail_inside(project, "boundary", {"a.mp4": b"A"}, exc=MediaCanceled("stopped"))
    assert not _keep(project, "boundary").exists()


def test_a_keyboard_interrupt_preserves_nothing(project) -> None:
    with pytest.raises(KeyboardInterrupt):
        with _render_scratch(project, "boundary", dir=project.segments_dir) as tmp:
            (Path(tmp) / "a.mp4").write_bytes(b"A")
            raise KeyboardInterrupt
    assert not _keep(project, "boundary").exists()


# ------------------------------------------------------------ the size budget


def test_an_oversized_file_is_not_copied(project) -> None:
    big = b"\0" * (_SCRATCH_FILE_CAP_BYTES + 1)
    _fail_inside(project, "compose-final", {"concat.mp4": big, "concat.txt": b"file 'x'\n"})
    kept = _keep(project, "compose-final")
    assert not (kept / "concat.mp4").exists()
    assert (kept / "concat.txt").exists(), "the cheap, useful file was dropped too"


def test_an_oversized_file_is_named_rather_than_silently_dropped(project) -> None:
    """No silent caps: a manifest that omits what it skipped reads as 'this is
    everything', which is the failure mode the cap would otherwise introduce."""
    big = b"\0" * (_SCRATCH_FILE_CAP_BYTES + 1)
    _fail_inside(project, "compose-final", {"concat.mp4": big})
    text = _manifest(project, "compose-final")
    assert "concat.mp4" in text
    assert "skipped" in text
    assert str(len(big)) in text, "the size that tripped the cap is not stated"


def test_the_total_budget_is_enforced(project, monkeypatch) -> None:
    """A long film's compose scratch is many pieces each UNDER the per-file cap.
    Without a total budget the "bounded" claim is only true for one file."""
    import manju.media.render as render

    monkeypatch.setattr(render, "_SCRATCH_TOTAL_CAP_BYTES", 250)
    _fail_inside(project, "compose-final",
                 {f"piece_{i:04d}.mp4": b"\0" * 100 for i in range(6)})
    kept = _keep(project, "compose-final")
    total = sum(p.stat().st_size for p in kept.iterdir() if p.suffix == ".mp4")
    assert total <= 250, total


def test_the_budget_keeps_the_cheap_evidence_first(project, monkeypatch) -> None:
    """Smallest first. concat.txt (the piece list — the single most diagnostic
    file in a compose failure) is a few hundred bytes; alphabetical order would
    let concat.mp4 eat the whole budget before reaching it."""
    import manju.media.render as render

    monkeypatch.setattr(render, "_SCRATCH_TOTAL_CAP_BYTES", 600)
    _fail_inside(project, "compose-final", {
        "concat.mp4": b"\0" * 500,
        "concat.txt": b"file 'a.mp4'\n",
        "piece_0000.mp4": b"\0" * 500,
    })
    kept = _keep(project, "compose-final")
    assert (kept / "concat.txt").exists(), "the small, useful file lost the race"


def test_a_budget_skip_says_it_was_the_budget(project, monkeypatch) -> None:
    """Two different reasons to skip; the manifest must not conflate them."""
    import manju.media.render as render

    monkeypatch.setattr(render, "_SCRATCH_TOTAL_CAP_BYTES", 150)
    _fail_inside(project, "compose-final",
                 {f"piece_{i:04d}.mp4": b"\0" * 100 for i in range(3)})
    # The ROWS, not the header — the header legitimately names both caps.
    reasons = re.findall(r"^skipped\t\S+\t\d+\t(\S+)$",
                         _manifest(project, "compose-final"), re.M)
    assert reasons, _manifest(project, "compose-final")
    assert set(reasons) == {"total-cap"}, reasons


def test_the_manifest_rows_stay_in_path_order(project, monkeypatch) -> None:
    """Copy order is by size; the RECORD is read by a human and stays sorted by
    path, the same determinism rule the rest of the build follows."""
    import manju.media.render as render

    monkeypatch.setattr(render, "_SCRATCH_TOTAL_CAP_BYTES", 10_000)
    _fail_inside(project, "compose-final",
                 {"c.mp4": b"\0" * 10, "a.mp4": b"\0" * 300, "b.mp4": b"\0" * 200})
    names = re.findall(r"^(?:kept|skipped)\t(\S+)\t", _manifest(project, "compose-final"), re.M)
    assert names == sorted(names), names


# ---------------------------------------------------------------- the manifest


def test_the_manifest_lists_every_kept_file_with_its_size(project) -> None:
    _fail_inside(project, "boundary", {"a.mp4": b"AAAA", "b.mp4": b"BBBBBB"})
    text = _manifest(project, "boundary")
    assert re.search(r"^kept\ta\.mp4\t4$", text, re.M), text
    assert re.search(r"^kept\tb\.mp4\t6$", text, re.M), text


def test_the_manifest_says_the_directory_is_disposable(project) -> None:
    """The owner will find this directory before they find any documentation of
    it. It has to explain itself in place."""
    _fail_inside(project, "boundary", {"a.mp4": b"A"})
    text = _manifest(project, "boundary")
    assert "可以删除" in text, text
    assert "boundary" in text, text


def test_the_manifest_records_the_failing_stage_and_when(project) -> None:
    _fail_inside(project, "boundary", {"a.mp4": b"A"})
    text = _manifest(project, "boundary")
    assert re.search(r"20\d\d-\d\d-\d\dT", text), text


# ------------------------------------------ discoverability: failures + log


def test_a_note_points_at_the_preserved_directory(project) -> None:
    """`manju failures` is where both audiences look after a failed build. A
    preserved directory nobody is told about is the same as no directory."""
    from manju.core.failures import read_failures

    _fail_inside(project, "boundary", {"a.mp4": b"A"})
    notes = [f for f in read_failures(project.root) if "render-debug" in f.get("cause", "")]
    assert notes, [f.get("cause") for f in read_failures(project.root)]
    assert notes[-1]["level"] == "info", notes[-1]
    assert "boundary" in notes[-1]["cause"]


def test_the_note_is_a_degradation_not_a_second_error(project) -> None:
    """The build already recorded the real error. A second error-level record
    would double-count the failure in status and the ledger."""
    from manju.core.failures import read_failures

    _fail_inside(project, "boundary", {"a.mp4": b"A"})
    levels = {f["level"] for f in read_failures(project.root)}
    assert levels == {"info"}, levels


def test_the_log_line_names_the_directory(project) -> None:
    lines: list[str] = []
    with pytest.raises(MediaError):
        with _render_scratch(project, "boundary", dir=project.segments_dir,
                             log=lines.append) as tmp:
            (Path(tmp) / "a.mp4").write_bytes(b"A")
            raise MediaError("boom")
    assert any("render-debug" in ln for ln in lines), lines


# ------------------------------------------------- preserving must never mask


def test_a_preserve_failure_never_masks_the_real_error(project) -> None:
    """Guard the guard. Debug plumbing that can itself raise would turn a
    readable ffmpeg error into a confusing one — the exact opposite of the
    point. Block the keep root with a FILE so mkdir cannot succeed."""
    root = _scratch_keep_root(project)
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_text("not a directory", encoding="utf-8")
    err = MediaError("the real ffmpeg error")
    raised = _fail_inside(project, "boundary", {"a.mp4": b"A"}, exc=err)
    assert raised is err


def test_a_note_failure_never_masks_the_real_error(project, monkeypatch) -> None:
    import manju.media.render as render

    def explode(*a, **k):
        raise RuntimeError("failures ledger is wedged")

    monkeypatch.setattr(render, "record_failure", explode)
    err = MediaError("the real ffmpeg error")
    raised = _fail_inside(project, "boundary", {"a.mp4": b"A"}, exc=err)
    assert raised is err
    assert (_keep(project, "boundary") / "a.mp4").exists(), "the copy was lost too"


# --------------------------------------------------------------- one owner


def test_render_has_exactly_one_scratch_owner() -> None:
    """Three call sites used TemporaryDirectory directly; a fourth would quietly
    reintroduce the evidence-destroying behaviour."""
    from manju.media import render

    body = Path(render.__file__).read_text(encoding="utf-8")
    # Statement lines only: this repo's grep-pins have been tripped four
    # recorded times by a token sitting in a docstring, and the owner's own
    # docstring says "drop-in for tempfile.TemporaryDirectory(dir=...)".
    hits = [ln.strip() for ln in body.splitlines()
            if ln.strip().startswith("with tempfile.TemporaryDirectory(")]
    assert len(hits) == 1, hits


def test_every_render_scratch_is_the_owner(project) -> None:
    from manju.media import render

    body = Path(render.__file__).read_text(encoding="utf-8")
    assert body.count("_render_scratch(") >= 4, "a call site stopped using the owner"
