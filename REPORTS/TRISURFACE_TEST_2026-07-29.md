# 三面实测 — CLI · MCP · GUI(2026-07-29)

店主要求:把三个面(CLI、MCP、GUI)都**当工具深度用一遍**,找出所有会绊人、
看不懂、不顺手的地方。本报告**只测不修**:每一条都是跑命令/点页面时撞上的,
带复现步骤与归属 `file:line`;没有一条来自"读代码时觉得"。修复留给后续波次
逐条走红-先行流程。

## 方法与环境

- Ubuntu 24.04 沙箱,**ffmpeg 6.1.1-3ubuntu5(钉的版本)**,Python 3.11.15,
  extras 全装(dev/jianying/capcut/mcpvideo/edgetts + httpx 手补,见 F-23)。
- CLI:从 `manju new` 起真做了一部竖屏片(7 镜、2 条台词、Edge TTS 真配音、
  手动素材接管、en 多语言 overlay、baseline 审批、全部 9+ 种导出、pack/unpack/
  fixity、relink 断链恢复、migrate 有理帧率来回、ingest 批次、series 剧集),
  共走约 60 条命令,含错误路径。
- MCP:自写 stdio JSON-RPC 客户端驱动 `manju serve-mcp`:26 个工具逐类调用、
  坏参数、坏 JSON、锁违规写入、CAS 冲突、collaborative/unattended 两个 profile。
- GUI:Playwright + Chromium 真浏览器,13 个导航页 + 4 个专业页全爬
  (记录 pageerror/console/失败请求),并交互:新建镜头、导出中心生成/人工
  确认、审片、构建计划弹窗、--readonly、工作区选择器、board --serve。
- 上一波(`UX_REAL_USE_2026-07-25.md`)已修的问题**逐条避开**,本报告只记新发现。

## 结论先说

引擎判断层面依旧一次都没错(锁、CAS、金钱闸、append-only、fixity 全部经受住
了对抗性调用)。发现集中在三类:**同一契约在三个面执行不一致**、**上一波修过
的问题在另一个面还活着**、以及**新手第一次撞墙的地方**。按后果排序:

| # | 发现 | 面 | 后果 |
|---|---|---|---|
| F-01 | `final_export` 确认闸**只在 CLI 生效**,MCP/GUI 直通 | 三面 | agent 可绕过店主设定的外向制品确认 |
| F-02 | 配音/手动登记**不进 run ledger**;`rebuild-index` 后凭空多 6 行 | CLI | 付费 TTS 花费账面不可见;rebuild ≠ 原生状态 |
| F-03 | 静音母版行印 `实测 integrated None LUFS / TP None dBTP` | CLI+GUI | 上一波修了 `manju masters`,共享的导出中心引擎没修 |
| F-04 | `/create` 页每次加载抛 `ManjuApiError has already been declared` | GUI | 上一波在三个页修掉的同一 bug,第四个页还活着 |
| F-05 | redo 完 status 仍叫你 redo,**从不叫你 select** | CLI+GUI | 照建议敲进死循环 |
| F-06 | 镜头 schema 报错是裸 pydantic 语,不给正确形状 | CLI | 新手写第一个镜头必撞 |
| F-07 | `extra="allow"` 下打错字段名**无任何提示** | CLI | `duration_ms` 静默不生效,镜头落回 auto |
| F-08 | 漏斗/引导指到「分镜页新建镜头」,按钮**不在分镜页** | GUI | 指路指错门 |
| F-09 | Edge TTS(推荐的免费路)**没有脚手架路径**,模板三步全不适用 | CLI | 首个配音供应商要靠翻 README 手写 |
| F-10 | `--readonly` 下导出中心 15 个写按钮照常可点 | GUI | 服务端有拦(403),显示层没禁用 |
| F-11 | `locale add` 给无台词镜头也铸行,`missing=5` 永不清零 | CLI | 多语言状态永远看着欠债 |
| F-12 | baseline 审批拒绝不点名**哪个导出过期** | CLI | 只能自己去扫表 |
| F-13 | `manju history` 把结构化 detail 印成 Python repr 墙 | CLI | events 有摘要器,history 没接 |
| F-14 | board 项目页花费印 `0.0 None` | GUI | `.get("currency","")` 撞上显式 None |
| F-15 | ref 图入库静默 stale 三个镜头,ingest 不提后果 | CLI | 下次 build 是重生成(真钱) |
| F-16 | `voice --preview` 失败信息:§8.6 指向不存在的文档、无命令、把试听说成修复 | CLI | 死路信息 |
| F-17 | pullsheet「PDF skipped — no headless-Chromium in this environment」**从不探测** | CLI | doctor 明明测得 chromium ✓;是「未实现」装成环境事实 |
| F-18 | MCP 可分支失败仍多用 `code:"error"`(锁违规/CAS/未知提案) | MCP | 错误码税目工作停在了 CLI |
| F-19 | MCP export 只认 srt\|otio\|jianying,CLI 有 9+ 种 | MCP | agent 出不了 capcut/ttml/edl/fcpxml/xmeml/vtt |
| F-20 | 人工确认一键落账,无二次确认、无 GUI 撤销 | GUI | 误触即成永久「已人工确认」记录 |
| F-21 | `series new` 的下一步不带 `cd`(`manju new` 带) | CLI | 照敲报 no series found |
| F-22 | `analyze`/`segments` 拒绝语:contract §2 行话 / 裸 `[Errno 2]` | CLI | 店主无从行动 |
| F-23 | 按 CLAUDE.md 的 dev-loop 安装,collection 7 个模块红(缺 httpx) | 环境 | 测试直接 import httpx,没有任何 extra 声明它 |
| F-24 | 杂项纸割:qc brief 同一命令一行重复两遍;`blockers=0,5 项`粘连读成小数;prompt 的 Scene 是字典倾倒(name 在尾);README 把 `--approve-baseline` 写成带值;relink apply 对同文件第二行报「拒绝」 | CLI | 各为一行的小刺 |

逐条展开如下。每条给复现、看到的原文、为什么是问题、归属。

---

## 一、跨面一致性(最值得先看的三条)

### F-01 `final_export` 确认闸只拦 CLI,MCP 与 GUI 直通

项目 `ask_before` 含 `final_export` 时(默认如此):

```
$ manju export --srt
waiting_user: final_export 在 ask_before 中 — 导出是外向制品确认(非金钱花费)。确认后重试: manju export --yes …
```

同一项目、同一时刻,经 MCP 调 `export` 工具(formats=["srt"]、["jianying"]):
**直接写盘成功**,无任何 waiting_user。GUI 导出中心的「生成 / 更新」同样直通。

- 闸的实现在 `cli.py:2895-2899`(export)与 `cli.py:3146-3150`(package)——
  **CLI 层**;MCP 的 `_h_export`(`mcp/tools.py:585`)和 GUI 的
  `_act_exports_generate`(`gui/server.py:4490`)直接调 exporters,不读
  `ask_before`。
- 对比之下,**金钱闸走的是引擎层**(`build/graph.py: spend_gate`),所以 MCP 的
  `build`/`redo` 正确地拿到结构化 `waiting_user`、要显式 `assume_yes: true`
  (且 fail-closed `is True`,写得非常好)。同一个 ask_before 机制,两个 token
  两种待遇。
- GUI 上人点按钮尚可算「点击即确认」;**MCP 上没有人**——一个 agent 可以在
  店主从未确认的情况下产出剪映草稿等外向制品。CLAUDE.md 的不变量写着
  "security checks live in service/core (not just CLI)",这一条恰好是反例。
- 这不是「设计上 agent 免检」:`skills/manju/SKILL.md:101` 白纸黑字教 agent
  「凡是命中 ask_before 列表(如 …、final_export、…)必须停下来问人」——
  **契约明确适用于 agent,执行却只剩 agent 的自觉**。
- 建议方向(未做):把 final_export 判定挪进导出入口共享层,MCP 报
  `waiting_user`(agent 已有该分支),GUI 弹一次确认或声明点击即确认。

### F-02 配音与手动登记不进 run ledger;`rebuild-index` 之后账本凭空变厚

真实时间线(全新项目,Edge TTS 配音 2 条 + en locale 配音 2 条 + 手动 take 2 个):

```
$ manju tasks          # 7 行,全是 caption_card
$ manju spend          # by provider 只有 caption_card;覆盖 7/7 条记录
$ manju rebuild-index
runtime rebuilt; ledger: 13 runs, 0 pending jobs
$ manju tasks          # 13 行:多出 edge ×4(含 locales/en)与 manual_import ×2
```

- 机制:`record_run` 全仓只在生成 take 的路径调用一次(`build/graph.py:308`);
  `manju voice`(单发/批量/locale)与 `select --file` 登记从不写 ledger。
  而 `rebuild()`(`runtime/state.py:676`)是**从 sidecar 逐个重铸行**的,
  于是重建出的账本比原生长出 6 行。
- 两个后果:①「SQLite 可丢弃,rebuild 从 text+media 重建」这条纪律的隐含
  前提是 rebuild ≡ 原生状态,现在不等;② 配音接的是**真云端供应商**
  (edge 免费,但 generic_tts 带 `cost.per_call` 的就是真钱)——
  `manju spend` 的存在意义是"事后逐笔记账",这笔恰好记不到。
  sidecar 里也只有 `remote.currency: CNY` 而无金额,连兜底口径都是空的。
- 顺带的小面:`manju tasks` 页脚给零金额也标 `?`(`0 ?  (7 runs)`、
  `合计 0 (混合/mixed)`),而 `manju spend` 对同一账本印干净的 `0`
  ——上一波给 spend 定的「零金额不标 ?」规矩,tasks 没跟。

### F-03 「I=None LUFS」上一波修了 `manju masters`,共享引擎里还在

上一波结论表 #9 修的是 `manju masters` 输出(静音总线改说 `静音 (silent)`)。
但导出中心的行文案出自**另一处**——`build/exportstatus.py:845`:

