# GUI 方向计划 (2026-07-14) — 个人视频生产操作台 (#50)

Mandate: the owner supplied a direction document ("收敛成一套高度偏向个人
习惯的视频生产操作台") with instructions to think independently first,
verify the material objectively, then proceed. 10-hour budget.

## Independent position (formed before consulting the document)

The GUI's remaining value is not more surface — it is delivering the
engine's EXISTING intelligence (#44 per-shot todo, #45 next_step_key,
#46 上次动作) at the moment the owner opens the tool, and sharpening the
one attention bottleneck of AI video production: judging generated takes.
The document's core thesis matches; several of its factual premises were
stale against this tree and were corrected rather than followed:

- "已阅快照用项目名作键" — already fixed in #49a (identity token).
- "审片没有单条模式" — 队列模式 has existed since round X; what was
  missing is the default, the priority ORDER, verdict-advance parity and
  undo.
- "/edit 没有入出点" — the edit page has had I/O trim since W-era.
- "picker 不用 pinned/last_opened" — it sorts by both already.

## Landed (each verified live; 20 wave-probe checks + harness 21/21)

| # | item | what landed |
|---|------|-------------|
| D-1 | 审片 = 最强页面 | 队列模式成为有未审内容时的默认(显式开关才落偏好);队列按"最阻塞优先"排序(无 take 的殿后 — 无可判);好 与 通过 一样自动进下一条;`u` 撤回刚才的好/弃(恢复被覆盖的备注 defaultValue 与已审状态);图例补 `u 撤回` |
| D-2 | 首页 = 继续工作页 | `继续上次工作` chip(common.js 每页记录 manju-last-<identity>,首页永不覆盖轨迹;/review 位置直接拼进标签);cockpit 状态计数从统计变队列 — 点击即筛选分镜网格并跳转 |
| D-3 | 交给 Claude | /review 卡片 `复制给 Claude`(镜头/选用/状态/备注/文件清单/目标模板);工作台失败卡 `复制诊断上下文`(步骤/原因/证据/日志/任务/镜头文件)— AI 留在 GUI 外(§0),GUI 只递干净上下文 |
| D-4 | 六组导航 | 工作台·创作·镜头·审片·成片·工具箱 — 纯呈现层:所有 17 条链接留在 DOM(CSS 悬停/聚焦下拉,ws-menu 同语言),_NAV 仍是唯一标签 owner,新手模式隐藏项与单页组塌缩(审片→审片,工具箱→素材库)行为不变,aria-current 唯一 |

The queue-restore interaction paid for one real bug mid-wave: the
restored 上次位置 card was clobbered by setQueueMode's own updateQueueUI
(queue head sync) — the card is now captured BEFORE entering queue mode;
the probe pins the reload-restore live.

## Dispositioned from the document, not landed (with reasons)

- 日常/维护双模式取代新手/专业 — the mode system is deeply pinned
  (glossary/mj-*); the grouped nav delivers the same daily reduction
  without re-keying the modes. Revisit only if the owner asks.
- 固定个人按钮(补齐项目/输出最终版…) — the workbench build panel
  already runs exactly these through the plan modal; the cockpit hero
  already names the ONE next action. Duplicate entrances fork owners.
- AI 提案收件箱强化 — proposals panel + director approval flow exist;
  nothing bounded left to add this wave.
- 中英双写全面收敛 — exact bilingual strings are pinned across the test
  suite ("选用 (Select)" etc.); the glossary toggle already handles the
  vocabulary layer. Wholesale relabeling = churn against pins for a
  cosmetic gain. Below the line.
- 审片自动连播/限时快审、跟随构建、拖拽分镜、虚拟化、Electron/Tauri、
  账号/云/协作/插件市场 — unchanged rejections (#45/#49a; the document
  itself rejects most of the same).
- `manju gui --app` 薄壳 + Windows 通知 — deferred: touches the frozen
  CLI surface + Windows-only launch behaviour untestable in this
  environment; bounded and worthwhile as its own small wave on a Windows
  session.
- take preload=none — re-rejected: preload="metadata" paints the grid
  thumbnails; posters only exist where server thumbs ride along.

## Verification

- 5 new pins (test_ux_polish.py, 87 total), red-first by stash.
- 20/20 direction-program live checks (queue default/order/advance/undo/
  persist, nav groups + hover menu + beginner collapse + unique
  aria-current, clipboard content of both AI-handoff buttons, continue
  chip target + position, strip-count filter click); wave-2 probe 19/19
  re-green after the restore-clobber fix; harness 21/21.
- Union GUI batch 401/1/0; full suite green (count in DECISIONS #50).
