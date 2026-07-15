# Manju 深度 Bug 扫描（第二轮 · 修复后复扫）

| | |
|---|---|
| **日期** | 2026-07-15 |
| **HEAD** | `2de176e` |
| **相对** | 第一轮扫描 + P0/P1/P2 修复波次之后 |
| **方法** | 三路并行只读探索 + 主会话证据核对 |
| **本轮是否改代码** | 否（仅扫描）→ **已全部修复并提交** |

---

## 执行摘要

第一轮修复项 **多数仍有效**（§5 锁 re-check、`validate_lang`、`gen auto`、cancel `force`、voice `O_EXCL`、GUI refreshGen/retryable 等）。

本轮发现 **3 个新的 P0**，其中 **1 个是上一波修复引入的回归**：

| P0 | 一句话 |
|----|--------|
| **R2-P0-1** | `synthesize_locale_voices` 在 `run_build` 已持锁时再取 `build_lock` → **自死锁** → locale 配音被吞成 warning → **外语成片仍用母语音频** |
| **R2-P0-2** | locale final 的 QC 仍探针 **base** `renders/final/`，不是 `locales/<lang>/` → **假绿 / 查错片** |
| **R2-P0-3** | `common.js` `post()` 在 webclient 路径把所有 2xx **硬编码成 status 200** → 依赖 `202` 的 job 提交全线假失败，可 **双点付费生成** |

另有一批 P1（serial 预算熔断缺失、GUI import 无 build_lock、library 缺 thread lock、CAS 缺口等）。

---

## 已知修复：仍正确（不重复开单）

| 项 | 状态 |
|----|------|
| MCP / GUI §5 锁内 re-check | OK |
| `_act_lock` 锁内 seal | OK |
| `validate_lang` / voice 路径 | OK |
| `failures` thread lock + 无锁不写 | OK |
| `Library.set_tags/set_note` | OK |
| `checked_shot_write` CAS 绑定字节 | OK |
| voice `O_EXCL` + 单镜 CLI 锁 | OK |
| `--gen auto` = missing+stale | OK |
| cancel `force=True` | OK |
| refreshGen / retryable / poll canceled / anyActive | OK |
| import Windows 名 / transcribe 项目内 / 下载 atomic | OK |
| 无 `shell=True` / 不安全 YAML | OK |

---

## P0 — 必须优先

### R2-P0-1. Locale voice 嵌套 `build_lock`（**回归 · 由上轮 P1-5 引入**）

| | |
|---|---|
| **位置** | `build/locale_build.py` ~98–99；`build/graph.py` ~1531–1538；`runtime/buildlock.py` ~175–176 |
| **问题** | `BuildLock` **不可重入**。`run_build` 已持锁；`synthesize_locale_voices` 再 `with build_lock` → `BuildLocked`。graph 用 `except Exception` 收成 **warning**，构建可继续 `ok`。 |
| **核对** | 进程内二次 `build_lock` 确认 raise `BuildLocked`。 |
| **后果** | `manju build --lang en` 有配音计划时 **永远合不成 locale voice**；overlay/compiler 回落 **base 音频** → 静默错误成片。CLI 单独 `manju voice --lang` 无外层锁，可正常。 |
| **修复** | 增加 `hold_lock=False` 或检测已持锁则跳过；`BuildLocked` 不得软吞。locale plan 非空且 0 take 落地 → `ok=False`。 |

### R2-P0-2. Locale final 的 QC 查的是 base final

| | |
|---|---|
| **位置** | `qc/checks.py` ~908–909, ~990–993；`build/graph.py` locale render ~1810–1853 |
| **问题** | Locale 成片在 `renders/final/locales/<lang>/`；`run_qc` → `_newest_final` → `newest_final_path()` 只看 **base** 目录。 |
| **后果** | locale 构建 QC 可能对着 **另一份** 片子；locale 独有缺陷不失败，base 缺陷可误杀 locale 构建。 |
| **修复** | `run_qc` / `_final_render` 接受 `final_path` 或 `lang`；graph 传入刚渲染路径。 |

### R2-P0-3. `post()` 把 202 抹成 200（webclient 统一后回归）

| | |
|---|---|
| **位置** | `gui/common_js.py` ~69–73 |
| **问题** | `requestJson` 成功只返回 body；`post` 写死 `status: 200`。legacy fetch 路径才返回真 status。 |
| **证据** | lab `doGenerate`：`if (res.status === 202 && res.data.job)` → 永远走失败 toast，但 job 已 `assume_yes: true` 提交。 |
| **影响面** | lab / exports / edit trim·rebuild / ingest plan·apply / series / storyboard batch / review pages — 凡 `status === 202` 判断。 |
| **后果** | UI 报失败 + 按钮重开 → **双点付费**；或半开状态（任务在跑、UI 当失败）。 |
| **修复** | `requestJson` 返回 `{status, data}` 或 `post` 用 `response.status`；调用方可兜底 `data.job`。 |

