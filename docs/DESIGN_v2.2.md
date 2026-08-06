# Manju One 实现方案 v2.2

> 日期:2026-07-05(第三轮修订:既有工具采用阶梯 P0/P1/P2 落实、内容 QC 提前并机检化、云 API 通用配置打底;历轮决定记录见 §15)
> 性质:在 v1 参考方案基础上重新设计的实现方案,面向"在 Claude Code 里落地"这个真实前提。
> 核心目标不变:一个工具、一个项目、一条命令或一个按钮,从想法到成片;AI 可全自动、人可全手动、双方可随时互相接管。

---

## 0. 一句话方案与心智模型

**把 Manju One 做成一个"视频构建系统"(build system for video),而不是一个"带 AI 的剪辑软件"。**

心智模型对齐到 make / ninja / 编译器:

```text
源文件(人和 AI 共同编辑的文本 + 导入的媒体)
    ↓  构建规则(确定性的、可缓存的、可增量的)
派生产物(生成的素材、编译出的时间线、渲染的成片、导出的草稿、质检报告)
```

这个模型一次性解决四个核心目标:

```text
一键出片      = manju build(过期的重算,没变的跳过,失败的降级)
人工接管      = 直接改任何一个源文件、往目录里放任何一个素材,下次 build 自动生效
AI 接管       = Claude Code 改同样的文件、跑同样的命令,构建系统不关心作者是谁
失败可降级    = 降级链就是构建规则的一部分,单镜头失败不拖垮整片
```

**智能在系统之外,确定性在系统之内。** Manju 本体不调用任何 LLM,它只负责执行、渲染、校验;Claude Code 就是那个 AI 导演,通过文本文件、CLI 和 MCP 与它协作。

---

## 1. 与 v1 参考方案的关系

v1 的方向判断大体正确,以下核心资产**全部保留**:

ShotSpec 作为模型无关的镜头意图(这是全系统最重要的抽象,v1 说得对);Bible 体系(角色/场景/风格/道具一致性);TimelineSpec 中立时间线层,剪映/CapCut 只是导出格式;"人工导入也是一种 provider"的统一抽象;失败降级链(高质量视频 → 图生视频 → 首尾帧 → 静帧推拉 → 漫画分格 → 文字卡);autopilot / copilot / manual 三模式;"先做无模型闭环"的实施顺序;12 镜头验收样例。

以下决定**推翻重做**,每条附理由:

**① ZIP 单文件容器 → 目录即项目 + 按需打包。**
v1 自己承认项目会长到几十 GB。ZIP 里放 GB 级视频意味着:每次保存要重写归档、FFmpeg 无法直接读 ZIP 内的媒体(每次都要解包)、SQLite 根本无法在 ZIP 内工作(它需要随机写的真实文件)、保存中途崩溃 = 项目损坏。正确做法是 `雨夜便利店.manju/` 作为一个**带扩展名的目录**——心智上仍是"一个项目一个东西",物理上是普通文件夹,性能与崩溃安全都是原生的。真正需要单文件的场合(备份、迁移、归档)用 `manju pack` 产出 `.manjupkg`,`manju unpack` 还原。

**② 自研 Patch 协议 + 版本引擎 → git 就是 Patch 引擎。**
v1 设计了一整套 JSON Patch(op/path/from/to)、patch_index、版本快照、回滚系统。但落地环境是 Claude Code——它天生就会编辑文件、看 diff、用 git。如果项目真相是 git 仓库里的文本文件,那么"AI 提出修改、人审查、接受/拒绝/回滚"就是 git 的日常操作,免费获得,且比自研协议成熟一万倍。自研 Patch 引擎是在重复造一个更脆的轮子。锁定机制改用"值哈希锁"实现(见 §5),不需要 Patch 协议也能强制执行。

**③ 内置 AI Co-Director 层 → AI 完全外置。**
v1 的 "Manju AI Layer" 包含 agent roles、prompt/patch schemas、AI memory 等子系统。全部砍掉。Claude Code 本身就是一个完整的规划型 agent,在工具内部再造一个 agent 框架既重复又永远追不上。Manju 只需要提供三样东西让外部 AI 好用:**AI 可读写的文本格式、带 `--json` 的 CLI、一个 MCP server**,再加一份写给 AI 的操作手册(SKILL.md / CLAUDE.md)。"一键全自动"由 Claude Code 按手册里的 playbook 执行,或由 `manju auto` 薄封装 `claude -p` 实现。

**④ Node/TS + Python 双语言核心 → Python 单栈。**
硬依赖(pyJianYingDraft、pyCapCut、faster-whisper)都是 Python;媒体与 AI 生态在 Python;Claude Code 写 Python 顺手。双栈是持续的维护税。capcut-cli 这类 Node 工具作为外部二进制走子进程调用,和 FFmpeg 一个待遇——adapter 边界之外用什么语言都无所谓。

**⑤ 任务图调度器语义 → 构建系统语义。**
v1 用"任务图 + 状态机 + 调度器"驱动一切,状态靠人工维护,容易与实际不一致。改为:**过期与否由内容哈希推导**(镜头意图变了、素材换了,哈希就变,产物自动判定为 stale),状态字段只保留"人的决策"(选中哪个 take、是否批准、是否锁定)和"运行时簿记"(重试次数、日志)。决策不可推导所以存文本;簿记可重建所以存 SQLite。

**⑥ SQLite 承载项目状态 → SQLite 只承载可重建状态。**
一切"真相"(设置、Bible、镜头、选择、锁定、时间线规则)都是文本文件——git 可版本、AI 可直接读写、崩溃安全(临时文件 + 原子改名)。SQLite 只放任务队列、运行日志、缓存索引、缩略图索引,删掉整个 `.manju/` 运行时目录也能从文本 + 媒体完全重建。

