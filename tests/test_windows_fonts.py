"""W3 fonts (MANJU_WINDOWS_ONLY_LEAN_V3 §5.3) — ``find_font`` locates a
CJK-capable font on Windows, where no fontconfig exists.

Red-first evidence: at HEAD ``media/card.py`` had no ``_IS_WINDOWS`` flag and
no ``_win_font_dirs``/``_win_registry_font_files`` (every ``monkeypatch
raising=True`` below had no target), so on Windows ``find_font`` ran fc-match
(absent → None) then globbed ``/usr/share/fonts`` (absent → None): every
Windows machine silently degraded to drawtext's default font.

Design honoured: the POSIX chain stays byte-identical (fc-match families →
``:charset=4e00`` probe → FONT_ROOTS glob) and the two branches never consult
each other's sources (spies pin both directions); the Windows locator probes
a CURATED, ordered candidate list (Microsoft YaHei first — the modern UI CJK
font) against the two standard font stores, then falls back to the registry
Fonts lists — a handful of ``exists()`` checks, never a directory-wide glob;
every failure returns None honestly, so ``_font_inventory`` records the
existing ``"unknown"`` sentinel and glyph coverage stays UNKNOWN, never
guessed.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import manju.core.toolchain as toolchain_mod
import manju.media.card as card
from manju.core.toolchain import (
    cached_drawtext_font_hash,
    cached_tool_version_line,
)


@pytest.fixture(autouse=True)
def _clean_collector_caches():
    """test_fp_toolkeys' cache hygiene, mirrored exactly: the S4 collectors
    are process-cached — clear them around every test so a fake Windows font
    can never leak into another test in the same pytest process."""
    cached_tool_version_line.cache_clear()
    cached_drawtext_font_hash.cache_clear()
    yield
    cached_tool_version_line.cache_clear()
    cached_drawtext_font_hash.cache_clear()


# --------------------------------------------------------------------- helpers


def _no_fontconfig(*args, **kwargs):
    """The Windows branch must NEVER shell out to fc-match."""
    raise AssertionError("fontconfig consulted on the Windows branch")


def _fake_windows(monkeypatch, tmp_path: Path, *fonts: str) -> Path:
    """Point the Windows locator at a tmp %WINDIR% tree holding the named
    (fake) fonts. LOCALAPPDATA is redirected to a nonexistent dir and the
    fc-match helpers are booby-trapped so neither the host's fontconfig nor
    its real env can ever leak into a test. Returns the Fonts dir."""
    windir = tmp_path / "Windows"
    fonts_dir = windir / "Fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    for name in fonts:
        (fonts_dir / name).write_bytes(b"fake-font-" + name.encode())
    monkeypatch.setattr(card, "_IS_WINDOWS", True, raising=True)
    monkeypatch.setenv("WINDIR", str(windir))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no-localappdata"))
    monkeypatch.setattr(card, "_fc_match", _no_fontconfig)
    monkeypatch.setattr(card, "_fc_match_family", _no_fontconfig)
    return fonts_dir


# --------------------------------------------------------------------------
# POSIX byte-identity — the Windows sources are never consulted off Windows
# --------------------------------------------------------------------------


def test_posix_chain_unchanged_and_windows_sources_never_consulted(
        monkeypatch, tmp_path):
    """On non-Windows, find_font walks the EXISTING chain (fc-match families,
    then the :charset=4e00 probe, then the glob) and touches neither the
    Windows font dirs nor the registry helper — call-count spies prove it."""
    if os.name == "nt":
        pytest.skip("this pin is for POSIX hosts")
    calls = {"dirs": 0, "registry": 0}

    def spy_dirs() -> list[Path]:
        calls["dirs"] += 1
        return []

    def spy_registry() -> list[Path]:
        calls["registry"] += 1
        return []

    # raising=True doubles as red evidence: no such attributes at HEAD
    monkeypatch.setattr(card, "_win_font_dirs", spy_dirs, raising=True)
    monkeypatch.setattr(card, "_win_registry_font_files", spy_registry,
                        raising=True)
    # deterministic POSIX chain on any host: families miss, charset probe hits
    posix_font = tmp_path / "noto.ttf"
    posix_font.write_bytes(b"posix-font")
    monkeypatch.setattr(card, "_fc_match_family", lambda family: None)
    monkeypatch.setattr(
        card, "_fc_match",
        lambda query: posix_font if query == ":charset=4e00" else None)

    assert card.find_font() == posix_font
    assert calls == {"dirs": 0, "registry": 0}, (
        "POSIX must never consult the Windows sources")


def test_registry_helper_is_empty_list_off_windows():
    """The guarded ``import winreg`` fails off Windows — the helper's
    failure contract is an EMPTY LIST, never an ImportError."""
    if os.name == "nt":
        pytest.skip("this pin is for POSIX hosts")
    assert card._win_registry_font_files() == []


# --------------------------------------------------------------------------
# Windows dir scan — curated candidates against the two standard font stores
# --------------------------------------------------------------------------


def test_windows_dir_scan_finds_msyh_in_windir_fonts(monkeypatch, tmp_path):
    fonts_dir = _fake_windows(monkeypatch, tmp_path, "msyh.ttc")
    assert card.find_font() == fonts_dir / "msyh.ttc"


def test_windows_preference_order_msyh_beats_simsun(monkeypatch, tmp_path):
    """Microsoft YaHei is the modern UI CJK font — it must win over the
    legacy SimSun when both are installed (curated ORDER, not dir listing)."""
    fonts_dir = _fake_windows(monkeypatch, tmp_path, "simsun.ttc", "msyh.ttc")
    assert card.find_font() == fonts_dir / "msyh.ttc"


def test_windows_per_user_fonts_dir_consulted(monkeypatch, tmp_path):
    """Win10 1809+ installs "for me" fonts under %LOCALAPPDATA% — when the
    system store has nothing, the per-user store must be probed."""
    _fake_windows(monkeypatch, tmp_path)  # %WINDIR%\Fonts exists but is empty
    local = tmp_path / "Local"
    user_fonts = local / "Microsoft" / "Windows" / "Fonts"
    user_fonts.mkdir(parents=True)
    (user_fonts / "simhei.ttf").write_bytes(b"per-user-font")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    assert card.find_font() == user_fonts / "simhei.ttf"


# --------------------------------------------------------------------------
# Registry fallback — discovery source of last resort, failure-safe
# --------------------------------------------------------------------------


def test_windows_registry_fallback_finds_curated_font(monkeypatch, tmp_path):
    """Empty font dirs ⇒ the registry Fonts lists are the fallback; only a
    CURATED name is accepted (arial.ttf listed first must be skipped)."""
    _fake_windows(monkeypatch, tmp_path)  # both standard stores empty
    reg_dir = tmp_path / "elsewhere"
    reg_dir.mkdir()
    reg_font = reg_dir / "simhei.ttf"
    reg_font.write_bytes(b"registry-font")
    monkeypatch.setattr(
        card, "_win_registry_font_files",
        lambda: [reg_dir / "arial.ttf", reg_font], raising=True)
    assert card.find_font() == reg_font


def test_windows_registry_failure_degrades_to_none(monkeypatch, tmp_path):
    """A raising registry helper must yield find_font() is None — the
    contract is honest absence (callers degrade), never a crash."""
    _fake_windows(monkeypatch, tmp_path)

    def boom() -> list[Path]:
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(card, "_win_registry_font_files", boom, raising=True)
    assert card.find_font() is None


# --------------------------------------------------------------------------
# Integration — the S4 collector hashes the Windows font; UNKNOWN honesty
# --------------------------------------------------------------------------


def test_cached_drawtext_font_hash_hashes_the_windows_font(
        monkeypatch, tmp_path):
    """With a fake Windows font in place, core.toolchain's process-cached
    collector returns the REAL sha256 of its bytes (basename + hash — the
    absolute path never enters the record). The autouse fixture above clears
    the lru_cache before and after, exactly as test_fp_toolkeys does."""
    fonts_dir = _fake_windows(monkeypatch, tmp_path, "msyh.ttc")
    expected = "sha256:" + hashlib.sha256(b"fake-font-msyh.ttc").hexdigest()
    assert toolchain_mod.cached_drawtext_font_hash() == expected
    assert toolchain_mod._font_inventory() == {
        "drawtext_cjk": {"basename": "msyh.ttc", "sha256": expected}}
    assert (fonts_dir / "msyh.ttc").exists()  # and it hashed THAT file


def test_nothing_found_records_the_existing_unknown_sentinel(
        monkeypatch, tmp_path):
    """Empty stores + empty registry ⇒ _font_inventory records the EXISTING
    'unknown' sentinel (toolchain untouched by this wave) — coverage is
    UNKNOWN, never guessed."""
    _fake_windows(monkeypatch, tmp_path)  # empty stores
    monkeypatch.setattr(card, "_win_registry_font_files", lambda: [],
                        raising=True)
    assert toolchain_mod._font_inventory() == {
        "drawtext_cjk": toolchain_mod.UNKNOWN}
    assert toolchain_mod.cached_drawtext_font_hash() == "unknown"
