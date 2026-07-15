"""Cycle-5: MCP build/qc lang surface + subpage job envelopes."""

from pathlib import Path

from manju.gui import ingest_page as ingest_mod
from manju.gui import series_page as series_mod
from manju.mcp import tools as mcp_tools


def test_mcp_build_schema_has_lang() -> None:
    props = mcp_tools.TOOLS["build"]["inputSchema"]["properties"]
    assert "lang" in props


def test_mcp_build_handler_passes_lang() -> None:
    import inspect

    src = inspect.getsource(mcp_tools._h_build)
    assert "lang=" in src


def test_ingest_accepts_200_with_job() -> None:
    src = Path(ingest_mod.__file__).read_text(encoding="utf-8")
    assert "res.status === 202 || res.status === 200" in src


def test_series_accepts_200_with_job() -> None:
    src = Path(series_mod.__file__).read_text(encoding="utf-8")
    assert "res.status === 202 || res.status === 200" in src
