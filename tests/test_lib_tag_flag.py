"""`manju lib add --tag a --tag b` must keep BOTH (found by typing it).

`--tag` was documented as "comma-separated tags" and declared as a single
string option, so repeating the flag — the shape half the world's CLIs use —
silently kept only the LAST value. The user's own labels vanished with no
message, and nothing in the output hinted that anything had been dropped.

Both spellings, and any mix, now mean what they look like.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.cli import app

runner = CliRunner()


@pytest.fixture
def lib_home(tmp_path: Path, monkeypatch) -> Path:
    """An isolated private library — never the developer's real ~/.manju.

    Uses the module's own documented override (``MANJU_LIBRARY``) rather than
    reassigning HOME, so the test steers it the way a user would."""
    root = tmp_path / "library"
    monkeypatch.setenv("MANJU_LIBRARY", str(root))
    return root


def _asset(tmp_path: Path, name: str = "clip.mp4") -> Path:
    f = tmp_path / name
    f.write_bytes(b"\x00\x00\x00\x18ftypmp42" + name.encode())
    return f


def _tags(res_stdout: str) -> list[str]:
    import json

    # --json here is pretty-printed across many lines, so parse the whole body
    return json.loads(res_stdout.strip())["assets"][0]["tags"]


def _add(path: Path, *tag_args: str):
    return runner.invoke(app, ["lib", "add", str(path), *tag_args, "--json"])


def test_repeated_tag_flags_all_survive(lib_home: Path, tmp_path: Path) -> None:
    res = _add(_asset(tmp_path), "--tag", "测试", "--tag", "街景")
    assert res.exit_code == 0, res.stdout
    listed = runner.invoke(app, ["lib", "list", "--json"])
    assert sorted(_tags(listed.stdout)) == sorted(["测试", "街景"])


def test_comma_separated_still_works(lib_home: Path, tmp_path: Path) -> None:
    res = _add(_asset(tmp_path), "--tag", "测试,街景,夜景")
    assert res.exit_code == 0, res.stdout
    listed = runner.invoke(app, ["lib", "list", "--json"])
    assert sorted(_tags(listed.stdout)) == sorted(["测试", "街景", "夜景"])


def test_mixed_forms_are_deduped_order_preserving(
        lib_home: Path, tmp_path: Path) -> None:
    res = _add(_asset(tmp_path), "--tag", "a,b", "--tag", "b", "--tag", "c")
    assert res.exit_code == 0, res.stdout
    listed = runner.invoke(app, ["lib", "list", "--json"])
    assert _tags(listed.stdout) == ["a", "b", "c"]


def test_no_tags_is_still_fine(lib_home: Path, tmp_path: Path) -> None:
    res = _add(_asset(tmp_path))
    assert res.exit_code == 0, res.stdout
    listed = runner.invoke(app, ["lib", "list", "--json"])
    assert _tags(listed.stdout) == []
