"""TRISURFACE F-02 — voice and manual registrations must reach the run ledger
LIVE, not only via ``rebuild-index``.

Red-first evidence (TRISURFACE_TEST_2026-07-29.md §F-02): after real use the
organic ledger held 7 rows while ``manju rebuild-index`` reconstructed 13 —
the Edge-TTS voice rows and the manual_import rows existed only in the rebuilt
state. Consequences: ``manju tasks``/``spend``/``perf`` were blind to
potentially-PAID TTS during normal use (spend's whole point is 事后逐笔记账),
and the §3 discipline("delete .manju/ and rebuild reconstructs it")silently
implied rebuild ≡ organic, which it wasn't.

Owners: ``Project.register_voice_take`` (every voice path funnels through it —
providers, voicefix, align slices, the locale wrapper) and
``providers.manual.register_manual_take`` (select --file AND ingest video
takes). Rows mirror the rebuild derivation byte-for-byte — pinned here by
comparing live rows against a fresh ``rebuild()`` of the same project.
"""

from __future__ import annotations

from pathlib import Path

from manju.core.models import RemoteJobInfo, TakeSidecar, VoiceTakeSidecar
from manju.providers.manual import register_manual_take
from manju.runtime.state import RuntimeState


def _runs(project):
    with RuntimeState(project.root) as state:
        return state.run_log(50)


def _voice_sidecar(cost=None, currency=None):
    remote = {"job_id": None, "cost": cost}
    if currency is not None:
        remote["currency"] = currency
    return VoiceTakeSidecar(
        provider="edge",
        voice_hash="sha256:beef",
        params={"text": "今天也撑过来了。", "speaker": "linxia"},
        remote=RemoteJobInfo(**remote),
    )


def _register_voice(project, shot_id, *, lang=None, cost=None, currency=None):
    src = project.root / "_v.wav"
    src.write_bytes(b"RIFFfake-voice-bytes")
    dest = project.register_voice_take(project.load_shot(shot_id).id, src,
                                       _voice_sidecar(cost, currency), lang=lang)
    src.unlink()
    return dest


# ----------------------------------------------------------------- voice


def test_voice_registration_records_a_live_ledger_row(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _register_voice(tmp_project, "S001", cost=0.05, currency="CNY")
    rows = [r for r in _runs(tmp_project) if r["provider"] == "edge"]
    assert rows, "voice registration left no live ledger row"
    row = rows[0]
    assert row["shot"] == "S001"
    assert row["status"] == "succeeded"
    assert row["take"] == "voice_take_01"
    # the PAID facts ride the row — this is the spend-visibility core.
    assert row["cost"] == 0.05
    assert row["currency"] == "CNY"


def test_locale_voice_row_carries_the_locales_label(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _register_voice(tmp_project, "S001", lang="en")
    rows = [r for r in _runs(tmp_project) if r["provider"] == "edge"]
    assert rows and rows[0]["take"] == "locales/en/voice_take_01"


# ----------------------------------------------------------------- manual


def test_manual_take_registration_records_a_live_ledger_row(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    src = tmp_project.root / "media" / "imports" / "clip.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"fake-mp4-bytes")
    register_manual_take(tmp_project, "S001", src)
    rows = [r for r in _runs(tmp_project) if r["provider"] == "manual_import"]
    assert rows, "manual registration left no live ledger row"
    assert rows[0]["shot"] == "S001"
    assert rows[0]["take"] == "take_01"
    assert rows[0]["cost"] == 0.0


# ------------------------------------------------- rebuild ≡ organic invariant


def test_live_rows_match_a_fresh_rebuild_exactly(tmp_project, add_shot):
    """§3's implicit contract, now explicit: the ledger a session grows must
    equal the ledger ``rebuild()`` derives from the same sidecars — same
    (shot, provider, take, cost, currency) multiset."""
    add_shot(tmp_project, "S001")
    _register_voice(tmp_project, "S001", cost=0.05, currency="CNY")
    _register_voice(tmp_project, "S001", lang="en")
    src = tmp_project.root / "clip.mp4"
    src.write_bytes(b"fake-mp4-bytes")
    register_manual_take(tmp_project, "S001", src)

    def facts(rows):
        return sorted((r["shot"], r["provider"], r["take"],
                       float(r["cost"] or 0), r["currency"]) for r in rows)

    live = facts(_runs(tmp_project))
    with RuntimeState(tmp_project.root) as state:
        state.rebuild(tmp_project)
    rebuilt = facts(_runs(tmp_project))
    assert live == rebuilt


# ------------------------------------------------------------- best effort


def test_ledger_failure_never_fails_the_registration(tmp_project, add_shot,
                                                     monkeypatch):
    """The ledger is disposable bookkeeping (§3/§8.3) — a broken SQLite must
    not break the append-only media registration itself."""
    import manju.runtime.state as state_mod

    def _boom(*a, **k):
        raise RuntimeError("sqlite is on fire")

    monkeypatch.setattr(state_mod.RuntimeState, "__enter__", _boom)
    add_shot(tmp_project, "S001")
    dest = _register_voice(tmp_project, "S001")
    assert Path(dest).exists()
    src = tmp_project.root / "clip.mp4"
    src.write_bytes(b"fake-mp4-bytes")
    take = register_manual_take(tmp_project, "S001", src)
    assert take.media_path.exists()
