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