```python
f"母版字节存在且时间线未变;实测 integrated {loud.get('integrated_lufs')} LUFS / TP {loud.get('true_peak_dbtp')} dBTP"
```

静音 stem 的 loudness 是 None,于是 CLI 的 `manju exports` 表与 GUI `/exports`
卡片(实测截图)都印:

```
母版字节存在且时间线未变;实测 integrated None LUFS / TP None dBTP
```

「实测 None」自相矛盾——None 的意思恰恰是没测到东西(该总线静音)。
一处修复,两处同源文案,漏了共享的那处。

---

## 二、GUI

### F-04 `/create` 页每次加载抛 JS 错(上一波修过的同款,第四个页)

`pages_t.py:57-65` 用整段注释记录了这个 bug 并在 /subtitles、/mixer、
/packaging 修掉:GLOSSARY_HEAD 已带 `/webclient.js`,再引一次就是
`Identifier 'ManjuApiError' has already been declared`。**`create_page.py:139`
是同样的双引**(`curl /create | grep -c webclient.js` → 2),于是 /create 每次
加载必抛同一个 pageerror。注释里自己写的话:"a real error on every page load
is noise the owner learns to scroll past, and the next genuine one hides in it."

### F-05 redo 之后,status 仍叫你 redo(GUI 同源)

```
$ manju status      # S003 spec 已变:manju redo S003 重做,…
$ manju redo --all-stale --yes    # 3 ran(S003 有了 take_02,当前 spec 产物)
$ manju status      # S003 spec 已变:manju redo S003 重做,…   ← 一字不差
```

新候选在盘上等着选(redo 当场那行「挑选: manju select …」是对的,但那行会滚走),
而**接管入口**从不改口。GUI 驾驶舱同一引擎:按钮仍是「S003 待更新—建议重做」。
上一波把 newtake 档排在 stale 之后是对的(旧 spec 的 take 不该推荐);
但**spec 变更之后新产的 take** 正是「选它」正确、「再 redo」浪费的情形——
stale 档在「已有新候选」时应当换措辞指向 select。

### F-08 「分镜页点新建镜头」——按钮不在分镜页

- 漏斗分镜档(`build/funnel.py:335`):「manju gui → 分镜页「新建镜头」点着建」
- GUI 新手引导(`gui/onboarding.py:99`):「在分镜区点"新建镜头"」
- 实测:按钮只在**工作台首页**(`/` 的镜头面板,`gui/page.py:4985`,
  建议 id 正确给 S008);而导航里真有一个叫**分镜工作台**的 `/storyboard`,
  上面**没有任何创建镜头的入口**(只有行内编辑/批量/锁)。
  照字面去 /storyboard 的人找不到门。

### F-10 `--readonly` 的禁用只做了驾驶舱,没做导出中心

`manju gui --readonly` 下:驾驶舱「新建镜头」正确禁用(updateGates());
`/exports` 的 15 个「生成 / 更新」**全部可点**,点了才吃 403:

```
{"error": "readonly mode — 只读工作台,操作请回到项目机器"}
```

服务端防线是对的(装了才知道),但只读模式的意义是**看一眼就知道不能动**;
现在每个按钮都在邀请一次注定失败的点击。

### F-14 board 项目页:花费 `0.0 None`

`board/board.py:2177`:

```python
spend = f'{st.get("total_cost", 0)} {st.get("currency", "")}'
```

status dict 里 `currency` 键**存在且值为 None**,`.get` 的默认值不生效,
于是页面印 `花费 spend: 0.0 None`(实测截图)。CLI 的 status 对同一 null
已经处理(印 `花费 0.0`)。与 F-03 同类:Python None 直通 UI。

### F-20 「标记已人工确认」一键落账,无确认、无撤回

导出中心的人工确认是 append-only 核验日志(设计正确)。但按钮**单击即写**:
我的自动化脚本一次误击就给剪映草稿记上了
`✓ human @ 2026-07-29T05:15:03`——而这条记录的语义恰恰是
「**人**在桌面 App 里开过草稿确认无误」。误触成本与语义分量不匹配;
卡片上也没有任何撤销入口(账是只增的,但至少可以追加一条「撤回」)。
备注输入框在旁边,极易空着就点。

### GUI 其余:干净

13 个常规页 + providers/routing/doctor/compare 专业页,除 F-04 外
**零 pageerror**;工作区选择器(项目外启动)清晰可用;构建按钮先弹
「构建前计划 (plan before build)/免费 / 本地/无事可做」再谈执行;审片页
动作齐全(含「复制给 Claude」);board --serve 正常、媒体 Range 可拖。

---

## 三、CLI

### F-06 第一次写镜头:报错说的是 pydantic 语,不是人话

新手最自然的写法(`action: 一句话`,bible 里全是这种自由文本字段):

```
✗ shots/S001.yaml: schema invalid — action: Input should be a valid dictionary or instance of Action
```

不给正确形状(`action: {main, emotion}`)、不给示例、不指 `manju schema`。
我照直觉连错 7 个文件才去翻 schema。与漏斗那三处「建议照敲即被拒」同构:
知道错了,不知道对的长什么样。

### F-07 打错字段名,永远没人告诉你

`ManjuModel` 是 `extra="allow"`(`core/models.py:44`,刻意宽容,合理)。
但我写的 `duration_ms: 5000` 被静默保留为 extra,真字段 `duration` 落回
`"auto"`——**七个镜头全错,check 全绿,没有任何一处提示**。宽容进档 +
校验哑然的组合,正是打字错误的完美藏身处。check 完全可以在不拒绝的前提下
对「未知键 ≈ 已知键」给一行 advisory(仓里已有 did-you-mean 基建)。

### F-09 推荐的免费 TTS,没有一条脚手架路走得通

`manju providers add edge --type tts` 的产物与指引:

```
adapter: generic_tts            # 要手改成 manju.providers.edge_tts:EdgeTtsProvider
  2. export the API key:  export EDGE_API_KEY=…   # Edge TTS 无 key,这步是误导
  submit.url: https://api.example.com/v1/tts      # Edge 用不上,得整段删
```

`--adapter` 枚举(generic_cloud|comfyui|local_cmd|generic_tts|generic_asr)里
**没有 edge**。README 说 Edge TTS 是"works today, no vendor account"的第一
选择,而唯一到达它的路是翻 README 手写 manifest。配上 F-16(voice 失败信息
也不指 `providers add`),免费配音的首次配置是全程无导航的。

### F-11 `locale add` 给无台词镜头也铸行

7 镜里 2 镜有台词,`manju locale add en` 却写出 **7 行**(5 行的 base_hash
是空串哈希)。把两句真台词译完:

```
en: ok=2  missing=5  翻译过期=0     ← 这 5 行永远 missing
```

`missing=0` 不可达;localized build 实测不阻塞(所以是噪音不是闸),但状态页
永远像欠着债,而且分不清「你还欠翻译」和「这镜头本来无词」。

### F-12 baseline 审批拒绝不点名

```
$ manju exports --approve-baseline --reason "首版可发布"
approval blocked: REQUIRED_EXPORT_STALE — resolve them, or pass --accept-known-risk …
```

「them」是谁?实际是剪映/CapCut 草稿 + 封面三项过期,但这句话不列,
只能回 `manju exports` 自己扫全表。评估引擎明明知道 blocker 明细
(`--json` 的 release_assessment 里有)。

### F-13 `manju history` 的 detail 是 Python repr 墙

```
[human]  relink_apply (summary={'restored': 1, 'restored_unverified': 0, …}, rows=[{'id': 'take:S003/take_01', …}])
```

单行 700+ 列、带引号和 None 的字典倾倒。上一波给 `manju events` 人类视图
写了摘要器(跳结构噪音、截长值、点名省略);`manju history` 是另一个入口,
没接同一个摘要器。

### F-15 一张参考图,静默 stale 半部片

`manju ingest`(zhou_shu_ref.png)登记 ref 会改 bible → 引用该角色的
S003/S005/S006 全部 spec 变更 → stale。ingest 输出只有 ✓,一个后果都不提;
下一步 build 对这三镜是**重生成**(付费供应商=真钱,虽有金钱闸兜底)。
`manju impact` 有整套影响预报,ingest 的 bible 写入没接一句。

### F-16 `voice --preview` 的失败信息是死路

```
TTS 不可用: no TTS provider configured — fill a tts manifest (§8.6, type: tts, adapter: generic_tts) or use the keyless EdgeTtsProvider; a voice cannot be repaired without one
```

三处问题:§8.6 指向仓里没有的设计文档(上一波剥的是 help 列表,运行时错误
信息里还有);"use the keyless EdgeTtsProvider" 无命令可抄
(该指 `manju providers add`);我请求的是**试听**,句尾却说"voice cannot
be repaired"——repair 语境串台。

### F-17 pullsheet 的 PDF 说明在断言一个从没探测过的事实

`cli.py:2921` 无条件附加:

```
⚠ pull sheet: CSV+MD in exports/pullsheet/ (PDF skipped — no headless-Chromium/PDF-table path in this environment)
```

同一台机器上 `manju doctor`:`✓ chromium (html_render): /opt/pw-browsers/chromium`。
这句是「PDF 未实现」穿着「你的环境缺东西」的外衣——UNKNOWN 被说成了环境事实,
和本仓「never guessed」的话风相反。

### F-21 `series new` 的下一步漏了 `cd`

```
$ manju series new 深夜食堂系列
created series …/深夜食堂系列
  下一步:manju series new-episode E01 --title <标题>     ← 原地照敲:
$ manju series new-episode E01
no manju series found from …/work upward
```

`manju new` 的同款下一步是带 `cd 雨夜便利店.manju &&` 的。一对孪生命令,
一个教全,一个教半。

### F-22 `analyze` / `segments` 的拒绝语

- `manju analyze <真实媒体>` → `fixture analysis needs --fixture <committed json> (contract §2)`
  ——「committed json」「contract §2」是维护者行话;店主拿着一个 mp4,
  读不出下一步是什么(实际含义:本地分析要预置观察集,云分析要过资质门)。
