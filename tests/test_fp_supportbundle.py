"""FP Loop F — redacted diagnostic support bundle (library).

Red-first tests for :mod:`manju.core.supportbundle`. The through-line is the
honesty core: **redaction-by-construction** plus a **self-scan tripwire** that
refuses to write a bundle in which any private path or credential survived.

Every test drives the real public entrypoint
:func:`manju.core.supportbundle.build_support_bundle` over a synthetic project
built from the shared conftest fixtures.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from manju.core import supportbundle
from manju.core.supportbundle import BundleError, build_support_bundle


# --------------------------------------------------------------------------- #
# helpers                                                                       #
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _iso_providers(monkeypatch, tmp_path):
    """Hermetic provider-manifest dir so config digests never touch a real
    ``~/.manju/providers`` and stay deterministic (absent unless a test fills it)."""
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "_providers_iso"))


def _write_events(project, records) -> None:
    """Write events.jsonl from a list of dict records (one JSON object per line)."""
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    (project.root / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _zip_members(path: Path) -> dict[str, bytes]:
    """Decompressed {arcname: bytes} for every member of the zip."""
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


# The three species of leak the redactor must erase, embedded in a realistic
# events tail. Keys are a deliberate mix: one secret-shaped key (masked whole)
# and several innocuous keys (whose string values must be masked by content).
_LEAKY_EVENTS = [
    {"actor": "ai", "action": "spend",
     "detail": {"authorization": "Bearer sk-fakefakefakefakefakefake123"}},
    {"actor": "engine", "action": "call",
     "detail": {"message": "auth failed: Authorization: Bearer sk-anotherfaketoken1234567890abc"}},
    {"actor": "ai", "action": "upload",
     "detail": {"callback_url": "https://cdn.example.com/x.mp4?signature=abcdEF123&Expires=99"}},
    {"actor": "ai", "action": "render",
     "detail": {"output_path": "/home/alice/雨夜便利店.manju/renders/final/final_v1.mp4"}},
    {"actor": "ai", "action": "render",
     "detail": {"win_path": "C:\\Users\\alice\\AppData\\manju\\cache.bin"}},
    {"actor": "ai", "action": "render",
     "detail": {"mac_path": "/Users/bob/Movies/proj.manju/timeline/timeline.json"}},
]

# Raw substrings that must NOT appear anywhere in the written bundle.
_FORBIDDEN = (
    "sk-fakefake", "sk-anotherfake", "?signature=", "Authorization:",
    "Bearer ", "/home/", "/Users/", "C:\\Users",
)


# --------------------------------------------------------------------------- #
# 1. secrets/paths/signed-urls are gone from the WRITTEN zip bytes              #
# --------------------------------------------------------------------------- #

def test_bundle_redacts_secrets_paths_and_signed_urls(tmp_project):
    _write_events(tmp_project, _LEAKY_EVENTS)
    dest = tmp_project.root / "bundle.zip"

    summary = build_support_bundle(tmp_project, dest)

    assert dest.exists()
    members = _zip_members(dest)
    blob = b"\n".join(members.values()).decode("utf-8", errors="replace")
    for needle in _FORBIDDEN:
        assert needle not in blob, f"forbidden marker survived into bundle: {needle!r}"

    # the self-scan verdict rides the summary, clean.
    assert summary["self_scan"]["ok"] is True
    assert summary["self_scan"]["hits"] == []
    # redaction actually happened, and it's counted in MANIFEST.json.
    manifest = json.loads(members["MANIFEST.json"])
    assert manifest["redaction"]["total"] > 0
    assert manifest["redaction"]["abs_paths_rewritten"] >= 3       # posix + win + mac
    assert manifest["redaction"]["signed_urls_masked"] >= 1
    # the redacted events tail is present and carries the placeholders, not secrets.
    tail = members["events-tail.txt"].decode("utf-8")
    assert "<redacted" in tail
    assert "final_v1.mp4" in tail                                  # basename kept
    assert "sk-" not in tail


# --------------------------------------------------------------------------- #
# 2. the self-scan tripwire actually fires when redaction is defeated           #
# --------------------------------------------------------------------------- #

def test_self_scan_tripwire_refuses_to_write_when_redactor_is_noop(tmp_project, monkeypatch):
    _write_events(tmp_project, _LEAKY_EVENTS)
    dest = tmp_project.root / "unredacted.zip"

    # defeat THE redactor — every collector routes through redact_record.
    monkeypatch.setattr(supportbundle, "redact_record", lambda obj, stats=None: obj)

    with pytest.raises(BundleError) as exc:
        build_support_bundle(tmp_project, dest)

    # the tripwire fired, and NO zip was written.
    assert not dest.exists()
    # the error names markers/members, never the secret text itself.
    msg = str(exc.value)
    assert "self-scan" in msg
    assert "sk-fakefake" not in msg and "/home/alice" not in msg


# --------------------------------------------------------------------------- #
# 3. no media bytes ever enter the bundle — only counts                         #
# --------------------------------------------------------------------------- #

def test_no_media_bytes_only_counts(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    media_bytes = take.media_path.read_bytes()
    assert media_bytes  # sanity: the take really has bytes on disk

    dest = tmp_project.root / "nomedia.zip"
    summary = build_support_bundle(tmp_project, dest)

    members = _zip_members(dest)
    for name, data in members.items():
        assert media_bytes not in data, f"media bytes leaked into {name}"
        assert b"fakevideo" not in data, f"media bytes leaked into {name}"

    # counts ARE present: one shot, one take in the '1' bucket.
    shape = summary["project_shape"]
    assert shape["shots"] == 1
    assert shape["takes_per_shot_histogram"]["1"] == 1
    # the honest "what's NOT here" declaration is recorded.
    assert any(s["what"] == "media bytes" for s in summary["skipped"])


# --------------------------------------------------------------------------- #
# 4. deterministic — same project state ⇒ byte-identical zip                     #
# --------------------------------------------------------------------------- #

def test_bundle_is_byte_deterministic(tmp_project):
    _write_events(tmp_project, _LEAKY_EVENTS[:3])
    a = tmp_project.root / "a.zip"
    b = tmp_project.root / "b.zip"

    build_support_bundle(tmp_project, a)
    build_support_bundle(tmp_project, b)

    assert a.read_bytes() == b.read_bytes()
    # fixed 1980 timestamp on every member (reproducible archive).
    with zipfile.ZipFile(a) as zf:
        for info in zf.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
        assert zf.namelist() == sorted(zf.namelist())              # sorted members


def test_building_the_bundle_does_not_mutate_the_project(tmp_project):
    """Determinism's precondition: it's a PURE READ (no event appended)."""
    _write_events(tmp_project, _LEAKY_EVENTS[:2])
    before = (tmp_project.root / "events.jsonl").read_bytes()
    build_support_bundle(tmp_project, tmp_project.root / "x.zip")
    after = (tmp_project.root / "events.jsonl").read_bytes()
    assert before == after


# --------------------------------------------------------------------------- #
# 5. absent optional tools ⇒ "missing", never a crash                           #
# --------------------------------------------------------------------------- #

def test_absent_tools_report_missing_not_crash(tmp_project, monkeypatch):
    monkeypatch.setenv("PATH", "")            # ffmpeg/ffprobe now unfindable

    dest = tmp_project.root / "notools.zip"
    summary = build_support_bundle(tmp_project, dest)          # must not raise

    tools = summary["environment"]["tools"]
    assert tools["ffmpeg"] == "missing"
    assert tools["ffprobe"] == "missing"
    # the interpreter facts still come through.
    assert summary["environment"]["python"]
    assert dest.exists()


# --------------------------------------------------------------------------- #
# 6. a malformed events line is counted + skipped, bundle still builds           #
# --------------------------------------------------------------------------- #

def test_malformed_events_line_counted_and_skipped(tmp_project):
    good = json.dumps({"actor": "ai", "action": "note", "detail": {"n": 1}}, ensure_ascii=False)
    (tmp_project.root / "events.jsonl").write_text(
        good + "\n" + "{this is a torn line, not json\n", encoding="utf-8"
    )

    dest = tmp_project.root / "malformed.zip"
    summary = build_support_bundle(tmp_project, dest)          # still builds

    assert dest.exists()
    assert summary["redaction"]["events_malformed"] == 1
    assert summary["redaction"]["events_included"] == 2       # good line + the marker line
    tail = _zip_members(dest)["events-tail.txt"].decode("utf-8")
    assert "<malformed line skipped>" in tail
    assert "torn line" not in tail                            # never included verbatim


# --------------------------------------------------------------------------- #
# 7. summary dict shape and the in-zip MANIFEST.json agree                       #
# --------------------------------------------------------------------------- #

def test_summary_dict_and_zip_manifest_agree(tmp_project):
    _write_events(tmp_project, _LEAKY_EVENTS[:2])
    dest = tmp_project.root / "agree.zip"

    summary = build_support_bundle(tmp_project, dest)
    parsed = json.loads(_zip_members(dest)["MANIFEST.json"])

    # every manifest key is echoed verbatim in the returned summary...
    for key, value in parsed.items():
        assert summary[key] == value
    # ...and the summary is a superset carrying the local output facts only.
    assert summary["output"] == str(dest)
    assert summary["output_bytes"] == dest.stat().st_size
    assert parsed["format"] == "support-bundle-manifest.1"     # NOT a manju.*/vN schema
    assert "MANIFEST.json" in parsed["members"]


# --------------------------------------------------------------------------- #
# 8. provider config DIGESTS only — the manifest is hashed, never read           #
# --------------------------------------------------------------------------- #

def test_provider_config_digest_only_never_reads_contents(tmp_project, tmp_path):
    pdir = tmp_path / "_providers_iso" / "openai"
    pdir.mkdir(parents=True)
    secret = "sk-realrealrealrealrealreal1234567890"
    (pdir / "provider.yaml").write_text(
        "type: generic_cloud\nauth:\n  key_env: OPENAI_KEY\n  api_key: " + secret + "\n",
        encoding="utf-8",
    )

    dest = tmp_project.root / "digest.zip"
    summary = build_support_bundle(tmp_project, dest)

    digests = summary["config_digests"]
    assert any(d["provider"] == "openai" and d["digest"].startswith("sha256:") for d in digests)
    # the digest was computed, but NO byte of the manifest's contents leaked.
    members = _zip_members(dest)
    for data in members.values():
        assert secret.encode() not in data
        assert b"OPENAI_KEY" not in data


def test_malformed_provider_and_missing_reports_do_not_crash(tmp_project):
    """Degrade, never crash: absent events/failures/providers are facts."""
    # a project with NO reports/failures.jsonl and an empty events.jsonl still builds.
    dest = tmp_project.root / "sparse.zip"
    summary = build_support_bundle(tmp_project, dest)
    assert dest.exists()
    skipped = {s["what"] for s in summary["skipped"]}
    assert "failures-tail.jsonl" in skipped


def test_cli_support_bundle_wrapper_end_to_end(tmp_project, monkeypatch):
    """The `manju support-bundle` CLI wrapper (orchestrator-wired, roadmap
    §8.5): builds a real bundle in the project cwd, JSON summary carries the
    manifest keys, and the zip exists with a MANIFEST.json member."""
    import json as _json

    from typer.testing import CliRunner

    from manju.cli import app

    _write_events(tmp_project, _LEAKY_EVENTS)
    monkeypatch.chdir(tmp_project.root)
    out = tmp_project.root / "diag.zip"
    res = CliRunner().invoke(
        app, ["support-bundle", "--out", str(out), "--json"])
    assert res.exit_code == 0, res.output
    summary = _json.loads(res.stdout)
    assert summary["format"] == "support-bundle-manifest.1"
    assert summary["self_scan"]["ok"] is True
    assert "MANIFEST.json" in summary["members"]
    assert out.exists() and "MANIFEST.json" in _zip_members(out)
