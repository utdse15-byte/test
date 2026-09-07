# 新模型与工作流复核

核验日期：2026-09-06（用户时区）；本报告只区分官方声明和本地实现，不提供付费生成盲测结果。

## 本轮真正采用的能力

| 官方来源 | 核验内容 | 本地适配决定 |
|---|---|---|
| MiniMax Video V2 | H3 与 H3-Max 的参考输入、时长、分辨率不同；首尾帧与参考模式互斥 | 分成两个档，逐模式校验。H3-Max 不允许参考生成或 2K；图生视频画幅按 adaptive 处理 |
| Gemini Omni API | 型号为 gemini-omni-1.1-flash，支持视频编辑/延展，上传路径有时长和地区限制；当前不支持上传音频参考 | 新增薄作者档，不接通付费执行。1080p/4k 标为上采样，不误称原生生成；未核验的输出时长只告警 |
| Veo 3.1 API | 4/6/8 秒；高分辨率、参考输入受时长约束，延展要求先前生成的视频 | 按 Preview/Fast 具体型号分别建档，延展要求来源声明，且不把来源声明当作证明 |
| Runway Apps | 任务型入口，Apps 不提供 API | Keyframes 与 Edit Studio 为网页工作流档，不设置虚构的可执行模型 ID |
| Luma Ray3 Modify 指南 | 视频编辑可组合关键帧和角色参考；高强度可能损失原镜头运动；旧尾帧卡取第5秒 | 保留/改变约束分开填写；超过5秒输入不把第5秒卡当真实片尾；不继承为新版 Ray 或旧 Ray2 API 的通用能力 |
| Adobe Quick Cut | 从已上传素材产生可继续编辑的初剪 | 学习“可修改草稿在前、人工定稿在后”，不复制自动选片或绕过本项目人工门 |

## 官方来源

1. https://platform.minimax.io/docs/api-reference/video-generation-v2-create
2. https://ai.google.dev/gemini-api/docs/video
3. https://ai.google.dev/gemini-api/docs/omni
4. https://ai.google.dev/gemini-api/docs/veo
5. https://help.runwayml.com/hc/en-us/articles/45570040112531-Creating-with-Apps
6. https://lumalabs.ai/learning-hub/ray3-modify-user-guide
7. https://helpx.adobe.com/firefly/web/firefly-video-editor/create-quick-cut/quick-cut-overview.html

## 工程取舍

任务先于型号；一次交接冻结请求、能力证据和素材哈希；更换型号不污染原影片工程。
能力档为“有限核验范围”，不是厂商能力上限，也不是供应商执行合同。
输入参数检查、静态目录、实际本地封包已经实现；真实生成速度、价格、画质、可用地区和远程取消未通过用户账户验证。
没有复制竞争产品网页或引入收费 SDK，没有使用用户凭据，没有生成任何商业账单。

原 R4 验证报告明确记录封包失败，不能把旧聊天中的实现描述当作继承事实。本轮从已校验 R2/R3 重建源码。
