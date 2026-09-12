# R16 全项目导入导出矩阵

下列既有通道范围继承R15源码审计；R16新增统一入口与收工包，不将原功能重命名成新开发。

“可导出”“可读入”“可按原义带回”是三个不同承诺。下表仅归纳当前源码与本轮所列验证，不补写不存在的通用导入器；不是第三方软件最新版认证。

| 部分 | 导出/对外材料 | 导入/回程 | 不能混称的能力 | 源码依据 |
|---|---|---|---|---|
| 日常收工现场 | Desk/v1：原STUDIO.zip + 已加载未应用改稿/模板 + 模板草稿 | 完整核验、预览、确认后恢复；改稿和模板仍待处理 | 不含主影片工程/未应用目录或组合预览/派生报告/撤回；旧版只打开内层STUDIO.zip | `authoring/desk.py;workbench.desk.js` |
| 任意本地材料入口 | 不新增转换格式，沿用各自出口 | 根据内容选择原入口；媒体明确用途后再检查 | 识别不是批准，不支持的工程不假装还原；网络链接不访问 | `workbench.desk.js:identifyIntake` |
| 主影片完整工程 | `manju pack --out film.manjupkg`；`--full`包括可重建缓存，另有BagIt形式 | `manju unpack ... --dest 新目录`；验证包，拒绝覆盖目录 | 默认不包可重建缓存；链接目录/符号链接不追随，范围外文件、软件依赖与外部账号不归档 | `src/manju/cli.py:pack,unpack` |
| 离线三个工作区 | Studio/v1 ZIP，包含当前绑定实物、原始未完成草稿与历史 | 核验、预览、确认后恢复；R14可选择局部区域组合 | 不是主影片工程；未绑定文件、未应用预览和临时撤回不包含 | `authoring/studio.py,flexibility.py` |
| 镜头/返工/导演的外部文字 | R15 EDIT.json、带材料编号的TXT、可附原媒体ZIP | R15逐字段三方取回；冲突显式选择，上下文不符拒绝 | 不是任意JSON深合并，不自动写回主影片工程 | `authoring/exchange.py` |
| 独立工作现场 | Workspace/v1 ZIP/任务JSON | 原第04/05区恢复、任务导入 | 只给任务JSON不等于连实际视频一起备份 | `authoring/workspace.py,core.py;workbench.js` |
| 返工材料 | 完整原片、范围、上下文与要求的Repair ZIP | 原第06区独立恢复；文字可走R15 | 不自动裁片/AI修复/拼回，不保证区间外画面绝对不变 | `authoring/repair.py;workbench.repair.js` |
| 导演材料 | 原运动视频、原帧、目标PNG、锚点意图的Director ZIP | 原第07区恢复，目标图按锚点显式绑定；目标文字可走R15 | 浏览器原帧为显示参考，不是HDR/EXR母版；目标图限协议支持的PNG和画幅 | `authoring/director.py;workbench.director.js` |
| 外部图像/视频/音频 | R15按用途导出原字节与原名映射；主项目media文件仍独立保留 | 工作台素材按用途绑定，候选显式添加；主影片有`manju import` | 文件名相同不代表同一内容，不静默替换旧候选；不是所有编码都能浏览器播放 | `authoring/core.py;workbench.js;cli.py:import` |
| 候选和审片 | review JSON/工作现场/总备份 | 原审片协议验证既有决定链；新结果先加候选再人工审片 | R15只导回未提交说明，不导入新的批准/评分/锁片；技术通过不是人工批准 | `authoring/review.py;workbench.review.js` |
| 模型能力档 | 独立Catalog JSON及差异报告 | 预览当前任务影响，明确确认应用或回退 | 用户文本不是供应商能力证据；新档不自动进入原质量名单 | `authoring/catalog.py;core.py;workbench.catalog.js` |
| 质量证据 | 质量名单与证据输出 | 原有验证路径，不在R15改稿中编辑质量授权 | 本轮没有新增质量证据、没有更新排名或自动降档 | `authoring/quality.py` |
| 个人文字方法 | CreativeTemplate/v1 JSON | 只填空白或明确替换选中字段 | 不复制身份、实际媒体或批准；用户写的隐私不会自动脱敏 | `authoring/flexibility.py;workbench.flexibility.js` |
| 跨项目角色参考 | 角色参考包：角色/用途、控制/忽略项、来源及哈希 | copy-on-import，导入后不依赖外部原文件；保留来源 | 不是未经确认复制声音权利或未知角色语义 | `build/seriespack.py:build_reference_pack,import_reference_pack` |
| 剧集与素材模板 | EpisodeOutlinePackage；显式选择的style/bible、规则、交付档、reference、voice引用模板包 | 先inspect，显式apply；冲突rename/skip/abort；声音资料有现有权利检查 | 不猜剧集划分；没有模板市场；不是任意外部故事工程转换器 | `build/seriespack.py:inspect_outline,apply_outline,export_template_pack,import_template_pack` |
| 故事/角色/分镜真相 | 主工程文本文件、全工程包；镜头pullsheet可导出CSV/Markdown | 既有主应用/文本真相路径与结构验证；project→workbench镜头桥只读 | R15的EDIT.json并未覆盖主影片所有YAML/故事文件；没有任意Word/PDF故事回写 | `core/container.py;build/pullsheet.py;authoring/project_bridge.py` |
| 字幕 | SRT/ASS/VTT；TTML旁车 | 有人工SRT真相、`transcribe --from-srt`及对齐路径，受限回程可传字幕文本 | 未找到通用ASS/VTT/TTML的完整样式反向恢复器；不能说所有字幕格式全双向 | `exporters/srt_ass.py,ttml.py;providers/asr.py;cli.py:transcribe,align` |
| 时间线到外部剪辑 | OTIO、FCPXML、CMX3600 EDL、xmeml，另有可选剪映/CapCut路径及conform-loss报告 | 自有导出且基线保留的OTIO/FCPXML/剪映差异骨架有受限回程，先计划再显式apply | 本轮未在外部NLE应用测试；任意效果、样式、复合轨、原生/加密草稿不保证无损 | `exporters/;build/roundtrip.py;cli.py:export,roundtrip` |
| 任意外来FCPXML/EDL/OpenClap | 原导出器可产出相应格式或旁车 | `fcpxml/edl/openclap import-plan`只读分析建议、冲突和待重连媒体 | **只生成计划，不直接创建或还原完整工程，不下载远端素材，不自动落轨** | `exporters/fcpxml_import.py,edl_import.py,openclap.py;cli.py` |
| 诊断/哈希/谱系与验证报告 | JSON、Markdown等只读证据 | 只在指定验证器中核验 | 不能当生产命令、质量证明、版权证明或自动付款授权 | 各模块verify/report函数 |
| 插件、账号、远程服务 | 不随R15外部编辑材料导出 | 无任意脚本/凭据/远程URL执行入口 | 没有商业生成API集成、无自动同步，不把授权用户的信任变成程序执行 | R15协议常量与UI |

