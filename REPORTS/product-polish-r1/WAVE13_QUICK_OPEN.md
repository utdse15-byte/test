# Product Polish R1 Wave 13 — 全局“快速前往”

**功能提交：** `976de0f8a807841012afc483eba04b2835735f50` (`R1: add navigation-only quick open`)
**边界回归提交：** `b40cf4cd52a4f09f25888b055d51cd9b6bed5df7` (`test: harden quick open boundaries`)
**日期：** 2026-08-14（证据以 UTC 生成时间记录）
**执行边界：** 严格零成本；无真实 Provider、外部模型、凭据或网络生成。

## 结论

Wave 12 已经建立大型项目性能和视觉验收门。继续增加新的制作流程收益很低，当前最高杠杆问题变成：页面、镜头和项目越来越多后，用户仍然需要依赖顶部导航、记忆镜头编号或在多个页面间寻找入口。

本波增加一个全局 `Ctrl/Cmd+K` **快速前往**，但刻意不把它扩展成“命令执行器”。它只负责发现和定位：

- 打开已有页面；
- 按镜头编号、场景、动作、对白或角色定位镜头；
- 根据查询意图进入镜头实验室、Review Theater 或批量入库；
- 打开已有 Task Center；
- 聚焦工作区的“打开项目 / 新建项目”；
- 通过现有受保护的工作区动作打开最近项目。

它不能构建、生成、重做、选片、评价、审批、确认或执行 Director Proposal、付费或 Picture Lock。

## 产品体验

### 1. 一个全局入口

应用栏和项目工作区共用同一个入口：

```text
Ctrl/Cmd+K
```

触发按钮声明：

```text
aria-haspopup=dialog
aria-controls=mj-command-palette
aria-expanded
aria-keyshortcuts=Control+K Meta+K
```

不再为工作区、审片页和其它页面各维护一套搜索面板。

### 2. 不需要记住镜头编号

项目索引读取 canonical shot YAML 中有界的：

```text
镜头 ID
场景
主要动作
对白
角色
```

例如输入：

```text
推开卷帘门
```

会得到：

```text
打开镜头 S003
镜头实验室 · 她推开卷帘门 · 雨夜便利店
```

输入：

```text
审片 推开卷帘门
```

会进入：

```text
/review?shot=S003
```

输入“导入 / 入库 / 素材”时则保持现有批量入库深链。

### 3. 新手模式继续诚实隐藏专业页面

页面目录直接派生自 `_NAV` / `_NAV_GROUPS` / `PRO_ONLY_PAGES`。快速前往没有复制第二份页面清单。

新手模式下继续隐藏：

```text
对比
Provider
路由
Doctor
```

直接 URL 兼容行为不变。

### 4. 工作区也是同一产品

未绑定项目时，快速前往仍然可以：

- 聚焦“打开现有项目”；
- 聚焦“新建项目”；
- 搜索最近项目；
- 使用已有工作区切换保护。

它不会绕过 stale-tab、项目身份或新窗口语义。

### 5. 失败时退回安全导航

项目索引按需读取，并在页面内最多复用 15 秒。读取失败时：

- 清除可能属于旧项目的镜头和最近项目结果；
- 保留静态、模式感知的页面导航；
- 显示“项目索引暂时无法更新，页面仍可打开”；
- 下一次显式打开自动重试。

失败不会导致页面不可用，也不会继续展示陈旧镜头结果。

## 架构边界

新增唯一 owner：

```text
src/manju/gui/command_palette.py
```

数据来源：

| 结果 | 现有 owner |
|---|---|
| 页面 | `manju.gui.pages.navigation_items()` |
| 镜头 | canonical shot YAML 的有界文本摘要 |
| 最近项目 | `manju.core.recents` |
| 任务 | 现有 Task Center |
| 项目切换 | 现有 `/api/workspace/open` 与 `handleProjectAction` |

明确不读取：

```text
媒体探测
时间线编译
Provider 配置
Provider 凭据
FFmpeg
QC 媒体字节
```

索引只存在于浏览器内存，不是项目 truth、数据库、build input、Provider input 或 cache identity。

