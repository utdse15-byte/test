# Product Polish R1 · Wave 5 — 统一应用外壳与稳定制作阶段

日期：2026-08-13  
基线：`78f82437dd37c2c03b97bdd314c7df518674ad29`  
功能提交：`91b6e1bdc7a1de806c1ad0984427f79474161945`  
范围：共享应用栏、六阶段导航、当前阶段子导航、项目身份和执行模式可见性  
费用边界：严格零成本；真实 Provider、外部 API、凭据读取和付费请求均为 0

## 1. 产品判断

Wave 4 已把“剪辑 → 字幕 → 混音 → 包装 → 导出”连成一条成片旅程，但全局导航仍保留了早期工具台的形态：六个分组依赖 hover 下拉，页面实际存在十七个同权入口；新手提示在未关闭前跨页面重复出现；项目名、执行策略和视图模式又争夺同一行空间。更隐蔽的问题是 `/director`、`/storyboard`、`/lab`、`/ingest` 四个页面绕过共享 `chrome()`，因此默认按专业模式渲染，并缺少同一套术语、项目切换和 series chrome。

这不是缺功能，而是产品身份不稳定：用户每换一个页面，都需要重新理解“我在哪里、下一层入口在哪里、当前是不是本地安全”。

本波不增加路由、不增加项目状态、不引入前端框架，而是把现有页面收拢成同一款软件：

```text
应用栏：Manju · 当前执行模式 · 视图设置 · 当前项目
阶段栏：首页/工作台 · 创作 · 镜头 · 审片 · 成片 · 工具
子导航：只显示当前阶段内的页面
```

## 2. 已完成

### 2.1 六个制作阶段始终可见

旧表面是 hover-only 分组，十七个页面入口都在同一个导航结构中。现在六个阶段始终显示：

```text
工作台  创作  镜头  审片  成片  工具
```

当前阶段使用 `aria-current="step"`，当前阶段内页面使用 `aria-current="page"`。用户无需 hover 才能发现核心流程，也不会在每个页面面对十七个同权入口。

### 2.2 只展开当前阶段

例如进入“镜头”后，只显示：

```text
分镜  镜头实验室  批量入库
```

进入“审片”后，只显示审片相关页面。成片阶段没有重复全局子导航，因为 Wave 4 的页面内五步流程已经是更完整的 owner；全局只标记当前处于“成片”。

这避免两套“当前页”指示相互竞争。

### 2.3 所有主要页面共用同一 `chrome()` owner

修复了以下页面过去直接调用 `nav_html()` 的不一致：

- 导演助手；
- 分镜工作台；
- 镜头实验室；
- 批量入库。

现在它们与首页、成片和其他 server-rendered 页面一致地拥有：

- 当前新手/专业模式；
- 专业术语开关；
- 当前项目切换；
- series banner；
- 项目动作脚本；
- 当前执行策略；
- 同一 body mode class。

这项修复不是纯视觉：过去新手从深链接进入这些页面时，页面会无意中呈现为专业模式。

### 2.4 执行模式成为长期可见的信任状态

应用栏在所有页面显示 secret-free 的执行策略快照：

- `strict` → **本地安全**；
- `standard` → **标准执行**；
- `invalid` → **模式错误**。

本轮所有视觉证据均在 `MANJU_EXECUTION_MODE=strict_zero_cost` 下生成。没有读取云端密钥，也没有触发 Provider transport。

未知模式仍由既有 fail-closed owner 决定；GUI 只展示，不建立第二套策略。

### 2.5 当前项目身份可见

项目切换入口不再只显示泛化的“项目”，而显示当前项目名称，并保留截断、title 和原有 recents/open 行为。用户可以在任何页面确认自己正在操作哪个项目。

### 2.6 视图设置渐进披露

“新手 / 专业”和“显示专业术语”继续使用原有 API、data hook 与本地状态，但收进原生 `<details>` 菜单。六阶段流程获得视觉优先级，设置仍可通过键盘访问。

### 2.7 新手提示只在首页出现

旧提示会在未关闭前跟随所有页面。现在它只在首页首次出现，内容缩短为：

```text
新手视图已开启
高级工具已收起，项目内容不会改变。
```

