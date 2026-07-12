"""FP Loop Z1 — the ``qc/colorstats.py`` PIL adapter wall (packaging-defect fix).

The optimization audit found a latent packaging defect: ``color_stats`` did a
bare ``from PIL import Image`` while Pillow was declared NOWHERE in
``pyproject.toml``, ``build/doctor.py`` carried no PIL probe, and
``core/toolchain.py`` already tracked ``("PIL", "Pillow")``. On a clean install
(no toolbelt extras) the color-stats path crashed with a RAW ``ModuleNotFoundError``
while ``manju doctor`` reported green.

This suite pins the fix, mirroring the house adapter-wall pattern (exporters'
:class:`ExporterUnavailable`, edge_tts's ``ImportError`` → ``ProviderFailure``
wall, and the ``*Unavailable(RuntimeError)`` family throughout providers/media):

  * absence raises a STRUCTURED :class:`manju.qc.colorstats.ColorStatsUnavailable`
    (never a raw ``ImportError``) with the bilingual house message naming the
    exact fix ``pip install "manju[colorstats]"``, chaining the original
    ``ImportError`` as ``__cause__``;
  * ``build/doctor.py`` grows an INFORMATIONAL Pillow probe row that is
    present-when-installed and NEVER gates the aggregate ``ok`` (the toolbelt-row
    exit-code precedent);
  * the CLI surface (``manju doctor``) reports PIL state as structured data and
    never leaks a ``ModuleNotFoundError`` traceback.

Absence is simulated with the house ``sys.modules`` poisoning idiom
(``monkeypatch.setitem(sys.modules, "PIL", None)`` — the same mechanism
``tests/test_native_draft.py`` uses for ``pyJianYingDraft``: ``None`` in
``sys.modules`` makes any import of the name raise ``ImportError``). Pillow IS
installed in this environment, so the existing colorstats suite
(``tests/test_c15_color.py``) stays green.
"""

from __future__ import annotations

import json
import sys

import pytest
from typer.testing import CliRunner

from manju.build.doctor import run_doctor
from manju.qc import colorstats
from manju.qc.colorstats import ColorStatsUnavailable

from tests.fixtures.golden import GOLDEN_DIR
from tests.fixtures.golden.fake_reviewer import load_manifest

runner = CliRunner()

_CASES = {c["id"]: c for c in load_manifest()["cases"]}


def _img():
    return GOLDEN_DIR / _CASES["visual.scene.base"]["path"]


def _poison_pil(monkeypatch) -> None:
    """House idiom (tests/test_native_draft.py): ``None`` in ``sys.modules`` makes
    ``from PIL import Image`` raise ``ImportError`` — the exact mechanism a clean
    install (no Pillow) exhibits, without uninstalling anything."""
    monkeypatch.setitem(sys.modules, "PIL", None)
    monkeypatch.setitem(sys.modules, "PIL.Image", None)


# ------------------------------------------------------------------ the wall


def test_missing_pillow_raises_structured_unavailable(monkeypatch):
    """The bare import is gone: absence is the structured ColorStatsUnavailable,
    and the original ImportError is chained (never surfaced raw)."""
    _poison_pil(monkeypatch)
    with pytest.raises(ColorStatsUnavailable) as exc:
        colorstats.color_stats(_img())
    assert isinstance(exc.value.__cause__, ImportError)  # chained, not swallowed


def test_wall_message_is_bilingual_and_names_the_extra(monkeypatch):
    """The message names the EXACT fix and speaks both languages (house style)."""
    _poison_pil(monkeypatch)
    with pytest.raises(ColorStatsUnavailable) as exc:
        colorstats.color_stats(_img())
    msg = str(exc.value)
    assert 'pip install "manju[colorstats]"' in msg          # the exact fix
    assert "Pillow" in msg                                    # English half
    assert any("一" <= ch <= "鿿" for ch in msg)     # Chinese half


def test_compare_path_also_walls(monkeypatch):
    """compare_to_reference routes through color_stats, so it walls identically —
    the whole colorstats surface degrades structurally, not just one entry point."""
    _poison_pil(monkeypatch)
    with pytest.raises(ColorStatsUnavailable):
        colorstats.compare_to_reference(_img(), _img())


def test_unavailable_is_a_runtimeerror_family_member():
    """Structured family: a RuntimeError subclass (mirrors ExporterUnavailable /
    TtsUnavailable / PreviewUnavailable) that the CLI's existing RuntimeError /
    ``_fail`` handlers render as a clean line, never a traceback."""
    assert issubclass(ColorStatsUnavailable, RuntimeError)


# ------------------------------------------------------------------ doctor row


def test_doctor_pillow_row_present_when_installed():
    """Pillow IS installed here → a ✓ Pillow row, ok True (present-when-installed)."""
    rows = {c["name"]: c for c in run_doctor(None)["checks"]}
    assert "Pillow" in rows
    row = rows["Pillow"]
    assert row["ok"] is True
    assert row["line"].startswith("✓ Pillow")
    assert "installed" in row["detail"]


def test_doctor_pillow_row_is_informational_never_gates(monkeypatch, tmp_path):
    """Even absent, the Pillow row is informational (ok True, • glyph) and does
    NOT enter the aggregate ok — the toolbelt-row exit-code policy, verbatim."""
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "prov"))  # hermetic
    _poison_pil(monkeypatch)
    result = run_doctor(None)
    rows = {c["name"]: c for c in result["checks"]}
    assert "Pillow" in rows
    row = rows["Pillow"]
    assert row["ok"] is True                     # informational, never gates
    assert row["line"].startswith("• Pillow")
    assert 'manju[colorstats]' in row["detail"]
    # the aggregate ok is decided ONLY by env tools + provider manifests +
    # project check — an absent (or present) Pillow row must never enter it.
    gating = [c for c in result["checks"]
              if c["name"] in ("ffmpeg", "ffprobe", "project_check", "provider_manifest")
              or c["name"].startswith("provider:")]
    assert result["ok"] is all(c["ok"] for c in gating)


# ------------------------------------------------------------------ CLI path


def test_doctor_cli_reports_pillow_as_structured_data(monkeypatch, tmp_path):
    """``manju doctor --json`` surfaces the Pillow probe as structured JSON and
    never leaks a traceback — the CLI path returns structured data, not a crash."""
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "prov"))
    from manju.cli import app

    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    assert "Traceback" not in result.output
    assert "ModuleNotFoundError" not in result.output
    payload = json.loads(result.output)
    names = {c["name"] for c in payload["checks"]}
    assert "Pillow" in names


def test_doctor_cli_no_traceback_when_pillow_absent(monkeypatch, tmp_path):
    """The clean-install symptom is gone: even with PIL absent, the CLI degrades
    STRUCTURALLY (Pillow row ok True, informational) instead of raising the raw
    ModuleNotFoundError the audit found — no traceback ever reaches the terminal."""
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "prov"))
    _poison_pil(monkeypatch)
    from manju.cli import app

    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    assert "Traceback" not in result.output
    assert "ModuleNotFoundError" not in result.output
    payload = json.loads(result.output)
    pil = next(c for c in payload["checks"] if c["name"] == "Pillow")
    assert pil["ok"] is True                     # informational, never gates
    assert "not installed" in pil["detail"]
