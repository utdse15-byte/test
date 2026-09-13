# 2026-09-12 顶尖模型与创作流程核验

## 实际采用

### 1. GPT-Image-2.5 Sunburst：用具体入口做精修交接

OpenAI 2026-09-08 发布 Images 2.5；官方将 Sunburst 定位于对编辑精度要求更高的创作。模型页列出 `gpt-image-2.5-sunburst-2026-09-08` 固定快照及 `max` 质量档。本轮加入的是这一明确身份的离线任务说明，不是 API 执行器。另保留“自行选择顶尖工具”，两者均须由用户明确选择，不自动降档。

官方产品定位不是独立盲测结果；未使用付费生成、未作画质排名。ChatGPT 网页、OpenAI API 与 Runway 转接入口不同，不把一处参数当成所有入口的限制。本功能导出原图和目标要求，让用户在自己的外部工具中执行，回来仍须逐图查看。

来源：
- https://openai.com/index/introducing-chatgpt-images-2-5/ （2026-09-08）
- https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst （核验支持 max 与日期快照）
- https://docs.dev.runwayml.com/api-details/api_changelog/ （2026-09-08 的 GPT Image 2.5 接入是不同平台入口，仅作对照，不用于本轮参数模板）

### 2. Premiere 与 Runway：学减少搬运，不重复造插件

Adobe 2026-09-08 公告和 09-09 帮助页把生成放到当前时间线的区间、上下文与可继续编辑的结果中。Runway 09-08 的插件同样强调从原素材继续。这里采用的是“让创作上下文随任务走”的设计，而非声称 Manju 已拥有 Adobe 插件。

落实：在既有导演区内补上逐锚点任务 ZIP、每张独立 TXT、实际原帧和旧目标图；分批返回时同时核对共用镜头要求与对应锚点，不覆盖其他区域。没有新开一个十几个设置的工作区。

来源：
- https://blog.adobe.com/en/publish/2026/09/08/generate-create-directly-in-your-timeline-with-new-ai-powered-innovations-in-premiere-after-effects
- https://helpx.adobe.com/premiere/desktop/edit-projects/edit-with-generative-ai/generative-media-tool-overview.html
- https://runway.com/changelog （2026-09-08）
- https://help.runwayml.com/hc/en-us/articles/51683104370451-Creating-with-Edit-Studio

### 3. Comfy：完整底层流程，少量用户输入

Comfy 官方 App Mode 文章发布于 2026-03-10，不冒充本周新消息。它把已配置工作流包装成少量输入与输出；07-15 的批处理文章进一步区分“任务报告完成”与“文件真实可取”。本轮借鉴可重复交接和少量明确操作：一个 ZIP 发出，TASK.json 与实际结果带回，明确预览后再绑定。并继续实测下载本身，不以界面成功提示代替磁盘文件。

没有下载 Comfy 节点、连接云账户、运行远程脚本或把本地目录暴露给代理。

来源：
- https://blog.comfy.org/p/from-workflow-to-app-introducing
- https://blog.comfy.org/p/batch-generation-in-comfy-mcp-use

## 已核对但不擅自推进

人工偏好榜仍应按文生视频、图生视频与编辑任务分开看。同名模型的不同后训练、平台和快照也不能混用。本轮查阅 AA 对应榜单作为研究背景，但没有改变原 12 个视频能力档和质量名单，也未冒称它们已全部重新通过当日接口实测。

- https://artificialanalysis.ai/video/leaderboard/text-to-video
- https://artificialanalysis.ai/video/leaderboard/image-to-video
- https://artificialanalysis.ai/video/leaderboard/video-editing

Sora 视频接口在 OpenAI 官方退役表中仍列为 2026-09-24 关闭。本次核验日在此之前，不写成已关停，不新增专用依赖。
- https://developers.openai.com/api/docs/deprecations

公开 X 搜索本轮未取得可作为新增实现依据的近期原帖正文，混入的自动摘要和旧帖不作为能力证据。代码中的型号事实来自上述官方文档；检索不到不代表 X 上没有相关讨论。

## 结果的边界

这不是通用 PNG/HDR/PSD 编辑器。原帧仍为浏览器 SDR 参考；返回只接收通过实际浏览器解码的同画幅 PNG，原字节不变，不裁切、不上采样。先制作目标图，再交视频工具，不代表视频生成已自动完成。图像里含有 C2PA、文本等附加块时，文件整体被保留；本功能没有认证其签名或来源。

研究核验日：2026-09-12。资料更新后应新增/保留具体版本的入口记录，不应修改历史任务使其暗中变成另一型号。
