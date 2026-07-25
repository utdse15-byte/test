"""The Windows invariants, checked from Linux.

`.github/workflows/windows-ci.yml` is the hard gate, and it cannot run in a
Linux dev sandbox — but the properties it defends are written down (CLAUDE.md
§Windows specifics), and most of them are checkable as SOURCE facts anywhere.
Auditing them by hand once found two real cross-platform defects (scaffolds
written with CRLF; six locale scans sorting Path objects). This file keeps that
audit from having to be remembered.

What is pinned here — and equally important, what is NOT:

* Pinned: the single-owner rules (one filtergraph escaper, one command
  splitter), the msvcrt lock implementers, explicit UTF-8 on subprocess decode,
  and no CR in a scaffolded project.
* NOT pinned: anything that needs Windows semantics to observe — case-folded
  PurePath ordering, real msvcrt behaviour, CreateProcess quoting. Those only
  CI can answer, and a test that "passes" here without exercising them would be
  worse than no test, because it would read as coverage.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "manju"
PY = sorted(SRC.rglob("*.py"), key=lambda p: p.as_posix())


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _call_body(src: str, open_paren: int) -> str:
    """The text of a call, from its OPENING paren to the matching close.

    Counting must start AT the paren: starting from the callee name leaves the
    depth at 0 for every leading character, so the very first index breaks the
    loop and the "body" is two characters long."""
    assert src[open_paren] == "(", src[open_paren:open_paren + 20]
    depth = 0
    for i in range(open_paren, min(len(src), open_paren + 2000)):
        depth += (src[i] == "(") - (src[i] == ")")
        if depth == 0:
            return src[open_paren:i + 1]
    return src[open_paren:open_paren + 2000]


# ------------------------------------------------------------- single owners


def test_only_local_cmd_splits_command_templates() -> None:
    """Non-POSIX splitting lives in providers/local_cmd._split_command."""
    users = {p.relative_to(SRC).as_posix() for p in PY
             if re.search(r"^\s*[^#\n]*shlex\.split\(", _text(p), re.M)}
    assert users <= {"providers/local_cmd.py"}, (
        f"shlex.split outside the one owner: {users}")


def test_only_render_defines_the_filtergraph_escaper() -> None:
    """media/render._escape_filter_path is THE owner; others import it."""
    definers = {p.relative_to(SRC).as_posix() for p in PY
                if "def _escape_filter_path" in _text(p)}
    assert definers == {"media/render.py"}, definers


def test_filtergraph_escaping_is_never_open_coded() -> None:
    """No module may hand-roll the two-level escape next to the real one."""
    for p in PY:
        if p.relative_to(SRC).as_posix() == "media/render.py":
            continue
        body = _text(p)
        assert "\\\\:" not in body or "_escape_filter_path" in body, (
            f"{p.relative_to(SRC)} looks like it escapes filter paths itself")


# ------------------------------------------------------------- the msvcrt set


def test_msvcrt_is_implemented_only_by_the_quartet() -> None:
    """CLAUDE.md: the advisory FILE lock quartet is events/recents/library/
    failures, changed together. Other modules must DELEGATE (director calls
    events_lock) rather than fork a parallel implementation — a mention in a
    comment is fine, an `import msvcrt` is not."""
    implementers = {
        p.relative_to(SRC).as_posix() for p in PY
        if re.search(r"^\s*import msvcrt", _text(p), re.M)}
    assert implementers == {
        "core/events.py", "core/recents.py",
        "core/library.py", "core/failures.py"}, implementers


@pytest.mark.parametrize("rel", [
    "core/events.py", "core/recents.py", "core/library.py", "core/failures.py"])
def test_each_quartet_member_pairs_a_thread_lock(rel: str) -> None:
    """msvcrt.locking does NOT exclude two threads in the same process (each
    opens its own fd and both calls succeed — DECISIONS #38). Every member must
    pair the byte lock with an in-process threading.Lock, or the GUI/MCP server
    loses updates on Windows."""
    body = _text(SRC / rel)
    assert "threading" in body, f"{rel} locks bytes but not threads"


# ------------------------------------------------------- decoding + line ends


def test_every_text_subprocess_declares_its_encoding() -> None:
    """Without `encoding=`, Python decodes child output with the ANSI code page
    on Windows — which mangles the Chinese in ffmpeg/git output."""
    offenders, scanned, with_text = [], 0, 0
    for p in PY:
        body = _text(p)
        for m in re.finditer(r"subprocess\.(?:run|Popen|check_output)\(", body):
            scanned += 1
            call = _call_body(body, m.end() - 1)   # from the OPENING paren
            if "text=True" not in call:
                continue
            with_text += 1
            if "encoding=" not in call:
                offenders.append(
                    f"{p.relative_to(SRC).as_posix()}:{body[:m.start()].count(chr(10)) + 1}")
    # Guard the guard: an earlier version of this test counted parens from the
    # start of "subprocess.run(", so depth was 0 at index 1 and it truncated
    # every call to two characters — it saw no `text=True` anywhere and passed
    # vacuously. A test that reports coverage it does not have is worse than no
    # test, so the sample size is asserted too.
    assert scanned >= 20, f"detector found almost no subprocess calls ({scanned})"
    assert with_text >= 10, f"detector saw almost no text=True calls ({with_text})"
    assert offenders == [], f"text subprocess without encoding=: {offenders}"


def test_the_subprocess_detector_actually_catches_a_violation() -> None:
    """The negative case, so the check above can never quietly stop working."""
    bad = 'subprocess.run(["x"], capture_output=True, text=True)'
    good = 'subprocess.run(["x"], text=True, encoding="utf-8")'
    assert "encoding=" not in _call_body(bad, bad.index("("))
    assert "text=True" in _call_body(bad, bad.index("("))
    assert "encoding=" in _call_body(good, good.index("("))


def test_a_scaffolded_project_contains_no_cr(tmp_path: Path) -> None:
    """Path.write_text uses newline=None → CRLF on Windows. Scaffolds go
    through atomic_write_text (newline="\\n") so a project is byte-identical
    wherever it was created. Asserted on the OUTCOME, so it survives
    refactoring — and it is the one line-ending fact a Linux run can prove."""
    from manju.core.container import Project

    root = tmp_path / "w.manju"
    Project.create(root, name="w")
    bad = [p.relative_to(root).as_posix() for p in sorted(root.rglob("*"),
                                                          key=lambda x: x.as_posix())
           if p.is_file() and b"\r" in p.read_bytes()]
    assert bad == [], f"CR in scaffolded files: {bad}"
