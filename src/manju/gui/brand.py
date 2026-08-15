"""Small, local-only brand assets shared by the browser and Windows shortcut.

The GUI remains self-contained: these files ship inside the Python package and
are never fetched from a CDN.  Keeping one icon owner prevents the browser tab,
Start-menu shortcut and future package metadata from drifting into unrelated
marks.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

__all__ = ["app_icon_path", "app_icon_svg"]


def _asset(name: str):
    return resources.files("manju.gui").joinpath("assets", name)


def app_icon_svg() -> str:
    """Return the shipped SVG icon as UTF-8 text."""

    return _asset("manju-app.svg").read_text(encoding="utf-8")


def app_icon_path() -> Path:
    """Return the installed ``.ico`` path used by the Windows shortcut.

    Wheels installed by pip are unpacked into site-packages, so the resource is
    a stable real file for the lifetime of that versioned venv.  Raise loudly if
    packaging ever drops the icon instead of silently falling back to Python's
    executable icon.
    """

    path = Path(str(_asset("manju-app.ico")))
    if not path.is_file():
        raise FileNotFoundError(f"Manju application icon is missing: {path}")
    return path
