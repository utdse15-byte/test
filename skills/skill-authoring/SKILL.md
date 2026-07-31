---
name: skill-authoring
description: 写或扩展一个 Manju 技能的元程序(维护者定时调用)——frontmatter 上限、渐进式披露三层、doc-vs-skill 分界线(决策树+worked 例+清单+失败目录+eval)、baseline-vs-with-skill 度量、库健康度卫生(没触发过的技能就删)。保证技能库始终能被 core/skills 加载、被 test_skill_content 校验。触发词:写技能、加技能、改技能、skill、SKILL.md、frontmatter、技能库、维护。
when_to_use: 维护者要新增或调优一个 Manju 技能、或检查技能库是否合规健康时。
tags: [meta, task]
user_invocable: true
disable-model-invocation: true
---

# 技能编写(skill-authoring)

技能库是 Manju 的**能力表面**(§0:引擎不含 LLM,craft 全靠技能到达 agent)。写技能不是写文档——**文档给规则,技能告诉 agent 何时弯规则**。本技能是加/改一个技能的逐步程序 + 合规红线。

## 格式契约(照 core/skills.py 的解析 + anthropics/skills 规范)

一个技能 = `skills/<id>/SKILL.md`,YAML frontmatter + markdown 正文:

- **`name`**:≤64 字符,小写字母/数字/连字符,**不得含 "anthropic"/"claude"**。
- **`description`**:非空,**≤1024 字符**,第三人称,「做什么 + 何时用」,塞进创作者真会打的中文触发词(完播率/分镜/字幕/封面/短剧/穿帮…)——这是**触发机制**,写「pushy」一点防漏触发。
- **`when_to_use`**:一行中文——**它进 agent 看到的索引**(`skill_index_text`),必须非空、说清「何时用」。
- **`tags`**:领域标签 + 类型标签,**类型标签必写**:reference 型 `[reference]` / task 型 `[task]`(再配领域 `[craft]/[qc]/[series]/[funnel]/[meta]`)。
- **类型字段**:reference 型加 `auto: true`;task 型加 `user_invocable: true`;side-effecting/维护类再加 `disable-model-invocation: true`(自己定时,不自动触发)。*(core/skills.py 只读 name/description/when_to_use/tags/requires;类型字段是约定,解析器忽略但作者与人靠它分类。)*
- **正文 <500 行**;深度推到 `references/*.md`(agent 按需读);确定性动作是 `manju …` 调用(引擎为单一真相源,技能只建议)。
- **解析是宽容的**:frontmatter 坏了也仍是技能(id 取目录名、description 取首个标题)——但别依赖兜底,把 frontmatter 写全。

## 渐进式披露三层(库能扩张而不撑爆 prompt 的关键)

1. **元数据**(name+description,~100 词)——每个已装技能都预载进系统 prompt,让 agent 知道它存在。
2. **SKILL.md 正文**——只在触发时加载(<500 行)。
3. **`references/`/`scripts/`**——正文指向才加载;script 是**执行**不是读入(只有输出耗 token)。

`manju auto` 只全量注入核心 `manju` 协议 + 每个其他技能的一行索引;全文靠 `manju skills show <id>` 按需取。**技能描述共享 ~1% 上下文预算,溢出时最少用的先被丢**——描述要紧、要互相区分。

## doc-vs-skill 分界线(每个 craft 技能必带这五样 + eval)

1. **决策树 / 条件工作流**(「若竖屏短剧 → …;若横屏知识 → …」)把 agent 路由到对的子程序。
2. **≥3 个 worked 例(before→after + WHY)**——比描述更能传递「专业长什么样」(业余 prompt→修好的;死开场→带钩子的)。
3. **拷贝进工作笔记的清单**——agent 边做边勾。
4. **失败目录**(反模式:smell + 在 Manju 里怎么表现/哪条 QC/哪个页面)。
5. **Manju 落地**——把 craft 映到具体命令与字段(`rules.captions.max_chars_per_line`、`/storyboard`、`manju prompt --check`、mixer duck 旋钮、`manju package`、repair op、series 命令)。
6. **eval**:≥3 个 baseline-vs-with-skill 场景,证明它抬升产出——**「一个技能有 eval 证明它抬升产出」正是它区别于文档的线**。