**⑦ 30 天内做桌面工作台 UI → 静态 Review Board 先行,GUI 无限期后置。**
自用 + Claude Code 驱动的场景里,GUI 的性价比最低。`manju board` 生成一个静态 HTML:镜头卡片墙、每镜头候选 take 对比、缩略图、一键复制 `manju select S002 take_03` 命令。这覆盖"导演工作台"八成价值,成本不到二十分之一。真正的 GUI 等核心稳定后再议。

关于第三方工具的立场更新(第三轮):v2.1 曾把 capcut-cli、pyCapCut、mcp-video、HyperFrames 等后置,理由是"我无法核实其现状",这是认知上的保守,不是架构上的排斥。你确认这些工具此前已实测跑通,风险性质随之改变——不确定性消除后,它们应当尽早进入插件层,直接降低首版自研量。§2.5 给出完整的 P0/P1/P2 采用阶梯。架构不变的部分是:它们仍全部位于 adapter 边界之外,核心不依赖其中任何一个的存活,坏了只换一个适配器。

---

## 2. 分层架构

```text
┌──────────────────────────────────────────────────────────────┐
│  智能层(系统之外)                                             │
│  Claude Code = AI 导演:创作、规划、决策、修复                   │
│  人 = 同一身份:编辑同样的文件、跑同样的命令                      │
├──────────────────────────────────────────────────────────────┤
│  协作面                                                       │
│  文本文件(真相)│ CLI(--json)│ Manju MCP │ events 日志          │
│  Agent 工具带:mcp-video 等 MCP 工具,agent 可直接动手的视频层    │
│  (产物须经回写规则登记回项目,见 §2.5)                          │
├──────────────────────────────────────────────────────────────┤
│  Manju 引擎(确定性,不含任何 LLM 调用)                          │
│  构建图与缓存 │ 时间线编译器 │ 渲染管线 │ QC │ 导出调度           │
├──────────────────────────────────────────────────────────────┤
│  Provider / Adapter 层(全部可替换,进程外或库级隔离)             │
│  生成与包装:云视频/图像 API、云 TTS、HyperFrames(HTML→MP4)、    │
│              静帧推拉、文字卡、人工导入(也是 provider)           │
│  质检检查器:mcp-video(Python 库)+ 自研规则 + 云视觉模型         │
│  出口:pyJianYingDraft ∥ capcut-cli(剪映双路互备)、             │
│        pyCapCut(国际 CapCut)、OTIO、SRT/ASS;FFmpeg 渲染兜底    │
└──────────────────────────────────────────────────────────────┘
```

创作与执行的边界画在这里:**brief → 大纲 → 剧本 → Bible → 场次/镜头合同 → Animatic → Proof Shot → Proof Scene,仍属于创作反馈循环,由 agent 与人逐级验证;只有当前 Proof Scene 获人类 digest 批准、项目达到 `BULK_READY` 后,才进入全片批量生成,selected media 之后的组装/渲染/质检/导出才是确定性执行。** 引擎的构建图从"镜头 + 素材"开始。`manju build` 发现没有镜头文件时,只会提示"缺少创作阶段产物",绝不自己编；叙事项目未达到当前 production stage 时,无人值守付费 video 也会在 transport 前阻断——这条边界保证引擎可测试、可复现,并避免 Storyboard 后直接批量花费。

---

## 2.5 既有工具矩阵与挂载点(第三轮新增)

自研只保留没有现成替代品的部分:`.manju` 项目格式、构建系统、ShotSpec/TimelineSpec、stale/缓存逻辑、events/status/check、provider 适配框架。其余全部挂现成工具,按采用节奏分三档。

**P0 直接使用(进入主线里程碑):**

```text
FFmpeg / ffprobe   渲染与探测底座(M0)
git                版本引擎(M0)
Claude Code        AI 协作层(M2)
pyJianYingDraft    剪映草稿主路(M1)
capcut-cli         剪映草稿副路 + 现成草稿 lint + 可选 proxy 渲染(M1)
pyCapCut           国际 CapCut 草稿(M1;与 pyJianYingDraft 同族 API,增量很小)
mcp-video          三重身份(M2/M3):
                   ① Agent 工具带 —— 其 MCP server 直接进 Claude Code 配置,零开发
                   ② QC 检查器后端 —— 它同时是 Python 库,引擎直接 import
                      其质检/媒体分析能力,不经 MCP
                   ③ 后处理原语 —— 字幕、音频规范化、缩略图等
```

**P1 插件位(有现成实现就装,没有就走自研保底):**

```text
HyperFrames        html_render provider:标题卡/角色卡/章节卡/包装,
                   HTML+CSS → 确定性 MP4(M3 随降级链落地;drawtext 仍是零依赖保底)
OTIO               中立时间线出口(M1)
Vixel              FFmpeg 原语参考/备选,与自研 media/ 模块互补
VibeFrame 思路     agentic runtime 对照:JSON 输出、dry-run、cost gate、
                   机器可读报告——本方案已内建同类机制,接口细节可对照借鉴
Niren CASR 思路    导入真人素材的转录字幕思路,对应 M4 的云 ASR 插件位
```

**P2 仅作参考(看结构,不引依赖):**

```text
LumenX / Jellyfish / LocalMiniDrama / Toonflow / OpenCut
——平台形态、一致性管理、界面组织方式的对照样本
```

两条安全规则,让"多用现成工具"不破坏核心不变量:

**adapter 墙。** 每个工具都躲在一个适配器后面:manifest 声明能力,`manju doctor` 探活,版本锁定记录在案。任何一个停更、坏掉、格式变了,只换一个适配器,核心与项目文件不受影响。

