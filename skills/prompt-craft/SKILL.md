---
name: prompt-craft
description: 提示词编译与控制分配：把已确定的 SceneContract、ShotContract、资产和 ProviderManifest 编译成可追踪的执行指令。
when_to_use: 当镜头目的、opening、endpoint 已确定，需要分配参考/动作/相机/环境控制并编译 provider 指令时使用；不负责重新发明镜头意图。
tags: [directing, task]
user_invocable: true
---

# 提示词编译与控制分配

Prompt 不是故事创作入口，也不是一次成功保证。输入必须是：

```text
SceneContract relevant state
+ ShotContract
+ Bible assets
+ resolved reference ownership
+ selected production method
+ current ProviderManifest
+ current authoring evidence (optional)
```

## 编译决策树

1. 目的和 endpoint 未定：回 scene-design 或 shot-design，不写 Prompt。
2. 为每个变量分配唯一控制源：identity、presentation、behavior、cognition、residue、location、framing、lighting、pose、motion、camera、environment response、audio。
3. 发现两个 source 都控制同一变量：减少引用、改 controls/ignore，记录冲突，不靠更长 Prompt 解决。
4. 只把未被参考图、动作参考、首尾帧、实拍、2D/3D 预演或合成承担的变量编译进指令。
5. 读取 ProviderManifest 的 capabilities、limits、refs、cost、adapter。仅当 authoring evidence 状态为 current 且有来源时采用语法或负向指令；过期或缺失就标 unknown，不猜参考数量、时长、seed 或 negative 行为。
6. 写出一次实验只验证什么，并把 primary variable 与 held constants 分开。

## 输出契约

`manju prompt <shot> --json` 应能追溯以下结果：

- intent summary、opening state、visible motion、camera motion、物理响应、endpoint 和 stable hold；
- control map：变量、owner、reference lineage、source of truth；
- unresolved variables、compiled positive instruction、在证据支持时才有的 compiled negative instruction；
- experiment hypothesis、primary variable、unknown capabilities/warnings；
- ProviderManifest id/fingerprint、authoring evidence id/date/status。

如果生产方式是 `motion_reference`，动作参考承担动作和时序，Prompt 只补充未承担的身份、场景或 endpoint；如果是 `start_end_frame`，首尾帧承担端点，不得在文字中写出冲突的中间动作。任何 provider 参数都要经 preflight，不能凭模型记忆添加。

## 例：控制图（不是第二套真相）

```yaml
shot_id: S001
owners:
  identity: bible.characters.linxia.identity_ref
  presentation: bible.characters.linxia.costume_ref
  motion: refs.motion.door_handle_test
  endpoint: shot.contract.endpoint
  camera: shot.camera
unresolved: [room_echo]
positive_instruction: 门锁轻响后，手仍与门把连续接触，最后停在门缝打开的状态
negative_instruction: null
experiment:
  hypothesis: 缩小手部动作幅度会保持门把接触
  primary_variable: motion
  held_constant: [identity, presentation, camera, lighting, duration]
unknown_capabilities: []
```

此 YAML 仅是编译输出的示意；真正输入和输出以现有 CLI JSON、合同和 ProviderManifest 为准，不能保存到 reports 后要求引擎读取。

## 什么时候不该用

镜头目的或 endpoint 仍在争论时转到 `shot-design`；参考已经生成、要看片并给 disposition 时转到 `review-take-and-route-repair`；要修改 source 字段时转到 `direct-shot-source-patch`。

## Eval

1. 一个动作参考已承担 motion 时，control map 中 motion owner 必须是 reference，Prompt 不得重复拥有它。
2. authoring evidence 过期时，输出 unknown capability，并拒绝写固定厂牌公式。
3. 同一变量同时由两张参考图控制时，先报告 ownership conflict，再编译。
