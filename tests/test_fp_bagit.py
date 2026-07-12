"""FP Loop U1 — `manju pack --bagit` serialized BagIt bag (roadmap item 8).

Red-first. `manju pack --bagit` writes an RFC 8493 SERIALIZED bag INSIDE the
same .manjupkg zip (opt-in; default pack output is unchanged):

    bagit.txt                 "BagIt-Version: 1.0\n" + "Tag-File-Character-Encoding: UTF-8\n"
    manifest-sha256.txt        payload manifest, lines "<hex>  data/<path>\n"
    bag-info.txt               DETERMINISTIC fields only — External-Identifier,
                               Bag-Software-Agent, Payload-Oxum; NO Bagging-Date
    tagmanifest-sha256.txt     digests of the tag files INCLUDING the MANJU_*
                               transport members (they ride as tag files at bag root)
    data/<project tree>        the payload

ONE FIXITY TRUTH PER FORMAT: in --bagit mode MANJU_FIXITY.json is OMITTED — the
BagIt manifests ARE the fixity authority. unpack + `manju fixity` AUTO-DETECT
the bag layout (bagit.txt member present) and verify payload + tag files;
restore extracts data/ as the project tree and drops tag files with the SAME
remove-on-mismatch discipline as the default path.

The member names and bagit.txt bytes are the CONTRACT — literal strings here,
deliberately NOT imported from cli (mirrors test_fp_fixity / test_fp_archive_meta).
The existing pack/fixity pins (test_fp_fixity.py, test_fp_archive_meta.py) must
stay green — this loop EXTENDS the region, it does not rewrite it.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.cli import app

# --- contract strings (black-box) ---
BAGIT = "bagit.txt"
MANIFEST = "manifest-sha256.txt"
TAGMANIFEST = "tagmanifest-sha256.txt"
BAGINFO = "bag-info.txt"
DATA = "data/"
FIXITY = "MANJU_FIXITY.json"
RESTORE = "MANJU_RESTORE.txt"
CONTRACTS = "MANJU_CONTRACTS.yaml"
TOOLCHAIN = "MANJU_TOOLCHAIN.json"
MANJU_TAGS = (CONTRACTS, RESTORE, TOOLCHAIN)
BAG_TAGS = (BAGIT, BAGINFO, MANIFEST)
BAGIT_TXT_BYTES = b"BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n"

runner = CliRunner()


# --------------------------------------------------------------------------
# helpers (mirroring test_fp_fixity's small-dup convention)
# --------------------------------------------------------------------------

def _seed(project) -> None:
    """A known media payload + a CJK-named payload file, so structure/tamper
    tests don't depend on the exact scaffold file set and CJK is exercised."""
    media = project.root / "media" / "probe.bin"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"probe-payload-" + b"x" * 40)
    (project.root / "场记").mkdir(exist_ok=True)
    (project.root / "场记" / "第一场.txt").write_text("雨夜便利店 场记\n", encoding="utf-8")


def _pack(project, out: Path, monkeypatch, *, bagit: bool = False) -> Path:
    _seed(project)
    monkeypatch.chdir(project.root)
    argv = ["pack", "--out", str(out)] + (["--bagit"] if bagit else [])
    res = runner.invoke(app, argv)
    assert res.exit_code == 0, res.output
    return out


def _rebuild(src: Path, dst: Path, *, drop=(), replace=None, add=None) -> Path:
    """Copy ``src`` into ``dst`` with member-level edits (tamper/delete/add).
    Members not touched are copied verbatim, so the manifests keep describing the
    ORIGINAL bytes — exactly the shape a corrupted/edited bag has in the wild."""
    replace = replace or {}
    add = add or {}
    drop = set(drop)
    with zipfile.ZipFile(src) as zin:
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            zout.comment = zin.comment
            for info in zin.infolist():
                nm = info.filename
                if nm in drop:
                    continue
                data = replace[nm] if nm in replace else zin.read(nm)
                zout.writestr(zipfile.ZipInfo(nm), data)
            for nm, data in add.items():
                zout.writestr(zipfile.ZipInfo(nm), data)
    return dst


def _members(archive: Path) -> list[str]:
    with zipfile.ZipFile(archive) as zf:
        return zf.namelist()


def _read(archive: Path, name: str) -> bytes:
    with zipfile.ZipFile(archive) as zf:
        return zf.read(name)


