"""Minimal JSONPath extraction for provider manifests (§8.6).

Supports exactly what cloud API responses need — dotted keys and list
indices: ``$.data.task_id``, ``$.results[0].video_url``, ``data.status``
(the leading ``$.`` is optional). Anything fancier belongs in a dedicated
Python adapter, not in config.

The path is parsed by ONE strict tokenizer (:func:`_tokenize`) that consumes
the string CONTIGUOUSLY from beginning to end. Malformed syntax — empty
segments, repeated dots, non-numeric or unclosed brackets, a bare key wedged
after ``]``, trailing junk — is REJECTED with :class:`JsonPathError`, never
silently ignored. The old ``re.finditer`` scanner skipped over anything that
did not match, so ``$.data..id`` read as ``$.data.id``, ``$.data[abc]`` as
``$.data.abc`` and ``$.arr[0]junk.y`` silently redirected the read/write — a
malformed manifest could poll the wrong task, download the wrong output, or
inject a reference into the wrong body field with no configuration error.
"""

from __future__ import annotations

from typing import Any


class JsonPathError(KeyError):
    pass


def _tokenize(path: str) -> list[tuple[str, Any]]:
    """Parse a mini-JSONPath into an ordered list of steps — ``("key", name)``
    or ``("index", int)`` — consuming the WHOLE string. Raises
    :class:`JsonPathError` on any malformed or unaddressable path.

    Grammar: an optional leading ``$``; then one or more steps, each either
    ``.key`` or ``[digits]``; when there is NO leading ``$`` the first step may
    also be a bare ``key`` (so ``data.status`` works). A ``key`` is one or more
    characters none of which are ``.``, ``[`` or ``]``."""
    if not isinstance(path, str) or not path:
        raise JsonPathError(f"empty or non-string path {path!r}")
    saw_dollar = path.startswith("$")
    i = 1 if saw_dollar else 0
    steps: list[tuple[str, Any]] = []
    n = len(path)
    while i < n:
        c = path[i]
        if c == ".":
            i += 1
            j = i
            while j < n and path[j] not in ".[]":
                j += 1
            key = path[i:j]
            if not key:
                raise JsonPathError(f"path {path!r}: empty key segment at index {i}")
            steps.append(("key", key))
            i = j
        elif c == "[":
            close = path.find("]", i)
            if close == -1:
                raise JsonPathError(f"path {path!r}: unclosed '[' at index {i}")
            inner = path[i + 1:close]
            if not inner or not inner.isdigit():
                raise JsonPathError(
                    f"path {path!r}: '[{inner}]' is not a numeric index")
            steps.append(("index", int(inner)))
            i = close + 1
        elif not steps and not saw_dollar and c not in ".[]":
            # a bare LEADING key (no ``$`` and no leading dot), e.g. "data.status"
            j = i
            while j < n and path[j] not in ".[]":
                j += 1
            steps.append(("key", path[i:j]))
            i = j
        else:
            raise JsonPathError(
                f"path {path!r}: unexpected character {c!r} at index {i}")
    if not steps:
        raise JsonPathError(f"path {path!r}: no addressable segments")
    return steps


def extract(data: Any, path: str) -> Any:
    """Resolve a mini-JSONPath against parsed JSON. Raises JsonPathError with
    the failing segment so misconfigured manifests produce debuggable errors."""
    node = data
    for kind, seg in _tokenize(path):
        try:
            node = node[seg]
        except (KeyError, IndexError, TypeError):
            label = f"[{seg}]" if kind == "index" else repr(seg)
            raise JsonPathError(f"path {path!r}: segment {label} not found") from None
    return node


def assign(data: Any, path: str, value: Any) -> None:
    """Set ``value`` at ``path`` in a parsed body dict — the write counterpart of
    :func:`extract`, used to inject a reference into a manifest's body_template
    (§8.6 ``refs.field``). Missing intermediate dict keys are created; list
    indices must already exist. Raises :class:`JsonPathError` on an empty/
    malformed path or an index into a non-list."""
    segments = _tokenize(path)
    node = data
    for kind, seg in segments[:-1]:
        try:
            if kind == "index":
                node = node[seg]
            else:
                child = node.get(seg)
                if not isinstance(child, (dict, list)):
                    child = {}
                    node[seg] = child
                node = child
        except (KeyError, IndexError, TypeError, AttributeError):
            label = f"[{seg}]" if kind == "index" else repr(seg)
            raise JsonPathError(
                f"path {path!r}: cannot descend into {label}") from None
    kind, seg = segments[-1]
    try:
        node[seg] = value
    except (IndexError, TypeError, AttributeError):
        raise JsonPathError(f"path {path!r}: cannot assign leaf") from None