**回写规则。** Agent 工具带允许 agent 绕过构建系统直接处理媒体——这是特性不是漏洞,但产物必须经 `manju select <shot> --file <path>` 登记为 manual take,或放入 imports 后再登记,才算进入项目;禁止原地覆盖任何已登记文件。构建系统的哈希一致性由此不会被旁路破坏。

---

## 3. 项目格式:`xxx.manju/` 目录

```text
雨夜便利店.manju/
├─ project.yaml            # 项目设置:画幅、fps、模板、模式、ask_before、导出 profiles
├─ story/
│  ├─ brief.md             # 一句话创意
│  ├─ outline.md           # 大纲
│  └─ script.md            # 剧本(含分场与对白)
├─ bible/
│  ├─ characters.yaml      # 角色:外观、声音、性格、参考图路径、locked 字段
│  ├─ scenes.yaml          # 场景
│  ├─ props.yaml           # 关键道具
│  └─ style.yaml           # 画风、色彩、节奏、字幕样式引用
├─ shots/
│  ├─ index.yaml           # 镜头顺序 + 全局默认(时长策略、候选数、降级链)
│  ├─ S001.yaml            # 每镜头一个文件(diff 干净、可并行编辑、锁定粒度自然)
│  └─ S002.yaml …
├─ media/
│  ├─ imports/             # 人工导入,只读语义,工具永不删改
│  ├─ refs/                # 参考图、音色样本
│  └─ gen/                 # 生成产物,只增不改
│     └─ S002/
│        ├─ take_01.mp4
│        ├─ take_01.yaml   # sidecar:provider、参数、seed、spec_hash、probe、QC 结果
│        └─ take_02.mp4 …
├─ timeline/
│  ├─ rules.yaml           # 组装规则:转场默认、BGM 与 ducking、字幕样式、镜头间 padding
│  └─ timeline.json        # 编译产物(manual 模式下转为手工真相,见 §6)
├─ captions/
│  ├─ captions.srt         # 编译产物,可手改并标记 manual
│  └─ captions.ass
├─ renders/
│  ├─ segments/            # 段级缓存(内容寻址,可整目录删除重建)
│  ├─ proxy/
│  └─ final/               # final_v1.mp4、final_v2.mp4…只增不覆盖
├─ exports/
│  ├─ jianying/  capcut/  otio/
├─ reports/
│  ├─ qc.json  qc.md       # 机器读 + 人读双格式
│  ├─ repair_plan.yaml
│  └─ frames/              # QC 抽帧,供 agent 看图判断一致性
├─ proposals/              # AI 想改锁定内容时,写提案到这里等人批
├─ events.jsonl            # 协作日志:谁在何时做了什么(双向接管的关键)
├─ .manju/                 # 运行时:state.sqlite、任务队列、缓存索引、进程锁
├─ .gitignore              # media/gen 大文件、renders、exports、.manju/ 不入库
└─ .git/                   # manju new 自动 init;真相文本全部入库
```

三条基础纪律,整个系统的稳定性都建立在它们之上:

**真相是文本,媒体是只增的。** 所有决策(包括"S002 选中 take_03"这一行)都在 YAML 里,git 一行 diff 可审可回滚。媒体文件一旦写入永不覆盖——重做只会产生 take_04。于是"回滚一个镜头"= 回滚一行文本,媒体本体无需版本管理。

**imports 神圣不可侵犯。** 工具没有任何代码路径会删除或修改 `media/imports/` 下的文件,`manju gc` 也不碰。这是 v1 "AI 默认不能删除原始素材"的硬件级实现——不是靠 AI 自觉,是引擎根本没这个能力。

**SQLite 可以随时炸掉。** `.manju/` 整个目录删除后,`manju rebuild-index` 从文本和媒体完全重建。备份一个项目 = 备份目录(或 `manju pack`),没有任何隐藏状态。

---

## 4. 核心数据模型

所有 YAML/JSON 都有对应的 Pydantic 模型,并导出 JSON Schema。`manju check` 对全项目做 schema 校验 + 引用完整性校验(镜头引用的角色存在吗?选中的 take 文件在吗?)+ 锁校验。**agent 每次编辑后必须跑 check,这是它的安全网。**

### 4.1 ShotSpec(每镜头一个文件)

```yaml
# shots/S002.yaml
id: S002
scene: convenience_store          # 引用 bible/scenes.yaml
characters: [linxia]
duration: auto                    # auto = 由对白 TTS 时长 + padding 推导;或写死秒数
camera:
  shot_size: close_up             # 标准枚举:extreme_wide/wide/medium/close_up/extreme_close_up
  movement: slow_push_in
  angle: eye_level
action:
  main: 林夏接过硬币,镜头推进到硬币年份:2036
  emotion: 震惊、克制
dialogue:
  speaker: linxia
  text: 这不可能。
continuity:
  prev: S001
  locks: [character:linxia, scene, prop:future_coin]
quality:
  must_show: [硬币年份 2036 清晰可读]
  avoid: [多余手指, 人脸漂移, 黑屏]
generation:
  strategy: best_available        # 或 manual(等人工导入)
  candidates: 2
  fallback: [image_to_video, still_frame_motion, caption_card]
  prompt_override: null           # agent 可为特定模型手写 prompt,覆盖模板编译
status:
  selected_take: take_03          # ← 人的决策,文本即真相
  approved: false
locked: [dialogue.text, duration] # 值哈希锁,见 §5
```

### 4.2 Take sidecar(产物的血统证明)