---

## P1

| ID | 问题 | 位置 |
|----|------|------|
| **R2-P1-1** | 串行生成无 mid-run `budget.limit` 熔断（并发有） | `graph.py` serial loop vs concurrent trip |
| **R2-P1-2** | `--lang` + 非 `final` 目标污染 base proxy/export 路径 | `graph.py` ~1810 条件 |
| **R2-P1-3** | Locale final render 无 `cancel_scope` | `graph.py` ~1810–1853 vs base ~1880 |
| **R2-P1-4** | Locale voice 失败仅 warning，仍可绿过带母语音频成片 | `graph.py` ~1536–1542 + compiler fallback |
| **R2-P1-5** | Library `_index_lock` Windows 无 thread lock（events/failures 已有） | `library.py` ~136–145；upload/frame-add 无 quick_mutex |
| **R2-P1-6** | GUI import / lib_use 无 `build_lock`，check-then-write 名冲突可踩 sacred imports | `server.py` upload / lib_use vs CLI `_write_lock` |
| **R2-P1-7** | `register_take` 仍 check-then-copy（voice 已 O_EXCL） | `container.py` ~527–549 |
| **R2-P1-8** | take_note / sb_approve 无 guard_paths / post-check | `server.py` |
| **R2-P1-9** | 镜头栏「新建」打开已存在镜头时 **未传 rev** → CAS 跳过 | `page.py` ~4447–4448 |
| **R2-P1-10** | SPA 工作台 take-note/好弃 无 `expected_rev`（review 页有） | `page.py` vs `pages.py` |

---

## P2

| ID | 问题 |
|----|------|
| R2-P2-1 | Job 终态字段在 runner 锁外赋值 |
| R2-P2-2 | GUI CSRF `!=` 非 constant-time（board 已用 compare_digest） |
| R2-P2-3 | 新建镜头 index 追加在 build_lock 外 |
| R2-P2-4 | gui_state 30s stale steal |
| R2-P2-5 | jobs.jsonl 无跨进程锁 |
| R2-P2-6 | TTS 预览 poll 不认 canceled |
| R2-P2-7 | 部分 `apiRaw`/ingest 也硬编码 200 |

---

## 建议修复顺序

1. **R2-P0-3** `post()` 保留真实 HTTP status（面小、阻断全站 job UX + 双点费）  
2. **R2-P0-1** locale voice 去掉嵌套锁 / `hold_lock`（修回归 + 外语成片）  
3. **R2-P0-2** QC 接 locale final path  
4. **R2-P1-4** locale 配音计划必须落地，否则 hard fail  
5. **R2-P1-1 / 3 / 2** 串行预算、locale cancel_scope、lang×target 路由  
6. **R2-P1-5～10** library/import/take/CAS  
7. P2 加固  

---

## 结论

- 第一轮「安全与 GUI 合并阻塞」修复 **大体站住**。  
- 第二轮 **最严重问题** 是：  
  1）上轮 voice 加锁导致 **locale build 自死锁**；  
  2）webclient 统一导致 **202→200 协议塌缩**；  
  3）locale QC **查错成片路径**。  
- 本报告只扫描；需要时可按上表直接开修。

---

## FIXES（R2 全部落地）

| ID | 修复 |
|----|------|
| R2-P0-1 | `synthesize_locale_voices(hold_lock=False)` from `run_build` |
| R2-P0-2 | `run_qc(final_path=…)` + graph 传入 locale/base render |
| R2-P0-3 | `requestJson({returnStatus})` + `post()` 保留真实 status |
| R2-P1-1 | 串行生成 mid-run budget trip |
| R2-P1-2 | `--lang` + `proxy` 拒绝 |
| R2-P1-3 | locale final `cancel_scope` |
| R2-P1-4 | locale voice plan 未全落地 → hard fail |
| R2-P1-5 | library `_index_lock` Windows thread lock |
| R2-P1-6 | GUI upload / lib_use 持 `build_lock` |
| R2-P1-7 | `register_take` O_EXCL |
| R2-P1-8 | take_note / approve → `checked_shot_write` |
| R2-P1-9 | 镜头栏传 `rev` |
| R2-P1-10 | SPA `shot.rev` + take-note `expected_rev` |
| R2-P2 | job 锁内终态、CSRF compare_digest、index 锁、TTS poll 等 |

**回归：** `test_project_bugfix_*` + GUI/locks/library/mcp/voice… → **230 passed**
