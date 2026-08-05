# 文档收口波 —— 把「明知而未做」清单关掉(2026-08-01)

《软能力审计》(`SOFT_CAPABILITY_2026-07-31.md`)§六留了一张"知道、但这波没做"
的清单。本波把它逐条关掉。留档一条不留地做完是本波的目标,除了一条**继续留档
不做**的(XDG,理由在末尾)。

判据没变:**每条都要能说出它是"文档说了假话"还是"东西找不到"**,只是排版
难看的不做。四条都过了这个判据,第三条是本波唯一一次"文档在骗人"。

## 一、README 的读者被两拨人抢(找不到)

| | 之前 | 之后 |
|---|---|---|
| 正文 | 583 行 / 48.3k 字符 | 497 行 / **27.4k 字符** |
| 目录 | 无 | 有,每个 `##` 小节都在,置于第 7 行 |
| 命令参考表 | README 里 90 行 | `docs/CLI.md`(独立文件) |

那张表的真实读者是接手的 AI 会话(90 行、每行一个命令的语义合同),而 README
是店主进门的第一页。混在一起,谁都读不痛快。

**锚点不靠手写**:目录由与测试**共用同一个 slug 函数**生成(去反引号与标点、
小写、空格转连字符、CJK 保留),所以链接与标题按构造一致;另有一条测试钉住
锚点唯一——重复标题会让目录跳错地方。

**扫描跟着内容走,不是跟着文件名走。** `tests/test_fp_docs.py` 原本只扫
README 的命令表(「每一行的 `manju …` 必须解析到活的 typer 注册表」)。表搬走
之后,把扫描面改成 **README + docs/CLI.md 的并集**:断言一个字没动,输入更宽。
搬家当场验证了这条钉是活的——中间态下它红了两条(spans 不足 50、找不到 toolmap
行),正是它该有的反应。

## 二、v2.1 与 v2.2 并排放着,谁也没说哪份作数(找不到)

两份设计约 82% 重复。按文件名排序,一个冷启动会话**先读到的是过时的那份**。

- `docs/DESIGN_v2.1.md` → `docs/archive/DESIGN_v2.1.md`(与 `REPORTS/archive/`
  同一处置:历史不删,只降级)。
- 归档件开头写明:被 v2.2 取代、这里只留"当初怎么想的"、**别拿它改代码**、
  §N 引用一律以 v2.2 为准。
- README 的两处链接改指现行版;新增一条测试:活文档(README/CLAUDE.md/
  STATE.md/docs/skills)不得再指旧路径,DECISIONS 与 REPORTS 作为历史账允许保留。

## 三、`LAST_GREEN.yaml` 自称由 CI 盖章,而没有任何 workflow 调它(**说了假话**)

这是本波唯一一条"文档在骗人",也是最值得记的一条。

文件头写着 "meant to be written by CI ... Do NOT hand-edit them to success —
let CI stamp it",两个平台一直是 `pending`。读它的人(包括接手的会话)会得出
"门禁从没在同一个提交上双绿过"的结论。**实际上是:没人跑过生成器。**
`.github/workflows/` 里 `update_last_green` 出现 0 次。

**修法是把话说对,不是接一条跨 workflow 的自动流水。** 单用户软件里,为了让
一份"信息性、永不作构建输入"的状态文件自动盖章而引入 workflow_run 跨工作流
编排 + 回写默认分支,成本与风险都不划算。生成器的文件头现在明说:维护者手动
跑、没有 workflow 调它、`result: success` 只能出现在真 run id 旁边。

**同时用真数据首次盖章**(证据全部亲手取自 GitHub Actions):