```yaml
# media/gen/S002/take_03.yaml
provider: cloud_video_x
spec_hash: sha256:ab12…           # 生成时的镜头意图哈希
params: {seed: 123456, model: x-video-1.5, duration_s: 4}
remote: {job_id: job_8f2c91, cost: 0.32, currency: CNY}   # 断点续轮询与逐笔记账的依据
compiled_prompt: "……"             # 模板编译或 override 的最终 prompt,存档以便复现
probe: {duration_ms: 4080, width: 1080, height: 1920, fps: 24}
qc: {tech_passed: true, notes: [硬币年份可读]}
created_at: 2026-07-05T21:14:00
```

### 4.3 过期(stale)判定与"不践踏人的选择"

`spec_hash` 只对**影响画面的字段**做规范化序列化后取哈希:scene/characters(连同其 Bible 摘录)、camera、action、quality、generation 参数。`status`、`locked`、备注都不参与。

判定规则刻意保守:

```text
镜头无任何 take                  → build 时生成(缺失即构建)
selected_take 的 spec_hash 一致   → 跳过(缓存命中)
spec 变了但已有 selected_take     → 标记 stale 并提示,默认不动
                                    只有 --regen-stale 或 manju redo 才重做
人工导入的 take(spec_hash=manual)→ 永不自动作废,只提示
```

这条规则是"人机互不践踏"的核心:系统自动补缺,但绝不擅自推翻任何一方已经做出的选择。

### 4.4 TimelineSpec

沿用 v1 的多轨结构,收紧三点:时间一律用**整数毫秒**(fps 只在渲染期出现,杜绝浮点误差累积);转场作为 clip 的出场属性而非独立对象;文件头记录输入哈希,标明它是哪次编译的产物。

```json
{
  "meta": {"compiled_from": "sha256:…", "mode": "compiled"},
  "fps": 24, "width": 1080, "height": 1920, "duration_ms": 60000,
  "tracks": {
    "video":    [{"shot": "S001", "take": "take_02", "start_ms": 0, "duration_ms": 3000,
                  "transition_out": {"type": "fade", "duration_ms": 300}}],
    "overlay":  [{"kind": "title_card", "template": "chapter", "start_ms": 0, "duration_ms": 1500}],
    "voice":    [{"source": "gen/S001/voice_take_01.wav", "start_ms": 0}],
    "music":    [{"source": "imports/bgm_suspense.mp3", "start_ms": 0, "gain_db": -18, "ducking": true}],
    "captions": [{"start_ms": 0, "end_ms": 3000, "text": "那枚硬币,来自十年后。"}]
  }
}
```

---

## 5. 锁定与审批:不需要 Patch 协议的实现

```text
manju lock S002 dialogue.text
  → 在 S002.yaml 的锁记录里写入该字段当前值的哈希

manju check(以及每次 build 前)
  → 逐条比对:锁定字段的当前值哈希 ≠ 锁记录哈希 → 报错,build 拒绝执行

manju unlock S002 dialogue.text
  → 仅限交互式终端 + 二次确认;MCP 不暴露此命令;
    AI 操作手册明令禁止 agent 调用
```

AI 想改锁定内容的正规通道:写一个提案到 `proposals/0001_S002_dialogue.md`(想改成什么、为什么),然后在对话里告诉人。人同意 → 人自己 unlock、改、重新 lock(或让 AI 改后人来 lock)。整个流程没有自定义协议,只有文件、哈希和一条禁令,但锁是**硬约束**——即便 agent 犯浑直接改了文件,check/build 也会当场拦下。

git 在旁边提供第二层保障:所有文本变更都有历史,任何时刻 `git revert` 都能回到任何状态。

---

## 6. 时间线编译与手工接管语义

`manju timeline build` 是一个**纯函数**:输入 = 镜头顺序 + 各镜头 selected_take + 对白/配音时长 + rules.yaml,输出 = timeline.json。同样输入永远得到同样输出。

时长推导规则(戏剧/口播类模板的默认行为):镜头 `duration: auto` 时,时长 = 该镜头配音音频时长 + rules 里的前后 padding,再受 min/max 裁剪;字幕时间轴跟随音频;BGM 自动裁到片长并按 rules 尾部淡出。**音频驱动画面时长**,这是 v1 提到但没展开的关键机制,短视频节奏全靠它。

手工接管的语义必须显式,否则"重新编译把我的手工剪辑冲掉了"会成为最大痛点:

```text
timeline/rules.yaml 里 mode: compiled(默认)
  → timeline build 直接覆盖 timeline.json

mode: manual
  → timeline.json 成为手工真相,build 拒绝覆盖,
    只输出 timeline.generated.json 供人/AI 对比合并
```

从"AI 全自动"切到"我手工精剪时间线"就是改这一个字段——这正是"互相接管"在时间线层的具体形态。更常见的接管其实发生在更上游:人不满意某镜头,直接把自己的素材丢进 imports 并 `manju select S002 --file media/imports/my_clip.mp4`(内部登记为 manual take),下次 build 自动采用。

---

## 7. 渲染管线(FFmpeg 具体策略)

不要用一条巨型 filter_complex 渲染整个时间线——那种命令没法维护、没法增量、没法定位错误。采用**分段归一化 → 拼接 → 叠加 → 混音**的流水线:

```text
① normalize   每个选中 take → 统一规格的中间段
              (缩放/补边到项目分辨率、统一 fps、yuv420p、音频 48k 立体声)
              缓存键 = hash(源文件哈希 + 归一化参数) → renders/segments/<key>.mp4
② transition  相邻两段之间有转场的,单独渲染接缝小段(xfade/acrossfade),同样缓存
③ concat      主视频轨用 concat demuxer 拼接(规格已统一,秒级完成)
④ overlay     标题卡/贴片作为叠加层;字幕编译为 ASS 烧录(样式来自 style.yaml
              的字幕模板:字体、描边、安全区、最大行数、断句规则),同时输出外挂 SRT
⑤ audio mix   人声轨对齐拼接;BGM loudnorm + sidechaincompress 实现 ducking;
              成片按平台 profile 做两遍 loudnorm(如 -14 LUFS)
⑥ mux         → renders/final/final_v{n}.mp4,版本号只增;proxy 走同管线低参数
```

