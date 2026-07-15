"""Generate large GUI scale fixtures (no network, lightweight media).

Usage::

    python tests/fixtures/make_gui_scale_project.py /tmp/gui10 --shots 10 --takes-per-shot 5
"""

from __future__ import annotations

import argparse
import json
import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _minimal_png(path: Path, r: int = 40, g: int = 80, b: int = 160) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes([0, r & 255, g & 255, b & 255]))
    raw = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", idat) + chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def build(out_dir: Path, *, shots: int, takes_per_shot: int, name: str) -> Path:
    from manju.core.container import Project
    from manju.core.models import ShotSpec, TakeSidecar
    from manju.core.yamlio import write_yaml

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    project = Project.create(out_dir / name, name=name, vertical=True, git_init=False)

    write_yaml(project.root / "bible" / "characters.yaml", {
        "linxia": {
            "name": "林夏",
            "appearance": "scale fixture",
            "voice": "calm",
            "personality": "alert",
        },
    })
    write_yaml(project.root / "bible" / "scenes.yaml", {
        "convenience_store": {
            "name": "便利店",
            "description": "scale fixture scene",
            "lighting": "cool white",
        },
    })

    staging = out_dir / "_staging"
    staging.mkdir(exist_ok=True)

    for i in range(1, shots + 1):
        sid = f"S{i:03d}"
        data: dict[str, Any] = {
            "id": sid,
            "scene": "convenience_store",
            "characters": ["linxia"],
            "dialogue": {
                "speaker": "linxia" if i % 2 else "",
                "text": f"这是第 {i} 句对白。" if i % 3 else "",
            },
            "action": {"main": f"镜头 {sid} 动作"},
            "duration": "auto",
        }
        shot = ShotSpec.model_validate(data)
        project.save_shot(shot)
        index = project.load_index()
        if sid not in index.order:
            index.order.append(sid)
            project.save_index(index)

        selected_name = None
        for t in range(1, takes_per_shot + 1):
            png = staging / f"{sid}_{t}.png"
            _minimal_png(png, r=(i * 3 + t) % 200 + 20,
                         g=(i * 5) % 200 + 20, b=(t * 40) % 200 + 20)
            info = project.register_take(
                sid, png,
                TakeSidecar(
                    provider="manual_import" if t > 1 else "local_fake",
                    spec_hash="manual" if t > 1 else f"spec{i:04d}{t:02d}",
                    seed=i * 100 + t,
                    params={"seed": i * 100 + t},
                ),
            )
            if t == 1:
                selected_name = info.name

        # Select first take for most shots; leave some unselected.
        if selected_name and i % 11 != 0:
            def _select(d: dict[str, Any], name: str = selected_name) -> None:
                st = d.setdefault("status", {})
                st["selected_take"] = name
                if i % 7 == 0:
                    notes = st.setdefault("take_notes", {})
                    notes[name] = f"00:01 note {sid}"

            project.update_shot_raw(sid, _select)

    runtime = project.runtime_dir
    runtime.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (runtime / "jobs.jsonl").write_text(
        json.dumps({
            "ts": now, "id": "seedrun001", "kind": "build", "state": "running",
            "params_summary": {"target": "final"}, "error": None,
            "project_id": "scale-seed",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return project.root


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Make GUI scale Manju project")
    p.add_argument("out_dir", type=Path)
    p.add_argument("--shots", type=int, default=10)
    p.add_argument("--takes-per-shot", type=int, default=5)
    p.add_argument("--name", default="scale_gui")
    args = p.parse_args(argv)
    root = build(args.out_dir, shots=args.shots,
                 takes_per_shot=args.takes_per_shot, name=args.name)
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
