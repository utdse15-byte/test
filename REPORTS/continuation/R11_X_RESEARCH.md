# R11：X 线索、官方核验与实际采用

核验日：2026-09-08。研究对象：Manju One 的外部视频生成、视频编辑、返工、返回规格和本地执行边界。以下“已实现”指本项目代码，不表示商业平台已经接通或经用户账号实测。

## 结论

本轮最值得增加的是带实际原片的局部返工交接，而不是把每一个热搜型号做成按钮。新的编辑能力越强，越需要明确“改哪一段、哪些不能改、依据哪一份原片”。其次是按具体入口而不是品牌填能力表，并把标称清晰度与实际像素分开。

已在 R10 真正存在的源码上实施：独立返工协议和浏览器工作区；完整原片 ZIP 的导出、核验与重新打开；Seedance 2.5 的 BytePlus LAS 作者档；显式像素宽高返回检查。商业生成、模型权重下载和云端提交均未执行。测试与交付状态见 R11_VALIDATION.md，而非由研究报告推定通过。

## X 的检索范围与局限

检索覆盖 Seedance／Dreamina、Google Flow／Omni、Luma Ray、Runway、Higgsfield、LTX，以及视频编辑、首尾帧、参考一致性、局部修改、视频恢复、开源本地推理和成本讨论。使用公开索引检索与已发现的 X 帖子地址跟进，没有登录用户 X 账号，没有读取完整时间线或穷尽所有最新帖子。

截至核验日，多条 X 地址直读返回403、空页或抓取失败。搜索日期筛选也混入2025年和2026年春季旧内容；“近日抓取”不能解释为“近日发布”。部分命中是 /i/trending 的自动汇总，不是原帖，不能当成型号规格或实测结果。

因此，X只用于发现线索。进入能力档的参数来自对应官方文档；作者个人的速度、额度和画质反馈未转写为产品保证。未读到的正文没有被补写成引文。

| X 线索地址 | 本轮能确认的访问情况 | 工程依据 |
|---|---|---|
| https://x.com/dreamina_ai/status/2083056471147958714 | 再次直读403，不能引用正文 | Seedance官方发布页及LAS文档 [S1][S2] |
| https://x.com/GoogleAIStudio/status/2093020771245346930 | 再次直读403，不能引用正文 | Google Flow官方8月27日更新 [S4] |
| https://x.com/joshesye/status/2086451514634113470 | 仅有间接线索，直读未取得正文 | 个人额度节省数字未采用 |
| https://x.com/levelsio/status/2086224231277216132 | 未取得可核验正文 | 个人生成速度不作为性能预算 |
| https://x.com/runwayml/status/1978540989925949532 | 索引显示2025年10月15日旧帖 | 不当作本周更新，不据此扩充新型号能力 |
| https://x.com/umesh_ai/status/2041561378495082582 | 索引指向2026年4月的Seedance 2.0讨论 | 不混同Seedance 2.5或Runway原生模型 |

这些是访问情况记录，不是对帖子作者身份、所有内容或平台覆盖率的认证。未用截图OCR、未代用户登录，也未绕过访问限制。

## 对项目有价值的消息与处理

### 1. Seedance 2.5：把“生成”延伸为“可描述的修改”

官方7月31日发布材料介绍了带时间位置的编辑与延展。[S1] 本项目采用其工作流启发，而不伪装成已调用该模型：返工单独记录半开区间毫秒、前后参考长度、保留项、改变项和声音要求，绑定原视频和原任务的内容哈希。

交接包保留完整原片，不提前裁剪。原因是执行平台对输入时长、上下文和编辑模式不同，通用交接层不应替平台猜这些参数。当前方案只是让修改意图和证据完整可恢复，不声称自动保持范围外画面，也没有证明节省多少费用。

### 2. 同一个模型名，不代表各入口规格相同

BytePlus LAS 文档明确列出 `dreamina-seedance-2-5-260628`，该入口当前输出4至30秒、480p或720p。[S2] Higgsfield 9月3日的恢复指南则描述其自身Seedance 2.5 Edit与增强工作流，包括更高的输出选项。[S3] 两者不能合成一套“Seedance家族通用规格”。

新增档名为 `seedance-2-5-las`，共10个档，旧9档内容哈希逐项保持。官方材料中视频输入时长小节存在疑似复制错误，没有据此填入猜测的2.5总时长上限；白名单、输入像素/帧率、账号和请求总大小等未完整自动校验，档内明确提醒提交前复核。它不是 execution provider。

### 3. Higgsfield：局部修改与画面增强应当分工

9月3日官方指南区分针对性清理、重新着色/布光和画质增强，也提醒重建细节不等同于恢复原始事实。[S3] 本项目采用“先写清修改目标，再检查返回”的分工，不复制一键修复后的营销保证。原片、修改意图和返回候选应分别保留。

### 4. Google Flow：首尾控制与低成本草稿已是明确方向