- `manju segments <不存在的路径>` → `bad report: … [Errno 2] No such file or directory: '…'`
  ——裸 errno 直出。

### F-23 按文档装 dev 环境,collection 就是红的

CLAUDE.md dev loop:`pip install -e ".[dev]" -c constraints.txt`。照做后:

```
7 errors during collection — ModuleNotFoundError: No module named 'httpx'
```

`test_board_serve.py` 等 7 个模块**直接 `import httpx`**,而 `pyproject.toml`
没有任何 extra 声明它——CI 绿靠的是 mcp-video→mcp 的传递依赖顺路带进来
(本沙箱的镜像连这条传递路都没走通,装了全部 extras 仍缺)。测试直接 import
的包应当进 dev extra,或 CLAUDE.md 那行补一句。

### F-24 纸割集(各一行)

- `manju qc brief` 首行:`判读标准:manju skills show visual-qc-review(先 manju skills show visual-qc-review 获取判读标准 A–J)`——同一命令一行两遍。
- `manju exports` 发布行:`blockers=0,5 项未生成…`——逗号后无空格,读成小数 0,5。
- `manju prompt` 的 Scene 行是字典平铺:`Scene: desc: …, name: 雨夜便利店`(name 在尾、键名外漏),对比 Characters 行的人话格式;**这文本会真进供应商 prompt**。
- README CLI 表把 `--approve-baseline …` 写成带值参数;实际是 bare flag + `--reason`(照表敲吃 usage error,rc=2 正确)。
- `manju relink apply`:take 行与 timeline_source 行指同一文件,恢复一次后第二行报 `拒绝: target_already_present`——「已被上一行满足」被说成拒绝。
- EDL import-plan 的 7 个 window 全叫 `take_01`(8 字 reel 名丢镜头身份)——格式如此,但可在行尾附源镜头注记。

---

## 四、MCP(整体是三个面里最扎实的)

值得写进报告的**好**:26 工具 schema 一致用 `shot_id`;坏参数报
`invalid_argument` 并指 inputSchema;坏 JSON 回标准 `-32700`;锁违规写入被
拒且盘上零残留;CAS(`expected_rev`)冲突信息给全恢复路径;`build`/`redo`
的金钱闸 fail-closed(`assume_yes is True`,连 JSON 字符串 "false" 都防了);
unattended profile 把 `redo`/`director_confirm` 从 tools/list 摘掉、调用回
`agent_profile_denied` 并写明协作路径回去——教科书级。

剩余两条:

### F-18 可分支失败大多还是 `code:"error"`

锁违规写入、`director_execute` 打到 done/不存在的提案、CAS 冲突、未知工具,
全部 `code:"error"`。上一波的错误码普查给 CLI 立了「同一事实同一 code +
文档反捏造」的契约;MCP 面这些**恰恰是 agent 最该分支的失败**(锁违规→写
proposal;CAS→重拉 get_shot;未知提案→重新 propose),仍在未分类桶里。

### F-19 MCP export 的格式面是 CLI 的三分之一

`mcp/tools.py:594,610`:仅 `srt|otio|jianying`。CLI 有
srt/ass/vtt/ttml/otio/edl/fcpxml/xmeml/jianying/capcut/pullsheet。
agent 被指望走完整工作流(skill 里也这么教),但 capcut/ttml/edl/fcpxml/
xmeml 只能落回 shell。若是刻意收窄,错误信息也该说「其余格式走 CLI」。

---

## 五、这轮我自己犯的错(照例留档)

1. **管道吃掉退出码,险些报三条假「rc=0」**:`manju … | head; echo rc=$?`
   拿到的是 head 的 0。用 `>/dev/null` 复测后,relink/analyze/segments 的
   refusal 全部 rc=1、usage error rc=2,**没有任何 rc 缺陷**;上表相关条目
   只保留信息质量问题。教训与上一波「先怀疑测具」同款。
2. **自毁前提的锁测试,险些报一条假「MCP 绕锁」**:我先 `git checkout --`
   还原过 S001.yaml——锁本身在文件里,一并被还原掉了;之后 MCP 改写成功是
   因为**锁已不存在**。重新上锁复测:MCP 正确拒绝、零残留。
   (顺带确认:文件级回滚会连锁一起回滚,这是「truth is text」的正常语义。)
3. Playwright 首测 storyboard 用文本选择器没找到按钮就差点断言「按钮没了」;
   换 DOM 排查后确认按钮在驾驶舱,产出的是 F-08(指路错)而非「功能丢失」。

## 六、扫过而没有问题的

pack → fixity → unpack 往返(114 文件核验)· relink report/plan/apply
按哈希恢复 · migrate inspect/plan/apply/downgrade 来回(损失行、revert 命令、
CAS)· doctor(含 backup 行)· impact 预报 · explain --cost · perf/evaluate
的诚实声明 · qc tech/conformance/captions(UNKNOWN 不装 PASS)· masters ·
openclap export/inspect · EDL/FCPXML import-plan(零写入)· ingest 批次评审 ·
lib add/list(双 --tag 正常)· transcribe --text · align 提示链 · series
new-episode/status · snapshot/rollback(无可回滚时给替代)· lock/unlock 的
交互性边界 · watch --once · gc · propose · routing explain · providers
catalog/check · tasks manifest · director propose→confirm→run 全链 ·
Edge TTS 经代理真合成(preview 缓存命中)· CJK+空格文件名全程 · 中文项目名
的所有导出路径 · GUI 工作区选择器 · board --serve 全部标签页。

## 七、边界

- **只测不修**:本报告零代码变更,全部发现待后续波次按红-先行流程逐条处置
  (F-01/F-02 建议优先——一条是契约一致性,一条是付费可见性)。
- 全量测试套件与本报告无交集(无代码变更);collection sanity 5798 条通过
  (补 httpx 后,见 F-23);快循环(`-m "not ffmpeg"`)5689 passed / 0 failed。
- Windows 侧(`--app`、msvcrt 真行为)本沙箱依旧无法验证,与上一波结论不变。

---

# 第二轮(同日续测)

继续往之前没走到的深处走:GUI 编辑器逐个交互(字幕接管/还原、混音、打包、
镜头实验室、剪辑页)、修复 op、audition、逐语言 QC、视觉判读回填闭环、
align/roundtrip(带边车 apply)、bridge、tool、qualify、series sync、
support-bundle、损坏归档、交付 bundle、ingest 评审生命周期、任务恢复错误路径、
陈旧标签页守卫。新发现 6 条,其中一条是**完整断裂的闭环**。

## R2-1 v2 视觉判读闭环:每个消费端都断(本轮最重)

`manju qc brief --json` 的 `verdict_contract` **指示** agent 用
`manju.qc.verdict/v2` 形状回填(回显 packet_id + observations + findings)。
照它说的做,四个消费端四种下场:

| 消费端 | 结果 |
|---|---|
| CLI 人类输出 | **写入成功后当场崩栈**:`KeyError: 'levels'`(`cli.py:2425` 读 `result["levels"]`,而 v2 intake 的返回是 `{"written","bindings",…}` — `agent_review.py:1159`);满屏 Traceback,rc=1 |
| `manju qc`(汇入) | `agent_verdict_items`(`agent_review.py:1673`)走 **legacy** 读取器 `_read_records`,按 DR02 ruling #1 **跳过一切 v2 行** → v2 的 `findings` 永远不出现为 [AI判读] |
| `manju qc coverage` | 同一个 legacy 读取器 → 刚判读完的镜头显示 **never**(实测:qc_agent.jsonl 里躺着 `binding: "bound"` 的 S001 判读,coverage 报 `S001: never`,过期 0) |
| assurance(验收) | 唯一读 v2 的地方(`read_v2_records`),但没写 must_show/avoid 期望的镜头上,一条 findings-only 判读**没有任何可见落点** |

净效果:agent 按文档走一遍视觉 QC,产出**写进了盘、绑定了字节,然后在每个
人看的界面上都不存在**;CLI 还附赠一个栈。`--json` 路不崩(`_emit(result)`
不读 levels);MCP `qc_verdict` 也不崩 —— 但不可见性三处相同。

## R2-2 交付 bundle 的出界拒绝,漏的是 pathlib 内脏

```
$ manju exports --profile master --bundle --output <项目外路径>/delivery.zip
'/tmp/…/delivery.zip' is not in the subpath of '/tmp/…/雨夜便利店.manju' OR one path is relative and the other is absolute.
```

「bundle 必须落在项目内」这条规矩本身合理(项目内路径实测正常出包,
15 entries + SHA256SUMS);但拒绝语是 `Path.relative_to` 的异常原文直出
(连 "OR one path is relative…" 的库文案都带着),既没说规矩是什么,也没给
一个能抄的项目内示例。对照:`support-bundle --out` **允许**项目外路径 ——
两个"打包给人"的命令,一个能出去、一个不能且报库内脏,规矩本身也值得对齐。

## R2-3 陈旧标签页守卫:拦住了,但话是给开发者说的

DECISIONS #45 的场景实测:A 项目页面开着 → 同端口换 B 项目的服务 → 从旧标
签页点保存。**写入被正确拒绝**(403),盘上零变化 —— 守卫成立。但用户看到的
toast 是 `missing or invalid X-Manju-Token`:一个全中文工作台里的开发者行话,
没说人话(「这个标签页连着旧会话/别的项目,请刷新」)。顺带:旧标签页的
**读**请求(`GET /api/jobs` 等)对新项目照常 200 —— 旧 UI 壳可能安静地拿到
另一个项目的数据重绘,靠 `manju-project` meta 的客户端检测兜底,值得确认
每个轮询路径都真的接了。

## R2-4 support-bundle 的脱敏摘要是 Python repr

`redaction: {'events_included': 157, 'events_malformed': 0, …}` —— 与
F-13(history)同类的 repr 直出,只是规模小。同一行里 `self-scan ok: True`
的 True 也是 Python 字面量。

