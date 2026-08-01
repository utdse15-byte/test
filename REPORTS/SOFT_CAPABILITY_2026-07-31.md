# 软能力审计 — 文档、协议与 CLI 惯例(2026-07-31)

店主问:「硬能力测试差不多了,软能力呢?文档之类的够好吗?有没有吸收市面上
一些优秀的开源或者网友的经验?」本报告是回答,以及据此落地的修复。

## 方法

- **引用普查**:对 `src/` `docs/` `skills/` `README.md` `CLAUDE.md` 统计外部
  标准与社区实践的出现次数(域内标准 vs 工程惯例分开计)。
- **三路并行勘察**:面向店主的文档(按 Diátaxis 四象限)、CLI 惯例(逐条实测
  15 项)、面向 AI 的文档(19 个技能 + CLAUDE.md + CONTRACTS)。
- **尖锐结论一律亲手复核**:凡是"某某坏了/缺了"的指控,我自己重跑一遍再采信
  (本轮因此推翻了自己的一条断言,见下)。

## 一、结论:域内标准吸收极深,工程惯例几乎为零

这不是随机分布,是同一个成因的两面 —— **所有文档都写给"已经知道答案的人"**
(主要是 AI 会话写给下一个 AI 会话)。

| 吸收了(深度集成,非贴标签) | 处数 |
|---|---|
| OpenTimelineIO(NLE 交换) | 336 |
| EBU R128 / loudnorm(广播响度) | 270 |
| POSIX 路径与命令语义 | 117 |
| WCAG / aria(GUI 无障碍) | 49 |
| SMPTE 时码 | 10 |
| RFC 7807(problem details)→ 移植成 CLI 的 `--json` 错误信封 | 2 |
| clig.dev(错误重写成人话 + 给下一步) | 3 |

**RFC 7807 那一条值得单说**:127 个命令里 123 个有 `--json`,而且**失败路径也是
结构化的**(`{"error":…, "code":"no_project"}` 走 stdout、人话走 stderr)。市面上
绝大多数 CLI 一出错就退化成散文,这一条高于行业平均水平。

| 零吸收 | 判断 |
|---|---|
| SemVer / CHANGELOG / conventional commits(各 0 处) | **合理跳过** —— 单用户软件里是仪式,不建议补 |
| Diátaxis 文档框架(0 处) | 该补 —— 缺的正是它的**教程象限** |
| NO_COLOR(0 处,pty 实测被无视) | 该补 —— 最便宜的一分 |
| XDG / platformdirs(硬编码 `~/.manju`) | 留档不动 —— 迁移要动既有路径,风险大于收益 |

## 二、三个亲手复核过的事实

### 1. 教程早就做好了、能跑、全文档零提及

`manju new --demo` 造一个 12 镜「雨夜便利店」,画面走本地 `caption_card`。
**实测**:`manju build --yes` 2 分 19 秒、零花费、出 2.6MB 的 `final_v1.mp4`。
而 `--demo` 在 README、docs/、CLAUDE.md、STATE.md、skills/ 里出现 **0 次** ——
Diátaxis 的教程象限不是"缺",是"做好了没挂出来"。全仓收益/成本比最高的一处。

### 2. README 的 Quickstart 复制粘贴必失败

它 `manju import clips/开场.mp4 clips/雨夜.mp4`,下一条却
`manju select S001 --file media/imports/opening.mp4` —— 那个文件从没被导入过。
**产品是对的**:`manju import` 自己就印出了正确的下一条命令
(`manju select <镜头> --file "media/imports/开场.mp4"`),是 README 抄错了。
新手照着敲的第一条真命令就会失败。

### 3. 当天冻结的 MCP,技能文档还在当主路教(本会话的欠账)

店主 2026-07-31 冻结 MCP 面,我把它写进了 CLAUDE.md 与 DECISIONS,**没扫技能面**。
后果不止"过时"——`skills/manju/SKILL.md:57` 把「先 `get_shot` 拿 `rev`、回带
`expected_rev`」标为**强制**,而 `expected_rev` 在 `cli.py` 里只存在于 serve-mcp
的文档串中:**一个 CLI-only 会话被命令执行一个 CLI 根本没有的步骤。**
另外 `CLAUDE.md` 里 "skill" 出现 **0 次** —— 冷启动会话压根不知道有操作协议。

