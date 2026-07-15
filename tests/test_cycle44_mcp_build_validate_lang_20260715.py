"""Cycle-44: MCP build validates lang before run_build."""

from pathlib import Path

from manju.mcp import tools as mcp_tools


def test_mcp_build_validates_lang() -> None:
    src = Path(mcp_tools.__file__).read_text(encoding="utf-8")
    # _h_build body must call validate_lang
    assert "validate_lang" in src
    assert "invalid_argument" in src
    # extract _h_build region
    i = src.find("def _h_build")
    j = src.find("def _h_redo")
    chunk = src[i:j]
    assert "validate_lang" in chunk
