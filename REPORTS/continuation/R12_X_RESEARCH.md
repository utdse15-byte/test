# R12：顶尖模型优先与运动先行的导演交接

核验日期：2026-09-08。项目基线是实际可读取的 R11（636d010），不是先前聊天里未经封包证明的版本声明。

## 结论与采用范围

用户本轮明确只用顶尖模型，不接受为了省钱、省时间而降到弱档。因此本轮把“可兼容”与“当前值得优先考虑”分开：旧能力目录保留恢复价值，新的离线工作台默认启用质量短名单。名单不用价格排序，也不自动替人选模型。草稿表示尚未审片，不表示必须用低配模型。

这是一项有日期的策展决定，不是本项目对每个型号、入口或镜头做完盲测的结论。未登录商用平台、未花费生成额度、未下载模型权重。本轮开发使用合成媒体；功能正确性与真实生成画质是两种证据。

## 1. 视频模型：具体身份比厂商品牌重要

**独立评测快照。** Artificial Analysis 的含音频文生视频榜列出 Wan 3.0、Gemini Omni Flash、fal 后训练的 H3 Max 位于前列；置信区间有重叠，不能把相差几分说成每个镜头都更强。含音频图生视频的首位是 fal 后训练版 H3 Max；无音频图生视频则是 Omni / Wan 等前列。视频编辑榜当前将 Wan 3.0 列于首位。[S1–S3]

本轮短名单是以下三项，按任务约束筛选，不生成一个万能冠军排序：

| 身份 | 进代码的内容 | 不能混同的部分 |
|---|---|---|
| Wan 3.0 标准版，`wan3.0-video` | 新增独立作者档；文本、首帧、首尾帧及参考/编辑/延展约束 | 不用 Prime 速度版替代；模型级榜单不是全部控制模式的逐项验证 |
| fal 后训练 H3 Max，`minimax/h3-max` | 新增 fal 具体入口档；文本、单图、首尾帧 | 不是原 MiniMax 官方同名 H3-Max；1080P 经原生 768P 精修，不能说成原生1080 |
| `gemini-omni-1.1-flash` | 保留现有档，以“官方继任、家族证据”单独标注 | 榜单条目标的是5月的 Omni Flash，不是对8月27日1.1 stable端点的独立分数证明 |

Wan 官方9月4日文档将标准版与 Prime 分开；无输入视频时输出2至30秒，有视频时受输入加输出总时长限制。本地 v1 能力表达对视频条件模式采用“输入不超过15秒、输出不超过15秒”的保守子集；不是声称供应商所有模式都最多15秒。多参考的文件数与总时长也按具体入口写入校验。[S4]

fal 的输入表没有给出可据以落实的 duration 上下界，本轮保持未知并提示复核，不借用其他接口或输出分段字段的数字。quality expansion 可能扩写提示词，研究建议保存并检查实际扩写结果；本轮没有自动开启它或改写用户意图。[S5–S6]

Google 官方稳定版本日期用于继任身份说明，不把“Flash”字样当弱模型证据，也不把旧榜单的成绩贴在新稳定端点上。[S7]

**实际实现：** 原十个能力档对象逐项不变；只追加两个新档。质量名单包含来源、检查/复核日期、精确能力档哈希。能力档即使ID没变，只要内容变化，就不能继续沿用旧名单依据。过期名单显示复核告警，不悄悄改成便宜型号；将来日期的证据不生效。名单不是发布者签名，也不是永久排名。

## 2. 顶尖图像工具：用于先把目标画面做对

本轮另外核验了图像编辑前列与官方具体产品。AA 图像编辑页面将 MAI-Image-2.6 与 GPT Image 2 high 放在前列，页面缓存和相邻分数会变化，不采用单个截图宣布永久冠军。[S8]

微软9月4日说明 MAI-Image-2.6 及 Flash 进入 Foundry 公共预览；完整型号与 Flash 是不同选项。OpenAI 官方将 GPT Image 2 定位为新的高保真图像生成/编辑型号。[S9–S11]

这里的项目决定是：允许把使用者在这些外部工具中做好的目标 PNG 原样带回，不把图像API硬塞进视频能力档，也不以较小底片为理由缩小高像素目标图。没有接入它们的账号、没有实际生成图片，也没有假称其中一个在用户题材上一定更好。

## 3. X 线索与不寻常但可实施的方法

### 给每个参考素材一份明确工作

本轮早期公开索引检索读到了 ChatCut 的4月8日参考素材实践：把人物/衣服/运动/镜头/声音的用途拆清楚，而不是把一组附件交给模型猜。它是旧的实践帖，不是9月新发布。随后直读原帖返回错误，没有将缺失正文补成引文。[X1]