## 三、DECISIONS.md:好东西上开了个窟窿

索引表(2026-07-13 加)自述用途是"让会话不用通读就能找到管着某文件的决定"。
但它只覆盖顶层 1–51,**六个命名段落的 92 条决定一条未收** —— 而那 92 条正是
最近三个月、当下最相关的。

更糟的是编号:七个段落各自从 1 重开,`#1..#33` 横跨三个编号域。实测后果 ——
CLAUDE.md 用裸「DECISIONS #33」给 ffmpeg 6.1.1 钉作证(那句"上一个会话赔了
大半天"的警告),顺着找到的却是无关的顶层《W/X/Y/Z waves + 四小时优化研究》,
真正的证据在 `UX-REAL-USE #33`。**唯一一条本该救人一天的指引,指错了地方。**

## 四、本波落地(三段,22 条红-先行测试)

**波一 · 文档可达性**
- README 新增教程小节「第一部片:两条命令,零花费」——逐字给出真实输出、成片
  路径、五条零花费练手动作;「中文速览」加"隔了一阵子回来?"路径(doctor →
  样片 → STATE.md)。
- 修 Quickstart 的 `opening.mp4` → `开场.mp4`,并点明"`manju import` 会替你印
  出这一行"。
- DECISIONS.md 加**命名段落索引**(92 行)与**编号域规则**:裸 `#N` 只指顶层,
  命名段落必须带段名。「点名模块」一列由条目正文**机械提取**,提不到就记 `—`
  (54 条有、38 条无),不作推断。
- 修三处违规引用(CLAUDE.md 的 ffmpeg 钉 + 两个本会话写的测试)。

**波二 · 技能面 MCP 清扫**
- `skills/manju/SKILL.md:57` 改写为 CLI 原生的三条纪律(定点编辑 / 写完即
  `manju check` / 提交前 `git diff`),并附**实测证据**:字段锁在引擎核里守着,
  绕过 CLI 直接改文件同样当场拦下(`✗ locked field 'duration' changed`,
  check 与 build 均 rc=1)。
- §0 开头新增一句"你的操作面是 CLI(加 GUI),不是 MCP",引 `TRISURFACE-FIX #25`;
  `.manju` 可弃性从工作流深处提到开篇。
- `error-codes` 的 MCP 词表标为**冻结存档**(保留不删、保持绿),表尾再声明一次;
  `skill-authoring` 去掉 MCP 主路措辞、修「13 个技能」→ 以 `manju skills` 为准。
- CLAUDE.md 新增「其余的在哪」四条指路:操作协议 `skills/manju/SKILL.md`、
  仓库状态 `STATE.md`、来龙去脉 `DECISIONS.md`(含引用规则)、实测 `REPORTS/INDEX.md`。

**波三 · CLI 惯例与构建反馈**
- `-h` 与 `--help` 等价(`context_settings.help_option_names`)。
- `NO_COLOR` 生效:Click 的 `secho` 走 `ctx.color=False`,Rich 的帮助屏自带支持。
- **构建进度**:引擎自 UX wave 2 起就在发 `on_phase`(含 `gen:S003 (3/12)` 粒度),
  CLI 从未接线 —— 现在接上,**只走 stderr、只在交互终端**,单行 `\r` 刷新、结束
  清行。`--json` 与管道下 stdout 分毫不变(实测管道下 `…` 计数为 0)。

## 五、我自己的错(照例留档)

- **NO_COLOR 那条断言是我写错了,不是产品错。** 我先断言"NO_COLOR 下不得有任何
  ANSI",实测残留 `\x1b[1m`。逐码分解后真相是:默认出 `{1,0,2,1;33,1;32,1;36}`,
  `NO_COLOR=1` 出 `{1,0,2}` —— **颜色全没了,只剩粗体/暗淡/重置**,这正是
  no-color.org 的要求(它管 color,不管字体样式)。断言改成"没有颜色码",并把
  实测码表写进注释,免得下一个会话把粗体也"修"掉。
