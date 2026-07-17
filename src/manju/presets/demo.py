"""The built-in demo project (UX wave 2 item 8): 雨夜便利店, a complete
12-shot time-loop micro-story that builds to a finished video at ZERO cost.

``manju new <名字> --demo`` scaffolds TEXT TRUTH ONLY — brief, script, bible,
and 12 shots pinned to the free local ``caption_card`` provider (no reference
image needed, no cloud, no key). The finished video comes from the normal
pipeline afterwards::

    cd <名字>.manju && manju build --yes    # ffmpeg required, money not

Why it exists: onboarding (a tangible finished project to explore), AI-session
reference (an end-to-end project for validating changes), and demos to friends
(no spend, no content improvised on the spot). The engine never invents story
content at build time — this module is owner-authored fixture text, written
once, and everything it writes is plain hand-editable YAML/Markdown (§2/§3).
"""

from __future__ import annotations

from typing import Any

from ..core.container import Project
from ..core.models import ShotSpec
from ..core.yamlio import write_yaml

# The 科幻悬疑 micro-story: one beat per shot, 凌晨三点的时间循环。
# (Same story the M0 acceptance fixture tells — kept in prose sync by taste,
# not by import: tests/ must never become a src/ dependency.)
_BEATS: list[tuple[str, str, str]] = [
    # (scene, speaker, line)
    ("convenience_store", "linxia", "凌晨三点,便利店的门铃响了。"),
    ("convenience_store", "linxia", "那枚硬币上刻着的年份,是二零三六。"),
    ("convenience_store", "linxia", "周叔,这枚硬币不对劲。"),
    ("convenience_store", "old_zhou", "十年后的钱,怎么会出现在今天?"),
    ("convenience_store", "linxia", "监控里那个人,和我长得一模一样。"),
    ("convenience_store", "old_zhou", "他留下一句话:别相信明早的新闻。"),
    ("convenience_store", "linxia", "收银台的时钟,开始倒着走。"),
    ("rainy_street", "linxia", "窗外的雨,停在了半空中。"),
    ("rainy_street", "old_zhou", "林夏,你已经来过这里很多次了。"),
    ("rainy_street", "old_zhou", "每一次循环,都是从这枚硬币开始。"),
    ("convenience_store", "linxia", "如果我打破它,时间会不会重新流动?"),
    ("convenience_store", "linxia", "门铃再次响起——又是凌晨三点。"),
]

_BRIEF = """\
# 一句话创意

雨夜便利店里,店员林夏发现一枚来自十年后的硬币——凌晨三点的门铃,
把她困进了一场只有她自己察觉的时间循环。
"""

_SCRIPT_HEADER = """\
# 雨夜便利店(demo)

十二拍微型故事:科幻悬疑,单一场景为主,时间循环结构。
每一拍即一个镜头(S001–S012),对白就是分镜真相 —— 改这里之后
记得同步 shots/*.yaml(或用 manju ingest 流程重新导入)。

"""


def scaffold_demo(project: Project) -> list[str]:
    """Write the demo's text truth into a freshly created project.

    Returns the created shot ids. Zero cost by construction: every shot pins
    ``generation.provider: caption_card`` (the free local text-card provider,
    needs only ffmpeg — no reference image, no cloud key, no spend)."""
    write_yaml(project.root / "bible" / "scenes.yaml", {
        "convenience_store": {
            "name": "便利店",
            "description": "雨夜街角的二十四小时便利店,霓虹灯在水洼里融化。",
            "lighting": "冷白灯管,窗外霓虹泛蓝",
        },
        "rainy_street": {
            "name": "雨夜街道",
            "description": "便利店门外的窄街,雨点悬停在路灯光晕里。",
            "lighting": "路灯钠黄,雨幕反光",
        },
    })
    write_yaml(project.root / "bible" / "characters.yaml", {
        "linxia": {
            "name": "林夏",
            "appearance": "短发,黑色风衣,左手戴旧手表",
            "voice": "冷静、克制、略带沙哑",
            "personality": "警觉,不轻易相信人",
        },
        "old_zhou": {
            "name": "周叔",
            "appearance": "花白头发,深灰毛衣,总在擦同一只杯子",
            "voice": "低沉、缓慢,像在讲很久以前的事",
            "personality": "看似糊涂,其实什么都知道",
        },
    })

    (project.root / "story" / "brief.md").write_text(_BRIEF, encoding="utf-8")
    script = _SCRIPT_HEADER + "\n".join(
        f"{i:02d}. [{scene}] {speaker}:{line}"
        for i, (scene, speaker, line) in enumerate(_BEATS, start=1)) + "\n"
    (project.root / "story" / "script.md").write_text(script, encoding="utf-8")

    created: list[str] = []
    for i, (scene, speaker, line) in enumerate(_BEATS, start=1):
        shot_id = f"S{i:03d}"
        data: dict[str, Any] = {
            "id": shot_id,
            "scene": scene,
            "characters": [speaker],
            "dialogue": {"speaker": speaker, "text": line},
            "duration": "auto",
            # ZERO-COST pin: the free local text-card provider — a demo must
            # never be one `build --yes` away from real spend.
            "generation": {"provider": "caption_card"},
        }
        project.save_shot(ShotSpec.model_validate(data))
        created.append(shot_id)

    index = project.load_index()
    for shot_id in created:
        if shot_id not in index.order:
            index.order.append(shot_id)
    project.save_index(index)
    return created