def _payload_map(archive: Path) -> dict[str, bytes]:
    """{rel (without data/): bytes} for every payload member of a bag."""
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(archive) as zf:
        for nm in zf.namelist():
            if nm.startswith(DATA) and not nm.endswith("/"):
                out[nm[len(DATA):]] = zf.read(nm)
    return out


def _parse_baginfo(raw: bytes) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        label, _, value = line.partition(":")
        fields[label.strip()] = value.strip()
    return fields


# =====================================================================
# default mode stays byte-identical / structurally unchanged
# =====================================================================

def test_default_mode_deterministic_and_bag_absent(tmp_project, tmp_path, monkeypatch):
    """Golden pin: default (no --bagit) pack is deterministic AND carries NONE
    of the bag members — MANJU_FIXITY.json is still the last member (the K pin)."""
    a = _pack(tmp_project, tmp_path / "a.manjupkg", monkeypatch)
    b = _pack(tmp_project, tmp_path / "b.manjupkg", monkeypatch)
    assert a.read_bytes() == b.read_bytes(), "default pack must stay deterministic"
    names = _members(a)
    assert names[-1] == FIXITY, "default mode keeps MANJU_FIXITY.json as last member"
    for bag_member in (BAGIT, BAGINFO, MANIFEST, TAGMANIFEST):
        assert bag_member not in names, f"default mode must not write {bag_member}"
    assert not any(n.startswith(DATA) for n in names), "default mode has no data/ payload prefix"


def test_default_mode_still_has_fixity_bagit_omits_it(tmp_project, tmp_path, monkeypatch):
    """One fixity truth per format: default ⇒ MANJU_FIXITY.json present; bagit ⇒
    MANJU_FIXITY.json OMITTED (the bag manifests are the authority)."""
    default = _pack(tmp_project, tmp_path / "d.manjupkg", monkeypatch)
    bag = _pack(tmp_project, tmp_path / "b.manjupkg", monkeypatch, bagit=True)
    assert FIXITY in _members(default)
    assert FIXITY not in _members(bag), (
        "bagit mode must OMIT MANJU_FIXITY.json — never two fixity authorities")


# =====================================================================
# bag structure
# =====================================================================

def test_bagit_exact_member_set(tmp_project, tmp_path, monkeypatch):
    bag = _pack(tmp_project, tmp_path / "bag.manjupkg", monkeypatch, bagit=True)
    names = set(_members(bag))
    payload = {n for n in names if n.startswith(DATA)}
    assert payload, "a bag must carry a data/ payload"
    expected_tags = set(BAG_TAGS) | {TAGMANIFEST} | set(MANJU_TAGS)
    assert names == payload | expected_tags, (
        f"unexpected member set; extra={names - payload - expected_tags}, "
        f"missing={(payload | expected_tags) - names}")
    assert FIXITY not in names


def test_bagit_txt_exact_bytes(tmp_project, tmp_path, monkeypatch):
    bag = _pack(tmp_project, tmp_path / "bag.manjupkg", monkeypatch, bagit=True)
    assert _read(bag, BAGIT) == BAGIT_TXT_BYTES


def test_bagit_manifest_line_format_and_digests(tmp_project, tmp_path, monkeypatch):
    bag = _pack(tmp_project, tmp_path / "bag.manjupkg", monkeypatch, bagit=True)
    raw = _read(bag, MANIFEST).decode("utf-8")
    lines = raw.splitlines()
    assert lines, "manifest must list payload files"
    listed: dict[str, str] = {}
    for line in lines:
        digest, sep, path = line.partition("  ")  # two-space separator
        assert sep == "  ", f"manifest line must use a two-space separator: {line!r}"
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
        assert path.startswith(DATA), f"payload manifest path must be under data/: {path!r}"
        listed[path] = digest
    with zipfile.ZipFile(bag) as zf:
        payload = {n for n in zf.namelist() if n.startswith(DATA)}
        assert set(listed) == payload, "manifest must list exactly the data/ members"
        for nm, want in listed.items():
            assert hashlib.sha256(zf.read(nm)).hexdigest() == want


def test_bagit_payload_oxum_math(tmp_project, tmp_path, monkeypatch):
    bag = _pack(tmp_project, tmp_path / "bag.manjupkg", monkeypatch, bagit=True)
    fields = _parse_baginfo(_read(bag, BAGINFO))
    assert "Payload-Oxum" in fields
    octets_str, dot, streams_str = fields["Payload-Oxum"].partition(".")
    assert dot == ".", "Payload-Oxum is octetcount.streamcount"
    with zipfile.ZipFile(bag) as zf:
        sizes = [zf.getinfo(n).file_size for n in zf.namelist() if n.startswith(DATA)]
    assert int(octets_str) == sum(sizes)
    assert int(streams_str) == len(sizes)