增量渲染是这套设计的免费赠品:换掉 S002 的 take → 只有 S002 的段和它两侧的转场接缝需要重渲,concat 和混音都是廉价操作。v1 验收标准里"替换一个镜头后能只重渲染相关部分"由此天然满足。每次渲染把完整 FFmpeg 命令行写入日志——出问题时人和 AI 都能直接复现单条命令排查。

---

## 8. Provider 协议(云优先)

生成后端已确定为**只用云 API**(决定 4/5)。第三轮补充了使用侧的要求:默认给一份**通用配置**,你后续只填几个关键字段就能跑,特殊的再手工定制。于是形态定为两层:**配置驱动的通用云适配器打底(§8.6),per-API 的 Python adapter 作为逃生门**——常规 REST 形态的 API 填配置即接入;鉴权古怪、流程特殊的,让 Claude Code 拿着 API 文档写一个几十行的专用 adapter,十分钟级别的事。两条路共用同一套异步任务模型、失败分类、断点续轮询与成本记账。

### 8.1 异步任务基类:云生成的统一形状

云视频/图像 API 几乎都是同一个形状:提交任务拿 job_id → 轮询状态 → 完成后下载产物。基类只规定这三步:

```python
class CloudProvider:
    def submit(self, task) -> RemoteJob        # 返回 remote_job_id,立即落库
    def poll(self, job) -> JobStatus           # queued / running / succeeded / failed(含失败类型)
    def download(self, job, dest) -> list[Path]
```

**remote_job_id 提交成功即写入 SQLite 任务表。** 这一条是云场景下"长任务中断可恢复"的全部秘密:进程被杀、断网、电脑重启,`manju build` 重新跑起来后,对"已提交未完成"的任务做的是**恢复轮询**而不是重新提交——绝不重复扣费。

失败类型必须区分,因为应对完全不同:

```text
rate_limited / timeout       → 指数退避自动重试(可安全重试)
content_rejected(审核拒绝)  → 不重试;拒绝原因全文入 run log,交给 agent 改写
                               prompt 重提,或直接走降级链。国内云 API 普遍带
                               内容审核,悬疑/惊悚题材必然会撞上,这是一等公民
                               失败类型,不是边角情况
provider_error / invalid     → 记录后换 provider 或降级
```

### 8.2 Provider manifest 与注册

```yaml
# ~/.manju/providers/cloud_video_x/provider.yaml
id: cloud_video_x
type: video
adapter: manju.providers.cloud.video_x:VideoXProvider   # 指向具体 adapter 类
capabilities: [text_to_video, image_to_video, vertical]
limits: {max_duration_ms: 6000, max_resolution: 1080x1920,
         max_concurrent: 3, rate_limit_per_min: 10}
cost: {per_second: 0.08, currency: CNY}                  # 用于预估与记账
auth: {key_env: VIDEO_X_API_KEY}                         # key 只走环境变量/全局配置
```

key 永远不进项目目录(`manju check` 扫描常见 key 模式,发现即报错)。limits 由引擎侧队列强制节流,不指望靠 API 端返回 429 来教育我们。

### 8.3 成本护栏

全云生成意味着每次 build 都可能花真钱,护栏分三层:

```text
事前  manju build --dry-run:列出将执行的任务清单与逐项成本预估
      (镜头数 × 候选数 × 时长 × 单价)
事中  project.yaml 可设 budget 上限;累计花费超限 → 队列暂停,状态转 waiting_user
事后  每次调用逐笔记账入 run log(provider、时长、金额、remote_job_id);
      take sidecar 带上该条的成本;manju status 显示项目累计花费
```

### 8.4 保底 provider 与人工导入

降级链的末端必须是**不依赖网络的本地能力**,否则断网就出不了片:`ffmpeg_kenburns`(参考图静帧推拉)与 `caption_card`(文字卡,HTML → 帧序列 → 视频)是纯 FFmpeg/本地渲染实现,永远可用。路由规则保留 v1 的降级链精神:候选 provider 按能力与可用性排序,失败依次换 provider → 换 seed 重编 prompt → 降级策略(图生视频 → 静帧推拉 → 文字卡)。

`manual_import` 依旧是一个 provider——它的"执行"就是等人放文件或登记既有文件,让人工素材与云生成素材在下游完全同权。云 TTS 同走基类(多数 TTS 是同步接口,基类允许"submit 即完成"的退化形态)。云 ASR 降为按需:漫剧链路的字幕来自剧本对白 + TTS 时长对齐,根本用不到 ASR;它只在"导入真人素材需要转录"时有用,后置到 M4。

### 8.5 Prompt 编译(不变,但云语境下更重要)

模板层把 ShotSpec 字段 + 所关联 Bible 条目的摘录填进各家 API 的参数模板;需要精雕的场合,**agent 就是最好的 prompt 编译器**——手写结果放进 `prompt_override`,编译层原样透传。编译结果、实际提交的完整请求参数、审核拒绝时的原因全文,全部进 sidecar/run log——花过的每一分钱都可复盘、可复现。

### 8.6 通用云配置(默认打底)

多数云生成 API 是同一套 REST 形态:POST 提交任务拿 job_id、GET 轮询状态、按 URL 下载产物。通用适配器把差异压缩成一份配置文件,接入一家新 API 时你只需要填标 ★ 的字段:

```yaml
# ~/.manju/providers/my_video_api/provider.yaml
id: my_video_api
type: video
adapter: generic_cloud              # 内置通用适配器
capabilities: [text_to_video, vertical]
auth:
  key_env: MY_VIDEO_API_KEY         # ★ key 的环境变量名
  header: "Authorization: Bearer {key}"
submit:
  url: https://api.example.com/v1/videos                 # ★
  body_template: {prompt: "{prompt}", duration: "{duration_s}", seed: "{seed}"}  # ★
  job_id_path: $.data.task_id                            # ★ JSONPath
poll:
  url: https://api.example.com/v1/videos/{job_id}        # ★
  status_path: $.data.status
  status_map: {SUCCEEDED: succeeded, FAILED: failed, PROCESSING: running}  # ★
  result_url_path: $.data.video_url                      # ★
failure:
  content_rejected_when: [contentPolicy, risk_control]   # 响应命中即判审核拒绝
limits: {max_concurrent: 2, rate_limit_per_min: 6}
cost: {per_second: 0.08, currency: CNY}
```

通用适配器覆盖不了的(请求签名、multipart 上传参考图、WebSocket 推送结果……)就写专用 adapter 类,manifest 的 `adapter:` 字段指过去即可,断点续轮询、失败分类、成本记账等机制完全复用。`manju doctor` 对每个已配置 provider 做一次干跑探活,配置填错当场暴露,不必等到花钱时才发现。

---

## 9. QC 与修复

三层检查,由浅入深:

```text
存在层   引用的文件都在、ffprobe 可读
技术层   时长与时间线一致(容差)、分辨率/fps 达标、黑屏/冻结抽样、
         静音/爆音、LUFS、字幕重叠/越界/超行数、导出草稿 lint
         (实现优先复用 mcp-video 的质检与媒体分析函数,缺什么才自研)
内容层   三级检查器,机检打底、agent 兜底,M3 随生成一起落地:
         ① must_show 断言化 —— 可机检项直接变成断言:
            "硬币年份 2036 清晰可读"→ 抽帧 OCR;
            "角色在画面中"→ 人脸/主体检测
         ② 一致性初筛 —— 关键帧 vs Bible 参考图,交给云视觉模型按
            checklist 出结构化判定(qc_vision provider,走 §8.6 同一套配置)
         ③ agent 终审 —— reports/frames/ 抽帧保留,机检不确定的
            交 Claude Code 看图裁决
```

产出三个文件:`qc.json`(机器读)、`qc.md`(人读)、`repair_plan.yaml`(每个问题给出建议动作:换 seed 重做 / 换 provider / 降级 / 需要人工,并标注是否 auto-safe)。`manju repair --auto` 只执行 auto-safe 项,其余进入人工待办。修复动作本质上就是修改镜头文件的 generation 参数再 build——没有独立的修复子系统,一切回到构建循环。

关键立场(第三轮明确):内容 QC 是引擎在 qc 阶段直接调用的检查器链,**不依赖 agent 在场**——manual 模式下同样工作,Claude Code 只是第三级仲裁,不是唯一通道。

---

## 10. 三模式与 AI 协作协议

三种模式的区别不在引擎(引擎对谁都一样),在**谁发起、何时问人**:

```text
manual    人编辑文件、跑 CLI,AI 不出现。Manju 就是一个本地剪辑/合成/导出工具。
copilot   人在 Claude Code 里对话:AI 写剧本改镜头、跑 build、给出 take 对比;
          人审 git diff、挑 take、上锁。project.yaml 的 ask_before 列表
          (如 expensive_generation、final_export、lock 变更)约定 AI 必须停下来问的节点。
autopilot 一条指令到成片。v1 期实现 = Claude Code 按 SKILL.md 里的 playbook
          从 brief 一路执行到 QC 通过;`manju auto "一句话"` 作为薄封装
          (内部调 claude -p)后续再加。
```

AI 协作协议不是代码,是一份随仓库分发的 **SKILL.md(兼作 CLAUDE.md)**,核心条款:

```text
开工三步:manju status --json → 读 events.jsonl 尾部 → 读 project.yaml 的 mode/ask_before
每次编辑后必跑 manju check;check 不过不许 build
禁区:不调 unlock、不动 media/imports、不覆盖 final
工具带产物必须回写:用 mcp-video 等直接处理媒体后,产物经
manju select --file 登记回项目才算数;禁止原地覆盖已登记文件
想改锁定内容 → 写 proposals/ 并在对话中说明
花钱或长耗时操作(视频生成、final render)按 ask_before 先询问;
问之前先跑 manju build --dry-run,把任务清单和成本预估一并贴给人
每完成一个阶段:git commit(格式 "[ai] 动词 对象:摘要")+ 引擎自动追加 events.jsonl
```

软约束(手册)与硬约束(CLI 层:unlock 仅限交互终端、imports 无删除代码路径、final 只增版本、`manju check` 强制锁校验)双保险。**双向接管的体验核心是 `manju status`**:任何一方接手时,一条命令看到当前阶段、哪些镜头缺/stale/待批、QC 遗留问题、建议的下一步——30 秒进入状态。

---

## 11. CLI 与 MCP 面

```bash
manju new 雨夜便利店 --vertical
manju status [--json]              # 接管入口:阶段、缺口、下一步、累计花费
manju check                        # schema + 引用 + 锁 校验
manju import <files…>              # 登记进 imports,自动转码代理/缩略图/波形
manju build [--target proxy|final|exports|qc] [--gen missing|auto|off] [--regen-stale] [--dry-run]
manju redo S002 [--candidates 4] [--provider X] [--seed N]
manju select S002 take_03          # 或 --file 指向人工素材
manju lock / unlock <shot> <field>
manju qc / repair [--auto]
manju export --jianying --capcut --srt --otio   # 剪映走 pyJianYingDraft∥capcut-cli 双路
manju board                        # 生成静态 HTML 评审板
manju pack / unpack                # 单文件归档往返
manju doctor / gc / events / rebuild-index
manju serve-mcp
```