## R2-5 ingest 评审动作只回显序号

`manju ingest-confirm <批次> --all-matched` → `[1] confirm ✓`。
五十项的批次里,「[7] [12] [31] ✓」不告诉你确认了什么;`ingest-review` 里
文件名明明都在。每行带上文件名是一行的事。

## R2-6 驾驶舱空计划弹窗没有出口(小)

构建计划为空时,弹窗只有「取消 (Cancel)」—— 想强制走一遍
compile+render 的人在驾驶舱没有门(/edit 页有「重新构建」)。
引擎判空是对的(实测 CLI build 同刻也是 skip);这是「没门」不是「判错」,
排低优先。

## 第二轮扫过而干净的

字幕编辑器完整接管闭环(改 cue → 原生 confirm 弹窗 → mode: manual 落盘 →
「还原自动字幕」按钮出现 → 还原回 compiled)· 混音/打包/镜头实验室/剪辑页
加载与控件 · /edit 的脏标记与引擎 explain 对账一致(unbuilt:false 时不亮)·
repair trim/retime/inout(全部追加新 take、指名 select)· qc --deep /
--lang en / --all-locales · audition 构建(audition_v1.mp4)· MANJU_ACTOR
事件归属 · 截断 .manjupkg 的 fixity/unpack 拒绝语(一行人话,不崩)·
fixity --info · 项目内交付 bundle · ingest confirm/flag/discard 状态机 ·
tasks attach-remote-job / abandon 对不存在 submission 的拒绝(指向
`manju tasks --json`)· doctor --windows 在 Linux 上不炸 · help-workflow
recover(每步是真命令)· align --from-srt(timing.json 落盘并指下一步)·
带边车的 roundtrip(检出我在 OTIO 里的那一刀 → apply 1 条 → check ok)·
series sync-bible 预览 / status --health · bridge plan(帧文件绑定 + digest,
`run` 在无合格供应商时按门拒绝)· `manju tool trim` 白名单解析。

## 第二轮我自己犯的错

1. **原生 confirm() 弹窗被 Playwright 默认驳回**,差点把「字幕保存二段确认」
   报成「静默丢弃」——真人会看到弹窗,是我的自动化把它按了取消。接上
   dialog-accept 后全流程正确。(本会话第三次:先怀疑测具。)
2. `pkill` 波及自己 shell 的作业控制,两轮 exit 144 之后才把服务进程改成
   `start_new_session` 拉起 —— 测试脚本的卫生问题,不是产品的。

## 第二轮之后的处置建议(合并第一轮)

优先级从高到低:R2-1(断裂闭环 + 崩栈)→ F-01(闸门跨面一致)→
F-02(付费可见性)→ F-04/F-03/F-14(同类 None/双引脚点,一次扫清)→
其余按表逐条。全部适合红-先行小步落地;R2-1 建议先补
「v2 也计入 coverage/汇入」的行为测试再动读取器,避免把 legacy 语义改坏。

---

# 修复波(同日落地)— TRISURFACE-FIX

店主发话「fix」。按上表优先级落了一波,**逐条红-先行**(每条新测试都先在
修复前的代码上验证过红,含两条以 ImportError 形式红的),四个新测试文件共
34 条:`test_trisurface_verdict_loop.py`(8)、`test_trisurface_export_gate.py`
(6)、`test_trisurface_voice_ledger.py`(5)、`test_trisurface_polish.py`(18,
含 3 条守卫)。细节见 DECISIONS.md `TRISURFACE-FIX` 节。

## 已修(18 项)

| 项 | 落点 |
|---|---|
| R2-1 v2 判读闭环 | intake 返回补 `levels`(CLI 人类分支不再崩且 `.get` 双形状 + 印 `bindings`);`qc_coverage` 把 v2 记录按 packet 字节归一进同一比较;`agent_verdict_items` 折叠 v2 findings(shot + unit 两档,字节一动照旧 已过期);legacy 行为逐字节守卫 |
| F-01 final_export 闸 | 一个 owner `build.graph.final_export_gate`(与 spend_gate 同址同 WaitingUser);CLI 三处(export/openclap/package)改走它,消息逐字未变;MCP `export` 接闸 → 结构化 `waiting_user`,`assume_yes is True` fail-closed,schema 增 optional 参数(DR05 特征化钉允许 ADD);GUI 刻意不接——人点按钮即是该 token 要的确认,理由写进 owner docstring |
| F-02 配音/手动进账本 | `register_voice_take`(全部配音路的咽喉,含 locale 标签)与 `register_manual_take`(select --file + ingest 视频 take)落 live 行;**live ≡ rebuild 钉为行为测试**(同一 multiset);账本失败绝不影响登记(best-effort 守卫) |
| F-03 None LUFS | `exportstatus._loudness_clause` 一个 formatter:静音说静音,单轴缺说 —,CLI exports 表与 GUI 卡片同源同修 |
| F-04 /create 双引 | 移除第二个 webclient.js(pages_t 注释里那只 bug 的第四份拷贝),页面渲染测试钉「恰好一次」 |
| F-05 stale 死循环 | stale 档发现「按当前 spec 重做的候选」时改口 `manju select <shot> <n>`;key 仍是 `stale`(agent 分支不动),无候选时措辞照旧(双向测试) |
| F-06 schema 报错教形状 | check 的校验错误对 Action/Dialogue/Camera 附「应为 {…}」+ `manju schema` 指路 |
| F-07 近似键 advisory | check 对「与真实字段近似的未知键」发 warning(`duration_ms` → `duration`);不相干的自由键保持沉默,extra=allow 语义不变、永不 gate |
| F-08 分镜按钮指路 | 漏斗分镜档改指「工作台首页镜头面板」——分镜工作台页本来就没有那个按钮 |
| F-12 审批拒绝点名 | `_approval_blocked_message` 逐个列 `CODE[scope]`,不再让人自己扫表 |
| F-14 board `0.0 None` | 币种 present-but-None 归一为空 + 不留尾随空格 |
| F-16 TTS 死路信息 | 一条共享 `TTS_UNCONFIGURED_MESSAGE`(三处 raiser 同源):给 `manju providers add` 的真命令 + Edge adapter 行 + 手动投放逃生门;§8.6 与「repair」串台措辞删除 |
| F-17 pullsheet 诚实注 | 「PDF 输出未实现」——不再断言没探测过的环境事实 |
| F-21 series cd | `series new` 下一步补 `cd <目录> &&`(与 `manju new` 同款礼貌) |
| F-23 httpx | 进 dev extra(七个测试模块直接 import 它) |
| F-24a/b | qc brief 判读标准行只出现一次命令;READY 行 `, N 项` 加空格不再读成小数 |
| R2-2 bundle 出界 | `_display_path`:项目内相对、项目外绝对——写成功后的成功行不再崩栈(出界 --output 本就是 delivery guard 允许的) |
| R2-3 令牌 403 | GUI/board 同一条 `TOKEN_403_MESSAGE`:机制名保留 + 人话(旧标签页→刷新) |

## 未修,留档(7 项,均为设计决定或超出本波边界)

- **F-09** Edge TTS 脚手架路径:需要 `providers add --adapter edge` 模板这一
  真功能;F-16 的新消息已把路指全,升级为模板另起一波。→ **第三轮已修**
- **F-10** readonly 下导出中心按钮可点:服务端 403 是对的;显示层禁用是
  GUI JS 改动,与 F-20 一起归 GUI 打磨波。→ **第三轮已修**
- **F-11** locale 给无台词镜头铸行:改的是 lines.yaml 形状或 status 语义,
  需要先定方向(过滤显示 vs 不铸行),不该顺手。
- **F-13** history 的 repr 墙 / **R2-4** support-bundle 摘要 repr:同一个
  「接 events 摘要器」活,合并处理。→ **第三轮已修**
- **F-15** ingest 改 bible 的后果静默:该接 `impact` 的预报,属功能接线。
  → **第三轮已修**
- **F-18/F-19** MCP 错误码税目扩展 / export 格式面追平 CLI:两者都动
  agent 契约(错误码文档反捏造测试、surface digest),值得单独一波。
  → **第三轮已修**
- **F-20** 人工确认一键落账无撤销:append-only 核验日志的撤回语义要先
  设计(追加「撤回」事件?),不该在打磨波里顺手定。

---

# 第三轮修复(店主「continue find and fix」)

上表七项遗留里,五项经细看**并不需要新的设计决定**,同日红-先行落地
(`tests/test_trisurface_round3.py`,12 条,先红后绿;DECISIONS `TRISURFACE-FIX`
第 9-12 条):

| 项 | 落点 |
|---|---|
| F-13 + R2-4 | 事件 detail 摘要器升为唯一属主 `core.events.event_detail_brief`,`manju events` 与 `manju history` 共用(history 不再印 700 列 Python repr);support-bundle 摘要行改 k=v(`self-scan: ok`,不再有裸 True/字典) |
| F-15 | `apply_ingest` 结果新增 `staled_shots`(additive):登记参考图后,引用该资产而**过期**的镜头逐个点名;CLI 打 ⚠ 后果行(实测:`引用 lin_xiaoyu 的 4 个镜头已过期(S002, S003, S004, S006)`),并说明 §4.3 默认不重做与金钱闸 |
| F-09 | `manju providers add <id> --type tts --adapter edge` 直接铸出**完整可用**的 keyless 清单:无 auth、无 submit、无 ★ 待填(专用头注释,不再印「fill the ★ fields」/「export the API key」这两个对 Edge 全错的步骤);`providers check` 即绿;TTS 未配置消息改指这条一步路 |
| F-22 | `analyze` 拒绝语说清「观察集 JSON」与 `--provider` 资质门两条真路(contract §2 行话删除);`segments` 对不存在的报告不再吐 `[Errno 2]`,点名 `manju analyze … --write` 是产出命令 |
| F-10 | 只读工作台的导出中心:15 个「生成/更新」「标记已人工确认」与备注输入框**渲染即禁用** + 「只读 readonly」横幅 + 批量按钮隐藏(实测 curl:15/15 disabled);服务端 403 原样保留(纵深不变) |
| F-18 | MCP 可分支失败获得专码:`locked_field`(→写 proposal)、`rev_conflict`(→重新 get_shot)、`unknown_tool`(→重读 tools/list)、`unknown_proposal`(typed 子类;→重新 propose);错误码技能新增 **MCP 专属小词表**(放在 CLI 分类块之外,反捏造扫描只认 cli.py,两张词表刻意分治) |
| F-19 | MCP `export` 追平 CLI:formats 枚举扩到 srt\|vtt\|ttml\|otio\|edl\|fcpxml\|xmeml\|jianying\|capcut(ADD-only),走 CLI 同款 exporter 调用与同一把 build lock;srt/vtt 报齐三个字幕兄弟文件;capcut 缺库时 ExporterUnavailable 进信封 |

