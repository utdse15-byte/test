"""No page shell may load the same script twice (found by driving the GUI).

`/webclient.js` declares `class ManjuApiError` at top level, so a second
`<script src>` for it is a SyntaxError that aborts the whole duplicate copy.
`pages_t.py` included GLOSSARY_HEAD (which already carries webclient.js) AND its
own copy, so opening 字幕 / 混音 / 打包 in a browser printed

    Uncaught SyntaxError: Identifier 'ManjuApiError' has already been declared

on every load. Nothing visibly broke — the first copy had already defined
everything — which is exactly how it survived: a real error on every page load
is noise the owner learns to scroll past, and the next genuine one hides in it.

Generic on purpose: the pin is "no shell loads any script twice", so the next
shell that composes a shared head stanza with its own tags cannot reintroduce
this for a different file.
"""

from __future__ import annotations

import re
from collections import Counter

import pytest

SRC_RE = re.compile(r'<script[^>]*\bsrc="([^"]+)"')


def _shell_html(kind: str) -> str:
    """Render each server-side page shell with the least ceremony possible."""
    if kind == "pages_t":
        from manju.gui.pages_t import _shell as shell_t

        return shell_t("字幕", "tok", "/subtitles", "<p>x</p>")
    if kind == "exports":
        from manju.gui.exports_page import _shell as shell_exports

        return shell_exports("导出中心", "tok", "<p>x</p>")
    from manju.gui.pages import _shell as shell

    return shell("审片", "tok", "/review", "<p>x</p>")


@pytest.mark.parametrize("kind", ["pages", "pages_t", "exports"])
def test_no_script_is_loaded_twice(kind: str) -> None:
    html = _shell_html(kind)
    counts = Counter(SRC_RE.findall(html))
    dupes = {src: n for src, n in counts.items() if n > 1}
    assert not dupes, f"{kind} shell loads these scripts more than once: {dupes}"


@pytest.mark.parametrize("kind", ["pages", "pages_t", "exports"])
def test_shell_still_loads_the_request_layer(kind: str) -> None:
    """The de-dup must not have removed the only copy."""
    srcs = SRC_RE.findall(_shell_html(kind))
    assert "/webclient.js" in srcs
