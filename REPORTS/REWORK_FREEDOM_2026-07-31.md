# 返工自由度审计 — 改了又改、推倒重来时,产品挡不挡人(2026-07-31)

店主定的目标:「实际使用的时候,CLI 或者 GUI 有极大的自由和操作空间,因为
可能会不断地调整返工之类的,考虑所有情况」。本报告把店主会做的每一种反悔
动作在真项目上打了一遍,记录哪些自由已经有、哪些走进了死胡同。

演练场:`manju new 返工演练 --demo`(12 镜、零花费),全程真跑真出片。

## 一、已经很自由的(实测,原样通过)

**A 选择与素材**
- take 随便来回切:`select S003 2` → `1` → `2` 全部放行,不拦不问。
- `rollback shot S003` 退回上一次选择,并如实说「back to take_01 (was take_02)」。
- 手动接管后**还能换回生成的 take**:`select --file` 铸 take_03,再
  `select S003 1` 换回 take_01,毫无阻碍。
- 状态如实呈报选项:「有更新的 take 未选用(take_02, take_03,现选 take_01)
  — manju select S003 3 选用,**或不动(现选依然有效,§3 只增不改)**」。

**B 内容返工**
- 删镜头(从 index 拿掉 S005):`check` 给出教科书级警告 —— 明说
  「build/timeline 会 EXCLUDE 该镜头」,并给**两条**出路(加回 order,或
  `manju build --include-unindexed`)。
- 改顺序、往中间插新镜头(S013 插到第 2 位):重建后播放序按 index 而非
  编号,**只生成了新镜头那一个 take**,17.9 秒完成。
- 编号顺序 ≠ 播放顺序这件事,产品处理得完全正确。

**C 项目级返工**
- 帧率 24 → 30:直接改 `project.yaml`,重建后成片实测 `30/1`。
- 竖屏 1080x1920 → 横屏 1920x1080:重建成片实测 1920x1080,旧素材**加黑边
  而非拉伸变形**(正确的保守选择);`redo` 一镜即按新画幅重铸(实测新 take
  是 1920x1080)。

**E 中断**
- Ctrl-C 中断构建:rc=130(符合惯例)、**无栈**、锁已释放、`manju check`
  绿、状态自洽。(此前一次审计说会抛栈,三个点位复测均未复现,按实测纠正。)

**F 版本与对比**
- `compare` 逐项报差异,连画幅变化都点名(`1080x1920 → 1920x1080`),
  是返工后"我改对了吗"的正解工具。
- git 时间旅行已在战役④单独验过(回滚真相 → 精确复原旧片)。

## 二、两处真缺口(已修)

### 缺口 1:可行动的提示,在任何面上都看不见

把项目改成横屏后,QC **逐镜**记下了
「take resolution 1080x1920 differs from project 1920x1080」并给出确切命令
(`manju repair --op croppad --shot S001 --mode center_crop`)。但:

- `manju qc` 汇总只数 errors/warnings → 印「0 errors, 11 warnings」
- `manju status` 的 QC 行同样只有两个数
- `manju build` 只印「QC: 通过」
- `manju qc brief` 零提及

实测:**29 条 info 里有 16 条带具体命令**(13 条分辨率 + 音乐 + 环境音 +
1 条内容),全部静默;店主看到的是「完成 ✅」。**返工的前提是知道自己有哪些
选项** —— 建议存在却看不见,等于没有。

**修法**:新增唯一计数器 `qc.checks.actionable_notes` —— 判据是
**`info` 且带非空 `suggestion`**(纯记账的「mid-point frame for visual
review」逐镜行不带 suggestion,天然不计入,不制造噪声)。三个面同步:

```
QC: 0 errors, 11 warnings, 16 可行动提示 → reports/qc.md
QC   errors=0 warnings=11 可行动提示=16(manju qc 看详情)
```

同一个计数器同时吃 `QCItem` 对象(CLI 持有)与 dict(status 读 qc.json),
两个面不可能对不上。

### 缺口 2:Ctrl-C 之后一片沉默

中断长构建后**输出零行**。技术上一切正常,但店主不知道:已经产出的 take
还在吗?要不要从头再来?—— 而引擎自己早有很好的取消话术(「已取消:N/M …
已产出的 take 不受影响」),只是 SIGINT 这条路从没接上。

**修法**:唯一属主 `cli._interrupted_message(verb)`,`build`/`redo`/`voice`
三处接住 `KeyboardInterrupt`,走 stderr、rc 仍 130:

```
已取消 build(Ctrl-C)—— 已经产出的 take 与成片都保留着(§3 只增不改),
真相文本没有被改坏。接着干:manju build;先看看现在到哪了:manju status
```

顺带:`--approve-baseline` 成功后补一行「改主意了?对新成片再跑一次即可,
以最新一次为准(历史只增不减)」—— 只增不改的账本没有"反审批",出路是**用
新决定盖过旧决定**,店主不该靠猜。

## 三、一处留档待店主拍板:GUI 无法把镜头移出成片

- **CLI 能**:从 `shots/index.yaml` 的 `order` 删掉那行(check 会明确告知
  后果与两条出路)。设计上这就是既定语义 —— ShotSpec **没有** skip/enabled
  字段,index order 就是"这一刀怎么剪"的唯一权威。
