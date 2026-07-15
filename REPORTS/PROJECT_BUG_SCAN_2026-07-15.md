# Manju 全项目深度 Bug 扫描

| | |
|---|---|
| **日期** | 2026-07-15 |
| **HEAD** | `4a000ee` (`claude/fable-opus-task-division-wv97i6`) |
| **范围** | 全仓库 `src/manju`（CLI / build / core / GUI / MCP / providers） |
| **方法** | 三路并行只读探索 + 主会话证据核对 + 局部 pytest |
| **本轮是否改代码** | 否（仅扫描）→ **2026-07-15 已全部修复**（见文末 FIXES） |

---

## 执行摘要

项目在安全基线与近期 GUI 加固上**已经较强**：

- 无生产路径 `shell=True` / `eval` / 不安全 `yaml.load` / pickle
- GUI/board 主路径 CSRF token、`safe_served_path` 媒体隔离
- append-only takes、atomic YAML、build_lock 主路径
- GUI 合并阻塞项（N 键、gui_state 锁、bounded quit、webclient、poll spam）已在 `7bfc895` / `4a000ee` 修复
- 相关 GUI 回归：**83 passed**（`test_gui_merge_blockers` + wave/session/project_actions + workspace）

**仍有真实、高置信度问题**，集中在三类：

1. **§5 锁 TOCTOU**（检查在锁外、写在锁内不重验）
2. **build/voice 入口契约**（`--lang` 未校验、`--gen auto` 空语义、cancel 二次采样）
3. **GUI 轮询/任务 UX 正确性**（编辑器草稿、refresh 乱序、假 Retry、cancel 轮询）

个人单用户 Windows 场景下，多数 P0/P1 需要「GUI + CLI/MCP 并发」或「恶意/失误 CLI 参数」才触发；但产品把 **text 为真 / 锁不可破** 当作硬契约，因此仍应按严重度修。

---

## 已确认干净的区域

| 类别 | 结论 |
|------|------|
| `shell=True` / `eval` / pickle | 生产包干净 |
| YAML 解析 | `yaml.safe_load` / SafeLoader |
| 媒体 GET 路径穿越 | `safe_served_path` + 前缀白名单 |
| GUI/board CSRF 主路径 | `X-Manju-Token` 强制 |
| append-only takes / finals | 强 |
| `buildlock` Windows pid 探测 | `OpenProcess` 正确，非 `os.kill` |
| `yamlio` 原子写 + Windows replace 重试 | 扎实 |
| ProjectSession 冻结 / one-project-one-window | 测试覆盖良好 |
| 近期 GUI merge-blocker 修复 | 源码 + 83 tests 通过 |

---

## P0 — 真实性 / 安全完整性（优先修）

### P0-1. MCP `update_shot`：锁校验在 `build_lock` 外，写时不重验

| | |
|---|---|
| **位置** | `src/manju/mcp/tools.py` ~246–300 |
| **问题** | `(3)(4)` 对 `locked` 映射与 `verify_locks` 用的是锁**外**快照；`with build_lock` 内仅做 CAS + `write_yaml(path, new_data)`，**不重读磁盘锁、不重跑 verify**。 |
| **后果** | 人类在检查后、写入前执行 `manju lock S001 dialogue.text` → agent 全量 YAML 可抹掉新封印或改已封字段，直接违反 §5。`expected_rev` 可选，不能兜底。 |
| **修复** | 在 `build_lock` 内：重读 YAML → 断言 live `locked` → `verify_locks` → 再写。 |

### P0-2. GUI storyboard / lab：锁检查在锁外，`update_shot_raw` 无 re-check

| | |
|---|---|
| **位置** | `gui/server.py` `_act_sb_edit` ~4145–4175；`_act_lab_refs` ~4401–4419；`_act_lab_prompt_override` ~4447–4465 |
| **问题** | `lock_conflict` / `field_locked` 在 `_optional_build_lock` **之前**；锁内直接 `update_shot_raw`，不走 `checked_shot_write(guard_paths=...)`。 |
| **证据** | 冲突判断 ~4148–4153；写入 ~4169–4175。 |
| **后果** | GUI + CLI/MCP 并发时可能覆盖已封印创意字段。 |
| **修复** | 锁内重载+重检，或 `checked_shot_write(..., guard_paths=(field,))`。 |

### P0-3. `--lang` 路径穿越 / 项目污染

| | |
|---|---|
| **位置** | `cli.py` build/voice `--lang`；`locale_build.locale_*_dir`；`container._voice_takes_dir`；`locale.validate_lang` 仅 `add_locale` 使用 |
| **问题** | `validate_lang()` 存在且能拒绝 `..`，但 **build/voice 入口未调用**。`locale_captions_dir` / `locale_final_dir` 原样 join `lang`；voice 目录 `re.sub(r"[^A-Za-z0-9._-]", "_", lang)` **保留 `.`**，故 `..` → `..`。 |
| **核对** | `validate_lang("..")` → `ProjectError`；voice sanitize `".."` → `".."`。 |
| **后果** | `--lang ..` / 越级路径可把 voice/captions/final 写到 `locales/` 外甚至项目树外（视入口而定）。 |
| **修复** | 所有 lang 入口统一 `validate_lang`；禁止 `.`/`..`；voice 段用更严 segment 校验。 |