仍然留档不动:**F-11**(locale 行形状)与 **F-20**(核验日志撤回语义)——
两者动的是契约的**含义**而非措辞,要么有自己的 DECISIONS 条目,要么不动。
→ **第四轮各自拿到了自己的 DECISIONS 条目并落地(见下)**

---

# 第四轮:最后两项,一项做了决定、一项做了最小护栏

(DECISIONS `TRISURFACE-FIX` #13-#14;`tests/test_trisurface_round4.py`,7 条,
红-先行,其中「后补台词变 missing」一条在旧行为下本来就绿——留作守卫。)

**F-11 的决定:无台词镜头不是翻译欠账。** `locale add` 不再给无台词镜头铸
hash-of-empty 行;`line_status` 对它们(含盘上遗留的空行,零迁移)回答
`not_needed`(无台词);**后补台词的镜头恰在那一刻变回 missing**(钉住);
**删掉台词后遗留的译文仍报 翻译过期**(那行需要人看,不静默)。CLI 只在
非零时印 `无台词=N`,非可操作行不进列表;localized 交付清单不再把幻影
missing 记为诊断。绿从此可达:实测两句真台词译完 → `missing=0`。

**F-20 的最小护栏:永久誓言前先问一次。** 标记已人工确认 的语义是「人已在
桌面 App 打开过该草稿」,一次误触就永久落账(第一轮我的自动化真误触过)。
按钮现在先弹原生 confirm(),写明在赌什么、且不可撤销 —— 与字幕接管同款
模式。**撤回语义仍然留档**:护栏挡住误触,不发明「反誓言」。

顺带扫过而干净的(第四轮):presets 三档列表与 `--preset vertical_ai_video`
建项(30fps 正确)· `create synopsis/beats` 模板生成 · `lib show/use/rm`
(use 的不覆盖后缀、rm 的 --yes 确认)· `compare --candidate` 的拒绝语
(我传错了用法,它答对了)。

---

# 加练轮(店主注资:「做一切可以做的,消耗我的额度,同时有用」)

