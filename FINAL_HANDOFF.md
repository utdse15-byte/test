# Manju One 当前交接 — Research / Dogfood R2

> 先读最新 `PROJECT_STATE_2026-09-06_09-44-32.md`，再读其任务索引。本文是当前产品交接入口；历史 handoff、测试数和旧分支不能覆盖当前 Git、合同和交付 manifest。

## 当前交付

原包 `ee1e524` 被完整验证后恢复为独立仓库，没有覆盖原文件。本轮交付是研究/修复候选，**不是已经完成 Windows 全部发布门的正式版**。
活动分支为 `improvement/research-dogfood-r2`；准确交付 SHA、tree、运行时代码对应关系以顶层 `PACKAGE_MANIFEST.json` 为准。

已修复：空项目无法新增第一条音效、第一张信息卡；旧创作窗口覆盖外部新稿；保存期间继续写作被误标为已保存或延迟刷新丢失；中文/超长素材文件名丢失扩展名。
新增 `ASSET_MAP.md`，明确首尾帧、真实上传文件名与多主体控制关系，且绑定不可变制作包身份。老 R1/R2 包仍按原契约校验。
未增加新模型后台、自动选片/审批、内部 LLM 或新平台依赖。

## 已有证据

最终可用范围 6366 通过 / 15 失败 / 22 跳过；相关回归 174 通过，两者重叠。
另有 24 个页面/视口与真实服务的组合验收、两秒素材回收、六秒三镜头制作/重渲染/导出、4 个真正历史代码创建的旧包兼容，以及最终 wheel 独立安装验证。
完整口径和失败归因见 `REPORTS/research-dogfood-r2/VALIDATION_2026-09-06.md`；研究见同目录 `RESEARCH_2026-09-06.md`。

## 使用与恢复

先把本包保存在独立目录；不要把 `project/` 覆盖进正在使用的旧目录，不要覆盖自己的 `*.manju` 影片工程。
本包保存完整源码、Git 历史、wheel 与证据，不包含用户影片媒体，也不是带齐依赖的一键离线安装器。
Windows 原有版本化安装、更新和回滚脚本保持原样；本轮没有在真实 Windows 上执行更新。
包顶层 `START_HERE.md` 说明入口与恢复方法；`repository.bundle` 可单独克隆完整历史。

日常入口仍为开始菜单 Manju 工作台，或 `manju gui --app --port 0`；帮助 F1，快速前往 Ctrl/Cmd+K。
当前操作保持 `MANJU_EXECUTION_MODE=strict_zero_cost`，不代表永久删除未来的付费能力。

## 仍开放的发布门

本地生成取消响应未完全闭环，不能把所有失败都归咎于沙箱。
真实 Windows 11 生命周期、Windows 子进程取消、固定 FFmpeg 6.1.1 的同提交 Ubuntu/Windows 全范围门仍需实际证据。
真实生成模型、真实素材质量、费用及远端取消没有验证。`REPORTS/LAST_GREEN.yaml` 仍是历史实测 `f076aee`，没有冒领新绿灯。

旧状态/交接全文保存在 `REPORTS/research-dogfood-r2/BASE_STATE_2026-08-15.md`、`BASE_HANDOFF_2026-08-15.md`，原 ZIP 与全部 Git 历史也原样保留。
