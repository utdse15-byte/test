# project.yaml 逐字段参考(F38)

面向店主的 `project.yaml` 参考:每个 `ProjectConfig` 字段的类型、默认值、作用,
以及代码注释里明确警告的**后果**。真值来源是
`src/manju/core/models.py` 的 `ProjectConfig`(以及 `CONTRACTS.yaml` 的
`project.yaml` 条目);字段名和取值一律保留英文,不翻译。

`project.yaml` 由 `core.container.Project.load_config` 加载,`manju check` 会用本
模型逐字段校验。下面按 `ProjectConfig` 里的声明顺序列出。

---

## `name`

- **类型 / 默认**:`str` / 无(必填,唯一没有默认值的字段)。
- **作用**:项目名。
- **后果**:必填;缺了它 `manju check` 直接报 schema 错误。

## `width` / `height`

- **类型 / 默认**:`int` / `1080`、`1920`。
- **作用**:帧尺寸(像素),脚手架默认 1080×1920 竖屏。
- **后果**:必须 **> 0**。0 或负数不是合法分辨率,`manju check` 在加载
  `project.yaml` 时就拒绝(不再让坏配置过 check 后在 build/compile 崩溃)。

## `fps`

- **类型 / 默认**:`int` / `24`。
- **作用**:整数帧率;当声明了 `edit_rate` 时,它是 `edit_rate` 的**遗留镜像**
  (给旧代码看的)。
- **后果**:必须 **> 0**(为 0 会让时间线逐帧对齐 frame-grid snap 除零崩溃)。
  声明了 `edit_rate` 时,`fps` **必须等于** `edit_rate` 的名义整数帧率
  (`nominal_int`)——详见 `edit_rate`。

## `edit_rate`

- **类型 / 默认**:`{num: int, den: int}`(`EditRate`) / 缺省(`None`)。
- **作用**:精确有理帧率(如 `{num: 24000, den: 1001}`)。**声明后它是唯一真值**,
  `fps` 只是给旧代码读的遗留镜像。
- **后果**:
  - `fps` **必须等于其 `nominal_int`**(唯一真值 / 镜像关系):
    `24000/1001 → 24`、`30000/1001 → 30`、`25 → 25`。两者不一致是结构化错误,
    错误信息同时点名 `fps` 与它应等于的 `nominal_int`,绝不允许「旧代码读 `fps`、
    新代码读有理值」的静默分裂真值。
  - `num`/`den` 必须是正整数,归一化后落在 `[1, 1000]` fps 之间,否则拒绝
    (`{2000, 1}`、`{1, 2}`、`0`、负数、`True` 都被判为非法帧率)。
  - 缺省(`None`)时序列化会**整键丢弃** `edit_rate`,没有声明该字段的项目在磁盘上
    字节完全一致。

## `mode`

- **类型 / 默认**:`"manual" | "copilot" | "autopilot"` / `"copilot"`。
- **作用**:协作模式。
- **后果**:只能是这三个字面量之一,否则 schema 报错。

## `build`

- **类型 / 默认**:`{mode: "quality" | "balanced" | "speed" | null}`(`BuildConfig`)
  / 缺省(`None`)。
- **作用**:项目默认的构建质量模式(并发 / 重试 / 策略偏置的档位)。
- **后果**:`build.mode` 只能是 `quality`/`balanced`/`speed` 或不设;typo 在
  `manju check` 报错。缺省(`None`)= 引擎默认(串行生成、无路由偏置、当下的重试
  次数),不写 `build` 段的 `project.yaml` 字节完全一致。命令行的
  `manju build --mode` **永远覆盖**本字段。

## `ask_before`

- **类型 / 默认**:`list[str]` /
  `["expensive_generation", "final_export", "lock_change"]`。
- **作用**:声明哪些动作在执行前需要先征询确认。

## `budget`

- **类型 / 默认**:`{limit: float | null, currency: str}`(`BudgetConfig`) /
  `{limit: null, currency: "CNY"}`。