三个阶段,全部真跑(DECISIONS `TRISURFACE-FIX` #15)。

## A. 视觉 QC 闭环第一次被真的用了

我(视觉模型)当判读员,在雨夜便利店项目上走完整环:给四镜写 `must_show`
期望 → `qc brief` 出题 → **亲眼读了 21 张评审帧** → 回填 15 条 v2 判读
(7 镜 + 8 个一致性组合)→ 覆盖率 **7/7 + 8/8** → 汇入 qc.md。结局全部诚实:

- 两个占位镜头(S001/S007 是彩条测试卡)被 assurance **如实拒收** ——
  「雨夜街道」「便利店夜景」不在画面里,这就是素材的真相;QC 从此永久
  盯着这两镜等真素材,这正是这套闭环存在的意义。
- 一条真发现:两字台词「谢谢。」被卡片断行器折成「谢/谢。」两行。
- 文字卡上无人物形象,八个一致性组合如实判「本轮不可判」而非硬给结论。

## B. 三条路径,三部完整的片子,零新缺陷

- **剧集**:深夜食堂 E01 全漏斗出片 + 配音;bible 从系列下发,E01 本地改动
  被**报告为分歧而非覆盖**;E02 建立后 season health 如实 NOT READY。
- **双语**:en+ja 双 locale 各自 Edge 配音、各自成片、逐语言字幕;
  `locale add` 只铸有台词的行(F-11 实地:`+2 lines`,`无台词=1`);
  `qc --all-locales` 两语齐检 aggregate=pass。
- **实拍修复**:四段真素材按命名约定 ingest(空镜头自动选中)、全片
  `xfade_fade` 转场在钉的 6.1.1 上零抖动、trim/retime 修复后重选重建、
  cover+teaser 都出了。唯二的绊脚是我自己选错 take 号 —— 解析器诚实拒绝
  并列出现有 takes。

## C. 崩溃安全战役(`tests/test_crash_safety_campaign.py`)

- **属性测试**(hypothesis):有理帧率网格 round-trip 恒等 + 投影误差
  严格小于半帧(int 与 1001 族同验)· 事件摘要行有界且永不 repr ·
  locale 行状态机对任意 (base, entry) 世界恰答一个诚实状态。
- **真 kill -9 注入**:对真实构建进程分批 SIGKILL,每次死后逐条验 §3 纪律
  ——真相 YAML 无一撕裂(原子写)、已有媒体字节分毫未动(只增)、事件尾
  可读、runtime 可重建、check 绿、恢复构建照常出片;并断言**确实杀中过**
  进行中的构建(否则测试自己喊「什么都没证明」)。
- **第一枪就是真收获**:hypothesis 找到事件 detail 键/值含换行时「单行
  摘要」变多行(错误信息真的会带换行)—— 已在唯一属主 `event_detail_brief`
  修复(折叠空白;`--json` 原文照旧),红-先行。

既有测试改动两处、均非弱化:`test_events_human_digest.py` 的 import 从
`manju.cli._event_detail_brief` 改指新属主 `manju.core.events.event_detail_brief`
(函数搬家,断言一字未动);`test_providers_routing.py` 的脚手架参数化测试
自动吃进了新别名 edge/edge_tts 并按「必有 ★」断言而红 —— Edge 模板**本来就
无字段可填**,该测试对这对别名改断**更强**的反向命题(不许出现 ★),其余
适配器的「必须标 ★」一字未动。

一条测试基建观察(当时记「无机制不动」):`test_mcp_copilot_e2e` 的两条 wire
测试在手工挑选的重负载批次里偶发 `queue.Empty` 超时,单跑与全量(`-n auto`
门配)均稳定绿。**后记:机制随后被锁定并已修复,见下文「门禁揭示轮」——
当时「不动」是对的(没锁定机制前改数字就是掩盖),但「无机制」错了。**

## 修复波里我自己犯的错(照例留档)

**F-05 的第一版修复从来不会在真项目里生效,而我的测试是绿的。** 我在检测
「按当前 spec 重做的候选」时用了 `compute_spec_hash(shot, bible)` 的默认参数,
而流水线盖进 sidecar 的是 `version=SPEC_VERSION, project_root=…` 的哈希
(build/stale.py 逐字如此)——两个值永不相等,分支永不触发。测试却绿,
因为**测试夹具的 sidecar 是用同一个错误调用铸的**:我在用自己的实现验证
自己的实现。是「改完后回到真项目重跑发现处」抓住的:S003 刚 redo 完,
status 照旧喊 redo。修正:检测改用 stale.py 的同款逐 take 判据
(per-take spec_version + project_root),测试夹具改按流水线的真实铸法。
上一波写过「桩测试比没有测试更危险」——这次差点原样重演,救回来的是
**修完必须回真项目走一遍**这个流程,不是测试本身。

## 改了一个既有测试,不是弱化

首轮全量抓出唯一一红:`test_cycle2_bugfix_20260715.py::test_rebuild_counts_voice_spend`。
读栈才发现它把 **state.sqlite 文件路径**当项目根传给了 `RuntimeState`(该类自己
拼 `.manju/state.sqlite`)——此前能绿只因登记时刻真正的 DB 文件还不存在,那个
畸形嵌套路径被静默 mkdir 成了目录;F-02 的实时记账让真文件先出现,畸形路径
立刻 `NotADirectoryError`。修的是测试**自己的构造错误**(全仓 grep:只有这
一处这么传),断言一字未动——rebuild 仍须导出该行与 2.0 花费。这正是
F-02 附带价值的一个实例:实时账本让一个潜伏的误用当场现形。

## 验证

- 新增 34 条测试全绿,每条修复前验证过红;`ruff check src/ tests/` 干净。
- **全部 18 项修复在真项目上逐条回放过原始复现步骤**:v2 判读回填不再崩、
  [AI判读] 项(含修复前留下的旧记录)出现在 qc.md、coverage 2/7 reviewed、
  MCP export 回 `waiting_user`、S006 新配音即时出现在 `manju tasks` 第 25 行、
  S003 的 stale 待办改口点名 take_03、静音母版行改说「该总线静音,无响度可测」。
- 邻接模块逐簇跑过:verdict 簇 114 条、MCP/gate 簇 133 条、ledger 簇
  359 条,全绿(其中 e2e 两条首跑红是我残留的 GUI 服务进程吃满 CPU 所致,
  杀掉后与改动无关地复绿——留档为测具卫生教训)。
- 全量套件(闸门同配置,ffmpeg 6.1.1):**5816 passed, 0 failed, 19 skipped**。
- **CLI surface 未变**(只改消息文本与内部实现,`test_fp_cli_snapshot` 全绿);
  MCP export schema 为 ADD-only(特征化钉明示允许);CONTRACTS schema ids 未动。
- **未验证**:`windows-ci.yml` 本环境无法运行,与前两波同界——改动无一触碰
  msvcrt/路径转义等 Windows 专属层,但 Linux 绿 ≠ Windows 绿,这句话上一波
  就写过,这里照写。

---

# 门禁揭示轮(2026-07-30:Windows 硬门自证了一次价值)

上一节的「未验证」一句写完不到一天就被兑现了:两轮合并触发的
`windows-ci.yml` 自动跑(ebcbfb0 与 0a02eb7)都是红的——两次都是
**同一条、且只有这一条**失败:`1 failed, 5800 passed, 55 skipped`。

## 红灯是我自己的测试,不是产品代码

`tests/test_trisurface_polish.py::test_display_path_handles_inside_and_outside`:

```
AssertionError: assert 'D:\\tmp\\trisurface-outside.zip' == '\\tmp\\trisurface-outside.zip'
```

根因:测试对「项目外路径」断言了 `str(outside)` 字面值,而 `_display_path`
的契约本来就是降级为 `str(Path(path).resolve())`;Windows 上 `Path("/tmp")/x`
是**无盘符路径**,`resolve()` 会把它锚定到当前盘(`D:\tmp\...`),字面值
自然不等。**产品代码是对的,错的是测试的期望。** 修法:改断平台解析后的
形态——`Path(shown).is_absolute()` 且 `Path(shown) == outside.resolve()`。

上一波原话「Linux 绿 ≠ Windows 绿」——这回验证这句话的恰好是写下它的
会话自己的测试行。硬门的存在意义(在店主的第一平台上跑同一套件)与
「远程 workflow_dispatch 可以代替本地 Windows 验证」这两件事,都实证了。

## 载荷敏感对策:#15 的「无机制」观察,机制已锁定并修复

加练轮留档说 `test_mcp_copilot_e2e` 两条 wire 测试「偶发超时,无机制不动」。
本轮把机制钉死了:

- **机制**:`tests/test_mcp.py` 的 `MCPClient.request` 与
  `tests/test_ledger_p1_gui_safety.py` 的 `_wait_job` 各自包着**真实工作**
  (走线的完整构建、五个 GUI 任务含一次真 build)却只给固定 10s 死线——
  CPU 抢占之下真实工作合法地超过 10s,死线先响。
- **确定性复现**:起 6 个 CPU 自旋进程再跑该批 → **3 条应声而红**;
  撤掉自旋、同批 21 条全绿(87s)。要红就红、要绿就绿,不是玄学。
- **修法**:两处死线 10s→120s(附注释留档)。死线是**故障检出延迟**,
  不是断言——内容断言一字未动,挂死仍然会被抓,只是给真实工作留出
  抢占余量。修后在**同样的自旋负载下**复跑:21 passed。

## 验证

- 三个测试文件定点跑全绿(polish 18 条、mcp+ledger 45 条);
- 全量套件(闸门同配置,ffmpeg 6.1.1):**5842 passed, 0 failed, 19 skipped**;
- 合并后重派 `windows-ci.yml`,以合并后的默认分支拿到 Windows 绿灯为收口
  (dispatch 的 b006352 run 含旧测试,预期红,不作数)。

---

# 卡片渲染波(2026-07-30:视觉发现的正式收账 + 复现时又量出一处)

加练轮 A 阶段留下的那条真发现(「谢谢。」折成两行)一直挂账未修。本波
从零复现时,把它修了,还顺带用像素测量抓出第二处一直存在但没人报过的
缺陷。两处都在 HTML 卡渲染器 `media/html_card.py`,一个属主,三个消费口
(caption_card provider、packaging 封面/预告卡、GUI 实时预览)自动同愈。

## 1. 短台词必折行(视觉 QC 的原始发现,机制钉死)

- **复现**:`render_card_png('谢谢。', 1080x1920)` → 「谢/谢。」两行;
  1920x1080 同样折。**任何**能放进一行的台词都会被折掉尾巴——不是
  「谢谢。」特殊,是所有单行卡。
- **机制**:`.card{max-width:82%}` 挂在匿名收缩包裹(shrink-to-fit)的
  flex 子块**里面**。CSS 循环百分比规则:内在尺寸阶段忽略该 max-width
  (包裹宽 = 文本单行宽 W),布局阶段再按 0.82·W 收紧 → 尾字永远被挤下去。
- **修法**:宽度上限移到 flex item 本身(`.stack{max-width:82%}`,百分比
  对确定的 body 解析),`.card` 不再限宽。长文本折行位与修前一致
  (82% 帧宽),chapter 模板的 `.wrap` 本来就是 flex item、无此病。

## 2. 底部 87px 白带(测量抓出,从未被肉眼报告)

- **测量**:修折行前后跑帧行分析,竖横两向渲染的卡**底部恒有 87 行纯白**
  (1080x1920 → 行 1833-1919;1920x1080 → 行 993-1079;drawtext 卡为 0)。
- **机制**(三组对照实验钉死):这版 headless Chromium 的**布局视口比
  --window-size 矮 87px,截图面却是全窗口**;视口之外什么都不栅格化
  (绝对定位红块放在 1833 之后不出现),唯一能到达那 87 行的是 canvas
  基色——纯色背景可作基色(全表面延展),渐变不能(回退纯白)。
- **修法**:canvas(html/body)只放**纯色边缘色 bg_edge**(每模板/每
  preset 的数据字段,声明渐变出帧边的色调,不解析 CSS);真渐变画在
  `body::before{position:fixed;inset:0}`。健康版本上 ::before 全覆盖、
  bg_edge 永不露面;受影响版本上底带变成与渐变末端 Δ≈1-2/255 的平色。
  卡内容始终垂直居中,永远不进底带区。
- 这条也解释了为什么加练轮我肉眼漏了它:QC 评审帧四边留黑裁切时,
  白带混进了「帧边」预期;逐行均值一量就藏不住。

**存量说明(店主须知)**:媒体只增,已有项目里旧模板渲出的卡 take 不会被
自动改写;spec 未变的镜头 `manju status` 也不会喊。想让旧片吃到新渲染,
对相应镜头 `manju redo <shot> --yes` 重渲(本地免费)再 `manju select` 即可;
不重渲的旧 take 原样保留,这正是只增纪律的本意。

## 验证

- 红-先行:新文件 `tests/test_card_visual_fixes.py` 7 条(短台词单行·两向 /
  长台词仍折 / 全帧涂满·两模板两向),修前 6 红 1 绿(长台词本来就折),
  修后全绿——断言全部落在**渲染像素**上(chromium+ffmpeg 门,test_round5
  同款前置)。
- 字节恒等钉未弱化:`test_edit_v3` 的 preset("")≡无preset 自洽 + 子串
  断言原样通过(它钉的是「preset 机制不漂移」,不是历史模板字节)。
- 邻接簇:edit_v3/round5/round_a/packaging/provider_floor 共 86 条全绿。
- 亲眼验收(与发现同一双眼):「谢谢。」单行居中、渐变铺满到底、无白带;
  长台词折三行、「。」不落行首(浏览器 kinsoku 正确)。
- 全量套件(与门禁第二轮修复同跑,ffmpeg 6.1.1):**5849 passed, 0 failed,
  19 skipped**。

---

# 门禁揭示·第二轮(2026-07-30:PR #29 的延迟 Windows 全量,3 失败全数收账)

PR #29 合并后,门禁在其 head(5c5cdd9,与合并进默认分支的内容相同)上还是
跑了一次完整 Windows 全量:**3 failed, 5803 passed**。display_path 修复本身
生效了(它不在失败名单里);三个新失败,两个是加练轮测试自己的平台病,
一个是又一处载荷敏感死线。全部当轮修掉:

1. **`signal.SIGKILL` 在 Windows 上不存在**(`test_crash_safety_campaign`
   的注入循环 AttributeError)。改用 `Popen.kill()`——POSIX 上就是 SIGKILL,
   Windows 上是 TerminateProcess,恰好正是店主故事里的「任务管理器杀」;
   同样突然、不可捕获、零清理,注入语义分毫不变。
2. **hypothesis 在 Windows 掷出 `base='\x85'`(NEL)戳破了我的状态机模型**。
   真相:YAML 1.1 往返把 NEL 行折叠成空格——落盘后的 truth 是 `' '`,
   而我的夹具用**落盘前**的字符串铸 hash、模型也用它预测(F-05 那课的
   属性测试版)。产品行为完全符合 DECISIONS #13(有译文时 hash 一致性
   说了算:匹配=ok、不匹配=翻译过期、孤儿行含在内;没译文才分
   missing/not_needed)——实测三格矩阵钉死后,模型照文档重写,夹具改从
   **重读的落盘 base** 铸 hash(与 locale add 同款)。测试更强了:现在
   它连「YAML 规范化会改写 truth 字符」这件事一起验。
3. **`test_fp_board_compare` e2e 的 httpx 默认 5s 死线**:那个 GET 在服务端
   为每对边界**真跑 ffmpeg 抽帧**,windows-latest 抢占下合法超 5s
   (上一次 Windows run 它绿,本次红——载荷敏感的标准指纹)。放宽到
   120s,断言未动;纯文件服务的两条 GET 不动(无证据不改)。

顺带一条流程观察:windows-ci 的 concurrency 组(workflow+ref,
cancel-in-progress)把我手动 dispatch 的 67cff09 run 在 44 秒时取消了——
所以「合并默认分支的 Windows 绿灯」由本轮修完后的重派来给,取消的那次
不算数也不用赔。

## 验证

- 定点:crash 战役全文件(含真 kill 轮)+ board compare 全文件 20 条全绿;
  `'\x85'` 世界手工三格复核(match→ok / deadbeef→过期 / 真台词→ok)。
- 全量套件(与卡片渲染波同跑,ffmpeg 6.1.1):**5849 passed, 0 failed,
  19 skipped**。
- **Windows 门禁收口:run 30526393193(PR #30 内容,与合并进默认分支的树
  相同)success —— 整个三面 arc 以来第一个 Windows 全量绿灯**,上述三修
  与卡片渲染波一并通过真实 windows-latest 验收。

---

# Windows 卡渲染波(2026-07-30:首选渲染器终于能在第一平台上跑)

