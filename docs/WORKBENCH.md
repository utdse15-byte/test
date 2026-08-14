# WORKBENCH.md — what "complete workbench-level capability" means (round S §4)

The rule: **every project capability a human needs day-to-day is reachable in
`manju gui` with one obvious affordance; everything the CLI can do that the
GUI deliberately cannot is listed here with its reason.** The GUI is a veneer
over the same core the CLI calls — no capability exists only in the GUI.

## 移出本刀 / drop a shot from the cut(2026-08-01 补齐,两面对等)

| Capability | CLI | GUI |
| --- | --- | --- |
| 把镜头移出成片 / 放回 | `manju cut drop S005` / `manju cut restore S005`(`manju cut` 看现状) | 剪辑台每张卡片的 **✕ 移出**;被移出的进「不在本刀里」托盘,一键**放回** |

语义(店主授权后定,按"最不容易后悔"):**移出不是删除** —— 镜头 YAML 永远
留在盘上,`index.yaml` 的 order 就是"这一刀",随时可放回,没有单向门。

安全:`permute_index` 的**排列**不变量原样保留(它是两个标签页同时改序时的
守卫,GUI-INDEX-P1-001);成员变更走独立写入器 `core.writes.set_cut_order`,
在 GUI 上**必须带 CAS 令牌**,老页面只能改序、丢不了镜头。

连带修掉一个真 bug:剪辑台此前画的是 `shot_ids()`(含盘上多余镜头),于是
被移出的镜头照样显示在主轨道,点一下 ▲ 就把它静默塞回成片 —— 现在主轨道
只画这一刀(`indexed_only=True`)。

## Deliberately CLI-only (containment, §5 — the GUI must NEVER grow these)

| Capability | Why not in the GUI |
| --- | --- |
| `unlock` | interactive-tty-only by design; a browser click is too cheap for breaking a seal |
| `gc --hard` | destructive; tty-only |
| `pack` / `unpack` | filesystem-level import/export of whole projects; shell territory |
| raw `git` beyond gitops panel (rebase, push) | git is the patch engine; plumbing stays in the shell |

## The capability matrix (CLI ↔ GUI affordance)

| Capability | CLI | GUI affordance (round; ✱ = round S) |
| --- | --- | --- |
| Project status / next step | `status` | 首页 Cockpit：唯一“现在最值得做” + 创作/镜头/成片三条只读旅程 + 待处理/运行中/最近成果；完整镜头、构建、时间线、任务、事件和版本按需展开 |
| Shots: view, states, why-stale | `status`, `explain` | shot cards + why-stale chips (R) |
| Shot production journey | `status`, `import`, `select` | 分镜规划 → 镜头实验室或批量入库 → 人工审片；shot deep-link 保持上下文，候选不自动选用、评价不自动锁片 |
| Shot fields editing | edit YAML | keystroke-validated editor (R17) |
| Shot order | edit index.yaml | ✱ reorder controls (S8a) |
| Takes: preview/compare/select/rollback | `select`, `rollback shot` | cards + A/B compare (Q/R) |
| Director notes / verdicts | edit YAML | 好/弃 chips + notes (R) |
| Generate: build/redo/voice | `build`, `redo`, `voice` | job buttons (R) |
| **Pre-generation explanation** | `build --dry-run`, `explain` | ✱ plan modal before every priced run: shots, providers, why, cost (S8a) |
| Spend gate | `--yes` | confirm dialog (R, live) |
| Batch operations | ✱ `redo --shots/--all-stale`, `voice --all` (S-E) | ✱ multi-select + bulk bar (S8a) |
| QC run + findings | `qc` | QC panel (Q/R) |
| **Human visual review** | `board` | ✱ review page: sequential shot review, frames + takes, 好/弃/redo/note, keyboard (S8b) |
| Repair ops | `repair --op …` | ✱ per-finding repair buttons on review page (S8b) |
| Providers: list/add/check | ✱ `providers …` (S-A) | ✱ providers page: status, doctor probes, add-from-template (S8b) |
| Routing strategies | ✱ `route …` (S-A) | ✱ read-only routing view + strategy picker (S8b) |
| Asset library | ✱ `lib …` (S-D) | ✱ library page: browse/preview/use-into-project (S8b) |
| Version compare | ✱ `compare` (S-D) | ✱ finals diff page: side-by-side + per-shot change map (S8b) |
| History / snapshot / rollback file | `history`, `snapshot`, `rollback` | git panel (R) + ✱ snapshot/rollback buttons (S8a) |
| Exports / package | `export`, `package` | 剪辑→字幕→混音→包装→导出共用连续成片流程与当前 final/锁片资格只读状态；导出中心按观看/精剪/字幕/平台草稿/包装分组；OTIO/FCPXML 单独显示载体与回程基线，只有“可安全回收”才允许可靠 round-trip；packaging editor lite (S8a) |
| Spend / tasks | `spend`, `tasks` | spend view (R) + ✱ tasks view (S8a) |
| Doctor | `doctor` | /api/doctor (R) + ✱ doctor page w/ fix hints (S8b) |
| Dependency-graph diagnostics | `explain --graph` (DR03B) | 依赖图 (Graph) button → summary chips, issues, per-node root cause (`/api/explain?graph=1`); read-only, opt-in like the flag |
| **Failure clarity** | ✱ structured failures everywhere (S-C) | ✱ failure cards: step, cause, hint, log link, retry (S8a) |
| **First-run guidance** | — (README) | ✱ onboarding checklist: 建项目→写剧本→生成→审片→导出, dismissable, per-user state (S8a) |
| Bible view/edit | edit YAML | bible panel read-only (Q) → ✱ guarded edit w/ lock awareness (S8a, locked fields read-only) |
| Rules / packaging YAML | edit files | ✱ raw-YAML editors with validate-on-save (S8a) |
| New project / presets | `new --preset` | ✱ new-project dialog in workspace switcher (S8a) |

## Interaction invariants (every ✱ feature obeys)

1. Same core functions as the CLI; every mutation records the same event.
2. Locks respected: locked fields render read-only with the 🔒 and a
   propose-instead hint; the GUI never writes through a lock.
3. Mutations hold the in-process lock; builds/redos the cross-process lock.
4. Priced actions surface the plan first (§8.3 + item 5): dry-run → modal →
   explicit confirm; never a silent spend.
5. Failures render as structured cards (step/cause/hint/log), never toasts
   with bare exception text (item 10).
6. Human assets and human decisions are never overwritten silently — batch
   ops skip locked/manual shots and say so.
