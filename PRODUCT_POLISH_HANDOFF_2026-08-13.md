# Manju One 产品打磨交接

更新时间：2026-08-14
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

## 12. Wave 7：镜头生产旅程

功能提交：`703a386 R1: connect the shot production journey`。

已完成：

- `/storyboard`、`/lab`、`/ingest` 共用分支式镜头制作路径：规划镜头 → 实验室或批量入库获得候选 → 人工审片；
- 流程只读派生当前 shot/take/review facts，不保存第二份进度，不自动选择、审批或锁片；
- `?shot=` 在实验室、入库和 Review Theater 之间保持当前镜头，显式深链优先于上次浏览位置；
- 镜头实验室将参考诊断、其它提示词、路由、脚手架和高级改写渐进展开，桌面高度下降 30.5%；
- 批量入库诚实区分无批次、空批次和读取失败；确认匹配明确不等于选择候选；
- 入库完成可检查本批次，确实加入视频 take 时可直接进入对应镜头审片；
- 历史 staged selection 改为“历史选择 / 历史自动选择”，不再暗示当前入库会自动选片。

证据：

- `REPORTS/product-polish-r1/WAVE7_SHOT_PRODUCTION_JOURNEY.md`
- `REPORTS/product-polish-r1/WAVE7_LAYOUT_FACTS.json`
- `REPORTS/product-polish-r1/WAVE7_BROWSER_FACTS.json`
- `REPORTS/product-polish-r1/screenshots/wave7-before/`
- `REPORTS/product-polish-r1/screenshots/wave7/`
- `REPORTS/product-polish-r1/wave7-tests/`

focused tests 共 252 passed，80 deselected；compileall、五份实际生成 JavaScript 的
`node --check`、`git diff --check` 通过。当前环境未安装 ruff；完整 pytest、Windows hard gate、
live localhost E2E 和真实 Provider 仍未冒充完成。

后续优先级：收敛“创作 → 导演提案 → 分镜”的前半程，继续复用现有 truth_patch_set、
来源引用和人工确认，不新增 authoring plan / event truth。

## 13. Wave 8：创作、提案与分镜旅程

功能提交：`2307612 R1: connect the authoring journey`。

功能范围：`/create`、`/director`、`/storyboard` 的前半程产品收敛；不改变
canonical text、Director Proposal 或 ShotContract owner。

已完成：

- 新增只读 `authoring_journey`，统一表达“直接写项目真相 / 可选外部提案 / 建立分镜”；
- 待处理 `truth_patch_set` 会优先提醒，非创作提案不阻断尚未完成的写作；
- 创建页以四份故事材料为主表面，完整七阶段 funnel 和生产资格渐进披露；
- 未保存草稿具有切换、模板、离页和重复提交保护；
- Director 创作提案直接展示 before / after、字符级修改和有界统一 diff；
- 确认与执行仍是两个动作，过期提案不能确认或执行，本地安全模式不运行付费提案；
- 分镜页只在创作 truth 有异常时显示交接提示，健康路径没有重复 journey；
- 窄窗口无页面横向溢出，创建页在新增旅程后仍比上一版更短。

证据：

- `REPORTS/product-polish-r1/WAVE8_AUTHORING_JOURNEY.md`
- `REPORTS/product-polish-r1/WAVE8_LAYOUT_FACTS.json`
- `REPORTS/product-polish-r1/WAVE8_TEST_SUMMARY.json`
- `REPORTS/product-polish-r1/screenshots/wave8-before/`
- `REPORTS/product-polish-r1/screenshots/wave8/`
- `REPORTS/product-polish-r1/wave8-tests/`

focused tests 共 176 passed；另有真实 renderer + Playwright `page.set_content` 的前端状态机证据。
当前环境缺少 `hypothesis` 和 `ruff`，更广 fast suite 未形成完成摘要；完整 pytest、Windows hard gate、
live localhost E2E 和同 SHA 双平台发布门均未冒充完成。

后续优先级：收口首页 Cockpit，让三条已完成旅程汇聚成一个可信的“现在最值得做”，不再扩流程或新增 AI 能力。

## 14. Wave 9：首页 Cockpit

功能提交：`015b365 R1: focus the home cockpit`。

已完成：

