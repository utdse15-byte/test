"""Cycle-57: every cancelable job kind has a KIND_ZH chip."""

import re
from pathlib import Path

from manju.gui.jobs import CANCELABLE_RUNNING_KINDS
from manju.gui import page as page_mod


def test_kind_zh_covers_all_cancelable_kinds() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    m = re.search(r"const KIND_ZH = \{(.*?)\n\s*\};", src, re.S)
    assert m, "KIND_ZH map missing"
    body = m.group(1)
    missing = [k for k in sorted(CANCELABLE_RUNNING_KINDS) if f"{k}:" not in body]
    assert not missing, f"KIND_ZH missing cancelable kinds: {missing}"