def test_bagit_baginfo_deterministic_fields_no_bagging_date(tmp_project, tmp_path, monkeypatch):
    bag = _pack(tmp_project, tmp_path / "bag.manjupkg", monkeypatch, bagit=True)
    fields = _parse_baginfo(_read(bag, BAGINFO))
    canonical = tmp_project.root.stem + ".manjupkg"
    assert fields.get("External-Identifier") == canonical
    assert fields.get("Bag-Software-Agent", "").startswith("manju "), fields
    assert "Payload-Oxum" in fields
    assert "Bagging-Date" not in fields, (
        "Bagging-Date is deliberately omitted for determinism (RFC 8493 §2.2.2 "
        "makes every bag-info element optional)")


def test_bagit_tagmanifest_covers_tag_files_including_transport(tmp_project, tmp_path, monkeypatch):
    bag = _pack(tmp_project, tmp_path / "bag.manjupkg", monkeypatch, bagit=True)
    raw = _read(bag, TAGMANIFEST).decode("utf-8")
    listed: dict[str, str] = {}
    for line in raw.splitlines():
        digest, sep, name = line.partition("  ")
        assert sep == "  "
        listed[name] = digest
    # covers the three standard tag files + the MANJU_* transport members
    for name in (BAGIT, BAGINFO, MANIFEST, *MANJU_TAGS):
        assert name in listed, f"tagmanifest must cover {name}"
    # never lists itself, never lists payload
    assert TAGMANIFEST not in listed
    assert not any(n.startswith(DATA) for n in listed)
    with zipfile.ZipFile(bag) as zf:
        for name, want in listed.items():
            assert hashlib.sha256(zf.read(name)).hexdigest() == want, name


def test_bagit_restore_note_states_bag_and_omissions_honestly(tmp_project, tmp_path, monkeypatch):
    bag = _pack(tmp_project, tmp_path / "bag.manjupkg", monkeypatch, bagit=True)
    note = _read(bag, RESTORE).decode("utf-8").lower()
    assert "bagit" in note, "the restore note must say this is a BagIt bag"
    # honest about the two deliberate omissions
    assert "manju_fixity.json" in note
    assert "bagging-date" in note


def test_bagit_two_packs_same_tree_byte_identical(tmp_project, tmp_path, monkeypatch):
    a = _pack(tmp_project, tmp_path / "a.manjupkg", monkeypatch, bagit=True)
    b = _pack(tmp_project, tmp_path / "b.manjupkg", monkeypatch, bagit=True)
    assert a.read_bytes() == b.read_bytes(), (
        "two --bagit packs of the same tree must be byte-identical (no timestamps)")


def test_bagit_cjk_filename_in_payload(tmp_project, tmp_path, monkeypatch):
    bag = _pack(tmp_project, tmp_path / "bag.manjupkg", monkeypatch, bagit=True)
    names = _members(bag)
    assert "data/场记/第一场.txt" in names, "CJK payload path is stored verbatim (UTF-8)"
    manifest = _read(bag, MANIFEST).decode("utf-8")
    assert "data/场记/第一场.txt" in manifest, "CJK path appears verbatim in the manifest"


# =====================================================================
# round-trip restore + tamper discipline (unpack auto-detect)
# =====================================================================

def test_bagit_round_trip_restore_equals_source(tmp_project, tmp_path, monkeypatch):
    bag = _pack(tmp_project, tmp_path / "bag.manjupkg", monkeypatch, bagit=True)
    payload = _payload_map(bag)
    dest = tmp_path / "restored.manju"
    res = runner.invoke(app, ["unpack", str(bag), "--dest", str(dest)])
    assert res.exit_code == 0, res.output
    assert (dest / ".manju").is_dir()
    # tag files dropped, data/ prefix gone — the restored tree IS the project
    for tag in (*BAG_TAGS, TAGMANIFEST, *MANJU_TAGS, FIXITY):
        assert not (dest / tag).exists(), f"transport tag file {tag} must be dropped"
    assert not (dest / "data").exists(), "the data/ wrapper must not survive restore"
    restored = {p.relative_to(dest).as_posix() for p in dest.rglob("*")
                if p.is_file() and not p.relative_to(dest).as_posix().startswith(".manju")}
    assert restored == set(payload), "restored tree must equal the packed payload"
    for rel, data in payload.items():
        assert (dest / rel).read_bytes() == data


