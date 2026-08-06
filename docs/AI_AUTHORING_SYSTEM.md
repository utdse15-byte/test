# AI Authoring System

本文件是 Manju 的仓库内自足创作说明。Manju 引擎不调用 LLM：agent 负责导演判断，CLI 负责合同校验、确定性编译、媒体登记、验收日志和装配。

## 真相边界

项目真相是 YAML/Markdown/JSON 和登记过的媒体字节。SceneContract 位于场次源文件，ShotContract 嵌入 ShotSpec；场次成员由 `shots/index.yaml` 顺序与 `ShotSpec.scene_id` 派生，SceneContract 不拥有 `shots` 列表。reports、expectations、prompt bundle、readiness view 都是派生物，不能成为下一轮编译输入。

媒体观察必须绑定 shot/take、字节 SHA-256、spec/contract digest、source revision、观察者和 evidence refs。观察不到就写 uncertain；accepted take 的瞬态 endpoint 可以驱动下一镜 opening，但不会静默变成人物 Bible 事实。

## 创作链

```text
brief → ending → scene map → script → SceneContract → ShotContract
→ asset/motion test → TEMP_AUDIO_FOR_ANIMATIC → Animatic
→ Proof Shot → Proof Scene → generation/review/experiment/source rewrite
→ picture lock → deterministic assembly → FINAL_AUDIO_FINISHING → delivery
```

Narrative Beat、Generation Beat、Edit Beat 分开。Prompt 只做控制分配和执行指令编译，不重新决定场次目的或人物心理。一次生成只验证一个主要不确定性；失败优先换控制来源、调度、剪辑或局部修复。

## Provider 知识

稳定导演知识在 `skills/*/SKILL.md`。易变 provider 知识只读当前 `ProviderManifest` 的 capabilities、limits、refs、cost、adapter，以及带日期、来源、owner 的 `authoring` evidence。没有 current evidence 时，negative 语法、参考数量、时长、seed 和型号公式都保持 unknown。

## 生产闸门

`manju production status --json` 派生以下阶段：

```text
AUTHORING → PROOF_SHOT_READY → PROOF_SCENE_READY → BULK_READY
```

Animatic approval 绑定当前路径和 exact bytes；Proof Shot/Proof Scene 绑定合同、媒体摘要、选择顺序、声音/voice/timing 和 human actor/reason。`--yes` 只确认花费，不能替代内容批准。没有真实 provider、素材和人类批准时只能报告未验证。

## 音频状态

`TEMP_AUDIO_FOR_ANIMATIC` 验证对白、呼吸、停顿、声音事件和播放节奏；它不等于最终 voice、mix、响度或交付。`FINAL_AUDIO_FINISHING` 只能在 picture lock 后重新校验 voice hash、对白、混音、字幕和交付格式。

## 推荐工作循环

```text
manju status --json
manju events --tail 30
manju production status --json
manju check
manju prompt <shot> --json
manju build --dry-run
manju qc
git diff
```

锁字段走 `manju director propose`；不 unlock、不覆盖既有媒体、不直接调用 Provider。完整技能入口见 `manju skills`，十个结构化最低导演判断见 `skills/evals/cases/`。