- **作用**:花费上限与币种(供事后逐笔记账 / 花费视图使用)。

## `export_profiles`

- **类型 / 默认**:`list[str]` / `["srt"]`。
- **作用**:项目的默认导出档列表。

## `preset`

- **类型 / 默认**:`str` / `"generic"`。
- **作用**:记录哪个 preset kit 脚手架出了本项目(`"generic"` = 普通 `manju new`)。
- **后果**:**纯记录**。preset 只在创建时预填文件,之后从不绑定项目——它写下的一切
  都保持普通、可手改的 YAML/markdown。

## `agent`

- **类型 / 默认**:`str | null` / 缺省(`None`)。
- **作用**:`manju auto` 使用的 agent CLI:已知名
  (`claude`/`codex`/`gemini`/`qwen`/`aider`)或模板如 `"claude -p {prompt}"`。
- **后果**:`None` → 依次回退到 `MANJU_AGENT` 环境变量,再 → 在 PATH 上探测已知
  agent。

## `cache_toolchain_keys`

- **类型 / 默认**:`list[str] | null` / 缺省(`None`)。
- **作用**:**严格 opt-in**,把指定的「会改变渲染输出字节」的工具链事实折进每个渲染
  缓存键。
- **后果**:
  - tokens **只能是 `ffmpeg` | `fonts`**:`ffmpeg` 是编码器本体、`fonts` 是字幕
    烧录字体,它们变了输出字节才会变。其它机器事实(os/python/依赖版本…)不改变
    输出字节,进键只会造成毫无意义的全量重建(键变了、字节没变),因此在校验层**直接
    拒绝**。空列表是错误(缺省才是关闭开关);重复 token 是错误。
  - **开 / 关都会导致全量缓存冷重渲**:打开——或以后关闭——会改变项目的每一个渲染
    缓存键,下一次 build 端到端 cache-cold(segment / boundary / final / animatic
    各重渲一次)。这正是特性:换了 ffmpeg、换了烧录字体会如实重渲,而不是端出陈旧
    工具链的字节。
  - 缺省(`None`)= 今日行为,缓存键与序列化字节永远一致。

## `color`

- **类型 / 默认**:`{tag_outputs: bool, input_transform: "srgb_to_bt709" |
  "p3_to_bt709" | null}`(`ColorSpec`) / 缺省(`None`)。
- **作用**:opt-in 色彩处理,两个各自独立、纯「声明」的色彩事实(这里的东西从不探测、
  从不猜测)。
- **后果**:
  - **缺省即关闭**:不写 `color` 块 = 不打标签、不做输入变换,缓存键与序列化字节
    完全一致。`color: {}` 不是合法的关闭状态(全默认块只会误导后来的人以为色彩管理
    开着)——要关就直接删掉 / 省略整个 `color` 块。
  - **`tag_outputs` 只打标签、不转换,且换键可逆**:给 finals/proxies 盖上管线本就
    在产的 `bt709`/`tv` 标签(未打标签的输出正是播放器和 NLE 会去猜的);它陈述
    「是什么」,不转换任何一个像素。开 / 关经哈希编码列表换键,可逆。
  - **`input_transform` 是「声明」源色彩(声明错 = 干净但错的画面)**:告诉引擎
    「我的素材是 sRGB / Display P3」,在唯一的归一化接缝(`media/normalize`)把它们
    转到 `bt709`。这是拍摄 / 导出素材的人做的逐项目声明——声明错了会产出**位移的
    画面(干净但错的画面)**,和写错 `fps` 一模一样;仅在 active 时折进
    segment/boundary 缓存键。
  - **需要 ffmpeg 带 zscale**:输入变换链依赖 ffmpeg 的 `zscale`(libzimg)滤镜;
    `manju doctor` 会预检——缺 `zscale` 时如实告警(渲染会失败,请换带 zimg 的
    ffmpeg 构建)。

---

改完 `project.yaml`,跑一次 `manju check`。
