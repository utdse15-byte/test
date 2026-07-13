"""Generate the 12-shot M0 acceptance asset (§13 M0).

This is the regression fixture the M0 end-to-end test builds on: 12 local video
clips, 12 Chinese caption lines, one BGM bed, 1080x1920. It uses ffmpeg via
subprocess directly (NOT manju.media, which is written by a sibling agent) and
imports only the frozen core surface.

Every take is registered as a ``manual_import`` provider with ``spec_hash =
"manual"`` and a real probe, so the whole project reads back as MANUAL and never
goes stale — exactly the M0 "human dropped in local clips" story.

Run standalone::

    python -m tests.fixtures.make_sample /path/to/output_dir
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from manju.core.container import Project
from manju.core.models import (
    Camera,
    Dialogue,
    ProbeInfo,
    ShotIndex,
    ShotSpec,
    ShotStatus,
    TakeSidecar,
)
from manju.core.models import SHOT_SIZES

# A 科幻悬疑 micro-story in 12 distinct Chinese beats (one per shot).
DIALOGUE = [
    "凌晨三点,便利店的门铃响了。",
    "那枚硬币上刻着的年份,是二零三六。",
    "周叔,这枚硬币不对劲。",
    "十年后的钱,怎么会出现在今天?",
    "监控里那个人,和我长得一模一样。",
    "他留下一句话:别相信明早的新闻。",
    "收银台的时钟,开始倒着走。",
    "窗外的雨,停在了半空中。",
    "林夏,你已经来过这里很多次了。",
    "每一次循环,都是从这枚硬币开始。",
    "如果我打破它,时间会不会重新流动?",
    "门铃再次响起——又是凌晨三点。",
]

SCENES = ["convenience_store", "rainy_street"]
CHARACTERS = ["linxia", "old_zhou"]

FF_COMMON = ["-y", "-hide_banner", "-loglevel", "error"]


def _write_bible(project: Project) -> None:
    from manju.core.yamlio import write_yaml

    write_yaml(
        project.root / "bible" / "scenes.yaml",
        {
            "convenience_store": {
                "name": "便利店",
                "description": "雨夜街角的二十四小时便利店,冷白灯管,货架反光。",
                "lighting": "冷白顶灯,窗外霓虹泛蓝",
            },
            "rainy_street": {
                "name": "雨夜街道",
                "description": "空无一人的湿滑街道,积水倒映着招牌。",
                "lighting": "路灯昏黄,雨丝密集",
            },
        },
    )
    write_yaml(
        project.root / "bible" / "characters.yaml",
        {
            "linxia": {
                "name": "林夏",
                "appearance": "短发,黑色风衣,左手戴一块旧手表",
                "voice": "冷静、克制、略带沙哑",
                "personality": "警觉,不轻易相信任何人",
            },
            "old_zhou": {
                "name": "周叔",
                "appearance": "微秃,灰色围裙,总在擦柜台",
                "voice": "温和迟缓,带点乡音",
                "personality": "念旧,守着这家店十几年",
            },
        },
    )
    write_yaml(
        project.root / "bible" / "style.yaml",
        {
            "global_style": {
                "name": "雨夜赛博悬疑",
                "palette": "青蓝主色,霓虹点缀,暗部偏冷",
                "mood": "压抑、悬疑、时间错乱",
                "pacing": "短镜头,快切,留白",
                "caption_style": "居中白字黑描边,底部安全区,最多两行",
            }
        },
    )


def _make_clip(dest: Path, freq: int, clip_seconds: float) -> None:
    """A distinct testsrc2 clip with a distinct sine tone (h264 + aac).

    Encoded with ``-preset ultrafast``: this is THROWAWAY source material for the
    regression fixture. Every take made from it is registered with the literal
    ``spec_hash="manual"`` and a hand-set probe (see ``make_sample_project``), so
    the clip's encoded bytes feed NO pinned hash — the byte-identity/idempotency
    pins are on the REAL render pipeline (veryfast/crf18), which is untouched.
    Do not "restore" a slower preset here expecting it to affect any golden.
    """
    subprocess.run(
        [
            "ffmpeg", *FF_COMMON,
            "-f", "lavfi", "-i", f"testsrc2=size=540x960:rate=24:duration={clip_seconds}",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={clip_seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )


def _make_bgm(dest: Path, seconds: float) -> None:
    subprocess.run(
        [
            "ffmpeg", *FF_COMMON,
            "-f", "lavfi", "-i", f"sine=frequency=110:duration={seconds}",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )


def make_sample_project(dest: Path, shots: int = 12, *, clip_seconds: float = 1.0,
                        with_bgm: bool = True) -> Path:
    """Build the sample project under ``dest`` and return its root.

    If ``dest`` already ends in ``.manju`` it is used verbatim; otherwise a
    Chinese-named project ``雨夜便利店.manju`` is created inside ``dest``.
    """
    dest = Path(dest)
    if dest.suffix == ".manju":
        root_hint, name = dest, dest.stem
    else:
        root_hint, name = dest / "雨夜便利店", "雨夜便利店"

    project = Project.create(root_hint, name=name, vertical=True, git_init=False)
    _write_bible(project)

    order: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for i in range(1, shots + 1):
            sid = f"S{i:03d}"
            freq = 220 + (i * 53) % 660  # a distinct tone per shot
            clip = tmpdir / f"{sid}.mp4"
            _make_clip(clip, freq, clip_seconds)

            take = project.register_take(
                sid,
                clip,
                TakeSidecar(
                    provider="manual_import",
                    spec_hash="manual",
                    probe=ProbeInfo(
                        duration_ms=int(clip_seconds * 1000),
                        width=540,
                        height=960,
                        fps=24,
                        has_audio=True,
                    ),
                ),
            )

            scene = SCENES[i % len(SCENES)]
            character = CHARACTERS[i % len(CHARACTERS)]
            shot = ShotSpec(
                id=sid,
                scene=scene,
                characters=[character],
                duration="auto",
                camera=Camera(
                    shot_size=SHOT_SIZES[i % len(SHOT_SIZES)],
                    movement="slow_push_in" if i % 2 else "static",
                    angle="eye_level",
                ),
                dialogue=Dialogue(speaker=character, text=DIALOGUE[i - 1]),
                status=ShotStatus(selected_take=take.name),
            )
            project.save_shot(shot)
            order.append(sid)

        project.save_index(ShotIndex(order=order))

        if with_bgm:
            bgm = project.imports_dir / "bgm.wav"
            _make_bgm(bgm, shots * clip_seconds + 2.0)
            rules = project.load_rules()
            rules.music.source = "media/imports/bgm.wav"
            project.save_rules(rules)

    return project.root


def main(argv: list[str] | None = None) -> int:
    """CLI entry (argparse — `--help` prints usage and creates NOTHING)."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate the 12-shot vertical regression sample project "
                    "(§13 M0 acceptance asset).",
    )
    parser.add_argument("output_dir", type=Path,
                        help="directory to create the sample project in")
    parser.add_argument("--shots", type=int, default=12,
                        help="number of shots (default: 12)")
    parser.add_argument("--clip-seconds", type=float, default=1.0,
                        help="duration of each generated clip (default: 1.0)")
    parser.add_argument("--no-bgm", action="store_true",
                        help="skip the background-music track")
    args = parser.parse_args(argv)

    root = make_sample_project(
        args.output_dir, shots=args.shots,
        clip_seconds=args.clip_seconds, with_bgm=not args.no_bgm,
    )
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