`manju build` 不带参数 = 一键:补齐缺失素材(默认 `--gen missing`,只补缺不重做,烧钱可控)→ 编译时间线 → 渲染 final → 跑 QC → 出所有配置的导出格式。

MCP server 是同一套核心库的薄封装,暴露:project 摘要、shot 读写(带校验)、build/redo/select/qc/export、events 追踪。危险命令(unlock、gc --hard)**不在 MCP 面上**。Claude Code 走 MCP 或直接"读文件 + 跑 CLI"都行,两条路等价,MCP 提供的是结构化 IO 与更安全的默认值。

---

## 12. 技术栈与代码仓库

```text
语言      Python 3.11+(单栈,理由见 §1-④)
CLI       Typer;全命令支持 --json
Schema    Pydantic v2 → 导出 JSON Schema(校验 + 文档一体)
状态      SQLite(标准库),仅存可重建状态
媒体      FFmpeg/ffprobe 子进程,命令显式拼装并全量入日志
生成/语音  云 API:通用配置适配器打底 + 专用 adapter 逃生门(§8);本地零模型零 GPU
草稿      pyJianYingDraft + capcut-cli 双路(剪映)、pyCapCut(国际 CapCut)
质检/后处理 mcp-video:Python 库身份进 qc 检查器与后处理,MCP server 身份进 Agent 工具带
包装      HyperFrames(HTML→确定性 MP4);drawtext 为零依赖保底
版本      git(manju new 自动 init)
```

```text
manju/
├─ pyproject.toml
├─ src/manju/
│  ├─ cli.py
│  ├─ core/        # models(Pydantic)、container、hashing、locks、events
│  ├─ build/       # 构建图、缓存、stale 判定、降级路由
│  ├─ media/       # ffmpeg 封装、probe、normalize、audio、captions(ASS 编译)
│  ├─ timeline/    # 编译器与模型
│  ├─ providers/   # base(云异步基类)、generic_cloud(通用配置适配器)、
│  │               # manual/kenburns/card、html_render(HyperFrames)、
│  │               # qc_vision、cloud/(专用 adapter,按需)
│  ├─ exporters/   # jianying(pyJianYingDraft)、jianying_cli(capcut-cli)、
│  │               # capcut(pyCapCut)、otio、srt_ass
│  ├─ qc/          # checks/、repair、report
│  ├─ mcp/         # server
│  └─ board/       # 静态 HTML 评审板
├─ skills/manju/SKILL.md      # 给 Claude Code 的操作手册(= AI 协作协议)
└─ tests/ + fixtures/         # 12 镜头样例等回归资产(测试用,不是模板功能)
```

模板系统整体砍掉(决定 6):风格、节奏、字幕样式这些原本属于"模板"的内容,本来就由 `bible/style.yaml` 和 `timeline/rules.yaml` 在项目级承载,创作阶段由 agent(或人)直接写入即可,`manju new` 只生成通用骨架。12 镜头竖屏漫剧降级为 tests/fixtures 里的回归测试资产——它验证引擎,不面向用户。将来若真有需要,"模板"无非是一套预填好的项目骨架,随时加回来,不欠架构债。

---

## 13. 里程碑(按闭环切,不按周排)

**M0 无 AI、无模型的骨架闭环。** new / import / 手写镜头文件并 select 指向导入素材 / timeline build / 字幕(取自镜头对白或手写 SRT)/ proxy + final 渲染 / 基础 QC / pack。
验收 = 12 镜头样例:12 段本地视频、12 条中文字幕、1 条 BGM、1080×1920,产出 proxy.mp4、final.mp4、captions.srt;重开项目状态完整;删一个素材 QC 能发现;换一个镜头只重渲相关段;中文路径与 Windows 路径通过。

**M1 草稿出口(剪映双路 + 国际)。** SRT/ASS 样式引擎;剪映双路出口:pyJianYingDraft 为主、capcut-cli 为副,互为验证;草稿 lint 直接用 capcut-cli 现成的 lint/info 能力,不自研;pyCapCut 顺手接入(与 pyJianYingDraft 同族 API,增量很小);OTIO 导出。
验收 = M0 样例项目经两条路都能在**锁定版本的剪映专业版**中打开,片段顺序、字幕、音轨正确;两路产物 lint 均通过;pyCapCut 产出的国际版草稿结构校验通过(有条件则实机验证)。

**M2 AI 协作层。** SKILL.md playbook、events.jsonl、`--json` 全覆盖、Manju MCP server、Agent 工具带接入(mcp-video 的 MCP server 进 Claude Code 配置,回写规则写入 SKILL.md)、锁与提案流程实战。
验收 = 在 Claude Code 中以 copilot 模式完整做一支片:AI 写剧本与分镜、人锁角色与对白、AI build、人挑 take、AI 用工具带修一处媒体并正确回写登记、导出——全程无需人手动改任何 JSON。

**M3 云生成 + 内容 QC。** 云异步任务基类(submit/poll/download、重启续轮询)、通用配置适配器(§8.6,第一家 API 优先走"填配置"接入)、成本护栏(dry-run 预估、预算熔断、逐笔记账)、云 TTS、保底 provider(kenburns / caption_card,caption_card 优先用 HyperFrames 实现、drawtext 兜底)、候选 takes、降级链、run log;内容 QC 检查器链同步落地:must_show 断言化(抽帧 OCR / 主体检测)+ qc_vision 云视觉初筛 + agent 终审。
验收 = 一句话 → 竖屏悬疑漫剧全自动闭环;人为模拟一次审核拒绝和一次中途断网,分别走"改 prompt/降级链"和"断点续轮询"仍出片;故意让某镜头违背 must_show,机检在 **agent 不在场**的情况下抓住它;记账与实际调用逐笔一致。