卡片工作牵出的下一个疑点,一查是真的:`find_chromium` 的候选全是 Linux
命名(`chromium`/`google-chrome`/`chrome-linux` glob)。店主的 Windows 11 上:

- `doctor --windows` 一行报「browser (board --app): ✓ Edge」(find_edge 认识
  Windows),另一行报「chromium (html_render): not found — 文字卡走 drawtext
  兜底」——**同一台机器,同一个 Chromium 家族,两行互相打脸**;
- 后果:M3 契约写明 HTML 卡是**首选**渲染器、drawtext 是地板,但在第一平台
  上首选渲染器**从未运行过**(除非店主手工设 CHROME_BIN),每张卡都静默
  落在地板上。没有任何 DECISION 说过这是设计。

修法(扩展唯一属主,不开叉):`find_chromium` 在 `_IS_WINDOWS` 下新增
一段——PATH 上的 `chrome` → Chrome 的规范安装根(Program Files /
LOCALAPPDATA,镜像 Edge 的检法)→ **`find_edge()` 兜底**(Windows 11 必有
Edge,Edge 就是 Chromium);playwright glob 补 `chrome-win/chrome.exe` 与
`headless_shell.exe` 两款 Windows 布局。若某版 Edge 的 headless 渲染失败,
provider 的 F13 适配墙照旧接住并降级 drawtext 带记录——安全性不依赖新路径。

## 验证

- 红-先行 4 条(`test_windows_doctor.py`,find_edge 同款 monkeypatch 模式):
  Chrome 安装根发现 / Edge 兜底 / POSIX 上 Windows 根保持关闭 / playwright
  chrome-win 布局。修前 3 红 1 绿(POSIX 门那条),修后 19 条全文件绿。
- POSIX 零扰动:round5 + 卡片像素 + round_a 共 23 条原样绿(发现顺序对
  既有环境的解析结果不变)。
- doctor 文案不用改:Windows 上「chromium (html_render)」行从此如实报
  找到的浏览器 ✓。
- 全量套件(ffmpeg 6.1.1):**5853 passed, 0 failed, 19 skipped**。

## 死线家族全仓清点(阴性结果,留档防重扫)

载荷敏感死线在 CI 连响三次(mcp wire、ledger GUI jobs、board e2e)后,按
同一准则把 tests/ 全扫了一遍:「固定 <30s 死线 + 背后是构建/ffmpeg 级真实
工作」的组合,**只有已修的那三处**。其余候选逐个核过并留在原值:
`test_gui_project_actions` 的 8s 等线程收尾(作业是测试内闭包,无真实
工作)、`test_c0911_gates` 三处 15s 是屏障文件轮询(自带 BARRIER_TIMEOUT
诚实出口)、`test_audit_findings` 的 5s 是纯线程回调、`test_board_serve`
的 httpx 默认 5s 全部打在预渲染 HTML/静态文件上。`signal.*` 平台面同扫:
killpg 测试有 POSIX skipif 门,crash 战役已改 `Popen.kill()`,再无第三处。
无证据不改——这条准则本身也是本轮的产出之一。

---

# 门禁揭示·第三轮(2026-07-30:探测器太诚实,戳穿了一个不完整的模拟)

PR #31(find_chromium 学会 Windows)的门禁 run 30527566029:**1 failed,
5818 passed, 53 skipped**。两个信号都重要:

1. **唯一红灯是模拟不完整,不是产品错。**
   `test_absent_tools_are_missing_never_a_crash` 用「清空 PATH + 删
   CHROME_BIN + 指空 playwright 目录」伪造「无 Chromium 的机器」——但
   runner 上装着真 Chrome,find_chromium 如今会查安装根(伪造从没清过),
   于是诚实回答 True,`assert ... is False` 应声而红。修法:缺席世界补上
   ProgramFiles / ProgramFiles(x86) / LOCALAPPDATA 三个根(与
   `test_find_edge_absent_is_none` 完全同款),断言一字未动。本地双世界
   仿真验证:带 Chrome 的 Windows → 找到 chrome.exe(runner 所见);补全
   的缺席世界 → None(修后所需)。
2. **chromium 测试道在 Windows 上第一次真的打开了**:skip 从 55 降到 53、
   passed 从 5803 涨到 5818——7 条卡片像素断言与 round5 的 html 测试
   在 windows-latest 上首次实跑,**全绿**。卡片两修(断尾/白带)从此有
   真实 Windows 像素级回归网,这正是 #19 那波想换来的东西。

---

# 收口(2026-07-30:默认分支拿到正式 Windows 绿灯)

三轮门禁揭示全部修完之后:

- **内容判定**:run 30530134961(PR #32 头 8376aee,与默认分支 a6c3707
  同树)Windows 全量 **success** — chromium 道全开后 0 failed。
- **正式凭证**:run 30531761066,`workflow_dispatch` 直接打在默认分支
  `claude/fable-opus-task-division-wv97i6` 的头 a6c3707 上,test 与
  install-smoke 双 job **success**(这台 runner 慢,套件跑了 39 分钟,
  但一路绿;60 分钟 job 上限内无任何挂死)。