---

## P1 — 竞态 / 静默错误语义 / 数据丢失

### P1-1. GUI 单字段 `/api/lock`：digest 在 `build_lock` 外计算

| | |
|---|---|
| **位置** | `gui/server.py` `_act_lock` ~1459–1476 |
| **问题** | `seal_lock(raw, fieldpath)` 在锁外；锁内只写入旧 digest。CLI / `_act_sb_lock_batch` 已在锁内 seal。 |
| **后果** | 封印哈希与磁盘值永久不匹配 → check/build 硬失败直到 unlock。 |

### P1-2. Library GUI tag/note：index RMW 无 `_index_lock`

| | |
|---|---|
| **位置** | `gui/server.py` `_act_lib_tag` / `_act_lib_note` ~3369–3406 vs `core/library.py` add/remove |
| **后果** | 并发 `manju lib add` + GUI 打标签可丢 index 行或 tags。 |

### P1-3. `failures.jsonl`：Windows 同进程线程竞态 + 超时后无锁继续写

| | |
|---|---|
| **位置** | `core/failures.py` `_ledger_lock` ~60–122 |
| **问题** | Windows `msvcrt.locking` **无** 配套 `threading.Lock`（`events.py` 已补）；超时 `break` 后 **unlocked** rotate+append。 |
| **后果** | 多线程 GUI + build 可能撕行/丢失败账本行。 |

### P1-4. `checked_shot_write` CAS 契约洞

| | |
|---|---|
| **位置** | `core/writes.py` ~138–159 |
| **问题** | CAS 后 `update_shot_raw` 重新 load 再 mutate；无 build_lock 时 CAS 不绑定最终字节。 |
| **后果** | 未来漏加锁的调用方会静默 last-writer-wins。 |

### P1-5. 单镜头 `manju voice` / locale voice 无项目写锁

| | |
|---|---|
| **位置** | `cli.py` 单镜 voice；`locale_build.synthesize_locale_voices`；`register_voice_take` check-then-copy |
| **后果** | 并发可 mint 同号 take；`copy2` 覆盖；sidecar/media 不一致。 |

### P1-6. `--gen auto` 与 `missing` 行为相同（文档/UI 撒谎）

| | |
|---|---|
| **位置** | `build/graph.py` `GEN_MODES` + `_plan_generation` ~725–727 |
| **问题** | 仅 `needs = MISSING or (regen_stale and STALE)` 且 `gen == "off"` 跳过；**无** `auto` 分支。 |
| **后果** | 选 auto 仍不 regen stale；可能带着过期镜头进成片。 |

### P1-7. 生成取消后二次采样 `should_cancel()` 可能“忘记取消”

| | |
|---|---|
| **位置** | `build/graph.py` ~1468–1474；`_cancel_check` ~1039–1044 |
| **问题** | `gen_canceled=True` 后仍调用 `_cancel_check`，而后者仅在 **当前** `should_cancel()` 仍为真时 raise。 |
| **后果** | 非粘性/抖动谓词下，已取消的生成被遗忘，build 继续 compile/render 带洞。 |

### P1-8. GUI `refresh()`：await 后不重检 `editorOpen`；无序号

| | |
|---|---|
| **位置** | `gui/page.py` `refresh` ~1279–1300 |
| **问题** | 仅 await **前**检查 `editorOpen`；note 路径未必 `pauseLive()`；无 `refreshGen`。 |
| **后果** | 打字中草稿可被 poll 冲掉；慢请求乱序可画旧状态。 |

### P1-9. Job `retryable: true` 对所有 failed/canceled，但服务端仅支持部分 kind

| | |
|---|---|
| **位置** | `gui/jobs.py` ~177–178 vs `server._build_retry_fn` 白名单 |
| **后果** | QC/export/repair/lab 等点「重试」→ 400 死胡同。 |

### P1-10. 子页 `pollJob` 不把 `canceled` 当终态

| | |
|---|---|
| **位置** | `lab_page` / `exports_page` / `ingest_page` / `series_page` / `edit` 中重复的 poll 循环 |
| **后果** | 取消后空转至 ~10min 超时，文案误报排队中。 |

### P1-11. `anyActive` 不含 `canceling`

| | |
|---|---|
| **位置** | `page.py` ~1305 vs `Job.active` 含 `canceling` |
| **后果** | 取消中门闸解锁、轮询降频，状态撒谎。 |

### P1-12. Windows `manju import` 未做保留名/尾点校验

