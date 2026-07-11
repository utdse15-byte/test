# AI_IDE_17 — WP0 存量审计基线 (Baseline)

日期:2026-07-11 · 分支:`claude/cost-optimization-strategy-cjfmn5`(基于 17f8b86)
契约:系列世界状态、身份变体、跨集连续性与可移植模板包
方法:只读审计,未改动任何文件。门槛裁定:series 子系统是已落地的真实代码 → 门槛读作 scope 纪律(只绑定既有 series 机器,凡无锚点者 SKIPPED_WITH_EVIDENCE),非整体跳过。

## series 子系统现状(全部真实存在,file:line)

| 面 | 位置 | 事实 |
|---|---|---|
| series root/index | `core/series.py:54` `SERIES_FILE="series.yaml"`;`SeriesConfig`/`EpisodeRef`(:101/:131,extra=allow,id 经 `_EID_RE` 校验防路径逃逸);`Series.find` 会越过 episode 的 project.yaml 向上找 series.yaml(:153) | series.yaml 顺序即分集顺序权威(`episode_ids` :254) |
| global bible sync | `Series.create` 用与项目相同的 BIBLE_FILES 铺全局 bible(:187);`new_episode` 从 series bible 播种分集 bible(:345);`sync_bible`(:613)diff/apply + locked 冲突拒绝 | canonical identity 层的存储位置=series bible;分集拥有自己的副本 |
| episode project identity | `episode_project_dir` = `episodes/<eid>.manju`(:223);`open_episode` 即普通 Project(:266) | 分集与单项目命令完全兼容 |
| split-script | `split_script`(:1065):只按显式 `# E0N` 标记确定性切分;apply=False 零改动;已编辑脚本拒绝覆盖需 `--force <eid>`(round-W #75) | WP4 的 inspect→confirm→apply 纪律样板 |
| series status | `series_status`(:463)复用 `_episode_build_summary`(:433 = evaluate_all + newest_final_path + spend_report);逐集降级 `{"error"}` 不炸表 | WP3 季健康的挂载点(`--json` 追加式) |
| 连续性看板 | `series_continuity`(:855):结论优先级 有问题>缺素材>待同步>完整;`_asset_continuity_matrix`(:529)一台引擎;`_voice_divergence`(:815)钉 VOICE_BIBLE_KEYS | 跨集视觉/语音漂移聚合已存在——季健康只再聚合,不重推 |
| CLI | `cli.py:6305` `series_app`(new/status/episodes/new-episode/sync-bible/characters/continuity/split-script) | 已有命令组;WP3 只加 `--health` 选项、WP4 加 `outline` 子命令(不建新命令组) |

## 消费面(本批绑定的既有机器)

- **18(17f8b86)**:`build/voiceid.py:template_export_gate`(缺权利→blocked)——WP5 模板包必须调用;`character_profile`/`provenance_complete`。
- **16 refs transfer 契约**:`providers/refs.py:428 REF_TRANSFER_VOCAB`(12 维度)+ 绑定条目 `{controls, ignore, subject_ref}`——契约的 controls/must_not_transfer 即 controls/ignore 词表,WP1 直接复用。
- **07C**:`build/baseline.py:759 release_assessment`(ready/blockers)——季健康逐集 verbatim 消费。
- **13C**:`build/delivery.py` bundle 纪律(`_bundle_members` 路径安全+去重、`_sha256sums_from` 流式摘要、原子写)——WP5 打包安全样板。
- **08_10_12C**:`qc/production.py:237 accepted_observed_state`(accepted 才有观测;从不回写)——WP2 continuity packet 的证据源。
- **15**:`qc/production.py:596 drift_trend`——季健康 verbatim 折入。
- **DR03A**:`build/shotpackage.py`(schema major、`_scan_secrets` :162、`_reject_unsafe_paths` :198、inspect→apply)——WP4 EpisodeOutlinePackage 的流程样板,helpers 直接复用。
- **P0/DR06**:`runtime/state.py:572 unresolved_submissions`(SQLite 可重建投影;DB 路径 `.manju/state.sqlite`)。
- **spend**:`build/spend.py:spend_report`(total/currency/budget_limit)——预算 vs 实际。

## 缺口(本批要补)

1. 无 §3 三层语义:variants 无 schema/checker/解析;transient 与 canonical 无显式分层标注。
2. 无跨集 world state 源文件与纯校验(ID/顺序/冲突/生效范围)。
3. 无 continuity packet(accepted ending → 下一集派生包)。
4. 无季健康聚合(release/未决提交/drift/refs/variants/locale/预算)。
5. 无 EpisodeOutlinePackage(集粒度 inspect→confirm→apply)。
6. 无 identity reference pack 与 copy-on-import。
7. 无 template pack 导出/导入(含 18 语音授权闸)。

## 关键约束(审计确认)

- 不建 SeriesDB/全局可变资产库/市场/CRDT/多人权限;跨集事实仍是文本。
- variants 是 series bible 条目上的**追加式** `variants:` 列表(bible 条目自由 dict,零 models.py 改动)。
- 季健康**纯聚合**每集 current evidence,任何一集 stale/blocked/unreadable 都不被 season ready 掩盖。
- SQLite 缺失(被删)→ 可解释(None+说明),不臆造为 0,不重建副作用。
- 预算 ≤8 生产文件;方案:2 个新模块(core/series_state.py、build/seriespack.py)+ cli.py 追加式编辑 = 3 个。