**M4 体验与巡航。** board 评审板、repair --auto、片头尾/包装模板套件(HyperFrames 模板库)、gc、云 ASR 插件位(导入真人素材转录,参照 Niren CASR 思路,按需)、doctor 完整化(ffmpeg/字体/磁盘/剪映草稿目录可写性/各云 provider 与工具带探活、key 有效性/中文路径)。
验收 = 三种模式各出一支片;删除 `.manju/` 运行时目录后 rebuild-index 完全恢复。

---

## 14. 主要风险与对策

**剪映草稿格式漂移** — 已知最大外部风险,且剪映专业版较新版本已对草稿文件加密/变更格式,pyJianYingDraft 只保证支持到特定版本(以其 README 标注为准)。对策:安装已验证的剪映版本并**关闭剪映自动更新**;剪映出口做成 pyJianYingDraft ∥ capcut-cli **双路互备**,每次导出后用 capcut-cli 的 lint 校验、两路互为对照;为 exporter 建快照测试;final.mp4 + SRT + OTIO 永远兜底——草稿只是出口之一,导不出草稿不影响出片。

**FFmpeg 复杂度失控** — 分段管线从结构上规避巨型 filter graph;每条命令入日志可单独复现;段级缓存让调试只面对小文件。

**长任务中断** — 任务队列在 SQLite,产物只增 + 幂等(哈希判重);云任务把 remote_job_id 落库,进程重启后是**恢复轮询**而不是重新提交——中断不会导致重复扣费,`manju build` 天然续跑。

**云 API 内容审核拒绝** — 国内云视频/图像 API 普遍带内容审核,悬疑/惊悚题材的 prompt 大概率会撞上。对策:把"审核拒绝"定义为独立失败类型(区别于超时/限速),绝不无脑重试;拒绝原因全文入 run log,路由交给 agent 改写 prompt 重提,或直接走降级链。

**成本失控** — 全云生成意味着每次 build 都可能花真钱。对策:三层护栏(§8.3):dry-run 事前预估、预算上限事中熔断、run log 事后逐笔对账;`manju status` 常显项目累计花费。

**小众开源工具的存续风险** — capcut-cli、mcp-video、HyperFrames 等都是社区仓库,可能停更或发生破坏性变更。对策:版本全部锁定(commit/版本号记入 lockfile);每个工具躲在 adapter 墙后,接口以 Manju 侧契约为准;每条能力都有替代路径(草稿双路互备、caption_card 有 drawtext 兜底、mcp-video 质检缺位则退回自研规则)——单个工具消失不阻断任何里程碑。

**项目膨胀** — `manju gc` 分级:清未选中 take 的段缓存 → 清旧 proxy → `--hard`(交互确认)清未选中 take 本体;imports 与 final 永不在清理范围。

**AI 失控改动** — 三层防线:值哈希锁(硬)、imports/final 的引擎级不可变(硬)、git 全历史可回滚(兜底)。

**Windows 与中文路径** — 全程 pathlib + 显式 UTF-8,fixtures 里常备中文路径样例项目,M0 起就在验收标准里。

---

## 15. 已确定的决定(按轮次)

**第二轮(2026-07-05):**

1. **项目形态**:目录即项目 + `manju pack` 单文件归档。
2. **技术栈**:Python 单栈。
3. **草稿出口**:国内剪映优先。(本条已被第三轮第 8 条扩充)
4. **生成后端**:只用云视频 API,移除 ComfyUI/本地 GPU 路线;异步基类、断点续轮询、审核拒绝一等失败类型、成本护栏。
5. **TTS/ASR**:只用云 API;ASR 后置按需。
6. **模板系统**:整体砍掉;12 镜头样例降级为回归测试资产。

**第三轮(2026-07-05):**

7. **既有工具采用阶梯确立**(§2.5):P0 直接使用——capcut-cli、pyCapCut、mcp-video 提前进主线;P1 插件位——HyperFrames、OTIO、Vixel、VibeFrame/Niren CASR 思路;P2 仅参考——LumenX、Jellyfish、LocalMiniDrama、Toonflow、OpenCut。自研范围收敛为:项目格式、构建系统、ShotSpec/TimelineSpec、stale/缓存、events/status/check、provider 适配框架。
8. **剪映出口双路互备**:pyJianYingDraft 为主、capcut-cli 为副,lint 用现成能力;pyCapCut 同期接入国际 CapCut。
9. **内容 QC 提前至 M3 且机检打底**:must_show 断言化 + qc_vision 云视觉初筛 + agent 终审;引擎直接调用,不依赖 agent 在场。
10. **Agent 工具带成为一等层**,配回写规则保护构建一致性(§2.5)。
11. **云 API 接入通用配置优先**(§8.6):填 ★ 字段即接入,专用 adapter 为逃生门。

仍留到落地时:**第一家云视频 API 选哪家**——现在的答案是"填一份 §8.6 的配置",连写 adapter 都往往不需要。

---

## 16. 收束

```text
Manju One v2
= 一个 .manju 项目目录(文本为真相、媒体只增、git 为版本引擎)
+ 一个确定性构建系统(哈希定过期、缓存做增量、降级保出片)
+ 一条协作面(文件 + CLI --json + MCP + SKILL.md)
+ 一组可替换适配器(云生成/云语音、剪映双路草稿、pyCapCut、OTIO/SRT、
  mcp-video 质检与工具带、HyperFrames 包装、FFmpeg 渲染保底)

一键出片:manju build
人工接管:改文件、放素材、select、lock
AI 接管:Claude Code 读同样的文件、跑同样的命令
互相接管:events + status,30 秒交接;锁与哈希保证互不践踏
```
