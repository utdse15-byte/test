# Product Polish R1 Report

## Baseline

- HEAD before changes: `fd93f52e8f16f58d24f5041972a6e80eb6d894c3`
- Fixture: local `manju new --demo` project, no cloud provider, no API key, no
  external transport.
- GUI: `manju gui ... --host 127.0.0.1 --port 0 --no-open`.

## Verification

Focused command:

```text
python -m pytest tests/test_product_polish_home_focus.py tests/test_zero_cost_provider_policy.py tests/test_cockpit.py tests/test_gui.py tests/test_gui_core.py tests/test_gui_modes.py tests/test_gui_page_shell_scripts.py -q
```

Result: `57 passed`.

Static checks:

- `python -m compileall -q src/manju`
- `git diff --check`
- served app bundle parsed by the existing GUI Node syntax test.

Browser checks:

- Desktop 1280x720: build panel y=641 px after polish; zero horizontal overflow;
  no console warnings/errors.
- Strict zero-cost desktop: build panel y=603 px; visible `严格零成本` chip with
  loopback/credential-free tooltip; zero horizontal overflow; no console errors.
- Mobile 390x844 baseline: zero horizontal overflow.
- Header help still opens the complete six-step onboarding checklist on demand.

Known repository baseline remains unchanged outside this wave: the full Windows
suite previously reported `6175 passed, 63 skipped, 10 failed`; the 10 failures
were environment-specific missing `grep`/`sh`, unrelated to this change.

## Wave 2 — 人工选片门与 Review Theater

- `build` / `redo` / batch ingest 不再自动写 `selected_take`；
- proxy/final/export/qc 在缺少人工选择时返回 `selection_required`；
- Review 主视频按当前卡片懒加载，判断动作前置并 sticky；
- 详情见根目录 `PRODUCT_POLISH_HANDOFF_2026-08-13.md`。

## Wave 3 — 成片与外部精剪信任闭环

功能提交：`f5566e8`。

- OTIO/FCPXML/JianYing baseline 写入失败不再静默；carrier 保留，但同一次 CLI/GUI 操作明确标记为 one-way。
- 无 baseline 的 roundtrip plan 为 `appliable=false` / `rows_reliable=false`；核心 apply fail closed，不再误报 `no_changes`。
- carrier-kind baseline 查找隔离，FCPXML 不会借用同名 OTIO sidecar。
- 导出中心增加“继续精剪”，直接区分 `可安全回收` 与 `仅可单向使用`，并支持 GUI 生成 FCPXML。
- 175 个相关测试逐文件通过；compile/import/JS syntax/diff checks 通过。
- Chromium localhost 被本沙箱管理员策略阻断；已用真实 HTML/CSS 做 1280×720 与 390×844 离线 Playwright 截图，live-server journey 留给用户机器确认。

完整记录：`WAVE3_FINISHING_TRUST.md`。
