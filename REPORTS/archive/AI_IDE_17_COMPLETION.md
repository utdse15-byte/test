# AI_IDE_17 — 完成报告 (Completion)

日期:2026-07-11 · 契约:系列世界状态、身份变体、跨集连续性与可移植模板包
纪律:文本事实源 · 三层严格分离 · 无全局资产 DB/市场/CRDT · 未 commit/push
未触碰:DECISIONS.md / README.md / tests/fixtures/ / golden/

## 交付概览(3 生产文件,预算 ≤8)

| # | 文件 | WP | 性质 |
|---|---|---|---|
| 1 | `core/series_state.py` | WP1(variants)/WP2/WP3 | 新增 |
| 2 | `build/seriespack.py` | WP1(packs)/WP4/WP5 | 新增 |
| 3 | `cli.py` | `series status --health` 折入 + `series outline` | 追加式编辑 |

测试:`tests/test_c17_series.py`(12)+ `tests/test_c17_packs.py`(14)= 26 用例,§10 十三行 1:1。

## 三层状态(按存储位置严格分离,ruling 1)

- **canonical** = series bible(既有 sync-bible 机器所有;本批**零写入**该层)。
- **variant** = bible 条目上追加式 `variants:` 列表(§3 形状:variant_id/valid_from_episode/valid_to_episode/changes/reference_roles/voice_profile_ref)。纯校验器 `variant_diagnostics`:VARIANT_ID_MISSING/DUPLICATE、UNKNOWN_EPISODE、RANGE_INVALID(按 series.yaml 顺序)、VARIANT_OVERLAP(重叠但字段不相交=提示)、VARIANT_CONTRADICTION(重叠且同字段异值=阻断级)、UNKNOWN_VOICE_PROFILE_REF。**解析确定性**:`active_variant` 取范围内 valid_from 最晚者,平局按 variant_id 字典序;`resolve_entry_for_episode` 产出带 `layer` 标注的派生视图(canonical 原文件不动,transient 明确不在此视图)。
- **transient** = 分集 source + accepted observations,绝不全局存储。`continuity_packet(series, eid)`:从 08_10_12C `accepted_observed_state` 派生该集各镜头 status/authored_locks/observed_endpoint + 截至该集的 world state;`layer="transient"`、`advisory=True`,**pin**:推导前后 series bible 与 episode bible 逐字节不变(accepted ending 永不自动升级为永久事实)。

## World state 源(WP2)

`<series>/world_state.yaml`(文本真相,Skill/Director 提 patch):entries `{id, subject, change{field:value}, effective{episode[,scene,shot]}, known}`。引擎只做 `world_state_diagnostics`:ENTRY_ID_MISSING/DUPLICATE、UNKNOWN_SUBJECT(bible 无此 id)、UNKNOWN_EPISODE、STATE_CONFLICT(同 subject+field+集异值)。派生视图 `world_state_at`:按(集序,文件序)应用,后声明的 patch 胜出,known/unknown 原样携带,未注册集返回 error。

## 季健康(WP3,纯聚合)

`season_health(series)`:逐集 verbatim 消费各唯一所有者——07C `release_assessment`(ready/blockers)、P0 `unresolved_submissions`(SQLite 投影;**DB 缺失→None+「可重建投影」说明,不臆造 0**,§10.11)、15 `drift_trend`、`refs_report` 缺失数、`list_locales`、`spend_report`(actual/currency/budget_limit)+ 系列级 variant/world-state 诊断。**Pin**:`ready` 仅当所有集可读且 release-ready 且无未决提交;任何 stale/blocked/unreadable 集都进 `not_ready` 点名(E02 删 project.yaml → season ready=False 且 E02 被点名,测试钉住)。挂载:`manju series status --json --health` 追加 `season_health` 键;不加 `--health` 时输出与旧版逐键一致(§10.1 钉住);无新命令组。

## EpisodeOutlinePackage(WP4)

`manju.episode-outline-package/v1`:`source_script` + episodes[{id, title, target_duration_s, source_span{start_line,end_line}, cliffhanger?, hook?, characters, locations}]。复用 DR03A 安全原语(`_scan_secrets`/`_reject_unsafe_paths`)+ schema 校验。引擎唯一创作管辖=span:SPAN_MISSING/INVALID/OVERLAP_OR_DISORDER/OUT_OF_RANGE + EPISODE_ID_INVALID/DUPLICATE。**inspect 零写入**(rglob 快照前后一致,钉住);apply 有诊断即拒绝,创建走**既有** `new_episode` 路径(series.yaml 注册),script 切片写入沿用 split-script 已编辑拒覆盖纪律。**§10.13 pin**:cliffhanger/hook 只是建议字段,verbatim 携带,缺省保持 None——引擎从不发明。CLI:`manju series outline <pkg> [--apply] [--json]`。