- **锁的第一次实测无效**:`manju lock` 命中 `ask_before` 闸没上成锁,我却拿它当
  "锁不生效"的证据。补 `--yes` 重测才拿到真结论。
- **管道又吃了一次退出码**:`check` 明明 rc=1,`... | tail` 让我看见 rc=0。本轮
  报告里我自己记过这个坑,一天之内第二次踩。
- **一条钉初版是自我满足的**:MCP 词汇检查本来"文件里出现『冻结』二字即全文
  豁免"。收紧为**邻近检查**(±400 字符)后当场抓到 `error-codes` 表格深处的
  `get_shot` —— 说明松版本是假绿。修法是把冻结声明补到表尾,而不是放宽钉。

## 六、明知而未做(留给店主定夺)

- README 仍无目录、仍 560+ 行(命令参考表占一半);建议后续把 `## CLI reference`
  挪进 `docs/CLI.md`。
- `docs/DESIGN_v2.1.md` 与 v2.2 约 82% 重复,尚未归档。
- `STATE.md` 的两个 `Last full-green SHA: pending` 仍是 pending。
- 四个"孤儿技能"(`localize-dialogue` 等)未达 `skill-authoring` 的五件套标准,
  也不在 `TAXONOMY_IDS` 与 §0 索引表里。
- 18/19 个技能缺「什么时候**不**该用」段落。
- `skill-authoring` 要求的第三层(`references/` `scripts/`)全仓零实现。
- XDG / `%APPDATA%` 配置位置;`--quiet`/`--verbose`;Ctrl-C 在 build 下仍抛栈
  (锁是干净释放的,只是观感)。

**补记(2026-08-01):这张清单已被「文档收口波」关掉** ——
见 `REPORTS/DOCS_CLOSEOUT_2026-08-01.md` 与 DECISIONS `DOCS-CLOSEOUT`。
README 目录 + 命令表搬进 `docs/CLI.md`、v2.1 归档、STATE.md 两个 pending 用真
run 数据落地(并修掉「由 CI 盖章」这句假话)、技能面四条(契约扩到全盘、
每个技能补「什么时候不该用」、4 个孤儿技能进 §0、第三层 `references/` 首次落地)
全部完成。**仅剩 XDG/`%APPDATA%` 继续留档不做**(维持本报告原判:迁移既有
`~/.manju` 路径风险大于收益)。至于 Ctrl-C 抛栈:`REWORK-FREEDOM #1` 的实地
复验推翻了这条——实测 rc=130、无栈、锁已释放,现在还会说清已产出的 take 保留。

## 七、验证

- 新增 `tests/test_docs_reachability.py`(11 条)+ `tests/test_cli_conventions.py`
  (11 条),每条修复前验证过红;`ruff check src/ tests/` 干净。
- 既有钉全绿:技能/内容契约/错误码协议/文档/工作流 138 条,CLI 表面快照 6 条
  (`-h` 未改动冻结面)。
- 现场复验:`--demo` 全链 2m19s 出片;真 pty 下进度逐阶段刷新、管道下 stdout
  `…` 计数为 0;`NO_COLOR=1` 颜色码归零。
- 全量套件见提交记录。

### 补记:定局全量又抓到我一处(第五条自留过错)

`tests/test_fp_xdist.py::test_no_raw_chdir_in_tests` 拦下了我新写的 NO_COLOR
测试 —— 它用了裸 `os.chdir`(即便有 try/finally),而 chdir 是**进程全局**的,
`-n auto` 下会污染同 worker 的下一条测试。修法不是改用 `monkeypatch.chdir`,
而是**根本不动本进程**:`script(1)` 子进程直接给 `cwd=`。这条钉正是仓库
"别弱化既有测试"的价值所在 —— 它替我挡住了一个我没想到的并行污染。
