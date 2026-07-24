"""Redacted diagnostic support bundle — the honest ``manju support-bundle`` core.

A support bundle is what a user hands to whoever is helping them debug: enough
of the *shape* of their environment and recent activity to diagnose a problem,
with **none** of their content, media, credentials, or private paths.

The design principle is **redaction-by-construction** (default-deny, allowlist):

    the bundle CONTAINS ONLY what this collector explicitly produces — it never
    copies a raw project file wholesale, never reads media bytes, never opens a
    provider auth block, and never emits an absolute private path.

Every string that could conceivably carry project data passes through the ONE
redactor (:func:`redact_record` → :func:`redact_text`) before it is written, and
then — belt *and* braces — the assembled bundle is scanned against its own
tripwire (:func:`self_scan`): if any of ``/home/`` ``/root/`` ``/Users/`` ``C:\\``
``Authorization:`` ``Bearer`` ``?signature=`` or an API-key token survives into
the output, the collector raises :class:`BundleError` and writes **nothing**.
That tripwire is what makes a redaction bug fail loudly instead of leaking.

The public entrypoint is :func:`build_support_bundle`. It is a **pure read** of
the project — it appends no event and mutates nothing, so building the same
project state twice yields a **byte-identical** zip (fixed 1980 member
timestamps, sorted members, no wall-clock anywhere).

The in-zip ``MANIFEST.json`` declares ``"format": "support-bundle-manifest.1"``
— a plain format string, deliberately **not** a ``manju.*/vN`` public schema id:
the bundle is a throwaway diagnostic artifact, not a truth surface.

Stdlib only (zipfile/json/subprocess/importlib/hashlib) — no new dependency, no
network. The secret token list is reused verbatim from
:data:`manju.core.check.SECRET_PATTERNS` (the ONE token list) so this can never
disagree with ``manju check``.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

from .check import SECRET_PATTERNS
from .container import Project
from .hashing import HASH_PREFIX, hash_file
from .safeio import SafeOutError, checked_out_path, publish_tmp

# The manifest is a diagnostic artifact, NOT a manju.*/vN public schema (see the
# module docstring and the Loop-F registry ruling): a plain format string.
BUNDLE_FORMAT = "support-bundle-manifest.1"

DEFAULT_EVENTS_TAIL = 200
DEFAULT_FAILURES_TAIL = 50

# Upper bound on bytes read from a runtime ledger before tailing (defensive: a
# hostile/huge ledger can't force an unbounded read). Well past any real tail.
_LEDGER_READ_CAP = 64 * 1024 * 1024


def _is_reparse_point(path: Path) -> bool:
    """Windows junction/reparse-point detection (POSIX: always False) —
    ``Path.is_symlink`` misses NTFS junctions; ``st_reparse_tag`` catches them
    without following. Local mirror of the safeio owner's check (kept here so a
    no-follow read never depends on a private import)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(st, "st_reparse_tag", 0))

# Optional deps we report present/absent (names + versions ONLY). A fixed,
# sorted tuple keeps the environment section deterministic.
_OPTIONAL_DEPS = (
    ("opentimelineio", "otio"),
    ("hypothesis", "hypothesis"),
    ("PIL", "pillow"),
    ("numpy", "numpy"),
    ("pydantic", "pydantic"),
    ("yaml", "pyyaml"),
)

# PATH tools whose --version first line we probe (absent ⇒ "missing").
_PATH_TOOLS = ("ffmpeg", "ffprobe")


class BundleError(RuntimeError):
    """Raised when the bundle cannot be written honestly.

    The dominant cause is the self-scan tripwire firing — a forbidden marker
    (private path / credential) survived into the assembled output. The message
    names the offending *markers and member files only*; it never carries the
    matched secret text, so the error is safe to log or surface anywhere.
    """


# --------------------------------------------------------------------------- #
# Redaction — the ONE seam. Everything that could carry project data goes       #
# through redact_record(); monkeypatching it to a no-op is what the tripwire    #
# test uses to prove the self-scan actually refuses to write.                   #
# --------------------------------------------------------------------------- #

# Values of any key matching these are masked wholesale (case-insensitive).
_SECRET_KEY_RE = re.compile(
    r"(?i)(key|token|secret|authorization|signature|password|bearer)"
)

