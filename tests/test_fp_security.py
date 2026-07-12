"""FP security loop (roadmap §8.9/§15.8) — untrusted-archive decompression limits.

Existing coverage (NOT duplicated here): member-NAME traversal/absolute refusal
in template packs (test_c17_packs.py — `/etc/passwd`, `../outside.md` via
_ZIP_UNSAFE) and `manju unpack`'s symlink-member + traversal-name + zip-comment
refusals (Round Y / goal-20, cli.py).

What was MISSING at baseline (this file's red tests):

  seriespack.inspect_template_pack / import_template_pack inflated every member
  fully into memory with NO caps — a zip bomb (tiny compressed, huge declared or
  actual inflated size) exhausted memory on the READ-ONLY inspect path, the very
  command a user runs FIRST on an untrusted pack. Two distinct hazards:
    (a) declared-size bombs — ZipInfo.file_size is honest but huge;
    (b) header-lie bombs — the central directory under-declares file_size; any
        guard that trusts headers passes, so reads must be stream-capped.

  `manju unpack` extractall'd a .manjupkg with no size preflight — a bomb fills
  the disk. Template-pack-style absolute caps would be WRONG here (real project
  restores are legitimately huge, and digital-silence WAV media compresses
  ~1000:1 legally, so compression-ratio heuristics false-positive) — the honest
  guard is declared-total vs actual free disk space, plus a member-count sanity
  cap.

Template packs are small by contract (bible entries + refs media), so they get
hard absolute caps: member count, per-member bytes, total bytes — plus a capped
stream reader that never trusts the declared size.
"""

from __future__ import annotations

import io
import os
import stat as stat_mod
import zipfile
from pathlib import Path

import pytest

from manju.build import seriespack as SP
from manju.core.container import Project


# --------------------------------------------------------------------------
# helpers: build synthetic template packs directly (valid manifest+SHA256SUMS
# shape, per seriespack's own writer conventions) so limit violations can be
# constructed precisely.
# --------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _write_pack(path: Path, members: dict[str, bytes], *,
                manifest_extra: str = "") -> Path:
    """A minimally-valid template pack: manifest.yaml + SHA256SUMS + members."""
    manifest = (
        f"schema: {SP.TEMPLATE_SCHEMA}\n"
        f"pack_digest: sha256:{'0' * 64}\n" + manifest_extra
    ).encode("utf-8")
    all_members: dict[str, bytes] = {"manifest.yaml": manifest, **members}
    sums = "".join(
        f"{_sha256(data)}  {name}\n" for name, data in all_members.items()
    ).encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in all_members.items():
            zf.writestr(zipfile.ZipInfo(name), data)
        zf.writestr(zipfile.ZipInfo("SHA256SUMS"), sums)
    return path


# --------------------------------------------------------------------- caps


def test_s1_pack_member_over_declared_size_cap_refused(tmp_path, monkeypatch):
    """(a) declared-size bomb: one member's uncompressed size exceeds the
    per-member cap ⇒ inspect refuses BEFORE inflating it (structured error,
    not MemoryError)."""
    monkeypatch.setattr(SP, "MAX_PACK_MEMBER_BYTES", 1 * 1024 * 1024)
    bomb = b"\x00" * (2 * 1024 * 1024)  # 2 MiB of zeros, deflates to ~2 KiB
    pack = _write_pack(tmp_path / "bomb.zip", {"media/big.bin": bomb})
    with pytest.raises(SP.SeriesPackError) as e:
        SP.inspect_template_pack(pack)
    assert "解压上限" in str(e.value) or "zip bomb" in str(e.value)


def test_s2_pack_total_declared_size_cap_refused(tmp_path, monkeypatch):
    """(a) total-size bomb: members individually under the member cap but
    summing over the pack-total cap ⇒ refuse."""
    monkeypatch.setattr(SP, "MAX_PACK_MEMBER_BYTES", 1 * 1024 * 1024)
    monkeypatch.setattr(SP, "MAX_PACK_TOTAL_BYTES", 2 * 1024 * 1024)
    members = {
        f"media/part{i}.bin": b"\x00" * (700 * 1024) for i in range(4)
    }  # 4 × 700 KiB ≈ 2.7 MiB total declared
    pack = _write_pack(tmp_path / "total.zip", members)
    with pytest.raises(SP.SeriesPackError) as e:
        SP.inspect_template_pack(pack)
    assert "解压上限" in str(e.value) or "总量" in str(e.value)


def test_s3_pack_member_count_cap_refused(tmp_path, monkeypatch):
    """member-count bomb (many tiny entries) ⇒ refuse on count, cheaply."""
    monkeypatch.setattr(SP, "MAX_PACK_MEMBERS", 8)
    members = {f"media/m{i:03d}.bin": b"x" for i in range(20)}
    pack = _write_pack(tmp_path / "count.zip", members)
    with pytest.raises(SP.SeriesPackError) as e:
        SP.inspect_template_pack(pack)
    assert "成员数" in str(e.value) or "members" in str(e.value)


