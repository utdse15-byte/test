"""events.jsonl — the collaboration log (§3, §10).

Who did what, when. This is the handover surface between human and AI:
`manju status` + the tail of this log gets either party into context in 30s.
Append-only, one JSON object per line, UTF-8.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVENTS_FILE = "events.jsonl"


def append_event(project_root: Path, actor: str, action: str, detail: dict[str, Any] | None = None) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": actor,  # "human" | "ai" | "engine"
        "action": action,
        "detail": detail or {},
    }
    path = Path(project_root) / EVENTS_FILE
    line = json.dumps(record, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def tail_events(project_root: Path, n: int = 20) -> list[dict[str, Any]]:
    path = Path(project_root) / EVENTS_FILE
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn write must not brick the log
    return events[-n:]