# "Authorization: Bearer <tok>" / "authorization=<tok>" — remove the literal
# word too (the self-scan hunts the bare "Authorization:" marker), value optional.
_AUTH_RE = re.compile(r"(?i)authorization\s*[:=]\s*(?:bearer\s+)?\S*")
# A standalone "Bearer <tok>" (or a lone "Bearer") — the self-scan hunts "Bearer".
_BEARER_RE = re.compile(r"(?i)\bbearer\b\s*\S*")
# Signed-URL query params — mask the WHOLE "?sig=…"/"&signature=…" pair so the
# "?signature=" marker itself disappears, not just its value.
_SIGNED_URL_RE = re.compile(
    r"(?i)[?&][\w.\-]*"
    r"(?:signature|sig|token|key|secret|password|credential|expires|se|sp|sv|sr)"
    r"[\w.\-]*=[^&\s\"'}]*"
)
# Absolute paths whose ROOT is sensitive — always rewritten to their basename so
# the self-scan markers (/home/ /root/ /Users/ C:\ …) can never survive.
_SENSITIVE_PATH_RE = re.compile(
    r"(?:/(?:home|root|Users|tmp|var|opt|mnt|media|private|data|Applications|etc|srv)"
    r"|[A-Za-z]:\\)[^\s\"'<>|]*"
)
# General POSIX absolute paths (best-effort), guarded so a URL's "scheme://host"
# is left intact (the slash must not follow a word char, ':' or '/').
_GENERAL_ABS_RE = re.compile(r"(?<![\w:/])(?:/[^\s\"'<>|:]+)+")

REDACTED = "<redacted>"


def _basename_sub(match: "re.Match[str]") -> str:
    """Replacement: collapse a matched absolute path to its basename only."""
    raw = match.group(0)
    parts = re.split(r"[\\/]+", raw.rstrip("/\\"))
    base = parts[-1] if parts and parts[-1] else ""
    return base or "<path>"


def _bump(stats: dict[str, int] | None, key: str, n: int) -> None:
    if stats is not None and n:
        stats[key] = stats.get(key, 0) + n


def redact_text(text: Any, stats: dict[str, int] | None = None) -> Any:
    """Mask secrets, signed-URL query values and absolute private paths in a
    free string. Non-strings pass through unchanged.

    The output is guaranteed to contain none of the tripwire markers: the auth
    header words, ``Bearer``, ``?signature=``, an API-key token, or a
    ``/home``|``/root``|``/Users``|``C:\\`` rooted path. Secret masking runs both
    before and after path rewriting, so a token exposed as a filename basename is
    still caught.
    """
    if not isinstance(text, str):
        return text
    text, n = _AUTH_RE.subn("<redacted-authorization>", text)
    _bump(stats, "auth_masked", n)
    text, n = _BEARER_RE.subn("<redacted-bearer>", text)
    _bump(stats, "auth_masked", n)
    text, n = _SIGNED_URL_RE.subn("?<redacted-signed-query>", text)
    _bump(stats, "signed_urls_masked", n)
    for pat in SECRET_PATTERNS:
        text, n = pat.subn("<redacted-secret>", text)
        _bump(stats, "secret_tokens_masked", n)
    text, n = _SENSITIVE_PATH_RE.subn(_basename_sub, text)
    _bump(stats, "abs_paths_rewritten", n)
    text, n = _GENERAL_ABS_RE.subn(_basename_sub, text)
    _bump(stats, "abs_paths_rewritten", n)
    # a token that only surfaced once a path was collapsed to its basename
    for pat in SECRET_PATTERNS:
        text, n = pat.subn("<redacted-secret>", text)
        _bump(stats, "secret_tokens_masked", n)
    return text


# Private-rooted paths ONLY (W2 §4.5 doctor hygiene): /home /root /Users and
# drive-letter roots carry a username; /usr /opt /etc /tmp system paths are
# DIAGNOSTIC and must stay readable in doctor output. Deliberately narrower
# than _SENSITIVE_PATH_RE (which also collapses /tmp, /opt, … for bundles).
_PRIVATE_PATH_RE = re.compile(
    r"(?:/(?:home|root|Users)|[A-Za-z]:\\)[^\s\"'<>|]*"
)


def redact_private_text(text: Any, stats: dict[str, int] | None = None) -> Any:
    """The NARROW composition for locally-rendered diagnostics (doctor):
    secrets, auth headers and signed-URL queries mask exactly as
    :func:`redact_text`, but only PRIVATE-rooted absolute paths (/home /root
    /Users, ``C:\\``) collapse to their basename — ``/usr/bin/ffmpeg`` stays
    readable because telling the user where a tool lives is doctor's job.
    Same owner, same building blocks; never a parallel redactor."""
    if not isinstance(text, str):
        return text
    text, n = _AUTH_RE.subn("<redacted-authorization>", text)
    _bump(stats, "auth_masked", n)
    text, n = _BEARER_RE.subn("<redacted-bearer>", text)
    _bump(stats, "auth_masked", n)
    text, n = _SIGNED_URL_RE.subn("?<redacted-signed-query>", text)
    _bump(stats, "signed_urls_masked", n)
    for pat in SECRET_PATTERNS:
        text, n = pat.subn("<redacted-secret>", text)
        _bump(stats, "secret_tokens_masked", n)
    text, n = _PRIVATE_PATH_RE.subn(_basename_sub, text)
    _bump(stats, "abs_paths_rewritten", n)
    for pat in SECRET_PATTERNS:
        text, n = pat.subn("<redacted-secret>", text)
        _bump(stats, "secret_tokens_masked", n)
    return text


