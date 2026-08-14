"""Shared document-level accessibility fragments for the local GUI.

The GUI has several independent server-rendered page modules, but they must all
expose the same document language, skip link, main landmark and no-JavaScript
fallback.  Keeping these tiny fragments here prevents the shells from drifting
again without introducing a new page framework or runtime state.
"""

from __future__ import annotations

import html

__all__ = [
    "HTML_LANG",
    "SKIP_LINK_HTML",
    "NOSCRIPT_HTML",
    "main_open",
]

HTML_LANG = "zh-CN"

SKIP_LINK_HTML = (
    '<a class="mj-skip-link" href="#main-content">跳到主要内容</a>'
)

NOSCRIPT_HTML = (
    '<noscript><p class="mj-noscript" role="alert">'
    '<strong>需要启用 JavaScript 才能使用工作台。</strong>'
    '<span>项目文件不会因此被修改；启用后重新加载即可。</span>'
    '</p></noscript>'
)


def main_open(css_class: str = "") -> str:
    """Return the one shared main landmark opening tag.

    ``tabindex=-1`` lets the skip link move keyboard focus into the actual page
    content without adding the main landmark to the normal tab order.
    """

    cls = css_class.strip()
    attr = f' class="{html.escape(cls, quote=True)}"' if cls else ""
    return f'<main id="main-content" tabindex="-1"{attr}>'
