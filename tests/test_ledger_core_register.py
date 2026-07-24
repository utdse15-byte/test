"""Audit-ledger regressions for take registration (CORE-001 / CORE-002).

CORE-001 (high): ``Project.register_take`` / ``Project.register_voice_take``
used to materialize the WHOLE source media file as one ``bytes`` object
(``media_file.read_bytes()``) before the destination fd was even opened. The
copy path is the DEFAULT (imports are never consumed) and it is exactly what
the owner hits when importing camera/render footage and on every generated
take, so peak RSS tracked file size — a multi-GiB take pages/OOMs a Windows
box AFTER generation already succeeded. These tests do not merely check that
the bytes arrived (that passed before the fix and proved nothing): they make a
whole-file read on the SOURCE a hard failure and pin the copy to bounded
chunks.

CORE-002 (low): ``local_cmd.output_ext`` was never checked against
``MEDIA_EXTS`` nor against the manifest's declared ``type``, so configuring
``.gif`` / ``.webp`` / ``.avi`` produced a SUCCESSFUL registration whose media
``Project.takes()`` can never resolve again (``media_path=None`` forever, in an
append-only namespace). Refused now at BOTH layers: the doctor
(``validate_for_generic``) at configuration time and core ``register_take``
itself (security/consistency checks live in service/core, not only in the CLI).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from manju.core.container import MEDIA_EXTS, ProjectError
from manju.core.models import TakeSidecar, VoiceTakeSidecar
from manju.providers.manifest import (
    COMFYUI_ADAPTER,
    LOCAL_CMD_ADAPTER,
    ProviderManifest,
)

# Bigger than the 1 MiB copy chunk, so a streaming copy MUST issue several
# bounded reads while a read_bytes() copy issues exactly one huge one.
BIG = 3 * 1024 * 1024 + 7


def _big_source(tmp_path: Path, name: str) -> Path:
    src = tmp_path / name
    src.write_bytes(bytes(range(256)) * (BIG // 256) + b"tail")
    return src


class _ChunkSpy:
    """Wraps the source handle so every read size is observable."""

    def __init__(self, fsrc) -> None:
        self._fsrc = fsrc
        self.sizes: list[int] = []

    def read(self, n: int = -1) -> bytes:
        self.sizes.append(n)
        return self._fsrc.read(n)

    def __getattr__(self, item):
        return getattr(self._fsrc, item)


@pytest.fixture
def no_whole_file_read(monkeypatch):
    """Make ``Path.read_bytes`` on a WATCHED source path a hard failure.

    Returns a `watch(path)` callable. Any other read_bytes (sidecars, project
    truth files) is untouched, so only the media copy is under the tripwire.
    """
    watched: set[Path] = set()
    original = Path.read_bytes

    def _guarded(self: Path):
        if Path(self) in watched:
            raise AssertionError(
                f"CORE-001 regression: whole source file read into RAM "
                f"({self})"
            )
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", _guarded)
    return watched.add


@pytest.fixture
def copy_chunks(monkeypatch):
    """Record the chunk sizes each streaming copy actually reads."""
    calls: list[_ChunkSpy] = []
    original = shutil.copyfileobj

    def _spy(fsrc, fdst, length=0):
        spy = _ChunkSpy(fsrc)
        calls.append(spy)
        return original(spy, fdst, length) if length else original(spy, fdst)

    monkeypatch.setattr(shutil, "copyfileobj", _spy)
    return calls


def _assert_bounded(calls: list[_ChunkSpy]) -> None:
    assert calls, "the copy never went through a streaming shutil.copyfileobj"
    sizes = [n for spy in calls for n in spy.sizes]
    assert sizes, "the source handle was never read in chunks"
    # bounded: never a single read of the whole (3 MiB) source
    assert max(sizes) <= 8 * 1024 * 1024, f"unbounded chunk size: {max(sizes)}"
    assert max(sizes) < BIG, "the source was swallowed in one read — not streaming"
    assert len([n for n in sizes if n != 0]) >= 2, (
        f"a {BIG} byte source took only {len(sizes)} read(s) — not streaming"
    )


# ------------------------------------------------------------------ CORE-001


def test_register_take_copy_does_not_read_whole_file(
    tmp_project, add_shot, tmp_path, no_whole_file_read
):
    shot = add_shot(tmp_project, "S001")
    src = _big_source(tmp_path, "camera_footage.mp4")
    no_whole_file_read(src)

    take = tmp_project.register_take(
        shot.id, src, TakeSidecar(provider="test", spec_hash="h")
    )

    assert take.media_path is not None
    # read around the tripwire: the bytes must still be byte-identical
    with open(src, "rb") as a, open(take.media_path, "rb") as b:
        assert a.read() == b.read()


def test_register_take_copy_streams_in_bounded_chunks(
    tmp_project, add_shot, tmp_path, copy_chunks
):
    shot = add_shot(tmp_project, "S001")
    src = _big_source(tmp_path, "render.mp4")
    expected = src.read_bytes()

    take = tmp_project.register_take(
        shot.id, src, TakeSidecar(provider="test", spec_hash="h")
    )

    _assert_bounded(copy_chunks)
    assert take.media_path is not None
    assert take.media_path.read_bytes() == expected
    # imports/copy semantics unchanged: the source survives.
    assert src.exists()


def test_register_voice_take_does_not_read_whole_file(
    tmp_project, add_shot, tmp_path, no_whole_file_read
):
    shot = add_shot(tmp_project, "S001")
    src = _big_source(tmp_path, "voice.wav")
    no_whole_file_read(src)

    dest = tmp_project.register_voice_take(
        shot.id, src, VoiceTakeSidecar(provider="test", voice_hash="h")
    )

    assert dest.exists()
    with open(src, "rb") as a, open(dest, "rb") as b:
        assert a.read() == b.read()


def test_register_voice_take_streams_in_bounded_chunks(
    tmp_project, add_shot, tmp_path, copy_chunks
):
    shot = add_shot(tmp_project, "S001")
    src = _big_source(tmp_path, "voice.wav")
    expected = src.read_bytes()

    dest = tmp_project.register_voice_take(
        shot.id, src, VoiceTakeSidecar(provider="test", voice_hash="h")
    )

    _assert_bounded(copy_chunks)
    assert dest.read_bytes() == expected
    assert (dest.parent / f"{dest.stem}.sidecar.yaml").exists()


def test_register_take_move_path_still_moves(tmp_project, add_shot, tmp_path):
    """``move=True`` (reframe / repair_ops) keeps its O_EXCL claim + rename."""
    shot = add_shot(tmp_project, "S001")
    src = _big_source(tmp_path, "reframed.mp4")
    expected = src.read_bytes()

    take = tmp_project.register_take(
        shot.id, src, TakeSidecar(provider="test", spec_hash="h"), move=True
    )

    assert take.media_path is not None
    assert take.media_path.read_bytes() == expected
    assert not src.exists()


def test_register_take_never_overwrites_an_existing_take(
    tmp_project, add_shot, tmp_path
):
    """The O_EXCL no-overwrite guarantee is unchanged by the streaming copy."""
    shot = add_shot(tmp_project, "S001")
    src = tmp_path / "a.mp4"
    src.write_bytes(b"first")
    first = tmp_project.register_take(
        shot.id, src, TakeSidecar(provider="test", spec_hash="h")
    )
    src.write_bytes(b"second")
    second = tmp_project.register_take(
        shot.id, src, TakeSidecar(provider="test", spec_hash="h")
    )

    assert first.name != second.name
    assert first.media_path is not None and second.media_path is not None
    assert first.media_path.read_bytes() == b"first"
    assert second.media_path.read_bytes() == b"second"


# ------------------------------------------------------------------ CORE-002


def _manifest(type_: str, output_ext: str) -> ProviderManifest:
    return ProviderManifest.model_validate({
        "id": "svd_local",
        "type": type_,
        "adapter": LOCAL_CMD_ADAPTER,
        "local_cmd": {
            "command": "python -c pass -o {out}",
            "timeout_s": 10.0,
            "output_ext": output_ext,
        },
    })


@pytest.mark.parametrize("bad_ext", [".gif", ".webp", ".avi", ".exe", ".txt"])
def test_doctor_refuses_output_ext_outside_media_exts(bad_ext):
    problems = _manifest("video", bad_ext).validate_for_generic()
    assert any("output_ext" in p for p in problems), problems


@pytest.mark.parametrize("ok_ext", ["mp4", ".MP4", "MOV"])
def test_doctor_tolerates_what_the_adapter_normalizes(ok_ext):
    """LocalCommandProvider already normalizes a dotless/odd-cased fill, so the
    doctor must not report a config that actually works."""
    problems = _manifest("video", ok_ext).validate_for_generic()
    assert not any("output_ext" in p for p in problems), problems


def test_doctor_refuses_output_ext_that_contradicts_type():
    problems = _manifest("image", ".mp4").validate_for_generic()
    assert any("output_ext" in p for p in problems), problems
    problems = _manifest("video", ".png").validate_for_generic()
    assert any("output_ext" in p for p in problems), problems


@pytest.mark.parametrize("type_,ext", [
    ("video", ".mp4"), ("video", ".mov"), ("image", ".png"), ("image", ".jpg"),
])
def test_doctor_accepts_a_coherent_output_ext(type_, ext):
    problems = _manifest(type_, ext).validate_for_generic()
    assert not any("output_ext" in p for p in problems), problems


def test_output_ext_check_is_scoped_to_local_cmd():
    """A non-local_cmd manifest never carries output_ext meaning — the default
    must not start reporting problems for comfyui/generic manifests."""
    m = ProviderManifest.model_validate({
        "id": "comfy", "type": "image", "adapter": COMFYUI_ADAPTER,
        "comfyui": {"workflow_file": "w.json"},
    })
    assert not any("output_ext" in p for p in m.validate_for_generic())


def test_register_take_refuses_an_unresolvable_suffix(
    tmp_project, add_shot, tmp_path
):
    """Core layer, not just the doctor: a take whose media Project.takes()
    could never resolve is refused instead of minted into append-only space."""
    shot = add_shot(tmp_project, "S001")
    src = tmp_path / "generated.gif"
    src.write_bytes(b"GIF89a")

    with pytest.raises(ProjectError) as exc:
        tmp_project.register_take(
            shot.id, src, TakeSidecar(provider="test", spec_hash="h")
        )
    assert ".gif" in str(exc.value)

    tdir = tmp_project.takes_dir(shot.id)
    if tdir.exists():
        assert list(tdir.glob("*")) == []
    assert tmp_project.takes(shot.id) == []
    assert src.exists()  # never consumed


def test_register_take_refusal_is_a_projecterror_existing_sites_catch():
    """Do not introduce a new exception that escapes an existing except."""
    assert issubclass(ProjectError, RuntimeError)


@pytest.mark.parametrize("ext", list(MEDIA_EXTS))
def test_register_take_accepts_every_media_ext(
    tmp_project, add_shot, tmp_path, ext
):
    shot = add_shot(tmp_project, "S001")
    src = tmp_path / f"ok{ext}"
    src.write_bytes(b"x")
    take = tmp_project.register_take(
        shot.id, src, TakeSidecar(provider="test", spec_hash="h")
    )
    assert take.media_path is not None and take.media_path.suffix == ext