- 至此:门禁揭示轮开头写下的收口条件 —— 「以合并后的默认分支拿到
  Windows 绿灯为收口」—— **达成**。从 ebcbfb0/0a02eb7 的连红,到
  display_path、载荷敏感对、SIGKILL/NEL/board 三修、卡片两修、发现器
  Windows 化、模拟补全 —— 六个波次(PR #29-#32 + 两个文档提交),
  默认分支在店主的第一平台上重新全绿,且比 arc 开始时多了一整条
  在真 Windows 上实跑的 chromium 像素回归道。

---

# 卡片渲染波·二(2026-07-30:preset 全览暴露出「涂满≠画对」)

收口后按标goal继续巡,把四个 preset 首次并排全渲了一遍 —— 两个新缺陷,
其中一个直接戳穿了我上一波的验收:

## 1. 渐变全体隐身:z-index:-1 的 ::before 被 body 自己的背景埋了

- **现象**:warm_gradient 整面平紫(顶部本应是 #ff7a45 橙);像素直探证实
  左上 = (122,28,172) = bg_edge 纯色。默认 caption/chapter 的渐变同样从未
  画出 —— 深色系平色肉眼难辨,才没被上一波发现。
- **机制**(CSS 绘制顺序):canvas(html 背景)→ **负 z 定位后代
  (::before 渐变)** → **body 自己的不透明背景(盖住前者)** → 内容。
  bg_edge 同时挂在 html,body 上,body 那份把渐变埋了。
- **修法**:`bg_edge` 只留 html(canvas 传播仍旧铺满整个截图面,含视口外
  87 行),body 不再带背景 —— ::before 从 body 背景后面浮出来。
- **两道防线为什么都漏了**(照例留档):①「涂满」测试只断言无白行,
  bg_edge 纯色打底时渐变缺席照样过;②我上一波亲眼「验收」fixed_p.png 时
  把平深蓝当成了渐变 —— 深色低对比下肉眼不可信。本波补的
  `test_gradient_actually_reaches_the_pixels` 按三个家族钉**色调行程**
  (caption 顶底行均差 ≥8、chapter 径向中心比角亮 ≥8、warm 左上暖右下冷),
  从此渐变退化平色必红。
- **诚实残余**:受影响的 headless Chromium 上,视口外 87 行只能是 bg_edge
  平色 —— warm 的高饱和对角渐变旁接缝可见,caption/chapter 几乎不可辨;
  健康浏览器(店主的 Edge/Chrome)上 ::before 全覆盖、无接缝。已是该
  约束下的最优解。

## 2. white_big 文字顶死右缘(14px/1920 ≈ 0.7%)

`justify:flex-end` 在 flex row 主轴上是**水平**推右,加上 .stack 无边距,
文字贴着帧边渲染,视觉上如同被裁。修法:`.stack`/`.wrap` 加 `margin:0 4%`
—— 居中 preset 分毫不动(对称边距不改变居中),推边 preset 获得 4% 真实
内距。`test_edge_pushed_text_keeps_a_margin` 钉 ≥3% 内距。

## 验证

- 红-先行:两条新像素测试修前红(渐变平色、右缘 14px),修后
  `test_card_visual_fixes.py` 9 条全绿;
- 四 preset + 两模板重渲亲眼复核:caption 海军蓝渐变、warm 橙→粉→紫、
  chapter 径向光斑、white_big 右缘内距 —— 全部本次真的看见了;
- 邻接簇 edit_v3(preset 字节恒等钉照过)+ round5 + packaging 66 绿;
- 全量套件(ffmpeg 6.1.1):**5855 passed, 0 failed, 19 skipped**;
- **Windows 门禁实跑绿(run 30536163223)**:渐变色调行程与 white_big 边距
  两条像素断言在 windows-latest 的真 Chrome 上通过 —— 阈值(顶底行程 ≥8、
  角部 RGB ±30、≥3% 内距)跨字体/渲染管线成立。

## 附:预览缓存的升级陷阱(同波修复)

卡片模板一天改了两轮,牵出 `gui/cardprev` 的缓存键问题:键是
`(text, 模板名, 尺寸, preset名)`,**不含模板内容**,而缓存躺在
`.manju/frames` 里**不随升级失效** —— 本次升级后,老预览会永远端出
修复前的旧观感,「预览所见 ≠ 构建所得」恰是预览存在意义的反面。
修法:把决定观感的数据本身(html 模板 CSS 串 + 两侧渲染器的 preset 行)
折进 cache_key —— 观感一变键即变,零维护、无需人工记得 bump 版本号。
红-先行(改模板内容后仍命中 → 断言 miss),edit_v3 全文件 29 绿,
GUI 消费端三文件 62 绿。

## 附二:缓存键陷阱全族清点(cover 修复 + 其余留档)

按「一处起火 → 全族排查」把全仓 `cache_key` 调用点审了一遍,按证据分三类:

- **同枪命中,修**:`packaging.cover_cache_key` —— card 模式封面经 html
  模板渲染,键却只含 spec;模板修复落地后旧 cover.png 在导出中心误报
  fresh。键现折入模板 CSS(仅 card 模式;frame 模式键字节不变,升级不产
  伪过期)。一个公式 exportstatus 共用,自动跟随。红-先行。
- **同类无火,不动**(载荷敏感同款准则):waveform(画风在代码里)、
  kenburns 片段(有 S4 toolchain 键机制)、audition slate。
- **早有防备**:`boards.py` 键里带 "board_v1" 版本令牌;render 段/边界/
  final 键有 S4 toolchain 机制 —— 作者早懂这坑。
- **明确不修**:intro/outro 卡资产地址(`packaging_card_relpath`)被
  **编译器**消费 —— 改地址即重写所有既有项目的编译时间线并连锁重渲
  finals,代价远超收益;旧观感资产走存量说明路线(删除
  `media/gen/packaging/` 下对应文件即可触发新渲)。

---

# 实地验证片(2026-07-30:五个卡片相关波在真实成片里的端到端复核)

全新迷你项目「fieldcheck」:三镜(长台词/单行「谢谢。」/无台词)、Edge TTS
配音、warm_gradient intro 卡、card 模式封面 —— 完整 build → package →
抽帧亲眼验收:

- **intro 卡在真实时间线里**:warm_gradient 橙→粉→紫全程活着,CHAPTER
  kicker、双行标题正常;底部 bg_edge 平色条即已记录的受影响-Chromium
  降级,过渡自然。
- **「谢谢。」台词卡单行**居中 + rule 饰线,导航蓝渐变可见,无白带 ——
  视觉 QC 闭环最初那条发现在成片里确认已愈。
- **字幕随 intro 正确平移**:captions.ass 欢迎光临 2.30-4.51、谢谢。
  4.80-5.41(镜头起点 +2s intro 偏移 + 配音 200ms 提前量,分毫不差);
  初看 5.6s 帧无字幕曾疑失步,查 ass 后确认那是**窗口外**(字幕跟配音
  时长,不铺满镜头)—— 虚惊留档。
- **封面**:chapter 径向光斑 + 标题,新 cover key(含模板 CSS 分量)照常
  出图,导出中心判 fresh。
- v1(无 intro)→ v2(有 intro)重建正确重渲、final key 正确变更。

**GUI 现场闭环**:起真 `manju gui` 打 `/api/card-preview`(warm_gradient):
首次渲染出图、左上像素 = (255,121,69) 即 #ff7a45 暖橙分毫不差、右下 =
#7a1cac,重键后的缓存二次请求 12ms 命中 —— 修复在单元(重键测试)→
像素(chromium 断言)→ 成片(final_v2)→ GUI 现场(预览端点)四层全部
验证。

一条纸割观察(不修,留档):packaging.yaml 的正确位置是
`timeline/packaging.yaml`(脚手架就在那,带全部提示);把它写到项目根
会被**静默无视**——check 不警告杂散配置文件。手写配置的人(如本次的我)
会踩;照着脚手架就地改的人不会。维护模式下按纸割记录。

## 附三:修复后 GUI 五页目视巡检(零新真缺陷)

fieldcheck 项目起真 GUI,工作台/审片/导出中心/packaging/分镜五页截图
逐一亲眼看过:packaging 表单正确回读手写 yaml(暖色渐变 preset 在位),
缩略条里成片开头的橙粉 intro 帧清晰可辨;导出中心对新 cover key 现场
判读正确(封面上新·key 匹配);分镜表、审片大图(渐变卡)、徽章与
词汇脚注全部如实。pageerror 零。三条纸割级观察(留档不修):
①审片页首卡头行被 sticky「已审」条压住数像素;②封面 card 模式下
frame_ms 选帧条仍显示(仅 frame 模式有意义);③封面 fresh 说明句
「final 字节 + 封面规格 未变」未列新加入键的模板分量 —— 陈述仍为真,
只是不再穷尽。

---

# 门禁揭示·第四轮(2026-07-30:chromium 道自己造出的新死线成员)

PR #38(纯文档)的门禁 run 30540783495 在**至今最慢的 runner**(套件
22 分钟)上红了一条:`test_edit_v3::test_card_preview_endpoint_accepts_
preset_param — TimeoutError`(socket 10s)。1 failed / 5822 passed。

- **机制,带一个反转**:这个端点测试在 Windows 上是**从 #31 起**才开始
  包真实工作的 —— find_chromium 学会 Windows 后,windows-latest 上
  `/api/card-preview` 会在请求内真启 Chrome 截图。此前死线家族清点把
  GUI urlopen 站点留在原值的理由(纯页面渲染)在当时完全正确;是
  chromium 道的打开让这一站变成了「小死线包真工作」。
- **修法**:`test_edit_v3`(10s)与 `test_gui_finish`(20s)的 `_req`
  助手加 timeout 形参 —— **默认值不动**(其余站点仍无证据),仅三处
  卡预览请求传 120s。断言未动。姐妹文件按同端点同机制的证据一并修,
  不等它自己烧起来。
- 家族第四次点火,四次全部是「固定小死线 + 背后真实工作」同一指纹;
  前三次:mcp wire(构建)、ledger jobs(构建)、board e2e(ffmpeg 抽帧)。

## 验证

- 两文件定点 51 绿;全量 **5857 passed, 0 failed**;
- **门禁自证完成:run 30543960060(f18c3ad,与默认分支 3ba8b46 同树)
  success** —— 家族第四修与此前十一波一样,在真 windows-latest 上落定。

---

# 店主定界(2026-07-31)

- **MCP 面冻结**(店主原话:「感觉没有必要,有 CLI 和 GUI 就可以了」,
  并要求告知后续接手的 AI)。落档于 CLAUDE.md 常备事实 + DECISIONS
  TRISURFACE-FIX #25:代码与测试原样保留、保持绿(已付费守护面,机械
  保绿属维护),但不再有任何新投入;「纯 MCP 整片」战役随之取消。
- **磁盘写满演习否决**(店主点名不做;nothing-speculative 验收门)。
- 核准四个真实使用战役,按序执行:①ffmpeg 误升级演习(DECISIONS #33
  的真实亏损背书)②供应商中途真死 ③规模现实性(60–100 镜)
  ④中文+空格路径全链 & git 时间旅行。逐战役立报,红-先行修复,
  照常走全量 + 门禁。

---

# 战役①:ffmpeg 误升级演习(2026-07-31)

**问题(实测坐实)**:把一个报 7.1 的 ffmpeg 放上 PATH(shim:`-version`
报 7.1-static,其余全部委托真 6.1.1 — 演习测的是**劝告层**,不是重证
ffmpeg 自己的回归,此处如实披露),`manju doctor` 的回答是:

```
✓ ffmpeg: /…/ffshim/ffmpeg
```

绿勾、无版本、零警告。而 DECISIONS #33 的实测记录是:7.x 的 acrossfade
转场腿同输入 1600 跑 9 败(6.1.1 为 0),错误报文是一条天书
(`Could not open encoder before EOF`),上一个会话为它赔了大半天。
店主哪天 winget/choco 顺手升级 ffmpeg,得到的就是「体检全绿 + 渲染
间歇性暴毙 + 无一字指路」。

**修法**(属主:`media/ffmpeg`,record-only,永不成为构建输入或门):

- `ffmpeg_version_line()`:版本首行事实,进程缓存,委托 S4 工具链采集器
  (唯一 `-version` 属主);`parse_ffmpeg_major` 对 git 快照/缺失诚实答
  None — 未知永远不猜。
- `ffmpeg_version_advisory()`:**只对有记录的区间**给一行人话 — 7.x 给
  实测过的 acrossfade 回归(带测量数字与回钉建议);≥8 给已记录的
  ffprobe 色彩标签偏移;5.x/未知**只陈述不判断**(nothing-speculative)。
- doctor 新增 `ffmpeg_version` 行:钉版 ✓、记录区间 ⚠、其余 • 信息行;
  `ok` 恒 True — 劝告永不改变退出码。
- **渲染失败现场**:`_raise_on_bad_exit` 对命中已知签名的 stderr 追加
  `known_failure_advisory` 一行(报本机版本 + 回钉指路),并把 failures
  存档的 hint 换成同一句 — GUI 失败页同步受益。当年那半天,今后在
  错误现场就能拿回来。

**验证**:16 条红-先行测试(解析/劝告/doctor 三态/签名富化/无签名
byte-identical);现场重放:钉版 `✓ …(与验证套件钉版一致)`,shim 下
`⚠ ffmpeg version: 7.1-static — 已知回归…建议换回 6.1.1`;既有 doctor
三文件 + media/ffmpeg 直接消费者批 95 条全绿;全量见提交。

## 战役①提交前全量的两红(附带修复 + 自留过错)

- **hypothesis 二次立功,打中的是我自己的检测器**:反例 key 本身是 `{'`,
  摘要输出 `{'=null` 完全诚实,而加练轮写的「repr 检测器」用子串匹配
  (`"{'" not in brief`)被对抗性 key 误伤。产品对,测试错。守卫改为精确
  命题:brief 永不等于 `str(detail)`/`repr(detail)`(它要抓的真回归 ——
  有人把格式化器换回 f-string 倾倒 —— 仍然一击必中),其余断言一字未动。
- **死线家族第五员**:`media/ffmpeg._ENCODERS_TIMEOUT_S`(固定 15s 包真
  `ffmpeg -encoders` subprocess)在全量 + 演习并发负载下点火一次,隔离即绿
  —— 与家族四次点火同指纹。按 #24 先例(单次现场点火即证)升到 120s;
  如实记录:12 个纯 CPU 自旋只把探测拖慢 3× 未击穿,现场的 12 个渲染
  worker + 演习混合负载更重。
- **自留过错两条**:①把战役②演习与**定局全量**并发跑了 —— 点火的额外
  载荷是我自己加的(此后定局跑保持洁净);②全量输出管道 `| tail` 截掉了
  traceback,导致两红要靠隔离复跑取证(此后全量输出完整落盘)。
