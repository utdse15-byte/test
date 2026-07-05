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
