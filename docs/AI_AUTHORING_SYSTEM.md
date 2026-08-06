# AI Authoring System

本文件是 Manju 的仓库内自足创作说明。只读本文件和一个 `.manju` 项目，就应能判断作者事实放在哪里、引擎能做什么、哪些证据允许推进生产，以及为什么一次成功构建不等于锁片。

Manju 引擎不调用 LLM：human/agent 负责导演判断，CLI 负责合同校验、确定性投影、批准过的 provider 执行、媒体登记、验收日志、装配与导出。引擎不替导演决定故事、表演、选择或 Picture Lock。

## 真相边界

项目真相是 YAML/Markdown/JSON 和登记过的媒体字节：

- `story/*.md`：故事、结尾、梗概、节拍和剧本；
- `story/scenes/<scene_id>.yaml`：SceneContract；
- `shots/<shot_id>.yaml`：ShotSpec，以及嵌入其中的 ShotContract、selected take 和人工决定；
- `shots/index.yaml`：唯一镜头顺序；
- `bible/*.yaml`：角色、地点资产、道具、风格和声音；
- `media/imports/`：永不改写的人类原件；
- `media/gen/<shot>/`：只增不改的 take、sidecar、prompt 和运行证据；
- `reports/verifications.jsonl` 与媒体绑定 verdict：人工批准和真实媒体观察。

SceneContract 位于场次源文件，ShotContract 嵌入 ShotSpec；`shot.scene` 保持地点资产语义，戏剧场次引用使用 `ShotSpec.scene_id`。场次成员由 `shots/index.yaml` 顺序与 `scene_id` 派生，SceneContract 不拥有 `shots` 列表。

reports 汇总、ExpectationSet、prompt bundle、readiness view、timeline 和 render 都是派生物，不能成为下一轮编译输入。删掉派生物后应能从文本真相与已登记媒体重新生成。

新项目包含两个可解析示例：

```text
story/scenes/SCENE_EXAMPLE.yaml.example
shots/SHOT_EXAMPLE.yaml.example
```

`.yaml.example` 不启用叙事门禁。把场次示例复制/改名为真实的 `story/scenes/SC001.yaml`，并让镜头通过 `scene_id: SC001` 引用它，才是显式 narrative opt-in。旧项目没有 `story/scenes/*.yaml` 时保持 `LEGACY`，无需迁移。

媒体观察必须绑定 shot/take、字节 SHA-256、spec/contract digest、source revision、观察者和 evidence refs。观察不到就写 uncertain；accepted take 的瞬态 endpoint 可以驱动下一镜 opening，但不会静默变成人物 Bible 事实。

## 创作链

```text
brief → ending → scene map → script → SceneContract → ShotContract
→ asset/motion test → TEMP_AUDIO_FOR_ANIMATIC → Animatic
→ Proof Shot → Proof Scene → generation/review/experiment/source rewrite
→ picture lock → deterministic assembly → FINAL_AUDIO_FINISHING → delivery
```

Narrative Beat、Generation Beat、Edit Beat 分开：

- Narrative Beat：人物/关系/知识/意图发生什么变化；
- Generation Beat：当前实验要让模型生成什么可见变化；
- Edit Beat：镜头在剪辑中的进入、停留和离开作用。

Prompt 只做控制分配和执行指令编译，不重新决定场次目的或人物心理。一次生成只验证一个主要不确定性；失败优先换控制来源、调度、剪辑或局部修复。下一镜从 accepted media 的 observed endpoint 续接，不从 Prompt 预期的 endpoint 猜测。

## SceneContract 与 ShotContract

SceneContract 至少要能回答：

```text
id / title / location_ref / time / purpose
entry_state / irreversible_change / exit_state / carry_forward / proof_scene
```

人物状态可记录 `knowledge`、`intention`、`emotion_residue`、`body` 和 `props`。这里保存戏剧变化，不重复地点 Bible，也不保存镜头列表。

ShotContract 位于 `shots/Sxxx.yaml: contract`，至少要能回答：

```text
purpose / viewer_must_perceive / opening / endpoint
performance.required|avoid / physics.required|avoid / sound
control.production_method|motion_source|primary_uncertainty
risk.primary|fallback_staging / acceptance / proof_shot
```

`action.main` 保存主要可见动作。Prompt、picture spec、QC expectations 和 director view 都从同一个 ShotSpec/ShotContract 投影，不能各自再写一份导演意图。

## Provider 知识