- 首页以一个只读、确定性的“现在最值得做”作为唯一最高优先级动作；
- 创作、镜头、成片三条已有旅程在首页汇合，不保存第四套流程状态；
- 首屏只保留待我处理、正在运行和最近成果；完整工程工作台进入惰性渐进披露；
- 高级工作台关闭时不构造镜头卡，不请求时间线、提案和隐藏成本估算；
- 修复 App 模式安全退出过去依附旧 header、折叠后消失的生命周期缺陷；
- 12 / 100 / 300 镜本地 Cockpit 投影形成性能基线。

证据：

- `REPORTS/product-polish-r1/WAVE9_HOME_COCKPIT.md`
- `REPORTS/product-polish-r1/WAVE9_TEST_SUMMARY.json`
- `REPORTS/product-polish-r1/screenshots/wave9/`
- `REPORTS/product-polish-r1/wave9-tests/`

后续优先级因此转向全局任务、通知、取消和安全退出，而不是增加新页面或新 AI 能力。

## 15. Wave 10：全局任务中心、诚实取消与安全退出

功能提交：`4988541 R1: unify the task lifecycle`。

已完成：

- 所有绑定项目的 GUI 页面共用一个永久 Task Center 和安全退出入口；
- 任务中心只读投影现有 `JobRunner` / `jobkinds`，不建立队列、数据库、scheduler 或重试协议；
- `waiting_user` 被视为需要人工处理，不冒充成功；正费用显示结构化估算，非费用确认回到对应操作页面；
- 任务阶段使用人话，同时在技术详情保留 raw phase；完成任务不再重复显示“已完成 / 已完成”；
- shared `requestJson` 接受 202 后即时通知 Task Center 刷新，重叠读取去重；
- 跨页面 active→terminal 只提醒一次，系统通知权限只在用户明确设置时请求；
- 远端取消结果不明时保留在需要处理区，显示 provider job identity，不提供立即重试；
- 付费取消使用 focus-safe 警示对话框，安全默认是继续等待；
- 退出对话框统一解释当前阶段、是否可取消、远端计费不确定性和尚未执行的队列；
- 大量队列/历史通过渐进披露完整可达，重要任务不被截断；
- 390px 应用栏和任务抽屉无横向溢出。

证据：

- `REPORTS/product-polish-r1/WAVE10_TASK_CENTER.md`
- `REPORTS/product-polish-r1/WAVE10_TEST_SUMMARY.json`
- `REPORTS/product-polish-r1/screenshots/wave10/`
- `REPORTS/product-polish-r1/wave10-tests/`

记录的非重叠 focused tests 为 167 passed；compileall、五份实际生成 JavaScript 的
`node --check`、`git diff --check` 通过。当前环境未安装 ruff；完整 pytest、Windows hard gate、
live localhost E2E 和同 SHA 双平台发布门仍未冒充完成。

后续优先级：统一中文产品文案、状态/错误/下一步语言、无障碍与视觉回归基线；不再增加新流程，
不做真实付费测试，不改变项目真相、append-only 媒体和人工决定边界。

## 16. Wave 11：中文产品语言、持久反馈与高缩放无障碍

功能提交：`77eefcb R1: unify accessible language and feedback`。

已完成：

- 新增共享文档无障碍 owner；所有主要 GUI 页面统一 `lang=zh-CN`、一个“跳到主要内容”、
  一个可聚焦 `main#main-content` 和诚实的无 JavaScript 保护说明；
- 工作区选择页改成真正的首次使用产品屏，修复其主容器与旧项目切换器同名 `.ws-wrap`
  导致桌面页面被错误压窄的 CSS owner 冲突；
- 打开或创建项目失败后，输入仍保留，持久错误区接收焦点，说明发生了什么、项目保护和
  下一步，技术详情渐进展开；
- 术语 `?` 改为原生按钮，支持 hover、focus、click、Escape 和焦点恢复，目标尺寸至少 24×24；
- SPA 与 server-rendered toast 统一语义：成功/警告有界消失，错误保持到明确关闭，重复提示
  合并，每条消息拥有自己的 `status` / `alert` live-region 语义；
- 旧页面在项目切换后显示真正的 `alertdialog`，聚焦唯一安全刷新动作，Tab 不逃逸，Escape
  不解除保护；
- 浏览器 404 变为可导航的中文产品页面，并明确项目文件没有被修改；
- 320 CSS px、200% 和 400% 文本证据均无页面级横向溢出；六阶段、项目、执行模式、任务和
  安全退出仍可访问；
- 删除 series 页面重复加载 `/webclient.js` 的隐患，并用 rendered-document 测试钉住所有主要
  shell 不重复加载脚本。

