"""Human-readable upload map over an already frozen handoff (no I/O)."""
from __future__ import annotations

import html
from typing import Any, Mapping


def _cell(value: Any) -> str:
    """Keep user-authored text inert and within a single Markdown table cell."""
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(item) for item in value)
    text = str(value) if value not in (None, "") else "—"
    text = html.escape(text).replace("|", "&#124;").replace("\r", " ").replace("\n", " ")
    return f"<code>{text}</code>"


def _row(*values: Any) -> str:
    return "| " + " | ".join(_cell(value) for value in values) + " |"


def render_asset_map(handoff: Mapping[str, Any], refs: Mapping[str, Any]) -> str:
    """A view of exact packaged paths; never infer provider-native capability.

    Only data frozen by provider_handoff.build_handoff is accepted. In
    particular, this does not resolve paths, read source assets or fetch URLs.
    """
    rows = [
        "# 素材上传对照表 / Asset map", "",
        f"镜头：{_cell(handoff.get('shot'))}", "",
        "先核对外部工具当前的时长、画幅、首尾帧、参考数量和音频支持。",
        "本表只说明创作意图与包内文件，不证明任何模型能够执行这些控制。",
        "不要把整个工程上传；只选择本表列出的包内素材，并在外部工具中指定对应角色。", "",
        "## 素材与槽位", "",
        "以下顺序先首帧、再尾帧、再其他关键帧与参考素材；不等于某个模型的原生槽位编号。",
        "同一文件可以承担多个角色；文件路径相同表示复用，不需要另找一份素材。", "",
        "| 顺序 | 内部标识 | 用途 | 包内相对路径 | 状态 |",
        "| --- | --- | --- | --- | --- |",
    ]
    frames = list(handoff.get("keyframes") or ())
    # Stable order within each authored group; never sort references by name.
    frames = sorted(frames, key=lambda row: {"start": 0, "end": 1}.get(row.get("role"), 2))
    inventory: list[tuple[Any, Any, Any, Any]] = []
    for frame in frames:
        role = frame.get("role") or "authored keyframe (support unverified)"
        inventory.append((frame.get("id"), role, frame.get("asset"),
                          "PACKAGED" if frame.get("asset") else "NOT_PACKAGED"))
    for asset in refs.get("physical_assets") or ():
        inventory.append((asset.get("id"), asset.get("kind"), asset.get("path"),
                          "PACKAGED" if asset.get("path") else "NOT_PACKAGED"))
    for index, values in enumerate(inventory, 1):
        rows.append(_row(index, *values))
    if not inventory:
        rows.extend(["", "本镜头没有参考素材；使用 prompt.txt 中的文字意图。"])
    rows.extend([
        "", "NOT_PACKAGED 表示包内没有该文件：外链未下载，或该关键帧只有文字意图。",
        "不要把内部标识当作文件名，不要认为本表自动上传或补齐了外链。", "",
        "## 参考绑定：分别保留主体与控制范围", "",
        "同一实体文件的多条绑定分别列出，不能因文件去重而丢掉另一人物或另一用途。", "",
        "| 参考标识 | 方言标签 | 主体范围 | 应参考 | 不应继承 | 包内相对路径 |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    bindings = refs.get("logical_bindings") or ()
    for binding in bindings:
        rows.append(_row(binding.get("physical_id"), binding.get("label"),
                         binding.get("subject_ref"), binding.get("controls"),
                         binding.get("ignore"), binding.get("asset") or "NOT_PACKAGED"))
    if not bindings:
        rows.extend(["", "未指定参考绑定；不要补造主体或控制要求。"])
    rows.extend([
        "", "方言标签是交接提示，不承诺目标工具原生识别；未指定的范围不能自行扩展。", "",
        "## 回收边界", "",
        "生成结果按镜头编号命名，按本包的回收说明预览后入库；不要自动选用、人工批准或锁片。",
        "校验和只证明包内字节一致，不证明生成器身份、版权许可或画面质量。",
        "机器真相仍是 handoff.json、refs.json 和清单；本表是同一冻结快照的可读视图。", "",
    ])
    return "\n".join(rows)
