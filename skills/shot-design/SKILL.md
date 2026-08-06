---
name: shot-design
description: 镜头合同设计：把一个可见动作和下一镜接口写成 ShotContract，并选择合适的生产/控制来源。
when_to_use: 当需要拆镜、改镜头目的、opening、endpoint、表演、声音、风险或实现方式时使用；不负责整场叙事。
tags: [directing, task]
user_invocable: true
---

# Shot Design

先设计观众要看见的变化，再决定相机。每个 Generation Beat 只承担一个主要动作或不确定性。

## 固定决策顺序

1. narrative purpose
2. viewer must perceive
3. opening state
4. one main visible change
5. endpoint / next-shot interface
6. performance and physical requirements
7. sound event and timing
8. production method / control source
9. primary risk
10. alternate staging
11. camera and composition
12. acceptance criteria

## 合同字段映射

| 判断 | 写入 ShotContract |
| --- | --- |
| 镜头为何存在 | purpose |
| 观众必须感知 | viewer_must_perceive |
| 开场真实状态 | opening |
| 表演与物理 | performance、physics |
| 声音落点 | sound |
| 实现和控制归属 | control |
| 主风险与替代调度 | risk |
| 可验收条件 | acceptance、endpoint |

相机只写 shots/*.yaml 的现有 camera 字段，并服务于上述合同；不要套用“情绪必特写、动作必跟拍”的公式。不要写固定平均镜长或固定变化频率，时长由表演、对白/呼吸、动作完成、观众读取、前后剪辑接口和 Animatic 决定。

## 实现方式

导演可以选择：TEXT_GEN、I2V、START_END_FRAME、MOTION_REFERENCE、V2V、2D_PREVIS、3D_PREVIS、LIVE_ACTION、HYBRID、STILL_PLUS_MOTION、COMPOSITE。写入时映射到 control.production_method 的小写枚举；能力是否存在由当前 ProviderManifest 决定，不由本技能猜测。

可解析的 ShotSpec 片段：

```yaml
id: S001
scene_id: SC001
characters: [linxia]
contract:
  purpose: 让观众看到她决定带走证据
  viewer_must_perceive: 她确认门内有人后仍选择开门
  opening: [右手在门把上, 呼吸未恢复]
  endpoint: [门打开一条缝, 她停住并看向屋内]
  performance:
    required: [手指先收紧再放松]
    avoid: [突然转身]
  physics:
    required: [门把和手接触连续]
    avoid: [手指穿过门把]
  sound:
    cue: 门锁轻响后留半拍静默
    diegetic: [门锁声]
    music: ""
  control:
    production_method: start_end_frame
    motion_source: video_ref
    primary_uncertainty: 手与门把的连续接触
  risk:
    primary: 手部形变
    fallback_staging: 用门框遮挡开门末段
  acceptance:
    action_required: true
    min_end_hold_ms: 300
  proof_shot: true
```

## 验收与循环

manju check 之后用 manju prompt S001 --json 查看编译 trace；生成后用 review-take-and-route-repair 按媒体哈希和观察证据评审。若意图不能压缩成一个可见变化，拆成多个镜头或改成剪辑 beat，不用形容词硬塞进一个合同。

## 什么时候不该用

还没有场次目的和不可逆变化时，转到 scene-design；已经有 accepted take、要判断留改重拍时转到 review-take-and-route-repair；只是修改 Provider 能力事实时读取 ProviderManifest 和 authoring evidence。

## Eval

1. 对一个“拿起杯子并发现消息”的请求，拆出一个主可见变化和明确 endpoint，或说明需要两个镜头。
2. 给定 motion_reference 已承担动作时，指出 Prompt 不应重复编译动作控制。
3. 给定 opening 与 endpoint，拒绝以固定镜长或景别公式代替表演和剪辑接口分析。