## 键盘与无障碍

- `Ctrl/Cmd+K` 打开 / 关闭；
- 上下键、Home、End 选择；
- Enter 打开；
- Escape 关闭；
- native `<dialog>`；
- combobox + listbox；
- `aria-activedescendant`；
- 触发按钮和输入框的 expanded 状态同步；
- 关闭后恢复焦点；
- 工作区聚焦动作在 dialog 完成关闭后再移动焦点；
- reduced-motion 下取消过渡；
- 390px 和 320px 产品视觉门无横向溢出。

## 性能

同一功能提交 `976de0f` 上，纯本地 12 / 100 / 300 镜项目的索引测量：

| 镜头数 | 中位数 | p95 | 最大值 |
|---:|---:|---:|---:|
| 12 | 0.594 ms | 0.846 ms | 0.846 ms |
| 100 | 4.162 ms | 8.254 ms | 8.254 ms |
| 300 | 11.512 ms | 12.091 ms | 12.091 ms |

这些是当前 Linux 沙箱中的本地观察，不是 Windows 发布承诺。快速前往保持 lazy：页面首次渲染不会读取项目索引，第一次打开才读取。

## 浏览器验收

新增：

```text
scripts/dev/product_command_palette_acceptance.py
```

它使用真实服务端 renderer、真实 CSS 和实际交付 JavaScript，通过 Playwright `page.set_content` 验证：

- 按动作搜索 S003；
- 审片意图进入 Review Theater；
- 工作区安全动作；
- 最近项目；
- 第一次读取失败后的诚实降级与第二次重试；
- 焦点、ARIA、窄窗口和无横向溢出。

结果：

```text
command-palette acceptance：PASS
核心视觉门：18 / 18 页面-视口组合通过
```

截图：

- `screenshots/wave13/command-palette-desktop.png`
- `screenshots/wave13/command-palette-narrow.png`
- `screenshots/wave13/command-palette-workspace.png`

## 测试

记录的非重叠 focused tests：

```text
142 passed
0 failed
```

分组：

| 范围 | 通过 |
|---|---:|
| 快速前往核心与浏览器状态机 | 9 |
| 性能行为门 | 5 |
| 可重复开发验收工具 | 4 |
| 共享无障碍契约 | 6 |
| 共享应用外壳 | 8 |
| 共享页面脚本 | 6 |
| 项目切换与安全动作 | 14 |
| 新手/专业模式 | 22 |
| 共享 GUI 页面 | 25 |
| 全局任务中心 | 16 |
| 项目工作区 | 27 |

静态检查：

```text
python -m compileall -q src tests scripts/dev    PASS
rendered command-palette.js | node --check       PASS
git diff --check                                 PASS
ruff                                              当前沙箱不可用
```

## 零成本事实

```text
真实 Provider 调用：0
外部模型/API 调用：0
凭据读取：0
付费调用：0
免费额度调用：0
```

## 没有改变的决策边界

```text
搜索结果 ≠ 执行
打开镜头 ≠ 生成
评价 ≠ 当前选择
当前选择 ≠ 镜头审批
镜头审批 ≠ Picture Lock
打开最近项目 ≠ 修改项目真相
```

没有新增项目 schema、数据库表、scheduler、内置 LLM、Manju MCP、React、Electron 或云端资源。

## 没有冒充完成的发布门

本波没有声称完成：

- 全仓完整 pytest；
- live localhost Playwright E2E；
- Windows App 模式真实键鼠 Dogfood；
- Windows hard gate；
- 同一 SHA 的 Ubuntu / Windows 双绿；
- 真实 Provider、计费、取消或 AI 视频质量。

## 下一步判断

快速前往解决的是成熟产品的“到达成本”，不应继续扩展成可以执行危险操作的命令系统。下一步最高价值仍然是：

```text
真实 Windows App 模式个人 Dogfood
→ 修复真实使用中的最后阻力
→ 同一 SHA Ubuntu / Windows 发布门
```

在这些证据完成前，不建议继续增加新的制作旅程或大规模重构。