## 既有验证层次（R15历史记录，不算作R16新执行）

R15新增通道：75项测试；源码版和独立安装版真实UTF-8文本、视频、目标PNG往返。原Studio格式：新结果由未改动的R14 Python验证器和HTML实际恢复，视频播放、重新导出字节一致。

主工程：本轮使用合成六秒三镜头工程副本，实际导出CSV、Markdown、SRT、ASS、VTT、TTML、OTIO、EDL、FCPXML、xmeml文件；脚本修改自有FCPXML后经既有回程显式应用修剪。再`pack --full`、`unpack`比对69个恢复文件。没有操作真实外部剪辑软件，原合成工程保持不变。

参考/模板/外来计划与字幕等范围：依据源码和列入选定回归的相关测试；不能推导为所有第三方文件均兼容。未执行完整7,082节点回归；详细范围见R15_VALIDATION.md。

## 你最常用的路线

外部模型或编辑器改提示词/导演要求：第09区导出外部ZIP → 解压、修改EDIT.json的values或带编号TXT → 第09区加载JSON/TXT → 看三方比较并选字段 → 应用 → 首页另存总备份。

外部模型返回图片/视频/音频：先按原用途入口绑定为新素材或新候选，检查规格再审片。不要篡改备份内的媒体哈希冒充旧文件；R15文字交换不替你换片。

外部剪辑软件：优先从本项目导出受支持格式并保留`.baseline`，修改后走`roundtrip`查看可回写项目。任意外来文件先走对应`import-plan`，查看不映射项；不要把报告当成已经导入成功。

跨机器继续：主影片用完整工程包；离线工作台用总备份；外部未应用改稿、模板、原外部软件工程各自另存。三者不能用同一个“已导出”提示互相代替。

## R16本轮验证与最常用路线

本轮范围读R16_VALIDATION.md。实际新旧版本互通验证用原R15页面及Python读收工包中的STUDIO.zip，已加载外部改稿/模板由R16收工包原样保留，不让旧格式误吞未知字段。

继续工作或接回材料先到首页“打开文件 / 拖入材料”；收工保存收工包并选回文件核验。对外只送文字时仍导出EDIT.json/模板；整影片迁移仍用pack/unpack。未应用改稿要单独分享时原入口仍在。
