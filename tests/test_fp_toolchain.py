"""FP Loop H — ``manju.toolchain-manifest/v1`` record-only reproducibility
evidence (roadmap §7.7).

Red-first pins, in four groups:

* **capture** — the manifest builds on this machine with the real ffmpeg /
  ffprobe ``-version`` first lines verbatim; an absent tool is the honest
  string ``"missing"`` (tools) / ``False`` (optional presence), never a crash;
* **determinism** — two calls on an unchanged machine yield an identical
  document and digest; the digest covers ``facts`` ONLY (volatile / envelope
  mutations never move it); timestamps stay OUT this loop (``volatile == {}``);
* **hygiene** — the serialized document leaks no absolute path, hostname or
  username (scan for ``/home/``, ``/root/``, ``/Users/``, ``C:\\`` and friends;
  fonts are recorded as basename + content hash only);
* **inertness** — ``write_toolchain_manifest`` is a derived, deletable
  projection under ``reports/toolchain/<digest>.json``; deleting it changes
  nothing; a grep-pin proves no build/core/providers/runtime path ever reads
  it back (record-only: content-key wiring is a declared FUTURE step in the
  module docstring, mirroring the fps migration pattern);
* **drift** — ``toolchain_drift`` is a pure compare returning exactly the
  changed fact rows ``{"fact", "old", "new"}``, and the digest moves iff a
  fact changed.
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import shutil
import re
import subprocess
from pathlib import Path

import pytest

from manju.core.hashing import canonical_json, hash_value
from manju.core.toolchain import (
    SCHEMA,
    manifest_digest,
    toolchain_dir,
    toolchain_drift,
    toolchain_manifest,
    write_toolchain_manifest,
)

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --------------------------------------------------------------------------- #
# capture — real tools verbatim, absent tools honest, never a crash            #
# --------------------------------------------------------------------------- #


def test_manifest_schema_and_facts_volatile_split():
    doc = toolchain_manifest()
    assert doc["schema"] == SCHEMA
    facts = doc["facts"]
    for block in ("manju", "python", "os", "tools", "optional_tools", "deps",
                  "fonts", "locale"):
        assert block in facts, f"missing fact block {block!r}"
    # RECORD-ONLY scope pin: nothing volatile this loop — no timestamps, no
    # counters. The document is reproducible on an unchanged machine.
    assert doc["volatile"] == {}
    assert doc["manifest_digest"] == manifest_digest(doc)


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe required")
def test_real_ffmpeg_versions_captured_verbatim():
    """On this machine the real ``-version`` first lines are carried verbatim."""
    facts = toolchain_manifest()["facts"]
    assert facts["tools"]["ffmpeg"].startswith("ffmpeg version")
    assert facts["tools"]["ffprobe"].startswith("ffprobe version")
    # first line only — the configuration line (which carries --prefix paths)
    # must never be captured
    assert "\n" not in facts["tools"]["ffmpeg"]
    assert "configuration:" not in facts["tools"]["ffmpeg"]


def test_python_os_locale_facts_are_machine_truth():
    facts = toolchain_manifest()["facts"]
    assert facts["python"]["version"] == platform.python_version()
    assert facts["python"]["implementation"] == platform.python_implementation()
    assert facts["os"]["system"] == platform.system()
    assert facts["os"]["release"] == platform.release()
    assert facts["os"]["machine"] == platform.machine()
    assert "hostname" not in json.dumps(facts["os"])  # system/release/machine ONLY
    assert facts["locale"]["filesystem_encoding"] == __import__("sys").getfilesystemencoding()
    # LANG is presence-only — the VALUE never enters the document.
    assert facts["locale"]["lang_set"] is ("LANG" in os.environ)
    lang = os.environ.get("LANG")
    if lang:
        assert lang not in canonical_json(facts["locale"])


def test_key_deps_present_or_absent_never_crash():
    """Each key dep is a version string when installed, the literal ``"absent"``
    when not — importing an absent dep must never raise."""
    deps = toolchain_manifest()["facts"]["deps"]
    for name in ("pydantic", "typer", "PyYAML", "Pillow", "OpenTimelineIO",
                 "hypothesis"):
        assert name in deps
        assert isinstance(deps[name], str) and deps[name], name
    # this repo declares pydantic/typer/PyYAML as hard deps — they must resolve
    for hard in ("pydantic", "typer", "PyYAML"):
        assert deps[hard] != "absent"


def test_absent_tools_are_missing_never_a_crash(monkeypatch, tmp_path):
    """Empty PATH ⇒ ffmpeg/ffprobe are the string ``"missing"``, optional tools
    are ``False`` — the manifest still builds (absence is a fact, not an error).

    The absence world must ALSO empty the Windows install roots: since
    find_chromium learned Chrome/Edge's canonical install locations, an empty
    PATH alone no longer simulates a Chromium-less machine on a runner that
    has Chrome installed (the probe honestly answered True — the gate caught
    the incomplete fabrication, not a product bug)."""
    emptybin = tmp_path / "emptybin"
    emptybin.mkdir()
    monkeypatch.setenv("PATH", str(emptybin))
    monkeypatch.delenv("CHROME_BIN", raising=False)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "no-browsers"))
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.setenv(env, str(emptybin))
    doc = toolchain_manifest()
    facts = doc["facts"]
    assert facts["tools"]["ffmpeg"] == "missing"
    assert facts["tools"]["ffprobe"] == "missing"
    assert facts["optional_tools"]["tesseract"] is False
    assert facts["optional_tools"]["chromium"] is False
    # fonts degrade to a well-formed entry or "unknown" — never an exception
    entry = facts["fonts"]["drawtext_cjk"]
    assert entry == "unknown" or (isinstance(entry, dict)
                                  and set(entry) == {"basename", "sha256"})
    assert doc["manifest_digest"] == manifest_digest(doc)


def test_font_inventory_is_basename_plus_content_hash_only():
    """The renderer's font (media/card.find_font — the card burn + captions
    overlay locator) is recorded as basename + sha256, or ``"unknown"``."""
    entry = toolchain_manifest()["facts"]["fonts"]["drawtext_cjk"]
    if entry == "unknown":
        pytest.skip("no CJK-capable font locatable on this machine (honest gap)")
    assert set(entry) == {"basename", "sha256"}
    assert "/" not in entry["basename"] and "\\" not in entry["basename"]
    assert entry["sha256"].startswith("sha256:")
    assert len(entry["sha256"].split(":", 1)[1]) == 64


# --------------------------------------------------------------------------- #
# determinism — identical doc + digest on an unchanged machine                 #
# --------------------------------------------------------------------------- #


def test_two_calls_identical_document_and_digest():
    a, b = toolchain_manifest(), toolchain_manifest()
    assert a == b
    assert canonical_json(a) == canonical_json(b)  # byte-identical serialization
    assert manifest_digest(a) == manifest_digest(b) == a["manifest_digest"]


def test_digest_covers_facts_only():
    doc = toolchain_manifest()
    mutated = json.loads(json.dumps(doc))
    mutated["volatile"] = {"noise": 1}          # envelope mutation…
    mutated["schema"] = "manju.other/v9"
    assert manifest_digest(mutated) == manifest_digest(doc)  # …never moves it
    moved = json.loads(json.dumps(doc))
    moved["facts"]["tools"]["ffmpeg"] = "ffmpeg version 999.0"  # a FACT change…
    assert manifest_digest(moved) != manifest_digest(doc)       # …always does
    assert manifest_digest(doc) == hash_value(doc["facts"])     # facts, whole, only


def test_manifest_digest_refuses_a_factless_document():
    with pytest.raises(ValueError):
        manifest_digest({"schema": SCHEMA})


# --------------------------------------------------------------------------- #
# hygiene — no absolute paths, no hostname, no username in the document        #
# --------------------------------------------------------------------------- #


def test_no_path_leak_no_hostname_no_username():
    serialized = json.dumps(toolchain_manifest(), ensure_ascii=False)
    for needle in ("/home/", "/root/", "/Users/", "C:\\", "/usr/", "/opt/",
                   "/tmp/", "/var/", "\\Users\\"):
        assert needle not in serialized, f"absolute-path leak: {needle!r}"
    hostname = platform.node()
    if hostname and len(hostname) >= 4 and hostname != "localhost":
        assert hostname not in serialized, "hostname leaked into the manifest"
    users = {os.environ.get("USER"), os.environ.get("LOGNAME")}
    try:
        users.add(getpass.getuser())            # os.getlogin-family pattern
    except Exception:
        pass
    for user in users:
        # "root" alone is covered by the "/root/" literal above; a bare generic
        # token would only false-positive, never catch a real leak.
        if user and user != "root":
            assert user not in serialized, f"username {user!r} leaked"


# --------------------------------------------------------------------------- #
# inertness — derived, deletable, never read back (record-only)                #
# --------------------------------------------------------------------------- #


def test_write_is_content_addressed_deterministic_and_deletable(tmp_project):
    doc = toolchain_manifest()
    path = write_toolchain_manifest(tmp_project, doc)
    assert path == toolchain_dir(tmp_project) / f"{doc['manifest_digest'].split(':')[-1]}.json"
    assert path.is_file()
    round_tripped = json.loads(path.read_text(encoding="utf-8"))
    assert round_tripped == doc
    first_bytes = path.read_bytes()
    assert write_toolchain_manifest(tmp_project, doc) == path
    assert path.read_bytes() == first_bytes      # byte-stable re-write
    path.unlink()                                # deletable projection:
    assert toolchain_manifest() is not None      # nothing consumed it; re-derivable


def test_write_refuses_a_tampered_embedded_digest(tmp_project):
    doc = json.loads(json.dumps(toolchain_manifest()))
    doc["manifest_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError):
        write_toolchain_manifest(tmp_project, doc)


def test_grep_pin_never_read_by_build_core_providers_runtime():
    """RECORD-ONLY pin: no build/core/providers/runtime module references the
    derived dir or imports the module — the manifest is evidence, never an
    execution/caching/authorization input. (cli.py — the one writer surface —
    lives outside these dirs on purpose.)"""
    import manju

    root = Path(manju.__file__).resolve().parent  # src/manju
    hits: list[str] = []
    pattern = r"reports/toolchain|core\.toolchain|from \.toolchain|core import toolchain"
    for sub in ("build", "core", "providers", "runtime"):
        out = subprocess.run(
            ["grep", "-rnE", pattern, str(root / sub),
             "--include=*.py", "--exclude-dir=__pycache__"],
            capture_output=True, text=True).stdout
        for ln in out.splitlines():
            if not ln.strip():
                continue
            # Windows grep output: D:\a\...\core/toolchain.py:12:... — the
            # drive colon breaks a naive split(":", 1) and the separators mix.
            path_part = re.match(r"^(?:[A-Za-z]:)?[^:]*",
                                 ln.replace("\\", "/")).group(0)
            if "core/toolchain.py" in path_part:
                continue
            hits.append(ln)
    assert hits == [], f"a build/core/providers/runtime path consumes the manifest: {hits}"


# --------------------------------------------------------------------------- #
# drift — pure compare, exactly the changed rows                               #
# --------------------------------------------------------------------------- #

_OLD_FACTS = {
    "tools": {"ffmpeg": "ffmpeg version 6.1.1", "ffprobe": "ffprobe version 6.1.1"},
    "python": {"version": "3.11.9"},
    "fonts": {"drawtext_cjk": {"basename": "wqy-zenhei.ttc", "sha256": "sha256:" + "a" * 64}},
}
_NEW_FACTS = {
    "tools": {"ffmpeg": "ffmpeg version 7.0.2", "ffprobe": "ffprobe version 6.1.1"},
    "python": {"version": "3.11.9"},
    "fonts": {"drawtext_cjk": {"basename": "wqy-zenhei.ttc", "sha256": "sha256:" + "b" * 64}},
    "locale": {"lang_set": False},
}


def test_drift_yields_exactly_the_changed_rows_sorted():
    rows = toolchain_drift({"facts": _OLD_FACTS}, {"facts": _NEW_FACTS})
    assert rows == [
        {"fact": "fonts.drawtext_cjk.sha256",
         "old": "sha256:" + "a" * 64, "new": "sha256:" + "b" * 64},
        {"fact": "locale.lang_set", "old": None, "new": False},
        {"fact": "tools.ffmpeg",
         "old": "ffmpeg version 6.1.1", "new": "ffmpeg version 7.0.2"},
    ]


def test_drift_removed_fact_row_has_new_none():
    rows = toolchain_drift({"facts": _NEW_FACTS}, {"facts": _OLD_FACTS})
    assert {"fact": "locale.lang_set", "old": False, "new": None} in rows


def test_drift_identical_docs_is_empty_and_digest_stable():
    assert toolchain_drift({"facts": _OLD_FACTS}, {"facts": _OLD_FACTS}) == []
    assert manifest_digest({"facts": _OLD_FACTS}) == manifest_digest(
        {"facts": json.loads(json.dumps(_OLD_FACTS))})
    # coupling: drift non-empty ⟺ digest moved
    assert manifest_digest({"facts": _OLD_FACTS}) != manifest_digest({"facts": _NEW_FACTS})


def test_drift_accepts_bare_facts_mappings_too():
    assert toolchain_drift(_OLD_FACTS, _OLD_FACTS) == []
    rows = toolchain_drift(_OLD_FACTS, _NEW_FACTS)
    assert any(r["fact"] == "tools.ffmpeg" for r in rows)


def test_drift_of_two_live_manifests_is_empty():
    a, b = toolchain_manifest(), toolchain_manifest()
    assert toolchain_drift(a, b) == []


# --------------------------------------------------------------------------- #
# CLI — manju toolchain [--write] [--json] [--diff OLD.json]                   #
# --------------------------------------------------------------------------- #


def test_cli_toolchain_json_runs_without_a_project(tmp_path, monkeypatch):
    """Machine facts need no project — only --write resolves one."""
    from typer.testing import CliRunner

    from manju.cli import app

    monkeypatch.chdir(tmp_path)  # NOT a manju project
    res = CliRunner().invoke(app, ["toolchain", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["manifest"]["schema"] == SCHEMA
    assert payload["manifest"]["manifest_digest"].startswith("sha256:")


def test_cli_toolchain_write_lands_in_reports_toolchain(tmp_project, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    monkeypatch.chdir(tmp_project.root)
    res = CliRunner().invoke(app, ["toolchain", "--write", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    rel = payload["written"]
    assert rel.startswith("reports/toolchain/") and rel.endswith(".json")
    assert not Path(rel).is_absolute()           # project-relative in output
    assert (tmp_project.root / rel).is_file()


def test_cli_toolchain_diff_reports_drift_rows_exit_zero(tmp_path, monkeypatch):
    """Drift is evidence, never an error — exit 0 with structured rows."""
    from typer.testing import CliRunner

    from manju.cli import app

    old = toolchain_manifest()
    mutated = json.loads(json.dumps(old))
    mutated["facts"]["tools"]["ffmpeg"] = "ffmpeg version 0.0-synthetic"
    old_path = tmp_path / "old.json"
    old_path.write_text(json.dumps(mutated), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    res = CliRunner().invoke(app, ["toolchain", "--diff", str(old_path), "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    facts_now = payload["manifest"]["facts"]
    expected = [] if facts_now["tools"]["ffmpeg"] == "ffmpeg version 0.0-synthetic" \
        else [{"fact": "tools.ffmpeg", "old": "ffmpeg version 0.0-synthetic",
               "new": facts_now["tools"]["ffmpeg"]}]
    assert payload["drift"] == expected


def test_cli_toolchain_diff_unreadable_old_fails_structured(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    monkeypatch.chdir(tmp_path)
    res = CliRunner().invoke(
        app, ["toolchain", "--diff", str(tmp_path / "nope.json"), "--json"])
    assert res.exit_code != 0
    assert json.loads(res.stdout)["code"] == "toolchain_diff_unreadable"