def redact_record(obj: Any, stats: dict[str, int] | None = None) -> Any:
    """THE redactor. Recursively redact a JSON-ish value:

    * a dict value under a secret-shaped key (``*key*``/``*token*``/``*secret*``/
      ``authorization``/``signature``/``password``/``bearer``) is masked whole;
    * every other string is run through :func:`redact_text`;
    * lists/dicts recurse; other scalars pass through.

    This is the single seam every collector routes through. The tripwire test
    monkeypatches it to the identity function to prove that, with redaction
    disabled, :func:`self_scan` refuses to let the bundle be written.
    """
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and _SECRET_KEY_RE.search(key):
                out[key] = REDACTED
                _bump(stats, "secret_key_masks", 1)
            else:
                out[key] = redact_record(value, stats)
        return out
    if isinstance(obj, list):
        return [redact_record(item, stats) for item in obj]
    if isinstance(obj, str):
        return redact_text(obj, stats)
    return obj


# --------------------------------------------------------------------------- #
# Self-scan tripwire                                                            #
# --------------------------------------------------------------------------- #

# (name, literal) markers that must NEVER survive into the bundle. Case-sensitive
# on the literal, as the addendum specifies; redaction is case-insensitive so
# lowercase variants are masked upstream regardless. Only the safe NAME is ever
# echoed into the manifest/verdict — embedding the raw literal would trip the
# scanner on its own report.
_SCAN_LITERALS = (
    ("posix-home", "/home/"),
    ("posix-root", "/root/"),
    ("macos-users", "/Users/"),
    ("windows-drive", "C:\\"),
    ("http-authorization", "Authorization:"),
    ("http-bearer", "Bearer "),
    ("signed-url-query", "?signature="),
)
_SECRET_TOKEN_NAME = "secret-token"


def self_scan(members: dict[str, bytes]) -> dict[str, Any]:
    """Scan assembled member bytes for forbidden markers. Returns a verdict
    ``{"ok": bool, "patterns": [...], "hits": [...]}`` where each hit is
    ``{"member": name, "marker": <safe-name>, "count": n}`` — the marker is a
    stable SAFE NAME (``posix-home``, ``secret-token``, …), NEVER the matched
    text and never the raw literal (so the verdict is itself safe to embed)."""
    hits: list[dict[str, Any]] = []
    for name in sorted(members):
        text = members[name].decode("utf-8", errors="replace")
        for marker_name, literal in _SCAN_LITERALS:
            count = text.count(literal)
            if count:
                hits.append({"member": name, "marker": marker_name, "count": count})
        token_hits = sum(len(pat.findall(text)) for pat in SECRET_PATTERNS)
        if token_hits:
            hits.append({"member": name, "marker": _SECRET_TOKEN_NAME, "count": token_hits})
    return {
        "ok": not hits,
        "patterns": [name for name, _ in _SCAN_LITERALS] + [_SECRET_TOKEN_NAME],
        "hits": hits,
    }


# --------------------------------------------------------------------------- #
# Collectors — each produces ONLY derived, allowlisted facts.                   #
# --------------------------------------------------------------------------- #