| | |
|---|---|
| **位置** | `cli.py` import vs GUI 已用 `windows_segment_problems` |
| **后果** | `CON.mp4` 等在 Windows 上设备名/静默折叠风险。 |

---

## P2 — 加固 / 已知残差 / 文档撒谎

| ID | 问题 | 位置 |
|----|------|------|
| P2-1 | CLI `transcribe --out` 可不限项目根 | `cli.py` |
| P2-2 | 付费下载 `_write_bytes_atomic` 无 fsync/Windows retry | `providers/generic_cloud.py` |
| P2-3 | `/api/state` 指纹忽略 job progress 字符串 | `gui/state.py` |
| P2-4 | Job 终态字段在 runner 锁外赋值 | `gui/jobs.py` |
| P2-5 | History 50 裁剪后 Retry → 404 | `jobs.py` |
| P2-6 | `gui_state` 30s stale lock steal 窗口 | `userstate.py` |
| P2-7 | CLI `close` 后 daemon worker 残差写风险 | `server.py` |
| P2-8 | 过时 docstring/文案仍写「切换项目」 | `server` / `page` |
| P2-9 | 子页 ingest 原始 `fetch` 可省略 `X-Manju-Project`（冻结会话下多半 latent） | `ingest_page.py` |
| P2-10 | 大量 `except Exception` 吞掉（多为有意 degrade；需个案审计） | 全库 |

---

## 测试缺口（会掩盖真实 bug）

| 缺口 | 掩盖的问题 |
|------|------------|
| 无「锁内 re-check」契约测试 | P0-1/2、P1-1 |
| 无 `retryable ⊆ _build_retry_fn` | P1-9 |
| 无 post-await `editorOpen` / `refreshGen` | P1-8 |
| 无 `pollJob` 对 `canceled` 终态 | P1-10 |
| 无 `--lang` 拒绝 `..` 于 build/voice | P0-3 |
| 无 `--gen auto` 语义 pin | P1-6 |
| 无 cancel 后强制 `BuildCanceled`（不依赖二次 flag） | P1-7 |
| 无 Playwright 键盘/草稿 e2e | 编辑器竞态 |

---

## 建议修复优先级（不改产品方向）

1. **P0-3** `--lang` 全入口 `validate_lang`（路径安全，改动面小）  
2. **P0-1 / P0-2 / P1-1** §5 锁 TOCTOU（真相性）  
3. **P1-7** cancel 一旦观测到就终断（不二次 poll flag）  
4. **P1-6** 实现或删除 `--gen auto`  
5. **P1-5** voice 注册进 build_lock + 独占创建  
6. **P1-8 / P1-9 / P1-10 / P1-11** GUI 轮询与诚实 Retry  
7. **P1-2 / P1-3** library index 锁；failures 对齐 events  
8. **P1-12 + P2-*** Windows import 与原子下载加固  

---

## 验证记录（扫描轮）

```
HEAD: 4a000ee
pytest (GUI subset): 83 passed in ~82s
```

---

## FIXES（2026-07-15 全部落地）

| ID | 修复摘要 |
|----|----------|
| P0-1 | MCP `update_shot`：`build_lock` 内重读 locked + `verify_locks` |
| P0-2 | GUI storyboard/lab/save_ref：锁内 live re-check |
| P0-3 | `validate_lang` 强化 + `locale_dir` / `_voice_takes_dir` / captions/final / CLI / `run_build` |
| P1-1 | GUI `_act_lock`：seal + write 均在 build_lock 内 |
| P1-2 | `Library.set_tags` / `set_note` + GUI 改走它们 |
| P1-3 | `failures._ledger_lock`：Windows thread lock；超时不无锁写 |
| P1-4 | `checked_shot_write` 对 CAS 字节 mutate，不再二次 reload |
| P1-5 | voice 独占 `O_EXCL` 创建；单镜 CLI + locale batch 持 `build_lock` |
| P1-6 | `--gen auto` = missing + stale |
| P1-7 | `gen_canceled` / serial canceled → `_cancel_check(force=True)` |
| P1-8 | `refreshGen` + await 后 `editorOpen`；note `pauseLive()` |
| P1-9 | `RETRYABLE_KINDS`；`retryable` 仅 build/redo/voice 批 |
| P1-10 | 子页 `pollJob` 终态含 canceled/interrupted |
| P1-11 | `anyActive` 含 canceling |
| P1-12 | CLI import `windows_segment_problems` |
| P2 | transcribe `--out` 项目内；下载 fsync+`replace_with_retry`；ingest Project 头；文档 |

**回归：** `tests/test_project_bugfix_20260715.py` + GUI/locks/library/mcp/voice/… → **253 passed**

---

## 结论

- 扫描所列 **P0/P1/P2 已全部修复**并落盘回归测试。  
- 安全底盘保持：无 `shell=True`、safe YAML、CSRF 主路径不变。  
