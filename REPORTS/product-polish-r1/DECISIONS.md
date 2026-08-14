# Product Polish R1 Decisions

## Scope

The guide was treated as evidence, not a checklist. The repository already had
shared navigation, cockpit data, onboarding, review, export, app-window launch,
and zero-cost transport gates. Rebuilding those surfaces would add risk without
improving the daily loop.

## Evidence-led choice

The local `manju new --demo` project was opened through the real GUI server and
checked in the in-app browser at 1280x720 and 390x844.

- Warm `/api/state`: 9.9-12.7 ms; warm `/api/cockpit`: 96-101 ms.
- Before polish, the real build controls began around y=1407 px.
- The home page showed the cockpit's live six-step progress, then auto-opened a
  second full onboarding checklist, then displayed low-value empty telemetry and
  a long evaluation/honesty report before the build panel.
- The 390px pass had no horizontal overflow, so responsive CSS was not the first
  problem to solve.

## Changes

1. The cockpit remains the automatic guide. The full onboarding checklist is
   still available from the header/cockpit help action, but is no longer auto-
   fetched and rendered a second time.
2. Empty-ish projects stop the cockpit support grid after the progress checklist.
   Deliverable/queue/approval/evaluation details appear after real work exists.
3. Evaluation is a collapsed, professional-mode detail. Beginner mode does not
   fetch a panel it is explicitly hiding.
4. The project header removes its temporary spend/next-step fallback once the
   cockpit arrives, preventing normal-state duplication while retaining a
   useful fallback during cockpit loading/errors.
5. The current execution policy is carried in `/api/state` and shown as a small
   chip: `严格零成本`, `标准执行`, or `执行模式无效`. Unknown non-empty values
   fail closed before provider transport or credential inspection.

## Result

After polish, build controls began around y=641 px in standard mode and y=603 px
in strict zero-cost mode. The measured pages had zero horizontal overflow and
no browser console warnings/errors. Strict mode displayed the loopback-only,
credential-free policy directly in the UI.

Screenshots:

- `screenshots/before/home-desktop.png`
- `screenshots/before/home-mobile.png`
- `screenshots/after-home-desktop.png`
- `screenshots/after-home-strict-zero-cost.png`

## Wave 3 — 交换文件成功不等于可安全回收

1. Carrier 与 baseline 是两个独立事实。baseline 是可删除派生证据，但缺失时不能对外宣称 round-trip ready。
2. Exporter 保留可用 carrier，不因 baseline 写入失败撤销单向交接；失败必须进入同一次 CLI/GUI 结果或 Python warning。
3. `plan_roundtrip` 在没有 T0 时可以只读分析，但其行不具备 apply 权威。核心 `apply_roundtrip` 自己拒绝，不把安全寄托在某个客户端。
4. Baseline 查找按 carrier kind 隔离；同 stem 不是同证据。
5. 导出中心按用户目标组织，OTIO/FCPXML 的 safe-recovery 状态优先于格式列表。
6. 本波不建立新真相、不改变人工选择、不做真实付费或网络测试。


## Wave 6 — Review Theater is a projection, not a new review system

1. `status.take_notes[selected_take]` remains the current candidate verdict owner; the page may parse its `好 / 弃 · rationale` prefix for presentation but must not create a parallel review store.
2. Candidate verdict, `selected_take`, shot approval and Picture Lock are four independent facts. No button or keyboard shortcut may collapse them into one action.
3. Queue priority is a session projection derived from current shot/take/QC/annotation facts. Its order and browser position are deletable UI state and never build input.
4. Review progress counts only the current selected candidate. Historical notes on an old take are evidence, not proof that the new selected take was reviewed.
5. QC frames, routing, repair and external-IDE context are evidence/secondary actions and belong behind progressive disclosure. The current candidate and human decision remain primary.
6. Narrow layout may reorder the queue before the card and make it horizontal, but must preserve all six production stages, current-shot visibility and zero whole-page horizontal overflow.
7. The existing one-main-video/lazy-alt/expand-to-compose consistency-board performance contract remains authoritative; the polish wave does not buy visual density with extra media work.
