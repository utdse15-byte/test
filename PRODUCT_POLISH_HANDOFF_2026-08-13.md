# Manju One 产品打磨交接

更新时间：2026-08-13
仓库：`G:\XXN\test`
工作分支：`codex/product-polish-r1`

## 1. 最终目标

Manju One 要成为一个人长期使用的本地电影工作台：打开后安静、清楚、好看，
离开几天再回来仍接得上，高频操作短而顺，错误不丢工作，自动化不替用户做创作
决定。参考文档是判断材料，不是机械任务清单。

不变边界：

- 严格零成本，不调用云 Provider、免费额度 API、外部搜索或云 OCR/ASR/VLM。
- 文本项目文件是唯一真相，媒体只追加。
- 不自动写 `selected_take`，不自动 Picture Lock。
- GUI 是共享核心的薄客户端，不做 React/Electron 重写。
- 不恢复 Manju MCP，不读取或配置 Provider Key。

## 2. 本轮已经完成

### Wave 1：工作台与执行信任

提交：`47c69c3 R1: polish the home cockpit and expose execution policy`

- 首页移除重复新手引导和空状态噪声。
- 空项目隐藏无意义的交付物、队列、评估区块。
- 当前执行模式长期可见：严格零成本 / 标准执行 / 无效配置。
- 拼错 `MANJU_EXECUTION_MODE` 会在 Provider transport、adapter 和凭据读取前失败。
- 首屏更早出现真正的构建操作。

证据：

- `REPORTS/product-polish-r1/REPORT.md`
- `REPORTS/product-polish-r1/DECISIONS.md`
- `REPORTS/product-polish-r1/screenshots/`

### Wave 2：人工选片门与 Review Theater

提交：`27900c6 R1: require human selection and streamline review`

- `build` 生成候选后不再自动选择；`proxy/final/exports/qc` 在编译前返回
  结构化 `selection_required`，并明确提示审片与 `manju select`。
- `redo` 只生成新 take，不写 `selected_take`。
- 批量 ingest 只落盘候选，不因空镜头自动选片。
- Review 主视频全部以 `data-src` 输出，前端只激活当前卡片并卸载上一条。
- “好 / 弃 / 通过 / 重做 / 跳过”等判断动作移动到播放器之前并保持 sticky。
- 跨镜一致性收进专业折叠区，只有用户展开后才请求并生成看板。
- 保留队列排序、`j/k/g/x/a/u/space`、位置恢复、CAS 写入和懒加载备选 take。

## 3. 当前验证

已通过：

```powershell
python -m pytest -q tests/test_manual_selection_gate.py tests/test_ask_before.py tests/test_buildlock_wiring.py tests/test_hash_versions.py tests/test_ingest.py tests/test_ingest_batches.py
# 145 passed

python -m pytest -q tests/test_fp_review_lazy.py tests/test_review_queue.py tests/test_gui_pages.py
# 45 passed

$env:PYTHONIOENCODING='utf-8'
python -c "from manju.gui.pages import render_pages_js; print(render_pages_js())" | node --check -
python -m py_compile src/manju/build/graph.py src/manju/build/ingest.py src/manju/gui/pages.py src/manju/cli.py
git diff --check
```

当前交付全量验证：

```powershell
python -m pytest -q -n auto
# 6185 passed, 63 skipped, 10 failed in 292.07s
```

10 项失败已逐项核对，全部来自当前 Windows 环境缺少 `grep` / `sh`：

- 3 项源码边界测试直接调用 `grep`；
- 6 项 `tests/test_local_cmd.py` fixture 调用 `sh`；
- 1 项 `tests/test_refs.py` fixture 调用 `sh`。

它们不是本轮产品逻辑回归。完整输出位于
`REPORTS/product-polish-r1/full-suite-wave2-final.txt`。

## 4. 仍需后续确认

- Review Theater 修改后的 1280x720 和 390x844 真实浏览器截图尚未补齐；现有
  `wave2/review-before.png` 只是修改前基线。接手后应验证首屏动作可见、任一时刻
  只有一个主视频带 `src`、展开一致性前请求数为零。
- `build/ingest.py` 和 `build/batches.py` 的少量历史注释仍提到旧的
  auto-stage 概念。运行时已不再产生 staged selection；批次层保留对旧批次记录的撤销
  兼容，不应粗暴删除。
- 目标是持续产品打磨，不应把本交接误读成“整个产品已经终局完成”。优先继续审计
  成片、导出和真实恢复旅程，而不是扩展新 AI 功能。

## 5. ZIP 的完整性边界

一个 ZIP 可以完整包含这次交付所需的内容：

- 当前工作树源码、测试、文档、报告和截图；
- 本交接文档；
- `repository.bundle`：完整 Git refs 与历史，可在另一台机器恢复仓库；
- `PACKAGE_MANIFEST.txt` 与包内文件的 SHA-256 校验清单。

ZIP 不包含、也不应该包含：

- Python、FFmpeg、Node.js、浏览器等机器级运行时；
- 用户主目录缓存、`.pytest_cache`、`__pycache__`、临时 demo；
- API Key、登录状态或任何凭据；
- 远端 GitHub 的 PR/CI 状态。

恢复源码与历史：

```powershell
git clone repository.bundle manju-one
Set-Location manju-one
git switch codex/product-polish-r1
```

若只需要查看当前文件，也可直接使用 ZIP 中的 `project/` 目录。
压缩包自身的 SHA-256 应通过交付消息或外部发布记录提供，不能可靠地把 ZIP 自身
哈希写进它自己内部。

## 6. 不要误做

- 不要为了让旧测试变绿恢复 build/redo/ingest 自动选片。
- 不要把 KEEP、评分或模型推荐当作 `selected_take`。
- 不要把派生报告、GUI state 或 SQLite 读回构建输入。
- 不要因页面文件较大先做框架重写。
- 不要用窄测试宣布整个产品已经完成。

## 7. 历史交接材料

根目录的 `IDE_HANDOFF.md` 与 `HANDOFF_BUDGET_SEMANTICS.md` 是此前阶段的输入材料，
为保留上下文而原样收进交付。它们记录的分支、HEAD、未提交状态和待办只对当时
有效；继续工作时以本文件、当前 Git 历史和实际测试结果为准。

## 8. Wave 3：成片与外部精剪信任闭环

功能提交：`f5566e8 R1: make finishing round trips explicit and recoverable`

已完成：

- OTIO、FCPXML、JianYing skeleton 的 carrier 与回程 baseline 分开报告；baseline 写失败不再静默。
- 无 baseline 的 roundtrip plan 标记 `appliable=false` / `rows_reliable=false`；核心 apply 自己拒绝。
- FCPXML baseline 查找不再误借同名 OTIO baseline。
- 导出中心新增“继续精剪”，显示“可安全回收 / 仅可单向使用”，支持 GUI 生成 FCPXML。
- 文档中的 `undefined` 残渣已清理。
- 175 个相关测试逐文件报告通过；compile/import/exports.js syntax/diff checks 通过。

证据：

- `REPORTS/product-polish-r1/WAVE3_FINISHING_TRUST.md`
- `REPORTS/product-polish-r1/screenshots/wave3/`

环境边界：当前沙箱 Chromium 被管理员策略禁止访问 localhost，所以没有声称 live-server Playwright 已通过；离线真实 HTML/CSS 截图已完成。下一位在 Windows App 模式补一次真实点击与 toast 确认即可。

后续优先级：继续统一“剪辑 → 字幕 → 混音 → 包装 → 导出”的成片旅程，不扩展新 AI 功能。
