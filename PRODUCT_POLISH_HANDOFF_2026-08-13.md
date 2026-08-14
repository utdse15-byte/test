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

## 9. Wave 4：连续成片旅程

功能提交：`2bc0df1 R1: connect the finishing journey`；
`2097fa0 R1: finish Chinese-first edit labels`。

功能范围：剪辑、字幕、混音、包装、导出五个既有页面的产品统一。

已完成：

- 新增共享成片流程条，五页始终可见当前阶段、上一步与下一步；
- 成片 freshness 和派生 Picture Lock eligibility 由现有 owner 懒读取一次；
- 文案使用“可进入锁片评审”，没有把资格冒充成已经锁片；
- `/edit` 首屏继续零 finishing 计算、零重新编译、零子进程；
- 导出页回到共享项目 / 新手专业模式 chrome；
- 剪辑、字幕、混音、包装改为中文优先，英文由现有专业术语开关控制；
- 窄窗口以 3+2 网格完整显示五个阶段，页面本身不横向溢出；
- status endpoint 已证明不写项目。

证据：

- `REPORTS/product-polish-r1/WAVE4_FINISHING_JOURNEY.md`
- `REPORTS/product-polish-r1/screenshots/wave4/`
- `REPORTS/product-polish-r1/wave4-tests/`

逐组相关测试共 172 passed：10 + 6 + 19 + 19 + 5 + 34 + 17 + 1 + 38 + 23。
四项明确未运行的 GUI FFmpeg 像素测试和未在时限内完成的其余 edit-v3 慢测试
均不冒充通过。

后续优先级：先打磨窄窗口下的全局应用导航与新手提示密度，再把外部精剪返回状态
接入成片阶段；继续保持只读、append-only、人工采用，不扩展新 AI 功能。阶段收口时
再运行同 SHA Windows / Ubuntu / pinned FFmpeg hard gate。

## 10. Wave 5：统一应用外壳与稳定制作阶段

功能提交：`91b6e1b R1: unify the application chrome`。

功能范围：共享应用栏、六阶段导航、当前阶段子导航、项目身份与执行模式的长期可见性。

已完成：

- 所有主要页面统一使用同一个 `chrome()` owner；修复导演、分镜、镜头实验室和批量入库
  过去绕过共享新手/专业模式、术语和项目切换外壳的问题；
- 顶部导航从 hover-only 下拉和多入口 pill wall 收敛为两层结构：应用栏 + 六阶段栏；
- 当前阶段才显示页级子导航；成片阶段沿用页面内的五步旅程，不重复一套链接；
- 当前执行模式在所有页面长期显示，本轮截图与验证均使用 `strict_zero_cost`，显示“本地安全”；
- 当前项目名称进入项目切换入口，新手/专业与术语设置进入渐进展开菜单；
- 新手提示只在首页首次出现，不再跨页面占据空间；
- 直接页面标题改为中文优先，英文只随现有专业术语开关出现；
- 390px 窄窗口以 3×2 网格完整显示六阶段，实测全局页面无横向溢出。

证据：

- `REPORTS/product-polish-r1/WAVE5_APPLICATION_CHROME.md`
- `REPORTS/product-polish-r1/screenshots/wave5/`
- `REPORTS/product-polish-r1/wave5-tests/`

相关 focused tests 共 177 passed；compileall、九份实际生成 JavaScript 的 `node --check`、
`git diff --check` 均通过。当前环境未安装 ruff，没有冒充已运行；也未运行 Windows hard gate、
完整 pytest 或真实 localhost Playwright journey。

后续优先级：以真实审片高频旅程为核心，继续收敛 Review Theater 的桌面双栏、窄窗口队列、
主操作和技术详情层级；不新增模型、自动选择、自动锁片或真实付费测试。


## 11. Wave 6：Review Theater

功能提交：`17ba7a7 R1: focus the review theater`；范围是 `/review` 的高频审片旅程，不改变选择、审批和锁片 owner。

已完成：

- 单条审片收敛为“当前候选 + 优先队列”的 Review Theater；桌面为稳定双栏，窄窗口队列横排；
- 队列按待选、待更新、当前 blocker/QC error、当前候选未评价、已评价待审批、无候选、已通过排序；
- 进度只统计已有当前选择的候选，未选择镜头单列；旧候选备注不会冒充新选择已评价；
- 页面明确分开当前评价、`selected_take`、镜头审批和 Picture Lock；推荐 / 不推荐都不会自动换选或锁片；
- 一个主要判断区置于播放器之前；QC 抽帧、路由、修复和 IDE 交接进入渐进展开区；
- “稍后处理”按优先队列前进；清除评价随当前事实显隐；IDE 交接分别报告当前评价与镜头审批；
- 390px 和 200% text 的静态真实 renderer 证据均无页面横向溢出；当前 rail item 会自动进入视野；
- 保留单主视频 `src`、其它候选 lazy 和一致性 board 展开后才生成的性能边界。

证据：

- `REPORTS/product-polish-r1/WAVE6_REVIEW_THEATER.md`
- `REPORTS/product-polish-r1/screenshots/wave6/`
- `REPORTS/product-polish-r1/wave6-tests/`

focused tests 共 262 passed；compileall、两份实际生成 JavaScript 的 `node --check`、
`git diff --check` 通过。当前环境未安装 ruff；没有运行完整 pytest、Windows hard gate、
同一 SHA 双平台门或 live localhost Playwright journey。

后续优先级：统一“分镜 → 镜头实验室 → 批量入库 → 审片”的镜头制作旅程；继续不扩模型、
不自动选择、不自动 Picture Lock、不做真实付费测试。
