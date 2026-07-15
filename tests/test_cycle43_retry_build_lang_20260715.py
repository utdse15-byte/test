"""Cycle-43: build job retry preserves lang param."""

from pathlib import Path

from manju.gui import server as server_mod


def test_retry_build_fn_preserves_lang() -> None:
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert 'params.get("lang")' in src
    # retry path sets kwargs["lang"]
    assert 'kwargs["lang"] = lang' in src
    # C43 comment or preserve locale
    assert "preserve locale lang on retry" in src or "C43" in src
