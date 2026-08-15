# Manju One 当前交接

> 这是当前唯一的活动交接入口。根目录中其它 `*HANDOFF*` 文档属于历史阶段参考；其中的分支、SHA、测试数和待办不得覆盖本文件、`STATE.md`、实际 Git 或交付包 manifest。

## 产品状态

Manju One 已完成 Product Polish R1 的主要产品化工作：

- Windows 开始菜单点击即用、无控制台启动、已有会话复用、日志、更新、回滚和 fail-closed 卸载；
- 工作区、六个制作阶段和统一应用外壳；
- 创作 → 可选提案 → 分镜；
- 分镜 → 镜头实验室 / 批量入库 → 人工审片；
- 剪辑 → 字幕 → 混音 → 包装 → 导出；
- 一个优先动作的首页 Cockpit；
- 全局任务中心与安全退出；
- Review Theater、人工选片门和 exact-media-bound 证据；
- 中文主界面、200%–400% 重排、键盘与无障碍契约；
- `Ctrl/Cmd+K` 只导航的快速前往；
- `F1` 全局帮助与支持、隐私说明、日志与显式诊断入口；
- 12 / 100 / 300 镜本地性能工具、核心视觉验收和零成本本地 RC 编排器。

当前最佳代码位于实际 checkout 的 `codex/product-polish-r1` 线性提交链。交付 ZIP 中的 `PACKAGE_MANIFEST.txt`、`repository.bundle` 和 `SHA256SUMS.txt` 是恢复某次交付的准确依据；不要在 tracked 文档中猜最终自引用 SHA。

## 2026-08-15 完整包接管结果

本轮不是覆盖旧仓库，而是先验证用户提供的完整 ZIP，再从其中恢复独立仓库。包内 Git HEAD 是旧正式仓库的线性后继；所有 project 文件、Git tree、bundle 和包内 SHA-256 均已逐项核对。接管后补齐了 Windows 回归、真实大项目读路径性能和离线 wheel 构建报告。

最终本机证据：

- 全仓 pytest：`6357 passed, 71 skipped`，退出码 0；Git for Windows 自带的 `sh` / `grep` 已加入测试 PATH，上一轮 10 个环境失败不再遗留；
- Python 3.14 性能中位数（12 / 100 / 300 镜 cockpit）：`43 / 251 / 724 ms`；
- Python 3.11 性能中位数：`52 / 308 / 887 ms`；
- 原性能门保持 `150 / 600 / 1400 ms`，没有放宽；
- wheel 使用机器上已经存在的离线 Python 构建后端生成，没有访问 package index；
- 本轮 Provider、凭据、免费额度、付费请求和模型下载均为 0。

具体交付身份和最终 RC 结果以交付包顶层的 `PACKAGE_MANIFEST.txt`、`FINAL_VALIDATION.md` 和 `LOCAL_RC_EVIDENCE/release-candidate.json` 为准。

## 现在的执行策略

当前开发与验证保持严格零成本：

```text
MANJU_EXECUTION_MODE=strict_zero_cost
```

这只是“现在不花钱”的操作状态，不是永久删除云端能力。未来付费路径仍保留 Provider、预算、人工确认、submission identity、poll/download 和恢复机制；第一次真实付费前仍需要单镜、单候选、硬上限的独立 Dogfood。

## 日常入口

```text
Windows：开始菜单 → Manju 工作台
CLI：manju gui --app --port 0
帮助：F1
快速前往：Ctrl/Cmd+K
环境检查：manju doctor --windows
脱敏支持包：manju support-bundle
```

## 本地发布候选门

本地、零成本、不会更新 `LAST_GREEN`：

```bash
python scripts/dev/product_release_candidate.py \
  --output REPORTS/product-polish-r1/local-rc
```

它组合 focused tests、严格零成本边界、性能、核心视觉、快速前往、帮助中心、Windows App self-test 和离线 wheel smoke，并为每个阶段保存独立日志。它不是完整发布认证。

如果当前 Python 没有 `setuptools.build_meta` / `wheel`，RC 会明确报告构建后端 unavailable，不会联网安装。可以显式指向机器上已经存在的离线构建解释器：

```bash
python scripts/dev/product_release_candidate.py \
  --output REPORTS/product-polish-r1/local-rc \
  --build-python <offline-python>
```

## 一个压缩包是否足够

足够，但前提不是“把源码目录压一下”，而是同时包括：

1. `project/`：最终提交的干净 tracked tree，可直接阅读和使用；
2. `repository.bundle`：完整 Git 历史和分支，可独立恢复；
3. `LOCAL_RC_EVIDENCE/` 与 wheel：最终 HEAD 的本地验收、截图、日志和安装产物；
4. `PACKAGE_MANIFEST.txt`、`SHA256SUMS.txt` 和恢复验证记录：证明没有漏文件或混入旧产物；
5. 本交接、最终验证说明和参考指南。

最终 ZIP 已按这个定义制作，并在另一个临时目录实际解压、逐项校验、从 bundle 克隆和比对 tree。只保留这个 ZIP 就能完整交接本次代码、历史和本地证据；用户自己的 `*.manju` 项目媒体仍是独立数据，不会也不应被塞进产品源码包。

## 仍需真实平台完成

以下事项不得由 Linux 沙箱、离线 renderer 或 focused tests 冒充：

1. 在真实 Windows 11 上从开始菜单完成安装、双击复用、关闭窗口后重开、安全退出、更新、回滚和运行中卸载拒绝。
2. 在同一个最终 SHA 上完成 Ubuntu full suite 与 Windows hard gate，并更新 `REPORTS/LAST_GREEN.yaml`。
3. 未来有预算后，完成一条真实 Provider、真实媒体、真实费用的受控 Proof Shot。
4. 核对真实 Provider 远端取消和计费语义；本地取消永远不证明远端已停止。

## 不要继续扩张的方向

在上述发布门完成前，不要：

- 增加新的产品旅程或 AI 模型；
- 自动选片、自动镜头审批或自动 Picture Lock；
- 把 LLM 放进 Manju 引擎；
- 恢复 Manju MCP；
- 引入 React、Electron 或远程 UI 资源；
- 把 reports、GUI state、浏览器索引或 SQLite 变成 build input；
- 在没有行为、浏览器和双平台基线时拆分大型核心模块。

## 权威顺序

发生冲突时按以下顺序判断：

1. 当前代码与自动测试；
2. `CONTRACTS.yaml`、`DECISIONS.md` 和 `STATE.md`；
3. 当前交付包 manifest / Git bundle；
4. `FINAL_HANDOFF.md`；
5. 历史 handoff、计划和研究文档。