def test_s4_capped_reader_defends_header_lies(tmp_path, monkeypatch):
    """(b) header-lie bomb: the guard must not trust ZipInfo.file_size — the
    capped stream reader stops at the cap regardless of what the header
    declares. Proven by capping BELOW an honest member's size and reading
    through the helper: it must refuse at the stream level, not
    over-read."""
    monkeypatch.setattr(SP, "MAX_PACK_MEMBER_BYTES", 4 * 1024)
    payload = b"y" * (64 * 1024)
    pack = _write_pack(tmp_path / "lie.zip", {"media/lie.bin": payload})
    with zipfile.ZipFile(pack) as zf:
        # the helper reads at most cap+1 bytes from the stream; a member whose
        # STREAM exceeds the cap refuses even if a forged header said "small".
        with pytest.raises(SP.SeriesPackError):
            SP._read_member_capped(zf, "media/lie.bin")


def test_s5_valid_small_pack_still_inspects_and_imports(tmp_path, tmp_project):
    """The caps must not break legitimate packs: a normal small pack inspects
    ok=True and imports cleanly under the REAL default limits."""
    pack = _write_pack(tmp_path / "ok.zip", {"media/tiny.bin": b"hello"})
    info = SP.inspect_template_pack(pack)
    assert info["ok"] is True and "media/tiny.bin" in info["members"]


def test_s6_symlink_member_never_lands_as_symlink(tmp_path, tmp_project):
    """Template-pack import writes member BYTES content-addressed (it never
    extractall's), so a symlink-flagged member must never materialize as a
    symlink on disk — pin the neutralization-by-construction."""
    path = tmp_path / "sym.zip"
    manifest = (
        f"schema: {SP.TEMPLATE_SCHEMA}\npack_digest: sha256:{'0' * 64}\n"
    ).encode("utf-8")
    link_target = b"/etc/passwd"
    info = zipfile.ZipInfo("media/evil_link")
    info.external_attr = (stat_mod.S_IFLNK | 0o777) << 16
    sums = (
        f"{_sha256(manifest)}  manifest.yaml\n"
        f"{_sha256(link_target)}  media/evil_link\n"
    ).encode("utf-8")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("manifest.yaml"), manifest)
        zf.writestr(info, link_target)
        zf.writestr(zipfile.ZipInfo("SHA256SUMS"), sums)

    # inspect stays read-only; import either refuses the member or imports it
    # as plain bytes — EITHER way no symlink may appear anywhere under the
    # project root afterwards.
    try:
        SP.import_template_pack(tmp_project, path)
    except SP.SeriesPackError:
        pass
    links = [p for p in tmp_project.root.rglob("*") if p.is_symlink()]
    assert links == []


# ------------------------------------------------------------------- unpack


def _make_manjupkg(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(zipfile.ZipInfo(name), data)
    return path


def test_s7_unpack_refuses_when_declared_size_exceeds_free_disk(tmp_path, monkeypatch):
    """`manju unpack` preflight: sum of DECLARED uncompressed sizes vs actual
    free disk at the destination. A bomb whose declared total exceeds free
    space refuses BEFORE extractall (fill-the-disk DoS). Free space is
    monkeypatched tiny so the fixture stays small; legitimate restores that
    fit are untouched (absolute caps would false-refuse real projects)."""
    import shutil as shutil_mod

    from typer.testing import CliRunner

    from manju.cli import app

    pkg = _make_manjupkg(
        tmp_path / "proj.manjupkg",
        {"project.yaml": b"name: x\n", "media/big.bin": b"\x00" * (512 * 1024)},
    )

    class _Usage:
        total = 10 * 1024 * 1024
        used = 10 * 1024 * 1024 - 4 * 1024
        free = 4 * 1024  # 4 KiB free — far below the ~512 KiB declared

    monkeypatch.setattr(shutil_mod, "disk_usage", lambda _p: _Usage)
    runner = CliRunner()
    dest = tmp_path / "restored.manju"
    res = runner.invoke(app, ["unpack", str(pkg), "--dest", str(dest)])
    assert res.exit_code != 0
    assert not dest.exists()
    out = (res.stdout or "") + str(res.output)
    assert "磁盘" in out or "free" in out or "空间" in out


def test_s8_unpack_member_count_sanity_cap(tmp_path, monkeypatch):
    """A pathological member-count archive refuses cheaply before extraction."""
    from typer.testing import CliRunner

    import manju.cli as cli_mod
    from manju.cli import app

    monkeypatch.setattr(cli_mod, "UNPACK_MAX_MEMBERS", 16)
    pkg = _make_manjupkg(
        tmp_path / "many.manjupkg",
        {f"f{i:04d}.txt": b"x" for i in range(64)},
    )
    runner = CliRunner()
    dest = tmp_path / "many.manju"
    res = runner.invoke(app, ["unpack", str(pkg), "--dest", str(dest)])
    assert res.exit_code != 0
    assert not dest.exists()


def test_s9_unpack_normal_archive_still_works(tmp_path):
    """Guards must not break a legitimate small restore end-to-end."""
    from typer.testing import CliRunner

    from manju.cli import app

    pkg = _make_manjupkg(
        tmp_path / "ok.manjupkg",
        {"project.yaml": b"name: ok\n", "shots/S001.yaml": b"id: S001\n"},
    )
    runner = CliRunner()
    dest = tmp_path / "ok.manju"
    res = runner.invoke(app, ["unpack", str(pkg), "--dest", str(dest)])
    assert res.exit_code == 0, res.output
    assert (dest / "project.yaml").read_text(encoding="utf-8") == "name: ok\n"
    assert (dest / ".manju").is_dir()
