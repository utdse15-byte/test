"""Minimal JSONPath extraction for provider manifests (§8.6).

Supports exactly what cloud API responses need — dotted keys and list
indices: ``$.data.task_id``, ``$.results[0].video_url``, ``data.status``
(the leading ``$.`` is optional). Anything fancier belongs in a dedicated
Python adapter, not in config.
"""

from __future__ import annotations

import re
from typing import Any

_SEGMENT = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


class JsonPathError(KeyError):
    pass


def extract(data: Any, path: str) -> Any:
    """Resolve a mini-JSONPath against parsed JSON. Raises JsonPathError with
    the failing segment so misconfigured manifests produce debuggable errors."""
    if not path:
        raise JsonPathError("empty path")
    trimmed = path.removeprefix("$").lstrip(".")
    node = data
    for match in _SEGMENT.finditer(trimmed):
        key, index = match.group(1), match.group(2)
        try:
            if index is not None:
                node = node[int(index)]
            else:
                node = node[key]
        except (KeyError, IndexError, TypeError):
            raise JsonPathError(
                f"path {path!r}: segment {key or '[' + index + ']'!r} not found"
            ) from None
    return node


def assign(data: Any, path: str, value: Any) -> None:
    """Set ``value`` at ``path`` in a parsed body dict — the write counterpart of
    :func:`extract`, used to inject a reference into a manifest's body_template
    (§8.6 ``refs.field``). Missing intermediate dict keys are created; list
    indices must already exist. Raises :class:`JsonPathError` on an empty path or
    an index into a non-list."""
    if not path:
        raise JsonPathError("empty path")
    trimmed = path.removeprefix("$").lstrip(".")
    segments = [(m.group(1), m.group(2)) for m in _SEGMENT.finditer(trimmed)]
    if not segments:
        raise JsonPathError(f"path {path!r}: no segments")
    node = data
    for key, index in segments[:-1]:
        try:
            if index is not None:
                node = node[int(index)]
            else:
                child = node.get(key)
                if not isinstance(child, (dict, list)):
                    child = {}
                    node[key] = child
                node = child
        except (KeyError, IndexError, TypeError, AttributeError):
            raise JsonPathError(
                f"path {path!r}: cannot descend into {key or '[' + str(index) + ']'!r}"
            ) from None
    key, index = segments[-1]
    try:
        if index is not None:
            node[int(index)] = value
        else:
            node[key] = value
    except (IndexError, TypeError, AttributeError):
        raise JsonPathError(f"path {path!r}: cannot assign leaf") from None