稳定导演知识在 `skills/*/SKILL.md`。易变 provider 知识只读当前 `ProviderManifest` 的 capabilities、limits、refs、cost、adapter，以及带日期、来源、owner 的 `authoring` evidence。没有 current evidence 时，negative 语法、参考数量、时长、seed 和型号公式都保持 unknown。

## 生产闸门

`manju production status --json` 派生以下阶段：

```text
AUTHORING → PROOF_SHOT_READY → PROOF_SCENE_READY → BULK_READY
```

Animatic approval 绑定当前路径和 exact bytes；Proof Shot/Proof Scene 绑定合同、媒体摘要、选择顺序、声音/voice/timing，以及同一条 accepted verdict 的 packet、稳定 evidence digest、canonical observed opening/endpoint 与 assurance projection。动态评审时间戳不进入 digest；endpoint/opening 改变会使旧 approval 失效，stale/rejected evidence 不会进入。`--yes` 只确认花费，不能替代内容批准。没有真实 provider、素材和人类批准时只能报告未验证。

阶段含义：

- `AUTHORING`：合同或当前 Animatic/人工批准尚未齐；
- `PROOF_SHOT_READY`：只允许推进声明为 proof shot 的付费镜头；
- `PROOF_SCENE_READY`：只允许推进 proof scene 的有序镜头；
- `BULK_READY`：proof-before-bulk 门禁已通过，可扩大付费生成范围；
- `LEGACY`：未显式使用 SceneContract，不启用新的叙事付费门禁。

`BULK_READY` 只回答“可否扩大生成花费”，不回答“是否已经 Picture Lock”。后者由当前 selected media 的资格派生。

## 媒体资格与 Picture Lock

所有 CLI、GUI、workflow、skill index 和 auto playbook 使用同一组词：

- `proxy-only`：provider 是 `caption_card` 或 `comic_panel`。可验证 pipeline plumbing、节奏占位和确定性装配，但永远不能证明最终画面；
- `candidate`：已登记的非代理视频候选，但当前性、人工 `review=approved`、assurance accepted 或当前 expectation digest 仍有缺口；
- `final-eligible`：当前 selected video、非代理、媒体可读且 current、人工批准、assurance accepted，并绑定当前 expectation digest；
- `none`：尚无已选中的登记视频，不冒充 candidate。

只有所有目标镜头都为 `final-eligible`，项目视图才会显示 `Picture Lock eligible`。这仍是“有资格进入人工锁片评审”，不是引擎自动锁片。

`build ok` 不等于 Picture Lock。fallback、caption card 或 deterministic render 即使成功产生 `renders/final/final_vN.mp4`，也不会把 `proxy-only`/`candidate` 自动升级为 `final-eligible`。运行：

```text
manju status
manju production status
manju production status --json
```

可看到当前 stage、三类媒体数量和 Picture Lock eligibility。readiness/eligibility 是纯派生视图，不写第二份状态文件。

## 音频状态

`TEMP_AUDIO_FOR_ANIMATIC` 验证对白、呼吸、停顿、声音事件和播放节奏；它不等于最终 voice、mix、响度或交付。`FINAL_AUDIO_FINISHING` 只能在 picture lock 后重新校验 voice hash、对白、混音、字幕和交付格式。

## 推荐工作循环

```text
manju status --json
manju events --tail 30
manju production status --json
manju check
manju prompt <shot> --json
manju build --target animatic
manju production approve-animatic <animatic> --reason <why>
manju build --dry-run
manju qc
git diff
```

生成和评审顺序：

1. 写故事与结尾、SceneContract、ShotContract；
2. 建资产/运动测试与 `TEMP_AUDIO_FOR_ANIMATIC`；
3. 构建并由人批准 exact Animatic；
4. 只做 Proof Shot，观察真实 opening/change/endpoint；
5. 连续观看并批准 Proof Scene digest；
6. readiness 到 `BULK_READY` 后才扩大付费生成；
7. 对 candidate 做媒体绑定 review/experiment/repair/rewrite；
8. 所有目标镜头 `final-eligible` 后由人决定 Picture Lock；
9. 再做 deterministic assembly、`FINAL_AUDIO_FINISHING` 与 delivery。

锁字段走 `manju director propose`；不 unlock、不覆盖既有媒体、不直接调用付费 Provider。完整技能入口见 `manju skills`，十个结构化最低导演判断见 `skills/evals/cases/`。没有真实 provider、真实素材、真实人工批准或 owner evidence 时，必须写“未验证”，不能用 caption-card 样片替代真实叙事 Dogfood。