| 字段 | 值 | 来源 |
|---|---|---|
| commit | `efd7783…`(PR #52 的头) | 两条 run 的 `head_sha` |
| ubuntu | run **30704812542** · success | ci.yml,test job 91381979165 |
| windows | run **30704812522** · success | windows-ci.yml,test job 91381978983 |
| ffmpeg | success | 同 run 的「FFmpeg present and at the pinned version(anti-silent-skip gate)」步骤 success |
| test_count | **null** | 没读到——尾部日志是 checkout 清理,不是 pytest 汇总 |

合并后的 `c7889b8` 与 `efd7783` **树字节相同**(都是 `aa87368…`,`git diff`
为空),所以这份证据覆盖默认分支当下的状态;这一点写进了 STATE.md 而不是塞进
YAML,因为生成器会重写 YAML。

`test_count: null` 是刻意的:**一个看起来合理的数字比空白更坏**。同一条纪律
下,新钉是双向的——`result: success` 必须带得出 40 位 SHA + 该平台的 run id +
盖章时间;文件若哪天再自称由 CI 盖章,workflow 里就必须真有那一步。顺手补了
一条"派生报告永不是构建输入"的钉(`src/` 里读它的地方必须为 0)。

STATE.md 的两个 `pending` 随之落地,`Current head` 那行也从早已不存在的分支
改成了当前默认分支。

## 四、技能面两条(找不到 + 契约作用域错)

### 4.1 内容契约只管一半

`TAXONOMY_IDS` 是"至少要有这些",却被当成"只管这些"。盘上 19 个技能里有 5 个
(`error-codes`、`continue-from-accepted-take`、`direct-shot-source-patch`、
`localize-dialogue`、`review-take-and-route-repair`)只被两条"frontmatter 能
解析吗"覆盖——名字长度、kebab 形态、类型标签、正文行数、LLM 调用签名一概没查。

契约改为覆盖**盘上每一个技能**(taxonomy 继续管"必须存在")。实测这 5 个本来
就合规——**但在此之前没人知道**,这正是把作用域改对的理由。

### 4.2 19 个技能,0 个写了「什么时候不该用」

技能库最贵的失败不是漏触发,是**误触发**:被拉进相邻场景的 agent 会照着这里的
决策树一路走完。`when_to_use` 只说何时用;反面得自己写。

18 个可选技能各补一节(表格:情形 → 去哪),且**必须指出去处**——另一个技能 id
或一条真命令。"别用我"而不说"去用那个"只是把 agent 晾在原地,所以这条也钉住了。
例:

| 情形 | 去哪 |
| --- | --- |
| (audio-finishing)只是换一首 BGM、没有响度或闪避问题 | 直接改 `timeline/rules.yaml` 的 `audio` 段 |
| (visual-qc-review)当前 agent 没有视觉能力 | 先跑 `manju qc` 的机检层,别猜画面 |
| (prompt-craft)这一镜用的是实拍素材 | prompt 根本不参与,`manju select --file` 登记即可 |

**核心 `manju` 协议豁免**:它 `auto: true` 全量注入、从不被"选择",不存在
"别加载它"这个决定。豁免不是洞——由一条**正面钉**守着:哪天它不再自动注入,
测试立刻要求它像其他技能一样补上。

### 4.3 渐进式披露的第三层:仓库零实现,而核心协议卡在 298/300 行

`skill-authoring` 要求三层(元数据 / 正文 / `references/`+`scripts/`),第三层
全仓 0 处。同时核心协议正好 298 行,硬限 <300——**再加一行就破**,而本波恰恰
要给它加 4 行(4 个从未出现在 §0 索引表里的技能)。

两张查表(命令速查 §11、术语速查 §10)移入 `skills/manju/references/`:

- 正文 298 → **268 行**(补回下面那段出口点名后 **282 行**,仍留 18 行余量),
  腾出的是未来每次修改的余量;
- 移走的是"查表"(用时才看),留下的是"规矩"(每次都要在场)——这正是分层的判据;
- 路径相对 SKILL.md 所在目录,agent 拿绝对路径靠
  `manju skills show <id> --json` 的 `path` 字段(实测有此字段);
- 新钉:每个被正文指向的 `references/*.md` 必须真的存在(指向不存在的文件 =
  白花一次读取,深度还丢了)。

**搬家的代价被既有钉当场抓住,而它是对的。** 定局全量里
`test_fp_polish::test_core_skill_cheat_sheet_documents_the_audited_surfaces`
红了:它钉的是「**只读注入面**的 agent 也必须知道 `--xmeml`/`--ttml`/`--bagit`/
`import-plan`/`migrate`/`locale` 这些出口存在」(UX 审计 F37 的遗产),而这些
token 恰好随命令表沉到了 references/。**没有放宽它去扫 references** —— 那会让
钉子变空,第三层按定义不是默认读的。正确的分层是**存在性留在注入面、用法沉到
第三层**:协议里补一段点名的出口清单(4 行,不是 27 行)。一句话教训:
**你不知道它存在,就永远不会去查。**

顺带:4 个"孤儿技能"进 §0 索引表(此前只有跑过 `manju skills` 的人才知道它们
存在),`localize-dialogue` 的类型字段对齐(task 标签却写着 `auto: true`),
`skill-authoring` 的合规清单与失败目录补上新规矩。

## 五、验证

- 新增 `tests/test_docs_closeout.py`(11 条),`tests/test_skill_content.py`
  加 5 条新钉并把 4 条既有参数化契约从 taxonomy 扩到全盘(13→19 个 id)。
  **每条修复前验证过红**:首轮 6 红(README 目录/命令表/归档/活链接/盖章声明)、
  技能轮 37 红(18×2 缺「什么时候不该用」+ §0 索引漏 4 个)。
- 既有钉:`test_fp_docs` 三条按内容搬家适配(断言未动、输入变宽)、
  `test_docs_reachability` 11 条、`test_skills` + `test_c081012_skills` 全绿,
  `ruff check src/ tests/ scripts/` 干净。
- 现场复验:`manju skills` 索引 19 条齐、`manju skills show manju` 尾部指向
  第三层、`--json` 的 `path` 是绝对路径。
- 定局全量:**6049 passed, 19 skipped**(本波前 5961 → +88 条新钉/新参数)。
  第一次定局跑出 2 红,两条都是我的:`DOCS-CLOSEOUT` 段落写了 7 条而索引只补了
  6 行(命名段索引钉抓的),以及上面那条注入面出口点名——都已修,复跑全绿。

## 六、仍然不做的一条

**XDG / `%APPDATA%` 配置位置。** 要迁移既有 `~/.manju` 路径(库、providers、
技能用户层都在里面),风险大于收益;`SOFT-CAP` 判过一次,本波维持原判。
这是清单上唯一剩下的一条。

## 附:本波的自留过错

**抖动猎杀被我自己污染了。** 全量套件重复跑(任务 #30)开在后台,而我在同一
棵树上改 README 与测试——第 1 轮(干净树,`5961 passed, 19 skipped`)有效,
第 2 轮起收集到的是改到一半的树。发现后当场停掉:**抖动猎杀必须在冻结的树上
跑**,不只是"不占 CPU"就行。改由本波合并后重开。