证据：

- `REPORTS/product-polish-r1/WAVE11_LANGUAGE_ACCESSIBILITY.md`
- `REPORTS/product-polish-r1/WAVE11_TEST_SUMMARY.json`
- `REPORTS/product-polish-r1/screenshots/wave11/`
- `REPORTS/product-polish-r1/wave11-tests/`

记录的非重叠 focused tests 为 347 passed；compileall、六份真实生成 JavaScript 的
`node --check`、`git diff --check` 和离线 Chromium 行为证据均通过。当前环境没有 ruff；
完整 pytest、live localhost E2E、Windows hard gate 和同 SHA 双平台发布门没有冒充完成。

后续优先级：停止增加新旅程，转向可量化的性能、浏览器旅程与视觉回归收口，再进行真实
Windows App 模式 200% 文本和同 SHA 发布门。继续保持零真实 Provider、零凭据、零费用。

## 17. Wave 12：性能与视觉验收门

功能提交：`38a7615 R1: make product performance measurable and lazy`；
视觉证据稳定化：`00436af R1: stabilize visual acceptance fixtures`。

已完成：

- 导出状态改为按需编译时间线：缺少交付物时直接给出诚实的 `missing`，不会为了说明
  “尚未生成”扫描所有镜头、探测媒体并重算 final/proxy 内容键；真正存在 final/proxy
  时仍只编译一次并共享同一组内容键；
- 首页 Cockpit 在一次请求中只读取一次创作漏斗，并供建议、唯一主动作和创作旅程复用；
  picture staleness 同样只评估一次，继续由原有 owner 决定语义；
- 新增零成本 `scripts/dev/product_polish_benchmark.py`，用本地 12/100/300 镜 fixture 测量
  build-state、Cockpit、Review HTML 和 Storyboard HTML；默认只报告，发布候选可显式设置
  Cockpit 中位数预算；
- 新增 `scripts/dev/product_visual_acceptance.py`，用真实服务端 renderer 和实际 CSS 检查
  工作区、创作、分镜、审片、剪辑、导出在桌面、390px 和 400% 等效宽度下的文档与
  reflow 契约；
- Playwright 只加入 `.[dev]`，不会进入普通 Manju 运行时；
- `docs/GUI.md` 记录两种工具和“离线真实 renderer 验收不等于 live HTTP E2E”的边界。

性能观察：

```text
12 镜 Cockpit 冷读：   692.262 ms → 48.521 ms   (-93.0%)
100 镜 Cockpit 冷读： 5561.350 ms → 284.854 ms  (-94.9%)
300 镜 Cockpit 冷读：15939.263 ms → 814.209 ms  (-94.9%)
```

新 HEAD 的五样本预热门：

```text
12 镜 median/p95：   40.402 / 42.448 ms
100 镜 median/p95： 290.869 / 301.931 ms
300 镜 median/p95： 826.736 / 848.276 ms
```

这些是当前沙箱的本地观察和 opt-in 门，不是跨机器承诺。视觉验收的 18 个页面/视口组合
全部无横向溢出、重复 ID、可见 `undefined` 或 browser page error。

证据：

- `REPORTS/product-polish-r1/WAVE12_PERFORMANCE_VISUAL_GATE.md`
- `REPORTS/product-polish-r1/wave12-tests/WAVE12_TEST_SUMMARY.json`
- `REPORTS/product-polish-r1/wave12-tests/performance-before.json`
- `REPORTS/product-polish-r1/wave12-tests/performance-after-cold.json`
- `REPORTS/product-polish-r1/wave12-tests/performance-after.json`
- `REPORTS/product-polish-r1/wave12-tests/visual-acceptance.json`
- `REPORTS/product-polish-r1/screenshots/wave12/`

记录的非重叠 focused tests 为 255 passed、31 deselected；compileall 和 `git diff --check`
通过。当前环境没有 ruff。live localhost verifier 被 Chromium 管理策略以
`ERR_BLOCKED_BY_ADMINISTRATOR` 阻止，证据已保留，未冒充通过；完整 pytest、Windows
App 模式、Windows hard gate 和同一 SHA 双平台门仍未完成。

后续优先级：停止增加产品旅程，用现有性能与视觉门完成真实 Windows App 模式人工验收，
再在同一 SHA 上跑 Ubuntu / Windows 发布门；若随后整理大模块，只做机械拆分，不与视觉
重设计混在同一提交。