def test_bagit_tampered_payload_fails_unpack_and_removes_dest(tmp_project, tmp_path, monkeypatch):
    bag = _pack(tmp_project, tmp_path / "bag.manjupkg", monkeypatch, bagit=True)
    bad = _rebuild(bag, tmp_path / "bad.manjupkg",
                   replace={"data/media/probe.bin": b"TAMPERED-DIFFERENT-BYTES"})
    dest = tmp_path / "bad.manju"
    res = runner.invoke(app, ["unpack", str(bad), "--dest", str(dest)])
    assert res.exit_code != 0, res.output
    assert not dest.exists(), "a failed-fixity bag restore must be REMOVED"
    assert "media/probe.bin" in res.output


def test_bagit_tampered_tag_file_fails_unpack_and_removes_dest(tmp_project, tmp_path, monkeypatch):
    bag = _pack(tmp_project, tmp_path / "bag.manjupkg", monkeypatch, bagit=True)
    bad = _rebuild(bag, tmp_path / "bad.manjupkg",
                   replace={TOOLCHAIN: b"{}"})  # a tag file covered by tagmanifest
    dest = tmp_path / "bad.manju"
    res = runner.invoke(app, ["unpack", str(bad), "--dest", str(dest)])
    assert res.exit_code != 0, res.output
    assert not dest.exists(), "tampering a tag file must fail the restore too"
    assert TOOLCHAIN in res.output


# =====================================================================
# fixity CLI auto-detect (verify without extract) + --info
# =====================================================================

def test_bagit_fixity_verifies_without_extract(tmp_project, tmp_path, monkeypatch):
    bag = _pack(tmp_project, tmp_path / "bag.manjupkg", monkeypatch, bagit=True)
    before = sorted(p.name for p in tmp_path.iterdir())
    res = runner.invoke(app, ["fixity", str(bag), "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["ok"] is True and payload["present"] is True
    assert payload["format"] == "bagit-1.0"
    assert payload["checked"] >= 1
    assert payload["mismatched"] == [] and payload["missing"] == [] and payload["extra"] == []
    assert sorted(p.name for p in tmp_path.iterdir()) == before, "fixity writes nothing"


def test_bagit_fixity_detects_payload_tamper(tmp_project, tmp_path, monkeypatch):
    bag = _pack(tmp_project, tmp_path / "bag.manjupkg", monkeypatch, bagit=True)
    bad = _rebuild(bag, tmp_path / "bad.manjupkg",
                   replace={"data/media/probe.bin": b"NOPE"})
    res = runner.invoke(app, ["fixity", str(bad), "--json"])
    assert res.exit_code != 0
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert any(r["path"] == "data/media/probe.bin" for r in payload["mismatched"])


def test_bagit_fixity_detects_tag_tamper(tmp_project, tmp_path, monkeypatch):
    bag = _pack(tmp_project, tmp_path / "bag.manjupkg", monkeypatch, bagit=True)
    bad = _rebuild(bag, tmp_path / "bad.manjupkg",
                   replace={BAGINFO: b"External-Identifier: forged\n"})
    res = runner.invoke(app, ["fixity", str(bad), "--json"])
    assert res.exit_code != 0
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert any(r["path"] == BAGINFO for r in payload["mismatched"])


def test_bagit_fixity_info_reports_format(tmp_project, tmp_path, monkeypatch):
    bag = _pack(tmp_project, tmp_path / "bag.manjupkg", monkeypatch, bagit=True)
    res = runner.invoke(app, ["fixity", str(bag), "--info", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["format"] == "bagit-1.0"
    assert payload["info"]["bag"]["format"] == "bagit-1.0"
    # human mode surfaces the bag format too, still read-only
    res2 = runner.invoke(app, ["fixity", str(bag), "--info"])
    assert res2.exit_code == 0, res2.output
    assert "bagit-1.0" in res2.output


def test_bagit_pack_json_reports_bag(tmp_project, tmp_path, monkeypatch):
    _seed(tmp_project)
    monkeypatch.chdir(tmp_project.root)
    out = tmp_path / "bag.manjupkg"
    res = runner.invoke(app, ["pack", "--out", str(out), "--bagit", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["bagit"] is True
    assert payload["format"] == "bagit-1.0"
    assert "." in payload["payload_oxum"]
