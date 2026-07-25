"""A POSIX-only call in a test must carry a Windows guard.

Found by the hard gate, not by us: run #255 went red on

    AttributeError: module 'os' has no attribute 'mkfifo'

from two cases in test_ledger_p0_safeio.py. The vector they test — plant a
FIFO where an output is about to be written — does not exist on Windows, so
the tests were right to exist and right to be POSIX-only. They simply had no
marker, and nothing on Linux could ever notice: `os.mkfifo` resolves fine
here, so a green local suite says nothing about it.

What makes this worth a guard rather than a one-line fix: the SAME wave got it
right in test_ledger_p0_provider_refs.py (`@pytest.mark.skipif(WINDOWS, ...)`)
and wrong here. The knowledge existed; only the enforcement was missing, and
the only thing that noticed was a 13-minute Windows job.

Deliberately narrow: it flags names that do not EXIST on Windows, so a missing
guard is a guaranteed AttributeError rather than a judgement call. Behavioural
differences (os.symlink needing privileges, os.link across filesystems) are a
different problem and are not guessed at here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

# Attributes absent from the `os` module on Windows. Each is an AttributeError
# waiting for the gate, never a subtle behaviour difference.
POSIX_ONLY = ("mkfifo", "mknod", "getuid", "geteuid", "setuid", "seteuid",
              "chown", "lchown", "fork", "getpgid", "setsid", "killpg")

CALL_RE = re.compile(r"\bos\.(" + "|".join(POSIX_ONLY) + r")\s*\(")
# A Windows guard in any of the shapes this repo actually uses.
GUARD_RE = re.compile(
    r"skipif\([^)]*(WINDOWS|os\.name|sys\.platform)"
    r"|pytestmark\s*=\s*pytest\.mark\.skipif"
    r"|hasattr\(os,", re.S)


def _test_files() -> list[Path]:
    return sorted((p for p in TESTS.rglob("test_*.py")), key=lambda p: p.as_posix())


def _users() -> list[tuple[Path, str, str]]:
    out = []
    for p in _test_files():
        body = p.read_text(encoding="utf-8")
        for m in CALL_RE.finditer(body):
            out.append((p, m.group(1), body))
    return out


def test_every_posix_only_os_call_sits_behind_a_windows_guard() -> None:
    offenders = []
    for path, name, body in _users():
        if not GUARD_RE.search(body):
            offenders.append(f"{path.name}: os.{name}()")
    assert offenders == [], (
        "POSIX-only os call with no Windows guard — this is an AttributeError "
        f"on the hard gate, invisible on Linux: {sorted(set(offenders))}")


def test_the_detector_sees_the_real_call_sites() -> None:
    """Guard the guard: a regex that matches nothing would pass forever."""
    found = {(p.name, n) for p, n, _ in _users()}
    assert found, "detector found no POSIX-only os calls at all"
    assert any(n == "mkfifo" for _, n in found), (
        f"the mkfifo call sites that turned the gate red are gone from the "
        f"detector's view: {sorted(found)}")


def test_the_two_gate_reddening_modules_are_guarded() -> None:
    """Named directly, so deleting the marker is loud rather than statistical."""
    for name in ("test_ledger_p0_safeio.py", "test_ledger_p0_provider_refs.py"):
        body = (TESTS / name).read_text(encoding="utf-8")
        assert "os.mkfifo" in body, f"{name}: no longer the case this pins"
        assert GUARD_RE.search(body), f"{name}: Windows guard is gone"


@pytest.mark.parametrize("snippet, guarded", [
    ('import os\nos.mkfifo(p)\n', False),
    ('WINDOWS = os.name == "nt"\n'
     '@pytest.mark.skipif(WINDOWS, reason="POSIX FIFO")\n'
     'def t():\n    os.mkfifo(p)\n', True),
    ('pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX")\n'
     'os.mkfifo(p)\n', True),
    ('if hasattr(os, "mkfifo"):\n    os.mkfifo(p)\n', True),
])
def test_the_guard_regex_classifies_correctly(snippet: str, guarded: bool) -> None:
    """The detector's own truth table — an over-eager GUARD_RE would silently
    excuse every file, which is the failure mode that matters."""
    assert CALL_RE.search(snippet), snippet
    assert bool(GUARD_RE.search(snippet)) is guarded, snippet