进一步核对了 ChatCut 自己的开源 agent-plugin 和当前官方文档：首尾帧与普通参考不是同一输入通道，素材编号要明确；切换型号可能改变有效输入，返回素材应作为新资产保留。[S12–S13] 本轮只吸收角色清晰和原片保留，不复制其低成本草稿、自动改规格等策略，也不把提示词附加“4K”当成真实4K输出证明。

### 先拍运动，再用高质量关键帧重画外观

把现成实拍、表演或运动片段作为时序底片。选真正展示修改对象的时刻，先把静态目标图做好，再交给有对应能力的视频编辑路径处理运动一致性。Runway Edit Studio 的官方步骤明确包含选帧、改图、先看图像版本、再生成视频；Luma 的指导也强调原运动与时刻目标图的分工。[S14–S16]

**R12 已实现这条方法的准备和交接部分：** 实际原视频、SDR原尺寸参考帧、毫秒时刻、同画幅目标PNG、该时刻目标外观、保留/改变项一起保存为独立导演ZIP。新窗口可重新打开播放。目标PNG允许高于底片像素且字节不变。尚未填写的目标或尚未制作的图也能保存为草稿。

**未实现部分明确保留：** 没有自动剪切、上传、调用生成、拼接、选择结果；不把浏览器播放毫秒叫作认证帧编号，不把SDR截图叫作HDR/EXR母版，也不保证生成结果会逐像素保留原动作或范围外画面。

Luma 学习中心与 Agents API 是不同入口：一边谈 Modify 工作流，另一边列更广的API能力；输入时长等口径不同，不能合并成一个家族承诺。因此导演区的16锚点只是本地资源上限，没有据此声称某个模型支持16个关键帧。[S15–S16]

### 把作品与可复现的过程放在一起

ComfyUI 官方文档强调工作流 JSON 和图像中的工作流信息可以用于恢复工作。这里学习的是可恢复性，不是采用旧示例模型或引入一套GPU依赖。[S17] R12的导演包同时带原片、原帧、目标图、计划和可读说明，并严格校验闭合文件集合；不只留一段容易丢素材的提示词。

### 分镜网格值得用，但本轮不把它当高精度母版

检索还出现2月的多角度分镜网格实践线索。[X2] 这可用于比较镜位和统一视觉构思，但格子裁出来的图片可能像素不足，也不能保证身份和构图一致。本轮没有实现自动网格生成或声称裁格可无损取得高精度分镜；允许使用者把最终独立目标图带回导演区。

## 4. 检索覆盖、冲突与证据强度

X 不是本轮能力声明的单一依据。公开搜索中存在无结果、错误的同词账号、旧帖与自动汇总；原帖直读出现403/内部错误，Luma学习中心直接打开也失败。9月8日再次检索 H3 Max / Wan3 / MAI / Omni 的多组X查询没有取得足以证明最新发布的原帖正文。没有“搜尽X”、登录订阅时间线或持续后台监控。

原帖可读索引是实践线索；官方工具文档说明该工具的功能或方法；模型官方接口说明入口约束；AA只提供该评测设置下的偏好统计。这四者没有互相冒充。未采用第三方“Seedance3”“原生4K万能模型”等缺少本轮一手核验的宣传。

下面URL为检索记录，内容可能继续变化。摘要为本轮归纳，不是网页全文快照。

## 来源

S1 https://artificialanalysis.ai/video/leaderboard/text-to-video

S2 https://artificialanalysis.ai/video/leaderboard/image-to-video

S3 https://artificialanalysis.ai/video/leaderboard/video-editing

S4 https://www.alibabacloud.com/help/en/model-studio/wan3-video-generation-api-reference

S5 https://fal.ai/models/minimax/h3-max/image-to-video/api

S6 https://blog.fal.ai/introducing-h3-max-by-fal/

S7 https://ai.google.dev/gemini-api/docs/models/gemini-omni-flash

S8 https://artificialanalysis.ai/image/leaderboard/editing

S9 https://microsoft.ai/news/pushing-the-quality-cost-frontier-with-mai-image-2-6/

S10 https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-mai-image

S11 https://developers.openai.com/api/docs/models/gpt-image-2

S12 https://github.com/ChatCut-Inc/agent-plugin/blob/main/claude/skills/video-gen/references/seedance2.md

S13 https://chatcut.io/docs/video-generation-models

S14 https://help.runwayml.com/hc/en-us/articles/51683104370451-Creating-with-Edit-Studio

S15 https://lumalabs.ai/learning-center/articles/ray-3-2-introduction-and-core-concepts （索引可读，直接打开失败）

S16 https://docs.agents.lumalabs.ai/guides/faq/

S17 https://docs.comfy.org/get_started/first_generation

X1 https://x.com/chatcutapp/status/2041763561333264865 （2026-04-08实践帖，索引线索，直接打开失败）

X2 https://x.com/harboriis/status/2020087133285892368 （2026-02-07分镜线索，直接打开失败，不作为新模型证据）
