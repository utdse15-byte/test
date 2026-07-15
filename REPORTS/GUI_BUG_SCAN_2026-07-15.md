# GUI 深度 Bug 扫描报告

- 分支: `claude/gui-personal-workbench-reliability-scale`
- 扫描起点: `7bfc895`
- 日期: 2026-07-15

## 方法

1. 静态审读 `page.py` / `userstate.py` / `shutdown.py` / `jobs.py` / `server.py` / `webclient.py`
2. 模式扫描: 无界 join、静默 except、位置型选择器、竞态写、observer 泄漏
3. 对照上一轮合并阻断项，验证修复是否仍站得住
4. 新发现缺陷当场修补并纳入本提交

---

## A. 已确认修复（阻断项复核）

| ID | 问题 | 复核结果 |
|----|------|----------|
| P0-N | `N` 误点「好」 | **已修**：仅 `[data-action="edit-note"]`；源码中无 `.tacts .btn` 兜底 |
| P1-hydrate | 状态只写不读 | **已修**：`bootstrapWorkspaceUI` 先 GET `/api/ui-state` 再 refresh |
| P1-lock | 多窗口丢状态 | **已修**：`update_gui_state` + O_EXCL 锁 + 唯一 tmp |
| P1-quit | cancel 无限 join | **已修**：有界轮询 + `shutdown_state=stuck` |
| P1-aria | 退出单条审片残留 | **已修**：退出时清 `aria-hidden` / `inert` |
| P1-observer | 替换不 unobserve | **已修**：`disposeShotNode` |
| P2-focus | 焦点对象失效 | **已修**：`captureFocusIdentity` / `restoreFocusIdentity` |

---

## B. 本轮新发现并已修

### B1. P1 — 轮询刷写个人状态（性能/正确性）

**路径**: `renderShots` 末尾 `saveUI({ shotsScroll })`  
**现象**: 每次 `/api/state` 重绘都 `POST /api/ui-state`，放大磁盘锁竞争与网络噪声。  
**修复**: 移除渲染路径写入；改为 `#shots` 的 `scroll` 防抖 400ms 保存。

### B2. P2 — `media_nodes_reused` 指标爆炸

**路径**: 复用卡片时对每个 video/img 累加，且跨 poll 不重置。  
**修复**: 每次 `renderShots` 开头重置计数器，指标变为「本帧快照」。

### B3. P1 — 工作台子页未加载 `webclient.js`

**路径**: exports / series / lab / ingest / director / create 仅加载 `common.js`。  
**现象**: `requestJson` 未定义，只能走 fetch 回退；与「统一客户端」不一致。  
**修复**: 各 shell 在 `common.js` 前插入 `/webclient.js`。

### B4. P2 — SPA `apiRaw` 未识别 `ManjuProtocolError`

**路径**: 只处理 `ManjuApiError`，协议错误会冒泡成未捕获 rejection。  
**修复**: `ManjuProtocolError` → `{ ok:false, status:502 }`；本地 fallback 对 2xx 非法 JSON 同样失败。

---

## C. 仍存风险（已知限制，非本次回归）

| 级别 | 问题 | 说明 |
|------|------|------|
| P1 | CLI `close()` 2s 后仍可能 `server_close` 而 worker 未死 | daemon worker 可继续跑完当前任务；已打 warning。完全阻塞 Ctrl-C 不适合个人工具，需产品取舍 |
| P2 | `after_current` 退出无墙钟上限 | 设计为等当前任务；长任务会长时间 `closing` |
| P2 | 锁文件 30s 陈旧可被抢 | 进程挂死超 30s 时另一窗口可接管；极端下双写窗口极小 |
| P2 | 草稿恢复无完整 UI 闭环 | 服务端 API 有；主 SPA 无 diff/恢复对话框（功能未完，非误操作） |
| P2 | 无 Playwright 级真实键盘 e2e | 当前 pin + 行为测试覆盖源码与服务端；浏览器实机仍建议 PR 后手测 |
| P3 | `connected_clients` 为线程启发式 | 仅诊断，不准确 |

---

## D. 安全面

| 项 | 结论 |
|----|------|
| XSS | 动态 DOM 以 `textContent` 为主；未发现新的 `innerHTML` 注入点 |
| CSRF | 仍强制 `X-Manju-Token` |
| Launch 任意路径 | 已限制 known catalog/session/recents |
| shell=True | launch 路径无 |
| 项目真相污染 | UI 状态仅 `~/.manju/gui_state.json`，不进 project YAML |

---

## E. 建议验收清单（合并前）

1. 聚焦 take → 按 `N` → 只开备注框，`take_notes` 不变  
2. 端口 A 设筛选 stale + 镜头焦点 → 杀进程 → 端口 B 同项目 → 筛选/镜头恢复  
3. 两窗口同时改不同项目的 UI 状态 → 两边都在  
4. 启动忽略 cancel 的 job → `cancel_running` → 约 60s 内 `stuck`，写接口 503  
5. `F` 开单条审片再关 → 无残留 `aria-hidden`  
6. Windows CI 绿  

---

## F. 结论

- 上一轮列出的 **合并阻断项在代码层已关闭**。  
- 扫描中新发现的 **刷写状态 / 指标失真 / 子页缺 webclient / ProtocolError** 已修。  
- 剩余项主要是 **CLI 硬退出与 worker 竞态**、**草稿 UI 未闭环**、**缺浏览器 e2e**，不构成「静默写错审片结果」类 P0。  
- **可以合入默认分支做 CI 验证**；合入后仍建议做一次 Windows 实机快捷键与跨端口恢复冒烟。
