# Product Polish R1 · Wave 3 — 成片与外部精剪的信任闭环

日期：2026-08-13  
基线提交：`b4d2bc093d52ed19d7399b7c5efa4b76f4f6f383`  
功能提交：`f5566e8` (`R1: make finishing round trips explicit and recoverable`)

## 目标

本波不增加新的 AI 能力，也不进行任何付费或公网调用。目标是修复一个成熟产品不能接受的语义混淆：

```text
交换文件已经导出  ≠  外部修改一定可以安全回收
```

FCPXML、OTIO 和剪映 skeleton 过去都会在写回程 baseline 失败时吞掉异常。用户看到的是“导出成功”，却无法知道该文件是否仍有可信的导出时 T0 可以对账；缺少 baseline 的 roundtrip 计划又可能把“无法证明”误读为 `no_changes`。

## 实现

### 1. baseline 失败不再静默

- 保留严格的 `write_baseline` 原语。
- 新增 exporter 使用的 `write_baseline_best_effort`：
  - 交换文件成功落盘时不会因派生 sidecar 失败而被删除；
  - CLI/GUI 同一次操作会收到明确 warning；
  - 直接 Python 调用会发出 `RoundtripBaselineWarning`；
  - 返回 `None` 明确表示“只能单向使用”。

覆盖：OTIO、FCPXML、JianYing skeleton。

### 2. 无 baseline 的计划是 inspection-only

`plan_roundtrip` 新增派生字段：

```json
{
  "baseline_state": "ready | missing",
  "appliable": true,
  "rows_reliable": true
}
```

缺失 baseline 时：

- `appliable=false`；
- `rows_reliable=false`；
- 没有 diff 行时返回 `baseline_missing / unverified`，不再伪造 `no_changes`；
- `apply_roundtrip` 核心自己 fail closed，不再只依赖 CLI 先拦截；
- 旧的手工构造 plan 没有 `appliable` 字段时保持兼容。

### 3. 不跨载体借用同名 sidecar

OTIO 与 FCPXML 通常拥有相同项目 stem。过去 project-wide 查找可能在 FCPXML baseline 丢失后找到同名 OTIO baseline。现在 carrier kind 已知时只在对应 `exports/<kind>/.baseline` 中搜索。

### 4. 导出中心新增“继续精剪”信任面

导出中心现在把 OTIO/FCPXML 的两个事实独立显示：

- 尚未导出；
- 文件有问题；
- 仅可单向使用；
- 基线有问题；
- 可安全回收。

其余产物按用户目的分为：

- 直接观看；
- 字幕；
- 平台草稿；
- 包装与声音。

FCPXML 现在可以直接从 GUI 生成。生成成功但 baseline 失败时，任务仍然返回 carrier，同时展示 sticky warning 和 `roundtrip_ready=false`。

### 5. 文档残渣与语义同步

- 删除 README、CLI、PINS、WORKBENCH 中残留的字面量 `undefined`；
- CLI 与 WORKBENCH 说明补充 inspection-only 与 safe-recovery 语义。

## 故障注入

新增测试覆盖：

1. baseline 目录不可写；
2. 直接 exporter 调用时 warning 不被吞掉；
3. baseline 删除后 plan 不得报告 `no_changes`；
4. core apply 拒绝无 T0 plan；
5. FCPXML 不得借用同 stem OTIO baseline；
6. export status 同时显示 ready 与 one-way；
7. CLI JSON 返回 baseline warning；
8. GUI FCPXML 正常导出为 roundtrip-ready；
9. GUI baseline 失败仍保留 carrier，并显示 degraded warning。

## 验证

以下文件逐个运行并报告通过，共 **175 passed**：

```text
tests/test_product_polish_finishing.py       6
tests/test_roundtrip_baseline_absent.py      4
tests/test_roundtrip_needs_baseline.py       8
tests/test_roundtrip_fcpxml.py              12
tests/test_export_center.py                 34
tests/test_gui_pages.py                     25
tests/test_fp_fcpxml.py                     33
tests/test_fp_fcpxml_import.py              35
tests/test_export_containment.py            10
tests/test_exports_table_alignment.py        5
tests/test_windows_export_conform.py          3
```

静态验证：

- `python -m compileall -q src tests/test_product_polish_finishing.py` — PASS
- touched-module imports — PASS
- `node --check` on rendered `/exports.js` — PASS
- `git diff --check` — PASS

`ruff` 不在当前环境中；尝试安装时沙箱 DNS 无法访问包索引，因此没有把它伪装成已运行。

## 浏览器证据与边界

沙箱 Chromium 的管理员策略会对 `http://127.0.0.1:<port>` 返回 `ERR_BLOCKED_BY_ADMINISTRATOR`。因此 live GUI server 浏览器 journey **没有**在本环境完成。

已使用同一 server-rendered HTML 和真实 CSS，通过 Playwright `page.set_content` 离线渲染并检查：

- 1280×720 desktop；
- 390×844 narrow viewport；
- OTIO `可安全回收`；
- FCPXML `仅可单向使用`；
- 无横向溢出；
- finishing 与普通交付物层级清楚。

证据：

- `screenshots/wave3/exports-finishing-desktop.png`
- `screenshots/wave3/exports-finishing-mobile.png`
- `screenshots/wave3/facts.json`

真实 localhost navigation、点击生成和 toast 展示已由 HTTP 测试覆盖底层行为，但仍应在用户机器的 Edge/Chrome App 模式补一次真实浏览器手工确认。

## 不变量

本波没有：

- 真实网络请求；
- Provider credential 读取；
- 云端或付费调用；
- 自动 select；
- 自动 Picture Lock；
- 新项目真相；
- React/Electron/MCP；
- 把 baseline 或 GUI status 变成 build input。

## 后续建议

下一批优先继续真实用户旅程，而不是扩展功能：

1. 在 Windows App 模式补 live screenshots 与 FCPXML warning toast；
2. 审计“剪辑 → 字幕 → 混音 → 包装 → 导出”的跨页连续性；
3. 统一成片阶段的页面 header、Picture Lock 和 stale 状态；
4. 再考虑全局命令搜索或大文件机械拆分。