## Identity reference pack + copy-on-import(WP1)

`build_reference_pack`:显式 items → pack 目录(`pack.yaml` 文本索引 + `media/` 内容寻址字节)。逐项记录 role(front/profile/…)、`controls`/`ignore`(=must_not_transfer;对既有 `REF_TRANSFER_VOCAB` 校验,未知维度/controls∩ignore 冲突→拒绝该项)、provenance{source,license_or_consent,note}+sha256;缺权利→`rights_missing` 标旗(下游模板列名被 18 闸拒)。`import_reference_pack`:hash 逐项复核(被改 pack→拒该项)、字节复制到 `media/refs/imported/<sha16><ext>`(append-only 内容寻址)、索引改写为项目相对路径落 `bible/refpacks/<pack_id>.yaml`、origin pack digest + provenance 全程携带(§10.12 跨项目可追溯,钉住)。**Pin(§10.7)**:导入后改写/损毁外部 pack,项目内字节与索引不受影响(钉住)。

## Template pack(WP5)+ 语音授权闸证明

`export_template_pack`:只从**显式选择**导出(bible 子集按 id 点名、style、rules.yaml、delivery_profiles、voice_profiles、media 内容寻址)。**闸证明**:每个 voice profile 过 18 的 `template_export_gate`——无 `voice_provenance{source,license_or_consent}` → 列入 `refused_voice_profiles`(带 gate reasons)且 `voice/profiles.yaml` 不入包(测试钉住);有权利 → 打包并在 manifest.selections 列名。打包纪律=13C:arcname 路径安全+去重、成员稳定排序、SHA256SUMS 由**写入时流式摘要**生成、SECRET_PATTERNS 扫 manifest、临时文件+replace 原子落盘。`inspect_template_pack`:只读、逐成员校验 SHA256SUMS(篡改→mismatches,导入拒绝,钉住)。`import_template_pack`:先验校验和;bible 冲突显式三策略 abort(默认,分毫未写)/skip(保本地)/rename(`<id>_imported`)——**绝不静默覆盖**(三态全钉);媒体内容寻址 append-only;导入记录(pack digest/origin/license/conflicts)落 `bible/refpacks/template_import_<digest12>.yaml`。无市场:公共 API 面无 rate/rating/favorite/star/market/social(钉住)。

## Pins 汇总(§10 十三行 → 测试)

1 旧行为不变(status 键集合+无 season_health 泄漏)· 2 variant 集范围解析 · 3 OVERLAP/CONTRADICTION 诊断 · 4 canonical/transient 不混淆(layer 标注+bible 零写)· 5 accepted observation 不写全局 Bible(digest 前后相等)· 6 pack 路径/许可/hash(词表校验+rights_missing+sha)· 7 copy-on-import 外部变化 inert · 8 季健康聚合 current evidence · 9 stale/unreadable 集不被 season ready 掩盖 · 10 outline 零写 inspect/确认 apply · 11 删 SQLite 可解释(None+说明)· 12 跨项目 provenance 可追溯(origin digest 链)· 13 cliffhanger/hook 仅提案(verbatim,缺省不发明)。另:语音授权闸两态、导入冲突三策略、SHA256SUMS 篡改拒绝、span 诊断拒 apply。

## 偏差/跳过(SKIPPED_WITH_EVIDENCE)

1. **模板包未含 pacing/hook 规则与 preview clip 的专门字段**:hook/cliffhanger/pacing 属 Skill 建议(契约 §9「Skill 建议,不是引擎硬事实」),已由 outline 的 advisory 字段与 rules.yaml(pacing 所在地)覆盖;preview clip 可作为 media_files 显式成员打包(通用内容寻址通道已在),未加专用角色字段——无既有 preview-role 锚点。
2. **prompt recipe refs**:项目内 prompt 配方无独立文件锚(promptlab 为派生报告),显式选择时可经 media/bible 通道进包;未发明新 schema。
3. **world_state 的 scene/shot 级生效粒度**:字段被携带(effective{scene,shot} 保留原样)但校验只到 episode 粒度——分集 scene/shot id 属分集项目命名空间,系列级校验它们需打开每集做 id 解析,属推测性基础设施,证据:`world_state_diagnostics` 只按契约「ID、顺序、冲突和生效范围」做了 episode 级。
4. `series continuity` 未改动:跨集视觉/voice drift 聚合已存在(15/AA7),季健康直接引用其所有者函数,不重复。

## 套件结果

- `tests/test_c17_series.py` + `tests/test_c17_packs.py`:26 passed。
- 触碰面回归:`-k "series or split or cli"` 283 passed。
- 全量套件:见最终回报。
