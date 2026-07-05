"""YAML/JSON file IO with crash safety.

All truth files are written via temp-file + atomic rename, always UTF-8,
always pathlib — Windows and Chinese paths are first-class citizens (§14).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


class _ManjuDumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.Dumper, value: str):  # keep multiline blocks readable
    if "\n" in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_ManjuDumper.add_representer(str, _str_representer)


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_yaml(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def write_yaml(path: Path, data: Any) -> None:
    text = yaml.dump(
        data,
        Dumper=_ManjuDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )
    atomic_write_text(Path(path), text)


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any, indent: int = 2) -> None:
    atomic_write_text(Path(path), json.dumps(data, ensure_ascii=False, indent=indent) + "\n")