def _manju_version() -> str:
    """manju version: importlib.metadata → git describe → "unknown"."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("manju")
        except PackageNotFoundError:
            pass
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
        )
        described = (out.stdout or "").strip()
        if out.returncode == 0 and described:
            return described
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _tool_version(name: str) -> str:
    """First line of ``<tool> -version`` (redacted); "missing" when absent or on
    any failure — an absent optional tool is a fact, never a crash."""
    exe = shutil.which(name)
    if not exe:
        return "missing"
    try:
        out = subprocess.run(
            [exe, "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "missing"
    for stream in (out.stdout, out.stderr):
        line = (stream or "").splitlines()
        if line:
            return line[0].strip()
    return "missing"


def _dep_version(module: str, dist: str) -> str:
    """"absent" or a version string for an optional dependency (name+version
    only). SUPPORT-P0-005: version comes ONLY from installed distribution
    metadata — we NEVER ``import`` the module to read ``__version__``, because a
    hostile project at ``sys.path[0]`` can shadow ``opentimelineio.py`` &c. and
    turn a diagnostic probe into arbitrary code execution. Unknown ⇒ "absent",
    never a project-supplied import."""
    del module  # never imported — metadata-only by distribution name
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version(dist)
        except PackageNotFoundError:
            return "absent"
    except Exception:
        return "absent"


def collect_environment() -> dict[str, Any]:
    """Tool + interpreter identity — names and versions only, no host identity
    (no hostname/user), no paths."""
    return {
        "manju": _manju_version(),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "tools": {name: _tool_version(name) for name in _PATH_TOOLS},
        "optional_deps": {
            dist: _dep_version(module, dist) for module, dist in _OPTIONAL_DEPS
        },
    }


_TAKE_BUCKETS = ("0", "1", "2", "3-5", "6-10", "11+")


def _bucket(n: int) -> str:
    if n <= 2:
        return str(n)
    if n <= 5:
        return "3-5"
    if n <= 10:
        return "6-10"
    return "11+"


def _safe_project_yaml(project: Project) -> dict[str, Any]:
    """The SAFE allowlisted subset of project.yaml: fps/width/height/mode, the
    name (dropped if it looks like a path), and budget PRESENCE only — never a
    budget amount, never a provider/auth value."""
    try:
        raw = project.load_config().model_dump()
    except Exception:
        return {"error": "project.yaml unreadable"}
    safe: dict[str, Any] = {}
    for field in ("fps", "width", "height", "mode"):
        if field in raw and isinstance(raw[field], (int, float, str)):
            safe[field] = raw[field]
    name = raw.get("name")
    if isinstance(name, str) and name and not re.search(r"[\\/]|^[A-Za-z]:", name):
        safe["name"] = name
    else:
        safe["name_omitted"] = True  # looked path-like (or absent)
    budget = raw.get("budget") or {}
    safe["budget_limit_set"] = bool(isinstance(budget, dict) and budget.get("limit") is not None)
    return safe


def _reports_schema_ids(project: Project) -> tuple[list[str], list[str]]:
    """(reports_subdirs, schema_ids) — subdir NAMES present under reports/, and
    the distinct schema ids DECLARED in report .json/.jsonl files (ids only, no
    content). Bounded and defensive: unreadable/oversized files are skipped."""
    reports = project.reports_dir
    subdirs: list[str] = []
    schema_ids: set[str] = set()
    if not reports.is_dir():
        return subdirs, sorted(schema_ids)
    subdirs = sorted(p.name for p in reports.iterdir() if p.is_dir())
    for path in sorted(reports.rglob("*")):
        if not path.is_file() or path.suffix not in (".json", ".jsonl"):
            continue
        try:
            if path.stat().st_size > 5_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        blobs = [text] if path.suffix == ".json" else text.splitlines()
        for blob in blobs:
            blob = blob.strip()
            if not blob:
                continue
            try:
                obj = json.loads(blob)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                sid = obj.get("schema")
                if isinstance(sid, str) and sid:
                    schema_ids.add(sid)
    return subdirs, sorted(schema_ids)


def collect_project_shape(project: Project) -> dict[str, Any]:
    """Counts and safe scalars only — NO shot/bible/timeline content, no media.

    * shot count, bible entry count, timeline clip count;
    * a histogram of takes-per-shot (bucketed, so an exact per-shot production
      count is never revealed);
    * reports subdirs present + schema ids seen in reports;
    * the SAFE project.yaml subset.
    """
    try:
        shot_ids = project.shot_ids()
    except Exception:
        shot_ids = []
    histogram = {b: 0 for b in _TAKE_BUCKETS}
    for sid in shot_ids:
        try:
            n_takes = len(project.takes(sid))
        except Exception:
            n_takes = 0
        histogram[_bucket(n_takes)] += 1

    try:
        bible_entries = len(project.load_bible())
    except Exception:
        bible_entries = 0

    timeline_clips = 0
    try:
        timeline = project.load_timeline()
        if timeline is not None:
            tracks = timeline.tracks
            timeline_clips = sum(
                len(getattr(tracks, name))
                for name in ("video", "overlay", "voice", "music", "sfx", "ambient", "captions")
            )
    except Exception:
        timeline_clips = 0

    subdirs, schema_ids = _reports_schema_ids(project)
    return {
        "shots": len(shot_ids),
        "bible_entries": bible_entries,
        "timeline_clips": timeline_clips,
        "takes_per_shot_histogram": histogram,
        "reports_subdirs": subdirs,
        "schema_ids": schema_ids,
        "project_yaml": _safe_project_yaml(project),
    }


# --------------------------------------------------------------------------- #
# SUPPORT-P0-001: strict structured-field allowlist for the ledger tails.        #
# The bundle promises "no content". A per-line redactor only masks the FEW       #
# secret/path patterns it knows — a client codename, an unreleased plot echo, a  #
# private prompt or an error detail all sail through. So the ledger tails carry  #
# ONLY structured, non-free-text facts (timestamps, controlled-vocab enums,      #
# counts, booleans, content hashes); every free-text value is DROPPED by         #
# construction, never merely redacted. This is default-deny by VALUE TYPE and    #
# PATTERN, so an unknown field can only be excluded, never guessed into PASS.    #
# --------------------------------------------------------------------------- #

# A machine enum/verb: lowercase snake token only, bounded. Deliberately narrow
# so uppercase codenames (CLIENT_CODENAME_RAVEN) and any free phrase (has a
# space, punctuation or uppercase) can never match.
_MACHINE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.\-]{0,39}$")
# A content hash — always safe (opaque, non-reversible), and useful evidence.
_ALLOW_HASH_RE = re.compile(r"^" + re.escape(HASH_PREFIX) + r"[0-9a-f]{64}$")
# An ISO-8601-ish timestamp (the ONLY free-form-looking string kept, under key
# "ts"); no path/secret shape can satisfy it.
_TS_RE = re.compile(r"^\d{4}-\d\d-\d\dT[0-9:.,+\-Z]{4,}$")
# Keys whose string value, when a machine token, is a controlled enum worth
# keeping. Everything else stringy is content and is dropped.
_ENUM_KEYS = frozenset({
    "actor", "action", "level", "step", "kind", "code", "state", "status",
    "phase", "mode", "reason", "result", "verdict", "outcome", "type", "stage",
})


def _allowlist_scalar(key: str, value: Any) -> tuple[bool, Any]:
    """(keep, value) for one leaf. Booleans/numbers are structured facts and
    always kept; a content hash is kept; a timestamp under ``ts`` is kept; a
    string under an enum key is kept ONLY as a lowercase machine token. Any
    other string (free text) is dropped."""
    if isinstance(value, bool):
        return True, value
    if isinstance(value, (int, float)):
        return True, value
    if isinstance(value, str):
        if _ALLOW_HASH_RE.match(value):
            return True, value
        if key == "ts" and _TS_RE.match(value):
            return True, value
        if key in _ENUM_KEYS and _MACHINE_TOKEN_RE.match(value):
            return True, value
    return False, None


def _allowlist_container(node: Any) -> Any:
    """Recursively project a JSON record onto the structured-fact allowlist.
    Dicts keep only surviving leaves / non-empty sub-containers; lists keep only
    structured scalars and non-empty sub-containers (a bare free string in a
    list — an argv token, a message — is dropped)."""
    if isinstance(node, dict):
        out: dict[Any, Any] = {}
        for key, value in node.items():
            k = key if isinstance(key, str) else ""
            keep, val = _allowlist_scalar(k, value)
            if keep:
                out[key] = val
            elif isinstance(value, (dict, list)):
                sub = _allowlist_container(value)
                if sub:
                    out[key] = sub
        return out
    if isinstance(node, list):
        kept_list: list[Any] = []
        for value in node:
            keep, val = _allowlist_scalar("", value)
            if keep:
                kept_list.append(val)
            elif isinstance(value, (dict, list)):
                sub = _allowlist_container(value)
                if sub:
                    kept_list.append(sub)
        return kept_list
    return {}


def _tail_slice(raw_lines: list[str], n: int) -> list[str]:
    """The last ``n`` lines, where "the last 0 lines" means NONE (SUPPORT-P1-001).
    Python's ``raw[-0:]`` is ``raw[0:]`` — the whole ledger — so asking a bundle
    to carry no events handed it every event instead. Any n <= 0 means none."""
    if n <= 0:
        return []
    return raw_lines[-n:]


def _allowlist_tail_lines(raw_lines: list[str], stats: dict[str, int]) -> tuple[list[str], int]:
    """Project each raw JSONL line onto the structured-fact allowlist; a
    torn/invalid line is COUNTED and replaced by a ``<malformed line skipped>``
    marker (never included verbatim). The surviving structured values still pass
    through the ONE redactor (belt & braces — a machine token can carry no
    secret pattern, so this only ever confirms). Returns (lines, malformed)."""
    out: list[str] = []
    malformed = 0
    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            out.append("<malformed line skipped>")
            continue
        kept = redact_record(_allowlist_container(obj), stats)
        out.append(json.dumps(kept, ensure_ascii=False, sort_keys=True))
    return out, malformed


def _read_ledger_lines_nofollow(path: Path) -> tuple[list[str] | None, str]:
    """Read a runtime ledger WITHOUT ever following a link (SUPPORT-P0-002).

    A hostile project can point ``events.jsonl`` / ``reports/failures.jsonl`` at
    a file OUTSIDE the project; a plain ``read_text`` would then copy an external
    secret into the shared bundle. So the leaf is ``lstat``'d and opened
    ``O_NOFOLLOW``: a symlink/junction or any non-regular file is REFUSED (the
    reason is recorded in the manifest, the link is never followed). Returns
    ``(lines, note)`` — ``lines is None`` with note ∈ {"absent",
    "refused: symlink/junction not followed", "refused: not a regular file",
    "refused: unreadable"}; otherwise the decoded lines with note "ok"."""
    try:
        st = os.lstat(path)
    except OSError:
        return None, "absent"
    if stat.S_ISLNK(st.st_mode) or _is_reparse_point(path):
        return None, "refused: symlink/junction not followed"
    if not stat.S_ISREG(st.st_mode):
        return None, "refused: not a regular file"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        # ELOOP on a raced symlink, or any open failure — never fall back to a
        # following read.
        return None, "refused: unreadable"
    try:
        try:
            fst = os.fstat(fd)
            if not stat.S_ISREG(fst.st_mode):
                return None, "refused: not a regular file"
            # Read only the trailing window (bounded memory) but keep true tail
            # semantics: seek to the last <=cap bytes, then splitlines/tail.
            if fst.st_size > _LEDGER_READ_CAP:
                os.lseek(fd, fst.st_size - _LEDGER_READ_CAP, os.SEEK_SET)
            chunks: list[bytes] = []
            while True:
                buf = os.read(fd, 1024 * 1024)
                if not buf:
                    break
                chunks.append(buf)
            data = b"".join(chunks)
        except OSError:
            return None, "refused: unreadable"
    finally:
        os.close(fd)
    return data.decode("utf-8", errors="replace").splitlines(), "ok"


def collect_events_tail(project: Project, n: int, stats: dict[str, int]) -> tuple[list[str], int, bool, str]:
    """The last ``n`` events.jsonl lines, allowlisted to structured facts. No
    link is ever followed (SUPPORT-P0-002). Returns
    (lines, malformed_count, source_present, note)."""
    raw, note = _read_ledger_lines_nofollow(project.root / "events.jsonl")
    if raw is None:
        return [], 0, False, note
    lines, malformed = _allowlist_tail_lines(_tail_slice(raw, n), stats)
    return lines, malformed, True, note


def collect_failures_tail(project: Project, n: int, stats: dict[str, int]) -> tuple[list[str], int, bool, str]:
    """The last ``n`` reports/failures.jsonl lines, allowlisted to structured
    facts (same no-follow + allowlist discipline as events). Returns
    (lines, malformed_count, source_present, note)."""
    raw, note = _read_ledger_lines_nofollow(project.reports_dir / "failures.jsonl")
    if raw is None:
        return [], 0, False, note
    lines, malformed = _allowlist_tail_lines(_tail_slice(raw, n), stats)
    return lines, malformed, True, note


def collect_config_digests(stats: dict[str, int]) -> list[dict[str, str]]:
    """Provider manifest DIGESTS only — the sha256 of each provider.yaml's raw
    bytes. The file is hashed, NEVER parsed and NEVER opened for its auth block,
    so a credential can't leak even through a redaction bug. Returns a sorted
    list of ``{"provider": id, "digest": "sha256:…"}``."""
    try:
        from ..providers.manifest import providers_dir
    except Exception:
        return []
    root = providers_dir()
    if not root.is_dir():
        return []
    digests: list[dict[str, str]] = []
    for manifest_path in sorted(root.glob("*/provider.yaml")):
        try:
            digest = hash_file(manifest_path)  # hashes bytes; does not parse
        except OSError:
            continue
        provider_id = redact_record(manifest_path.parent.name, stats)
        digests.append({"provider": provider_id, "digest": digest})
    return sorted(digests, key=lambda d: (d["provider"], d["digest"]))


# --------------------------------------------------------------------------- #
# Destination safety (SUPPORT-P0-003 / SUPPORT-P0-004)                          #
# --------------------------------------------------------------------------- #

# Inside-project top-level names that are truth / imports / config / internal /
# source-log — a diagnostic zip may NEVER be published onto (or under) any of
# them. Everything else inside the project (a top-level ``support-bundle.zip``,
# ``exports/``, ``reports/`` …) stays a legal, normal destination.
_PROTECTED_TOPLEVEL = frozenset({
    "project.yaml",      # config / truth
    "bible", "shots", "story", "timeline",  # truth surfaces
    "media",             # media + media/imports (append-only truth)
    "events.jsonl",      # runtime source log
    ".git", ".manju",    # VCS / disposable index
})
# Source-log ledger files that live UNDER reports/ (otherwise a normal publish
# subtree) — the failures ledger and its rotations.
_PROTECTED_REPORTS_RE = re.compile(r"^reports/failures(?:\.[^/]+)?\.jsonl$")
# Files whose inode identity must never be aliased (hardlink) by the output.
_INODE_GUARDS = ("project.yaml", "events.jsonl", "reports/failures.jsonl")


def _checked_bundle_dest(project: Project, dest: Path | str) -> Path:
    """Validate the bundle destination, returning it absolute (SUPPORT-P0-003 /
    -004). Reuses :func:`manju.core.safeio.checked_out_path` for leaf safety
    (rejects an existing symlink/junction/dir/non-regular target, so the final
    artifact can never BE a link and a pre-planted link can't be replaced), then
    adds a support-bundle-specific protected-domain rule: an inside-project
    destination may not land on truth/imports/config/internal or a runtime
    source log (nor a hardlink alias of one). Raises :class:`SafeOutError`."""
    target = checked_out_path(dest, kind="bundle 输出")  # leaf safety + abspath
    root = Path(project.root)
    try:
        rel = target.relative_to(root)
    except ValueError:
        return target  # outside the project — the normal --out use, allowed
    parts = rel.parts
    first = parts[0] if parts else ""
    if first in _PROTECTED_TOPLEVEL:
        raise SafeOutError(
            f"bundle 输出落在项目 truth/源日志域 {first}/ 下,拒绝 (会破坏项目真相或源账本): {target}",
            reason="bad_out",
        )
    if _PROTECTED_REPORTS_RE.match(rel.as_posix()):
        raise SafeOutError(
            f"bundle 输出落在运行时源账本上,拒绝: {target}", reason="bad_out")
    # hardlink-alias guard: a non-truth-looking path that is actually a second
    # name for a protected source file (SUPPORT-P0-004 aliasing).
    try:
        tst = os.stat(target) if target.exists() else None
    except OSError:
        tst = None
    if tst is not None:
        for guard in _INODE_GUARDS:
            gpath = root / guard
            try:
                gst = gpath.stat()
            except OSError:
                continue
            if (tst.st_dev, tst.st_ino) == (gst.st_dev, gst.st_ino):
                raise SafeOutError(
                    f"bundle 输出是受保护源文件 {guard} 的硬链接别名,拒绝: {target}",
                    reason="bad_out",
                )
    return target


# --------------------------------------------------------------------------- #
# Deterministic zip writer                                                      #
# --------------------------------------------------------------------------- #

def _write_zip(dest: Path, members: dict[str, bytes]) -> int:
    """Write ``members`` to ``dest`` as a reproducible zip (sorted member order,
    fixed 1980 timestamp, 0644). All bytes travel through
    :func:`manju.core.safeio.publish_tmp` — an unpredictable ``mkstemp`` sibling
    (O_EXCL) reached only via atomic replace, so a pre-planted predictable-name
    link can never capture the write (SUPPORT-P0-004) and a failure writes zero.
    Returns the output size in bytes."""
    with publish_tmp(dest) as tmp:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for arcname in sorted(members):
                info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, members[arcname])
    return dest.stat().st_size


# --------------------------------------------------------------------------- #
# Public entrypoint                                                             #
# --------------------------------------------------------------------------- #

def build_support_bundle(
    project: Project | str | Path,
    dest_zip: str | Path,
    *,
    include_events_tail: int = DEFAULT_EVENTS_TAIL,
) -> dict[str, Any]:
    """Build a redacted diagnostic support bundle for ``project`` at ``dest_zip``.

    A PURE READ of the project (appends no event, mutates nothing), so identical
    project state yields a byte-identical zip. The bundle carries only derived,
    allowlisted, redacted facts — environment shape, project counts, a redacted
    tail of events and failures, and provider-manifest digests — and NEVER media
    bytes, bible/shot/timeline content, provider manifests, or absolute private
    paths.

    Before anything is written the assembled output is run through
    :func:`self_scan`; if a forbidden marker survived, this raises
    :class:`BundleError` and writes NO zip. On success returns the summary
    manifest dict (a superset of the in-zip ``MANIFEST.json``: the same keys plus
    the local ``output`` path and ``output_bytes``).

    ``include_events_tail`` bounds how many trailing events.jsonl lines are
    included (default 200).
    """
    if not isinstance(project, Project):
        project = Project(project)

    # SUPPORT-P0-003 / -004: validate the destination BEFORE reading or writing
    # anything (truth/source-log domains and link/dir/special leaves refused; the
    # write later goes through an O_EXCL random temp). SafeOutError is re-raised
    # as BundleError so the CLI's existing ``except BundleError`` turns it into a
    # stable ``bundle_refused`` envelope instead of a traceback — and no bytes
    # are ever written on refusal (失败零写入).
    try:
        dest = _checked_bundle_dest(project, dest_zip)
    except SafeOutError as exc:
        raise BundleError(f"refusing to write support bundle: {exc}") from exc

    stats: dict[str, int] = {}

    environment = redact_record(collect_environment(), stats)
    project_shape = redact_record(collect_project_shape(project), stats)
    config_digests = collect_config_digests(stats)

    events_lines, events_malformed, events_present, events_note = collect_events_tail(
        project, include_events_tail, stats
    )
    failures_lines, failures_malformed, failures_present, failures_note = collect_failures_tail(
        project, DEFAULT_FAILURES_TAIL, stats
    )

    # ---- assemble the non-manifest members
    members: dict[str, bytes] = {}
    _ALLOWLIST_DESC = ("structured-field allowlist only (ts/actor/action/level/"
                       "step/kind/code + numeric/boolean/hash detail); free-text "
                       "fields dropped, not merely redacted")
    included: list[dict[str, str]] = [
        {"member": "MANIFEST.json", "describes": "index + self-scan verdict + redaction counts"},
        {"member": "(inline) environment", "describes": "manju/python/platform/tool + optional-dep versions"},
        {"member": "(inline) project_shape", "describes": "counts + bucketed take histogram + safe project.yaml subset + report schema ids"},
        {"member": "(inline) config_digests", "describes": "provider manifest sha256 digests (bytes hashed, never parsed)"},
    ]
    skipped: list[dict[str, str]] = []

    # SUPPORT-P0-002: a refused (symlink/non-regular) ledger records its REASON
    # in the manifest rather than being followed off-project.
    def _absent_reason(note: str, what: str) -> str:
        if note.startswith("refused:"):
            return f"{what} not collected — {note.split(':', 1)[1].strip()} (no-follow input boundary)"
        return f"no {what} in project"

    if events_present:
        members["events-tail.txt"] = ("\n".join(events_lines) + ("\n" if events_lines else "")).encode("utf-8")
        included.append({"member": "events-tail.txt", "describes": f"last {include_events_tail} events.jsonl lines — {_ALLOWLIST_DESC}"})
    else:
        skipped.append({"what": "events-tail.txt", "reason": _absent_reason(events_note, "events.jsonl")})

    if failures_present:
        members["failures-tail.jsonl"] = ("\n".join(failures_lines) + ("\n" if failures_lines else "")).encode("utf-8")
        included.append({"member": "failures-tail.jsonl", "describes": f"last {DEFAULT_FAILURES_TAIL} reports/failures.jsonl lines — {_ALLOWLIST_DESC}"})
    else:
        skipped.append({"what": "failures-tail.jsonl", "reason": _absent_reason(failures_note, "reports/failures.jsonl")})

    # These content facts are always-declared honesties about what is NOT here.
    skipped.append({"what": "media bytes", "reason": "never collected (counts only)"})
    skipped.append({"what": "bible/shot/timeline content", "reason": "never collected (counts only)"})
    skipped.append({"what": "provider manifests", "reason": "digest only; contents never read"})

    redaction = {
        "events_included": len(events_lines),
        "events_malformed": events_malformed,
        "failures_included": len(failures_lines),
        "failures_malformed": failures_malformed,
        "secret_key_masks": stats.get("secret_key_masks", 0),
        "auth_masked": stats.get("auth_masked", 0),
        "signed_urls_masked": stats.get("signed_urls_masked", 0),
        "secret_tokens_masked": stats.get("secret_tokens_masked", 0),
        "abs_paths_rewritten": stats.get("abs_paths_rewritten", 0),
    }
    redaction["total"] = (
        redaction["secret_key_masks"] + redaction["auth_masked"]
        + redaction["signed_urls_masked"] + redaction["secret_tokens_masked"]
        + redaction["abs_paths_rewritten"]
    )

    # ---- self-scan the assembled content (incl. the inline structured data),
    # BEFORE building/writing anything. A hit refuses the write.
    scan_targets = dict(members)
    scan_targets["__inline_environment__"] = json.dumps(environment, ensure_ascii=False).encode("utf-8")
    scan_targets["__inline_project_shape__"] = json.dumps(project_shape, ensure_ascii=False).encode("utf-8")
    scan_targets["__inline_config_digests__"] = json.dumps(config_digests, ensure_ascii=False).encode("utf-8")
    verdict = self_scan(scan_targets)
    if not verdict["ok"]:
        summary = ", ".join(
            f"{h['member']}:{h['marker']}x{h['count']}" for h in verdict["hits"]
        )
        raise BundleError(
            "refusing to write support bundle: self-scan found forbidden "
            f"marker(s) — {summary}. Redaction failed; no bundle was written."
        )

    manifest = {
        "format": BUNDLE_FORMAT,
        # SUPPORT-P0-001: a LIMITED guarantee, not a blanket "clean" claim. The
        # ledger tails are a structured-field allowlist (free text dropped by
        # construction); the self-scan below is a SECONDARY tripwire over the
        # assembled bytes, not the primary content guarantee.
        "guarantee": (
            "ledger tails limited to a structured-field allowlist (free-text "
            "fields dropped, not merely redacted); ledger inputs read no-follow; "
            "self_scan is a secondary tripwire, not a content guarantee"
        ),
        "self_scan": verdict,
        "redaction": redaction,
        "environment": environment,
        "project_shape": project_shape,
        "config_digests": config_digests,
        "included": included,
        "skipped": skipped,
    }
    members["MANIFEST.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    manifest["members"] = sorted(members)

    # Re-serialize MANIFEST.json now that it carries its own members list, then a
    # FINAL enforcement scan over every real member (manifest included).
    members["MANIFEST.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    final = self_scan(members)
    if not final["ok"]:
        summary = ", ".join(
            f"{h['member']}:{h['marker']}x{h['count']}" for h in final["hits"]
        )
        raise BundleError(
            "refusing to write support bundle: final self-scan found forbidden "
            f"marker(s) — {summary}. No bundle was written."
        )

    size = _write_zip(dest, members)

    summary = dict(manifest)
    summary["output"] = str(dest)
    summary["output_bytes"] = size
    return summary