- **GUI 不能**:`/api/index` 的写入器 `core.writes.permute_index` 只接受当前
  镜头集的**排列**。而这条不变量恰恰是两个标签页同时改序的安全守卫
  (GUI-INDEX-P1-001 的注释写明:没有它,第二个标签页的 ↑/↓ 会静默回滚第一个
  标签页的改序)。**放宽它 = 弱化既有安全属性**,不能这么修。
- 正解是**另起一个允许子集的写入器并自带守卫**,而不是动 `permute_index`。
  但语义要店主定:移出后镜头文件留在盘上(现状如此)还是一并归档?孤儿镜头
  在 status 里怎么呈现?GUI 上是"移出本刀"还是"删除镜头"?
- 已把这条**如实写进 `docs/WORKBENCH.md`** —— 那张矩阵此前宣称"日常能力都在
  GUI 有一个明显入口",这条是反例,且不在"故意只留 CLI"的清单里。文档先说
  真话,功能等店主定夺。

## 四、我自己的错(照例留档)

- **GUI 探测第一版方法错了**:我 curl 首页 grep 中文按钮词,全 0,差点报成
  「GUI 没有任何返工入口」。实际首页只有 3893 字节 —— 它是 JSON 客户端,
  按钮由 JS 渲染。改查服务端动作处理器表才拿到真相(`git_rollback_file`、
  `git_snapshot` 都在,是我第一次 grep 用错了名字)。
- **差点把"没提示"报成缺陷**:我先断定改画幅后"零提示",查证后发现 QC
  **早就逐镜写了**,只是级别是 info、藏在报告里。缺陷因此从"没有建议"缩小为
  "建议看不见" —— 结论更小,但这才是真的。

## 五、验证

- 新增 `tests/test_rework_freedom.py` 7 条(含 2 条真 ffmpeg 场景:改画幅后
  提示可见、Ctrl-C 话术与 rc=130),每条修复前验证过红。
- 现场复验:横屏演练场 `qc` 印「16 可行动提示」、`status` 印
  「可行动提示=16」;Ctrl-C 实测 rc=130 且印出取消话术。
- 全量套件见提交记录。

---

# 后半程:H–K 组补测(同一演练场,续)

## H 发布哪一版

- **旧成片全部在盘上**(只增不改),而"发哪一版"由基线决定:
  `manju exports --final final_v3 --approve-baseline` 这条路是通的。
- 第一次被**正确拒绝**:`approval blocked: CURRENT_FINAL_STALE[final] —
  resolve them, or pass --accept-known-risk (human-only)`。既不让人误发过期
  版本,又当场给出人类专用的越权开关。
- 带 `--accept-known-risk` 后放行,并把风险记进事件:
  `⚠ 已接受风险 known blockers: CURRENT_FINAL_STALE`,基线随即指向 v3。
  **这是"自由但诚实"的范本**:不拦死,但让你知道自己在做什么、并留档。
- `gc` 设计上安全:`imports/` 与 `final/` 永不触碰,`--hard` 只清未选用的
  take(且限交互式终端)。

## I 多语言返工

`locale add en` 之后想撤 —— 没有 `locale remove` 命令,但**直接删
`locales/en/` 目录**干净利落:`check` 绿,`locale status` 如实回到
「no locales — manju locale add en」。文本即真相,文件系统就是接口。

## J 「我改了台词,片子怎么没变?」—— 链条是诚实的(我的误报已验伪)

这是店主最可能误判为 bug 的一幕,值得写清楚:

1. 改 `shots/S001.yaml` 的 `dialogue.text` → `manju build`
2. **成片和字幕都还是旧词。** 我第一反应是"导出中心报『上新』是虚报"。
3. 查证结果:**不是虚报,是诚实。** 字幕跟的是**真实语音的对齐**
   (compiler:「captions snap to real speech」),而配音是 stale 的旧音频 ——
   音频里说的就是旧词,字幕如实显示旧词才对。若字幕擅自显示新词,反而会与
   耳朵听到的不符。
4. 而 status 早就点名了:`配音 stale=1` + 待办「S001 配音:manju voice S001」。
5. 决定性验证:`manju voice S001 --yes && manju build` → 时间线字幕变成
   「返工之后的新台词。」,`captions.srt` 同步跟上。

**结论:无缺陷。** 改词后要重配音才落地,这是"字幕必须与音频一致"的必然
推论,而不是遗漏;产品也在 status 里给了确切的下一步。

## K 其它

- **锁定内容的正规改动通道**:`manju propose` 写提案,与 MCP 面共用同一套
  编号/占位(O_EXCL 原子申领),锁不许绕但有正门。
- **做 A/B 两个版本**:整个 `.manju` 目录直接 `cp -r` 即得独立副本,
  `check` 绿、`status` 正常(副本沿用原名,想改名改 `project.yaml` 即可)。

## 又一次"验证挡住误报"(第三次,累计留档)

本轮我两次差点报错:①"改画幅零提示"——实为 QC 早已逐镜写好、只是级别是
info(缺陷因此缩小为"看不见");②"导出中心虚报上新"——实为字幕跟音频的
正确设计。两次都是**先复现再下结论**挡住的。加上软能力波里"NO_COLOR 断言
写错"那次,一天之内三次;这条流程纪律的价值已经不需要再论证了。