判断自由度:多解法用高自由度(散文启发式);操作脆弱用低自由度(精确脚本、「勿改此命令」)。「窄桥上的机器人 vs 开阔地」——按任务脆弱度校准具体程度。

## 写一个技能的程序(逐步)

1. 先想清它要抬升哪种产出,写 ≥3 个 eval 场景(before/after)。
2. 建 `skills/<id>/SKILL.md`,填全 frontmatter(上面契约)。
3. 正文按五件套 + eval 写;深度放 `references/`。
4. `PYTHONPATH=$PWD/src python3 -c "from manju.core.skills import list_skills; print([s.id for s in list_skills(None)])"` 确认能加载、出现在索引。
5. 跑 `OMP_THREAD_LIMIT=1 PYTHONPATH=$PWD/src python3 -m pytest tests/test_skill_content.py tests/test_skills.py -q` 保持绿。
6. 度量:baseline(无技能)vs with-skill 的 eval 通过率对花费;A/B 两版描述调触发准确度再定稿。

## 库健康度卫生(Cursor「删掉没触发过的规则」)

- 「同一件事解释三遍 → 该进技能;一个技能几周没触发 → 删掉。」
- 描述互不重叠(否则触发歧义、挤预算)。
- 一层引用嵌套;任何 >100 行的 reference 文件加目录。
- 简洁、祈使、讲「为什么」不只「是什么」、术语一致——复述模型已知的就是死重量。

## 合规红线(test_skill_content.py 会挡)

```
新增/改技能自检 · 逐条勾
[ ] name ≤64、无 claude/anthropic、kebab?
[ ] description 非空、≤1024、含中文触发词?
[ ] when_to_use 一行中文、非空(它进索引)?
[ ] tags 含 reference 或 task 类型标签?
[ ] 正文 <500 行?深度进 references/?
[ ] core/skills.list_skills 能加载、索引里有它、when_to_use 是中文?
[ ] 正文没有任何「引擎侧调 LLM」的代码签名(导入某 LLM SDK、在引擎里发起聊天补全/对话调用)——引擎永远 LLM-free?
[ ] (craft)决策树 + ≥3 before/after + 清单 + 失败目录 + Manju 落地 + eval 齐?
[ ] 核心 manju 技能仍能加载且 <300 行?
```

## 失败目录(写技能的反模式)

| 反模式 | 后果 / 抓法 |
| --- | --- |
| 只堆规则、无决策树/例 | 变成文档,eval 不抬升 → 加 before/after |
| 描述空泛无触发词 | 漏触发;写 pushy、塞中文词 |
| when_to_use 空 | 索引里没「何时用」→ agent 不知何时加载;test_skill_content 挡 |
| 正文 >500 行 | 撑 prompt;深度挪 references/ |
| 让技能里出现引擎调 LLM | 违反 §0;test 扫代码签名挡 |
| 技能长期不触发 | 删掉(卫生) |

## Manju 落地

- **加载/查看**:`manju skills [--json]`(列表)、`manju skills show <id>`(全文)。三层解析 project > user(`MANJU_SKILLS_DIR`)> bundled(`skills/`)。(MCP 面已冻结,见 DECISIONS `TRISURFACE-FIX #25`;它的 `skill_list`/`skill_show` 仍在,但不是推荐路径。)
- **测试**:`tests/test_skill_content.py`(内容契约)+ `tests/test_skills.py`(加载/解析/索引/CLI/MCP)。
- **核心协议**:`skills/manju/SKILL.md` 是 `CORE_SKILL_ID`,`manju auto` 全文注入 → 必须 <300 行、保住硬规矩。
- 核心手册 §0 的技能库表按创作阶段列出常用技能;完整清单以 `manju skills` 为准(仓库现有 19 个,含本篇与核心协议)。

## 验收 eval

1. 给一个只有规则的草稿,是否补出决策树 + ≥3 before/after + eval?
2. 一个 name 含「claude」或 description 超 1024 的技能,是否被指出违规?
3. 新技能加完,`test_skill_content.py` + `test_skills.py` 是否仍绿?
