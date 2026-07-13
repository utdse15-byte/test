"""FP — pytest-xdist safety net (optimization backlog item 5).

The suite is being made safe to run under ``pytest -n auto`` (one worker
process per core). xdist runs tests serially *within* a worker, so the classic
hazard is **shared process state that leaks from one test into the next** — and
the single worst offender in this tree is a test that changes the process
working directory and never restores it. A leaked ``cwd`` makes a *later*,
otherwise-unrelated test resolve a relative path against the wrong project and
fail non-deterministically depending on which worker picked it up.

Two red-first pins, plus one that keeps the dependency wired in both files:

* **cwd hygiene** — a lint-style tooth scans every ``tests/**/*.py`` source
  file for a raw ``os.chdir`` call and fails listing every ``file:line``. The
  blessed replacement is the function-scoped ``monkeypatch.chdir(...)`` fixture
  (or, in a module-level helper, ``pytest.MonkeyPatch.context()``), which
  restores the cwd at teardown *even when the test body raises mid-flight*. The
  offender list must be empty — there is no allowlist.
* **declared dev dependency** — ``pytest-xdist`` is declared in the ``dev``
  optional-dependency extra of ``pyproject.toml`` (so ``pip install .[dev]``
  brings the ``-n`` flag with it).
* **pinned for reproducible installs** — ``pytest-xdist`` carries an exact
  ``==`` pin in ``constraints.txt`` (mirroring how the rest of that file pins
  its whole closure), so CI resolves the same parallelism build every time.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parent

# Match a raw cwd-mutating call. The pattern is written with escapes so this
# very file never contains the bare literal it forbids; the scan also skips
# itself (below) as a second line of defence.
_RAW_CHDIR = re.compile(r"\bos\.chdir\s*\(")


def _normalize(name: str) -> str:
    """PEP 503-ish requirement-name normalization (enough for our closure)."""
    return re.split(r"[<>=!~;,\[\s]", name.strip(), maxsplit=1)[0].lower().replace("_", "-")


# --------------------------------------------------------------------------- #
# cwd hygiene — no raw chdir anywhere under tests/                             #
# --------------------------------------------------------------------------- #


def test_no_raw_chdir_in_tests():
    """Not one test file may mutate the process cwd without the restoring
    monkeypatch fixture — a leaked cwd is the #1 cross-test contamination bug
    under ``-n auto``."""
    self_path = Path(__file__).resolve()
    offenders: list[str] = []
    for py in sorted(_TESTS_DIR.rglob("*.py")):
        if py.resolve() == self_path:
            continue  # this pin names the forbidden call in its own message
        text = py.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _RAW_CHDIR.search(line):
                offenders.append(f"{py.relative_to(_REPO_ROOT).as_posix()}:{lineno}")
    assert offenders == [], (
        "raw cwd-mutating calls found in tests — they leak the process working "
        "directory into the next test under pytest-xdist. Use the "
        "function-scoped monkeypatch.chdir fixture (restores at teardown even "
        "on failure) instead:\n  " + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# dependency — declared in the dev extra AND pinned in constraints             #
# --------------------------------------------------------------------------- #


def test_pytest_xdist_declared_in_dev_extra():
    """``pytest-xdist`` sits in ``[project.optional-dependencies].dev`` so the
    parallel runner installs with the rest of the dev toolbelt."""
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = data["project"]["optional-dependencies"]["dev"]
    names = {_normalize(spec) for spec in dev}
    assert "pytest-xdist" in names, (
        f"pytest-xdist missing from the dev extra (found: {sorted(names)})"
    )


def test_pytest_xdist_pinned_in_constraints():
    """``constraints.txt`` carries an exact ``==`` pin for ``pytest-xdist`` so
    CI's ``pip install -c constraints.txt`` resolves a reproducible build."""
    pinned: dict[str, str] = {}
    for raw in (_REPO_ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        name, _, version = line.partition("==")
        pinned[_normalize(name)] = version.strip()
    assert "pytest-xdist" in pinned, (
        f"pytest-xdist has no ==pin in constraints.txt (pinned: {sorted(pinned)})"
    )
    assert pinned["pytest-xdist"], "pytest-xdist pin in constraints.txt is empty"