Google 8月27日更新介绍首尾帧、360p草稿及后续高分辨率工作流。[S4] R10已包含相关作者档及草稿晋升机制，本轮没有重复添加同名入口，也没有把上采样与原生生成混为一谈。返工协议沿用原有人工决定边界，不把恢复或导入变成新批准。

### 5. Luma Ray3.2：控制粒度有价值，规格冲突不能猜

官方发布材料与学习中心索引对关键帧上限出现16与64两种表述。[S5][S6] 学习中心直读失败，只取得索引内容，不能当作已完整读过的规范。本轮没有创建声称确定关键帧数的Ray3.2执行档；采用的是“修改需绑定原片与时间位置”的通用交接思路。后续型号适配必须先厘清具体入口与文档版本。

### 6. Runway：标称720p不能统一按短边720判定

Gen-4.5官方尺寸表中的21:9为1584×672。[S7] R10通用最低短边检查会将这一组合提示为不匹配。R11新增显式像素宽高检查，并提供需明确点击的Gen-4.5画幅参考按钮。

默认v1报告不改写，旧Request/v1哈希不变。指定宽高后生成独立的v2报告。这样既可正确检查明确的像素目标，又不把“用户选了这个尺寸”冒充视频来自某厂商。真实1584×672合成视频的浏览器与完整帧解码测试已执行，不是只测表格。

### 7. LTX：本地模型值得关注，但“更新”不等于功能超集

核验日的官方桌面仓库区分2.5 Fast本地生成、2.3 Fast本地Retake/Extend和Pro云端路径；硬件不足时它可能转为API模式。[S8] 模型卡另有组件与运行格式要求，不能把ComfyUI组件直接当成通用PyTorch安装包。[S9]

本轮没有下载权重、接受许可、安装新桌面应用或宣称本地推理通过。也没有自动复制其云端回退或提示词增强。优先实现可交给不同工具的原片返工单，保留未来真实本地执行适配的空间。GPU、许可、文本编码路径和用户机器资源要在实际接入时逐项验证。

### 8. Sora：退役窗口内不新增依赖

OpenAI退役页列出Videos API和Sora 2相关型号于2026年9月24日关闭，核验日尚未到期。[S10] 本轮不增加新Sora执行集成。旧工程和素材保持供应商独立，避免把即将退出的接口写成产品基础。

## 已落地与没有落地的界线

已落地的是本地代码：返工区域、独立计划、包含实际原片的封闭ZIP、只读CLI核验、跨浏览器/Python的协议校验、重新打开播放，以及精确像素检查和新作者档。用户仍需在外部工具执行视频修改并进行人工审片。

没有落地的是模型画质排名、实际费用节省、远程取消/计费确认、AI局部修复、帧精确自动拼接、本地LTX推理或任何新商业账号绑定。返工ZIP的哈希证明内容一致，不证明身份、版权、模型输出质量或范围外画面未改变。

原工作现场/v1保持兼容，因此新增返工区域须单独保存返工ZIP；它不会悄悄挤进旧工作现场格式。明确像素目标是临时检查设置，不修改请求，也不会随旧工作现场备份。这些边界同时写在页面和使用说明中。

## 官方来源索引

全部于2026-09-08核验。日期表示页面明确事件日或更新时间；未标明日期不猜测发布日。

[S1] ByteDance Seed，Seedance 2.5发布，2026-07-31：
https://seed.bytedance.com/en/blog/one-take-creation-flexible-referencing-introducing-seedance-2-5

[S2] BytePlus LAS，Video Generation Enhanced，页面更新时间2026-08-17：
https://docs.byteplus.com/en/docs/byteplus_las/video_gen_enhanced

[S3] Higgsfield，AI Video Restoration指南，2026-09-03；页面另有相对更新时间，不据此推算发布日：
https://higgsfield.ai/blog/ai-video-restoration-high-quality

[S4] Google，New creative controls in Flow，2026-08-27：
https://blog.google/innovation-and-ai/models-and-research/google-labs/new-creative-controls-google-flow/

[S5] Luma，Introducing Ray3.2，官方搜索索引可读，页面直读失败：
https://lumalabs.ai/news/introducing-ray-3-2

[S6] Luma，Ray3.2 introduction and core concepts，官方索引可读但未取得完整正文：
https://lumalabs.ai/learning-center/articles/ray-3-2-introduction-and-core-concepts

[S7] Runway，Creating with Gen-4.5，当前帮助文档，未猜测新发布日期：
https://help.runwayml.com/hc/en-us/articles/46974685288467-Creating-with-Gen-4-5

[S8] Lightricks，LTX-Desktop官方仓库，当前README：
https://github.com/Lightricks/LTX-Desktop

[S9] Lightricks，LTX-2.5官方模型卡，当前页面，不以搜索抓取日当作模型首发日：
https://huggingface.co/Lightricks/LTX-2.5

[S10] OpenAI，API Deprecations，Sora/Video API退役条目：
https://developers.openai.com/api/docs/deprecations

[S11] X，Advanced Search说明，日期、人和关键词筛选的官方说明；未登录时不声称已使用完整高级搜索：
https://help.x.com/en/using-x/x-advanced-search