用户可“查看全部功能”或“知道了”；其他页面不再被重复 banner 占用。

### 2.8 中文优先补齐到直接页面标题

创作、导演、分镜、实验室和批量入库的主标题默认只显示中文；英文被放入现有 `.mj-en` 层，只有开启“显示专业术语”才出现。没有新增 i18n 系统。

### 2.9 窄窗口完整显示六阶段

390px 宽度下阶段栏以 3×2 网格呈现，六个阶段始终可见。实测：

- 分镜页 `document.scrollWidth=380`，相对 390px viewport 横向溢出 0；
- 混音页 `document.scrollWidth=380`，横向溢出 0；
- 当前阶段和子导航完整可见；
- 品牌缩为 `M`，模式与项目入口保持可操作。

完整测量见 `screenshots/wave5/facts.json`。

## 3. 唯一 owner 与边界

- 页面与分组标签：`src/manju/gui/pages.py` 的 `_NAV` / `_NAV_GROUPS`；
- 共享 shell：`chrome()`；
- 执行策略事实：`providers.zero_cost.execution_policy_snapshot()`；
- 项目 truth：未修改；
- 新手/专业和术语状态：继续由现有 userstate / glossary API 拥有；
- 成片页级流程：继续由 `finishing_journey.py` 拥有；
- 浏览器状态、报告和截图：均为可删除派生物，不是 build input。

本波没有：

- 新项目 schema；
- 新数据库表；
- 新路由；
- React / Electron；
- Manju MCP；
- 自动选择；
- 自动 Picture Lock；
- 付费功能或真实网络测试。

## 4. 验证

### 4.1 focused pytest

逐批次报告共 **177 passed**：

| 测试范围 | 通过 |
|---|---:|
| application chrome 新行为 | 8 |
| 新手/专业模式 | 22 |
| 工作区与项目切换 | 27 |
| 共享 GUI 页面 | 25 |
| 首页聚焦 | 7 |
| 成片旅程 | 10 |
| cockpit | 13 |
| 创作页 | 17 |
| 分镜页 | 26 |
| 镜头实验室定向页面测试 | 3 |
| 入库/导演定向页面测试 | 2 |
| UX 导航与状态定向测试 | 3 |
| 零成本 Provider 策略 | 14 |

日志位于 `REPORTS/product-polish-r1/wave5-tests/`。

### 4.2 静态和前端语法

通过：

- `python -m compileall -q src tests`；
- 由仓库真实 renderer 生成的 9 份 JavaScript 执行 `node --check`；
- 最终 `git diff --check`（在收口提交前执行并记录）。

当前环境未安装 `ruff`；没有把它描述成通过，也没有联网安装。

### 4.3 视觉验证

使用真实 server HTML renderer 和仓库实际 CSS，通过 Playwright `page.set_content` 渲染：

- 分镜：1440×900、390×844；
- 混音：1440×900、390×844；
- 390×844 下打开的视图设置菜单。

打开菜单时实测 `document.scrollWidth=380`、相对 390px viewport 横向溢出 0，弹层左右边界均在视口内。脚本被移除，因此这些截图验证 server-rendered chrome、CSS、响应式布局和中文层级，不冒充完整 live-server 用户旅程。当前环境没有声明 localhost Playwright 全流程通过。

## 5. 未完成与诚实边界

本波没有运行：

- 完整 pytest；
- 完整 `tests/test_shot_lab.py`；
- Windows hard gate；
- 同一 SHA Ubuntu / Windows 发布门；
- live localhost Playwright journey；
- 真实 Provider / API / 凭据 / 付费测试。

因此 Wave 5 是产品打磨波完成，不是发布认证。

## 6. 下一优先级

下一波应回到 Manju 的最高频核心：**Review Theater**。

重点不是增加审片功能，而是用真实项目和浏览器证据继续打磨：

- 桌面主播放器与待审队列的稳定双栏；
- 窄窗口下队列、播放器与主操作的顺序；
- 一个清晰主操作；
- verdict、selected take 与 Picture Lock 的视觉层级；
- 技术证据抽屉；
- 任务、未阅和 QC blocker 的优先级；
- 1280×720、200% text 和键盘焦点。

继续保持零付费、append-only、人工选择和外部智能边界。
