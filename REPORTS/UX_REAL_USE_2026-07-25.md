# 真实使用波次 — 2026-07-25

这一波没有读代码找 bug,而是**把工具当工具用**:CLI 全流程跑通两个项目、用真实
浏览器(Chromium + Playwright)驱动 GUI 点完每个页面、导入真实素材、硬杀构建进程、
带任务退出、打包还原。发现的每一条都是"用的时候被绊到",不是"读的时候看出来"。

审计波次(AUDIT-LEDGER-WAVE,PR #23)已合并,本波次接在其后。

## 一、结论先说

共修 **17 处**,全部带测试。没有一处是正确性缺陷——引擎每次都做了对的事。它们是
**让人白忙、看不懂、或悄悄丢东西**的地方。

按后果排序:

| # | 问题 | 后果 |
|---|---|---|
| 1 | `manju import` 是素材变 take 的**单向门** | 白忙,且无出路 |
| 2 | 新素材进来**静默**不进片子 | 以为没生效,可能重做 |
| 3 | 首屏「下一步」推荐**必然失败**的命令 | 白忙 |
| 4 | 支持包遮掉自己最该带的**构建指纹** | 排障时拿不到关键证据 |
| 5 | `lib add --tag a --tag b` **静默吞掉**第一个 | 悄悄丢用户输入 |
| 6 | 三个页面每次加载都抛 JS 错 | 噪音掩盖真错误 |
| 7 | 审片页同一段判据**重复 12 遍** | 读不下去 |
| 8 | 「完成」与「构建进行中」**同屏** | 不敢信大标题 |
| 9 | `masters` 把 `I=None LUFS` 摆给用户 | 分不清静音与失败 |
| 10 | `segments` 拿到 mp4 吐**原始解码异常** | 不知道参数要什么 |
| 11 | `import-plan` 汇总行**单位不一致** | 1 挨着 12 个 ⚠ |
| 12 | 脚手架在 Windows 上写 **CRLF** | 跨平台字节/哈希漂移 |
| 13 | 六处 locale 扫描排 `Path` 而非 POSIX 串 | 不同平台先点名不同语种 |
| 14 | `pull-sheet` 计划只给计数,**不给内容** | 审查闸门形式在实质不在 |
| 15 | M0 e2e 测试**单条跑不了**(既有) | 调试时第一动作就失败 |

(12–13 见下节"Windows 硬闸跑不了时怎么办";14–15 见"审查闸门"与"测试隔离"。)

## 二、最值得记的三条

### `manju import` 曾是单向门(`41a6b17`)

去重规则跳过任何字节已在项目里的文件。但**「已在 imports/ 里」和「已经是某个
take」是两回事**——前者是原始素材,把它变成 take 是全新且合法的动作(take 追加进
`media/gen/`,imports 那份原样保留)。

一律跳过的后果是:先 `manju import`(而 `import` 的帮助恰恰邀请你这么做:
"Real footage/audio → media/imports"),这段素材**此后永远挂不到镜头上**。逐一验证过
没有任何逃生门:`--shot` 不行,`--on-duplicate import` 不行(那个开关只管**素材库**
重复),从项目外拿同内容、同名的副本重新喂也不行。而且没有任何提示指向出路,
因为出路不存在。

现在带角色(take/voice/ref)的行放行,并从项目里已有的那份取源;不带角色的普通
重复导入照旧跳过——去重仍然守着它本来要守的东西:不铸第二份原始素材。

### 新素材静默不进片(`41a6b17`)

`ingest` 给已选中的镜头加 take 时不会自动改选(append-only,人的选择不被覆盖,
**这是对的**)。但接着 `manju build` 说「final up-to-date」、成片没变,而 `status`
和 `explain` 仍在念旧 take。唯一痕迹是 `ingest-batches` 里的 `[pending=1]`,而没有
任何入口把你导过去。

`shot_next_action` 新增 `newtake` 档,**排在配音之前**:配音缺失本来就有一堆入口
提示,而"有个 take 等你决定"一个入口都没有。措辞写明这是决定不是缺陷。

排它**在 `stale` 之后**:老 spec 下产出的 take 同样过期,"选新的那条"是坏建议,
该 redo。这一条是我第一版写错、被既有测试抓出来后修正的。

### 支持包遮掉了自己最该带的证据(`956dc3c`)

密钥字段规则对字段名做**子串**匹配,于是 `content_key`、`final_key` 这些**构建内容
指纹**全被遮。真实跑一次:**6 次脱敏,6 次全是 `content_key`**——而"为什么重新
渲染了"正要靠比对 content key 回答。这个包在可靠地藏起自己最有用的东西,同时报告
着一个看起来很健康的 `secret_key_masks: 6`。

改用**精确名单**而非放宽正则,方向不可意外扩大:八个已知非密钥 `*_key` 放行,
其余(`api_key`,或任何没登记过的名字)照旧遮蔽。测试钉了两个方向,包括
`content_key_secret` / `my_content_key` 必须仍被遮。

## 二之二、Windows 硬闸跑不了时怎么办(`a319b3f` `7828cf1` `2b5bbd6`)

`windows-ci.yml` 是硬闸,但这里是 Linux 沙箱。**跑不了不等于验不了**:那道闸要守的
不变量是写下来的,那就逐条比对我改过的文件。抓到两类真缺陷,都不是靠猜:

**CRLF 漂移。** `Path.write_text` 用 `newline=None`,在 Windows 上把每个 `\n` 转成
CRLF。于是在 Windows 上脚手架出来的项目,字节和内容哈希都与别处不同。而
`atomic_write_text` 显式钉了 `newline="\n"`——其它真相文件早就走它了,**唯独脚手架
是例外**:packaging.yaml、五个 bible 文件(这一波我自己加的)、三个 story 模板、
`.gitignore`、全部 preset 种子。全部改走那个 owner,顺带也变成原子写。

**`sorted(Path)` 的大小写折叠。** `build/ingest.py` 里白纸黑字记着规则和事故:
"Windows folds case in PurePath ordering — gate run #4 moved plan row indices"。
六处 locale 扫描没照做,而它们的顺序决定了漏斗证据句、`manju status` 的下一步、
director 提案和导出中心**先点名哪个语种**。

**没有做全树大扫除。** 全仓库 28 处 `sorted(Path)`,逐个读后果,只改有实际后果的。
留下的例子:`gui/state.py` 的指纹由 `mtime_ns` 构成、只和同机器几秒前的值比对;
`_present_from_stage` 建的是用于比对的字典,顺序不影响结果。改这些是纯 churn,
而验收门明说不落投机改动。

**测试是源码钉,并且写明了为什么。** 大小写折叠在 Linux 上复现不了,行为测试在这台
机器上无论有没有 bug 都会过——那就什么也没证明。能验的是"没有任何 locale 扫描在排
Path 对象"、"新项目里任何位置不得出现 CR 字节",这才是规则真正要求的性质。

## 二之三、审查闸门与测试隔离(`bc22cb6` `2b5bbd6`)

**`pull-sheet` 的先审后写,形式在实质不在。** 它默认只出计划,就是为了让人先审。
但人看到的只有 `0 create · 1 update · 11 unchanged`——哪个镜头、哪个字段、改成什么
一概没有,审的人只能盲签或自己去 diff CSV。细节**早就算出来了**,`--json` 里一应
俱全,只是从没送到读计划的人眼前。现在列出变更行及其字段与新值;无变更时保持安静;
被锁定而将跳过的字段单独黄字点出。测试特意钉了"打印细节的分支仍然零写入"。

**M0 e2e 模块单条跑不了(既有缺陷,我自己踩到)。** 它靠测试**顺序**隐式传递"已构建"
前提,所以 `pytest tests/test_e2e_m0.py::test_m0_reopen_state_intact`——调试时的第一
个动作——会以一句光秃秃的 `assert False` 失败。我先怀疑是自己改坏的,**回退源文件后
照样失败**才确认是既有问题。改成显式的模块级 `built` fixture,四条现在都能单独跑通,
整模块仍然只构建一次。

## 三、观感修复(`c6a3f89`)

用户明确提出"看得懂、用得顺"优先,这一组专门修可读性:

- **判据说明提到区首**,只说一次(浏览器实测:可见重复 12 → 0)。
- **驾驶舱大标题读同一个 `build_lock` 信号**,不再与红条矛盾。
- **`manju status` 不再"完成 ✅"压着 12 条待办**,改为说明它没在数什么。
- **重复失败折叠**成一条带 `×N`,展开仍是该次的真实证据与时间戳。
- **骨架屏**替代顶部一行灰字(最重面板约 3 秒),含 `prefers-reduced-motion` 分支;
  底部 18 个"从未使用的技能"折叠进计数。

## 四、这一波我自己造的错(都已修)

诚实记账:

1. 一次编辑把分隔符写成了**字面 NUL 字节**,`page.py` 整个无法导入,一片 GUI 测试
   全红。
2. `newtake` 档**抢了 `stale`**(见上),是真的判断错误,改代码不改测试。
3. `_is_neg_inf` 忘了该文件里 `math` 是局部导入(ruff 抓到)。
4. 测试里把多行 JSON 按最后一行解析。
5. **在全量测试跑完前就宣称"验证通过"**,结果全量抓出第 2 条。这是流程错误,
   不是手误——此后每一轮都等全量出结果再下结论。

另外前几轮跑测试时用 `tail -16` 把失败清单截掉了,导致要靠逐模块二分找那第八条;
改用 `--tb=no -rf` 后才能真正对账。

## 五、改了两个既有测试,都不是弱化

- `test_ux_polish` 的阶梯 fallthrough 断言 `ok`,但它选的是两个 take 里较旧的那个,
  而它自己的注释写着"没有待办的 fresh 镜头"——那就**有**待办。改成选最新的 take,
  这才是那句断言一直想表达的意思。
- `test_funnel` 钉的是每阶段字典的**精确**键集合(API/MCP 契约),`satisfied` 是
  刻意新增的。把期望集合加上该键,**保留精确相等**而不是放松成子集——这个钉子的
  意义就是形状不能被"不小心"改大。

## 六、验证

全量测试每轮都跑,末轮:**7 failed / 5432 passed / 18 skipped / 15 errors**,与环境基线
逐条一致(7 条失败 + 15 条 error 全是沙箱 ffmpeg 缺 `drawtext` 滤镜)。零回归。
`ruff check src/ tests/` 干净;frozen CLI surface 与 CONTRACTS schema ids 未变。

**未验证**:`.github/workflows/windows-ci.yml`(硬闸)仍未运行——本环境是 Linux。
但见"二之二":该闸要守的不变量已按文档逐条比对过改动文件,并因此修掉两类真缺陷
(CRLF 漂移、`sorted(Path)` 折叠)。剩余不可本地验证的部分只能由 CI 覆盖。

## 七、扫过但没发现问题的

`presets` · `schema` · `series` 全流程(建剧集→两集→bible 下发→**本地改动不被覆盖、
报为分歧并给出下一步**)· `qc brief → verdict → 汇入 qc.md` 闭环 · 素材库 add/list/use ·
`history` · `evaluate`(诚实声明写得罕见地好:明说自己是镜子不是分数、相关不是因果、
小样本不可信)· `migrate inspect`(预报 13 个缓存段将失效)· `routing explain` 逐镜给
理由 · EDL 往返(诚实警告 `rate_assumed`)· `rollback`(两次显式选择后正常;首次
自动选用后的拒绝信息给了替代方案)· SIGKILL 打断构建后无损坏 · 带任务退出 19 秒
干净退出 · 中文+空格文件名全程无碍 · 窄至 420px 无横向溢出 · GUI 全部页面零
pageerror。

---

# 第二轮(同日续测)

继续按"用工具、不读代码"的办法往下走,又找到 10 处。仍然一个正确性缺陷都没有:
引擎每次都算对了,错的是**它怎么把算对的东西说给人听**。

## 一、有东西可查 vs 根本没东西可查

`manju roundtrip` 的 `truth_moved` 是拿导出时的 baseline 边车和当前时间线对账算出来
的。**没有边车时它保持默认值 `False`**,11 行全部 `state="ok"` —— 和"查过了,真相
没动"渲染成一模一样的结果。

这正好把 roundtrip 存在的意义倒过来:从剪辑软件回来的草稿,几乎**必然**不在
`exports/<kind>/` 原目录(边车在同级 `.baseline/`),也就是说**最需要提醒的那一次,
恰恰是唯一不提醒的那一次**。

改的是抬头一行:没有 baseline 就印 `truth_moved=未知 (unknown)` 加一条黄色说明,
点名缺什么、去哪找、`--apply` 前该确认什么。行数据和 `state` 词表一个字没动
(agent 在上面分支)。

## 二、你看见的名字,必须能打得出来

`bible/characters.yaml` 写 `name: 周叔`,`manju appearances` 印 `old_zhou  周叔`,
GUI 的角色 chip 上也是"周叔"。**但 `build_lookup` 只索引 `id` 和 `aliases`** ——
于是 `@周叔` 解析失败。

而且是**双重死路**:`all_names` 里全是 ASCII id,difflib 拿一个 CJK token 去比,
连"最相近"都给不出来。用户看到满屏的"周叔",打 `@周叔`,得到"未解析",没有任何
下一步。

现在 `name` 一起进索引,优先级 id > name > alias。同一个资产多一个把手,不会凭空
造出冲突(`is_collision` 按 `(kind, id)` 去重);真和别的资产撞名才算冲突,那本来
就该报。顺带 GUI chip 的 tooltip 认得"(经名字 周叔 解析)"——以前这种写法是断链 chip。

## 三、空标题下面什么都没有,读起来像坏了

`manju mentions` 在没有 @提及 的项目上只印一行标题就结束。用户分不清是"扫过了没有"、
"根本没扫"、还是"崩了",而且屏幕上没有任何地方说 @提及长什么样 —— 又是一条死路。

现在空状态说清扫了什么范围(12 个镜头 + story/*.md)、这不是错误、以及写法示例和
`--apply`。有命中时输出和 JSON 一字未改。

## 四、四处对不齐的表(同一个根因的四种长相)

1. **`manju exports`** 用 `f"{label:<18}"` 补位 —— 按**码点**数,而 CJK 一个码点占
   两列。状态列在第 27~37 列之间乱跳,最长的那行
   `M_AND_E_BUS_EXCLUSION_MASTER上新` **直接贴死没有空格**。这个仓库早有唯一属主
   `presets.display_width` / `pad`,另外四张 CLI 表都在用,只有这张没接上。接上,
   宽度从当前行实测,现在全部落在第 40 列。
2. **`manju tasks`** 印裸 `#{id}`。账本一过 10 行,`#9` 比 `#12` 短一格,后面
   shot/provider/status/时间**整体错开一格** —— 偏偏长历史才是要读它的理由。改成
   整体右对齐(` #9` 而不是 `# 9`,`#` 不和数字分家)。
3. **`manju spend`** 的 `合计 / total  0 ` 拖一个空格 —— 币种为空时 f-string 里那个
   空格照印,读起来像被截断。非零金额的未知币种标 `?`(和混合币种分支、tasks 页脚
   一致),零金额不标。
4. **`manju skills`** 硬编码 `:<22`,而 `continue-from-accepted-take` 有 27 个字符 ——
   那一行的说明文字比其它行右移五列。同样改成按当前行实测宽度。

顺带把整个 `cli.py` 的 `:<N}` 补位点扫了一遍:剩下的补的都是 ASCII 枚举/id
(PASS/FAIL、role、provider、shot id、hash),码点数==列数,不受这个根因影响。

## 五、下一步必须是一条能敲的命令

`next_step` 是用户唯一要读的那行,几乎每一档都以命令结尾。两档不是:

- **空项目**——一个新项目印出来的**第一句话**,只说"先写 shots/",指了个目录,没有
  命令,尽管 `manju new` 上一行刚推荐过 `manju create`。
- **broken 档**——只说"修复 broken 镜头:S003",而它正下方的逐镜待办**已经**知道两条
  补救命令。

两处都补上命令,`next_step_key` 一个字没动(agent 和 GUI 在上面分支)。

## 六、`manju events` 在项目终于有历史可读的时候变得读不了

人类视图直接 `json.dumps(detail)`。真实证据记录(`stage_attempt` 带 schema id、
run id、attempt id、每个产物的 spec hash、产物列表、语义摘要)一行 700~900 列 ——
帮助里写着"who did what, when"的命令,**恰好在有东西可看的时候变成一堵哈希墙**。

现在人类视图给摘要:跳过结构性噪音(schema / semantic_digest)、长值截断、**明说**
省了几项以及去哪看全文。`--json` 一直就在输出完整记录,一个字节没动(MCP 的
`events` 工具和所有 JSON 消费者走的是那条路)。最宽行从 ~900 列降到有上界的 ~208。

## 七、一行事件被排成了一列字(GUI)

驾驶舱"最近动态"里,`.ck-ev` 是窄栏里的 flex 行。flex 子项默认可收缩且
`min-width: auto`,于是长 action 名(`mentions_apply`)把 `.edetail` 挤到接近零宽;
而 `.edetail` 带着 `word-break: break-all`,**于是每个字符换一行**,一条事件变成
十几行高的竖梯:

```
mentions_apply   s
                 h
                 o
                 t
                 …
```

三件事缺一不可,测试三条都钉:固定格 `flex: 0 0 auto`、弹性格 `min-width: 0`
(否则永远不肯收缩)、行内 `word-break: normal`。全文挂 `title`,块改 `span2` ——
一栏放不下 `action  shot=S007  07:13:25`。`.edetail` 的公共规则保持 `break-all`
不动,别处是故意要那样折的。

## 八、我自己这一轮的两处错误(照例留档)

- **我写的对齐测试用 `ln.split()[0]` 取标签**,而标签里本来就有空格("成片 Final"),
  量错了列 —— 测试自己先红,才发现取法不对。
- **我写的 CSS 规则查询没先剥注释**,而我新加的注释里有逗号,于是按逗号切选择器时
  把整段注释粘到了第一个选择器上,两条测试假红。两处都是测试侧的错,但都印证同一件
  事:**测试先红一次是有价值的**,它这次红在了我自己身上。

另外一条值得记的:`test_exports_table_alignment` 里那条"行尾不留空白"的断言,**抓到了
我这次修复本身的缺陷** —— 我给状态列补了位,却没处理最后一列补位留下的行尾空格。

## 九、验证

末轮全量:与环境基线逐条一致(7 failed + 15 errors 全部是沙箱 ffmpeg 缺 `drawtext`),
零回归。`ruff check src/ tests/` 干净。10 处修复共 58 条新测试(8 个文件),**每一条都先验证过
红**(其中两条以 ImportError 形式红,那是最强的一种)。CLI surface 与 CONTRACTS
schema ids 未变。

**仍未验证**:`windows-ci.yml`。本环境是 Linux,这一条只能由 CI 回答。

---

# 第三轮:把"环境限制"当成待办,而不是当成借口

前两轮我一直说那 7 条失败 + 15 条 error 是"环境的锅"(沙箱 ffmpeg 缺 `drawtext`),
并且就那么放着了。这是错的——**没验证过就是没验证过**,理由再合理也不改变这一点。
这轮把它当成待办处理。

## 一、先把 drawtext 补上

沙箱里两个 ffmpeg 都没有 drawtext(7.0.2 static 的 configure 字符串里明明有
`--enable-libfreetype`,但 `-filters` 里就是没有)。换 BtbN 的构建后 drawtext 可用,
那 22 条**全部通过**。

也就是说:它们本来就是好的——但**这是现在才知道的,之前只是假设**。

## 二、换新 ffmpeg 之后,冒出 3 条新失败 —— 其中一条是真 bug

3 条颜色相关的失败。逐条查过 ffprobe 原始输出,同一条编码命令:

| ffmpeg | color_space | color_range | color_trc | color_primaries |
|---|---|---|---|---|
| 7.0.2 | bt709 | tv | **bt709** | **bt709** |
| 7.1 | bt709 | tv | **UNKNOWN** | **UNKNOWN** |
| master | bt709 | tv | **UNKNOWN** | **UNKNOWN** |

**ffmpeg ≥ 7.1 不再认 libx264 的 `-color_trc` / `-color_primaries` 输出选项。**

其中两条是测试夹具的问题(夹具用那两个选项造"带标签"的样本,于是在新 ffmpeg 上
造出来的样本根本没标签,测试就以"解析器坏了"的样子失败)。但**第三条是产品 bug**:

`test_windows_color.py::test_render_untagged_today_and_tagged_when_opted_in` 走的是
**Manju 自己的渲染路径**。`color.tag_outputs: true` 承诺四个轴全打 bt709,而在
ffmpeg ≥ 7.1 上只有两个轴落地——**用户主动开了这个开关,拿到的是悄悄少了一半标签的
母版**。这正是这个开关存在的意义所在,而且母版标错是交付被打回的常见原因。

修法:标签同时走 `setparams` 滤镜节点(直接盖在帧上)。三个构建(7.0.2 / 7.1 /
master)全部认;该滤镜从 ffmpeg 4.3 就有,所以钉死的 6.1.1 也有。输出选项**保留**
——老版本认的就是它,留着不花钱,两代 ffmpeg 谁都不依赖对方的行为。

两个必须同时成立的约束,测试都钉了:
- **没开这个开关的项目,滤镜图和 content key 必须逐字节不变** —— 谁都不该被一个
  自己没碰过的选项逼着重渲染。节点只在开启时折入,和 overlay_images / look /
  toolchain 的既有写法一致。
- **开了的项目必须换 key** —— 字节变了,key 不变的话,那份少了两个标签的旧母版会
  一直匹配自己的边车、永远不会被重渲染。**陈旧的 key 绝不能为它已经描述不了的字节
  背书。**

## 三、现在的验证状态

| ffmpeg | 结果 |
|---|---|
| **7.1**(有 drawtext) | **5530 passed, 0 failed, 0 errors** |
| 7.0.2(无 drawtext) | 5508 passed;失败的 7+15 条全部是 drawtext 缺失,且已在 7.1 上验证通过 |

**全套测试每一条都已在真实 ffmpeg 上跑绿过**,不再有"这条只能靠推测"的项目。

## 四、这轮的教训

我把"环境不支持"当成了终点,连着两轮在报告里写"这是环境的锅"。它确实是环境的锅
——但**能换环境**。真去换了以后,不但证实了那 22 条是好的,还顺带挖出一个用户会
真正被咬到的产品 bug(而且它在钉死的 6.1.1 上还看不见,CI 也抓不到)。

**"我验证不了"和"我没试过去验证"是两回事。**

`windows-ci.yml` 仍然只能由 CI 回答——那个是真的换不了环境。

---

# 第四轮:把 windows-ci.yml 拆开,看哪些其实能验

上一轮我说"`windows-ci.yml` 真的换不了环境"。**这句话把整个闸门当成了一块铁疙瘩。**
它不是——它是一串步骤,其中只有一部分真的需要 Windows 语义。拆开看:

| 闸门步骤 | 本机能验? | 结果 |
|---|---|---|
| setup-python 3.11 | ✅ | 本机就是 3.11 |
| choco install ffmpeg 6.1.1 | ❌ | Windows-only(且 6.1 构建已被上游下架) |
| ffmpeg 版本断言(PowerShell) | ✅ | **装了 pwsh 真跑,挖出一个洞** |
| `pip install -e ".[dev,jianying,capcut,mcpvideo,edgetts]"` | ✅ | **之前从没装过这些 extras** |
| `python -m pytest -q -n auto` 全量 | ✅ | 装上 extras 后重跑 |
| `manju --help` 控制台入口 | ✅ | 通过 |
| install-smoke(PowerShell 安装/回滚/卸载) | ❌ 行为 / ✅ 语法 | 三个脚本用真 pwsh 语法检查全过 |

## 一、我一直没装 extras —— 也就是说全量套件我一直跑的是另一条路

闸门装的是 `[dev,jianying,capcut,mcpvideo,edgetts]`,而我这几十轮全量跑的是
`[dev]`。查了一下:`pyJianYingDraft` / `pycapcut` / `mcp_video` / `edge_tts`
**四个全都没装**。也就是说每一次跑的都是"extra 缺失"分支。

装上以后重跑:**跳过数从 18 降到 11 —— 有 7 条测试从来没真正跑过,现在跑了,全绿。**

## 二、装了 pwsh 之后,闸门自己的防腐断言里有个洞

闸门用这句确认 ffmpeg 没被悄悄换掉:

```powershell
if ($v -notmatch "ffmpeg version 6\.1") { throw "ffmpeg is not the pinned 6.1.x build: $v" }
```

`6\.1` 是**前缀匹配**。我在真 PowerShell 7.4.6 里逐条跑过:

```
6.1.1-3ubuntu5   → 接受 ✓(应该)
6.10 Copyright   → 接受 ✗(不该 —— 6.10 不是 6.1.x)
```

一个存在意义就是"拒绝一切非 6.1.x"的守卫,**能被它自己要拒绝的版本满足**。改成
`"ffmpeg version 6\.1(\.|\s|$)"`,十个用例在真 pwsh 下逐条验过。ffmpeg 历史上没出过
6.10,所以这是潜在的洞而不是正在流血的伤口——但守卫的话术必须和它的行为一致。

顺带钉住的:闸门不得出现 `continue-on-error`、`pytest` 后面不得出现 `-k`/`-m`/
`--ignore` 收窄、extras 必须齐全、ffmpeg 存在断言必须在(没有它,套件可以靠静默跳过
约 63 个 ffmpeg 相关文件来变绿——那是唯一绝不能算数的绿)。

**我自己又犯了两次同一类错**:第一版测试直接在原文里搜 `continue-on-error`,而文件头
的注释里就写着"no continue-on-error anywhere",于是假红;另一条查 `-m` 收窄,而
`python -m pytest` 本身就含 ` -m `。都是"没剥掉包装就匹配"——和上一轮 CSS 注释含逗号
是同一个毛病。

## 三、当前验证状态

| 配置 | 结果 |
|---|---|
| **闸门同配置**(全 extras + `PYTHONUTF8=1` + ffmpeg 7.1 + `-n auto` 全量) | **5553 passed, 0 failed, 11 skipped** |
| ffmpeg 7.0.2 | 仅 drawtext 缺失那批失败,已在 7.1 上验证通过 |
| 三个 PowerShell 安装脚本 | 真 pwsh 7.4.6 语法检查全过 |
| 闸门内联 PowerShell(4 段) | 语法检查全过 |
| `manju --help` 控制台入口 | 通过 |

## 四、诚实记一条没能复现的失败

某一次全量跑里
`test_transitions_looks.py::test_applied_xfade_boundary_cache_reused_and_type_change_rerenders_only_boundary`
失败了一次。之后**单跑 3 次、模块并行跑 1 次、全量再跑 1 次,全部通过**,没能复现。

该测试在重并发下做真 ffmpeg 渲染并比对 `st_mtime_ns`,怀疑是负载下的偶发。**我没有
复现,就不会去"修"它** —— 往一条正在通过的测试里塞一个猜出来的修复,正是本仓库规矩
禁止的那种投机。记在这里,是因为下一个会话应该知道它偶发过。

## 五、剩下真正验不了的

- **Windows 的 OS 语义本身**:msvcrt 字节锁的真实行为、CreateProcess 引号规则、
  NTFS 大小写折叠的排序。
- **install-smoke 的运行时行为**:安装/回滚/卸载真的动了什么(语法验了,行为没验)。
- **choco 装 6.1.1 这一步**本身(而且 6.1 的第三方构建已被上游下架,本机拿不到)。

这三条只有真 Windows 能回答。**但它们比我上一轮说的"整个 windows-ci.yml 都验不了"
小得多** —— 那句话当时把能验的部分也一起放弃了,还因此漏掉了 extras 从没装过、以及
闸门断言里那个洞。

---

# 第五轮:"只有 Windows 能回答"里,有两条是我判断错了

上一轮我列了四条"只有真 Windows 能回答"的:msvcrt 字节锁行为、CreateProcess 引号
规则、NTFS 大小写折叠排序、install-smoke 运行时行为。**其中两条是错的。**

Python 自带这两件事的**纯 Python Windows 实现**,在 Linux 上照跑:

- `PureWindowsPath` 在**任何平台**都按大小写折叠做比较 —— Windows 的真实排序在
  Linux 上就能造出来。
- `subprocess.list2cmdline` 生成的正是 CreateProcess 会拿到的那条命令行 —— 这是
  真契约,不是手写的近似。

## 一、大小写折叠排序:差异在 Linux 上就能看见

```
sorted(PurePosixPath)    ['Banana', 'S010', 'Zebra', 'apple', 'cherry', 's002']
sorted(PureWindowsPath)  ['apple', 'Banana', 'cherry', 's002', 'S010', 'Zebra']
                          ↑ 两者不同
sorted(key=as_posix)     两种 flavour 下结果完全一致
```

CLAUDE.md 那条"`sorted(..., key=as_posix)` 保证跨平台确定性"的规矩,**现在是被执行
验证的,不再是被假设的**。

顺带查清一件事:我之前修的那六处 locale 扫描,现在排的是 `p.name`(字符串),本来
就跨平台稳定。所以我第一版写的"找 `sorted(<glob>)`"grep 钉子**根本对不上真实形状,
是个空钉子**。改成按行为断言 `list_locales` 的输出顺序,并**植入 Windows 排序验证
它会红**(`['de','en','Ja','ZH']` ≠ `['Ja','ZH','de','en']`)。

## 二、CreateProcess 引号:用真契约做往返验证

`list2cmdline` 造出 CreateProcess 会收到的命令行,`_split_command` 必须能从中还原
原始 argv —— 否则带真实 Windows 路径的 provider 模板会丢掉反斜杠、子进程 exit 127
(这正是闸门第一轮那个缺陷)。7 种 argv 形状(带空格的程序路径、UNC、CJK 目录、
`key=a b`)全部往返通过。

**植入回归验证过**:把 Windows 分支改回裸 `shlex.split`,5 条立刻变红。

## 三、我又纠正了自己一次

`test_windows_invariants_guard.py` 的文件头原本写着:大小写折叠排序和 CreateProcess
引号"只有 CI 能回答"。**那句话是错的**,而且代价很实在 —— 它让两条真实不变量白白
少了唯一一层便宜的检查。已改正并指向新文件。

**"这个验不了"本身就是一个论断**,而我这两轮连着把它下错了两次(先是整个
windows-ci.yml,再是这两条)。

## 四、那条偶发失败:又找了一轮,仍未复现

针对性压测:6 个 dd 进程占满 4 核的情况下单跑 **12 次**,0 失败;把它和 7 个
ffmpeg 重模块一起 `-n auto` 跑 **6 轮**,0 失败;此后全量又跑了 **2 次**,都绿。

累计:那条测试自那次失败后已通过约 **40 次**。我读了它的缓存键构成
(`hash_file(src)` + 尺寸 + fps + 时长 + target + 淡入淡出),**没有任何时间相关输入**,
所以不存在明显的逻辑竞态。**没有复现、没有机制,我就不会去改一条正在通过的测试。**

## 五、现在真正剩下的

- **msvcrt 字节锁的真实行为**(纯 Python 无从模拟)
- **install-smoke 的运行时行为**(语法已验,行为未验)
- **choco 装 6.1.1 这一步**(且 6.1 第三方构建已被上游下架)

比上一轮的四条少了两条。全量套件(闸门同配置):**5568 passed, 0 failed**。

---

# 第六轮:最后三条,一条关掉、一条改正、一条确认关不掉

## 一、install-smoke:能真跑,而且抓到了 grep 抓不到的东西

这些脚本里不碰 venv 的那部分是**可移植 PowerShell**(`Join-Path` / `Test-Path` /
`Remove-Item` / 指针文件),把 `$env:LOCALAPPDATA` 指到临时目录,**在 Linux 的 pwsh
下真跑起来了**。

原有的 `test_windows_install.py` 是**纯文本检查**:断言每条 `Remove-Item` 里出现过
`$p` 或 `$AppRoot`。这能挡住手滑,但**挡不住算错的值** —— `$p` 算高了一级,照样通过
每一条 grep,同时把用户的作品删掉。

我植入了两个 bug 做对照:

| 植入的 bug | 原文本测试 | 新行为测试 |
|---|---|---|
| `$p` 高一级(删掉整个 `Manju/`) | **通过** ❌ | 抓到 ✅ |
| 加一行删 `~/.manju`(仍然用 `$p`) | **通过** ❌ | 抓到 ✅ |

第二个尤其说明问题:**一个会删掉用户 providers/routing/素材库配置的脚本,能通过全部
文本检查。** 现在 9 条行为测试真跑 uninstall 与 rollback:app 被删干净、Logs 不带
`-Logs` 时保留、**项目和 `~/.manju` 原封不动**、重复卸载不报错、回滚指针互换(可以
再滚回来)、没有 previous 时**响亮失败**而不是假装成功、目标版本已被删时**拒绝**而不
是把 launcher 指向不存在的版本。

## 二、msvcrt:我装了 Wine 去验,结果证明 Wine 验不了这件事 —— 但顺手抓出我自己写的一句假话

Wine 9.0 + Windows Python 3.11.9 跑起来了(`sys.platform == 'win32'`,`msvcrt` 可用)。
针对争议点做了带正反对照的探针:

```
A: 同进程两线程、各自 fd   → t0 ACQUIRED, t1 BLOCKED   ← 排他了
B: 持锁时子进程来抢         → BLOCKED                   ← 正对照通过
C: 释放后子进程再抢         → ACQUIRED                  ← 正对照通过
```

探针是可靠的(两个正对照都对),但结论**和真机记录相反**。`core/events.py` 属主注释
里记着 gate round 2 在**真 windows-latest** 上的测量:**12 个并发线程,只活下来 6 行**。

所以:**Wine 的 msvcrt 是重新实现的,在这个语义上和 Windows 不一致。它是关于 Wine
的证据,不是关于 Windows 的证据。** 这条我没能关掉,而且现在知道**为什么关不掉**。

但这一趟没白跑 —— 它逼我去核对原始记录,于是发现**我自己在前面某一轮往
`test_windows_invariants_guard.py` 里写了一句假话**:

> "msvcrt.locking does NOT exclude two threads in the same process
> (**each opens its own fd and both calls succeed** — DECISIONS #38)"

括号里那个**机制是我编的**,任何记录里都没有。而且引的 DECISIONS #38 里 round 3b 明写
"the 12-thread probe PASSED on the real host"(那是**加了线程锁之后**那次的结果)——
引它反而像在自打嘴巴。

结论本身没错(真机确实丢了行),**错的是我给它编了个理由、又引了一条读起来相反的
出处**。已改成引用属主注释里的真实测量,并写明 Wine 在此不可采信。

**给下一个会话的教训:编造机制比说"不知道"更危险。** 它读起来像证据。

## 三、choco 装 6.1.1:确认关不掉

choco 只在 Windows 上有;且 6.1 的第三方 Linux 构建已被上游下架(我试过 BtbN 的
`n6.1-latest`,404)。这一步只能由真 CI 回答。

## 四、当前状态

全量套件(闸门同配置):**5577 passed, 0 failed, 11 skipped**。

| 原来"只有 Windows 能验"的四条 | 现在 |
|---|---|
| CreateProcess 引号 | ✅ 用 `list2cmdline` 真契约往返验证(植入回归验证过) |
| NTFS 大小写折叠排序 | ✅ 用 `PureWindowsPath` 造出真实差异(植入回归验证过) |
| install-smoke 运行时 | ✅ uninstall/rollback 在真 pwsh 下执行(两个植入 bug 都抓到) |
| msvcrt 字节锁真实行为 | ❌ 仍未验 —— **且已证明 Wine 不能替代** |

加上 choco 那一步,**真正剩下两条**,都需要真 Windows。

---

# 第七轮:我说了三次"要开 PR 才能触发 CI" —— 三次都是错的

`windows-ci.yml` 的 `on:` 里一直写着 `workflow_dispatch: {}`,而且文件头自己就解释了
为什么:

> "plus manual dispatch **so any single commit can be proven green on Windows
> before it is merged**."

**这个仓库的作者早就为我这个处境准备好了机制,我连着三轮没去看,还反复告诉你需要你
点头开 PR。** 这是本次会话里同一个毛病的第三次发作:**把没查过的东西当成查过了**。
前两次是 drawtext 和 PureWindowsPath,这次是它。

## 一、触发之后,第一件事就是发现硬闸是红的

而且不是我这几轮改出来的 —— 是**已经合并进去的那个审计波次**留下的。Run #255:

```
FAILED tests/test_ledger_p0_safeio.py::test_fifo_destination_is_refused
FAILED tests/test_ledger_p0_safeio.py::test_append_refuses_fifo
AttributeError: module 'os' has no attribute 'mkfifo'
2 failed, 5327 passed, 53 skipped
```

`os.mkfifo` 在 Windows 上**根本不存在**。两条测试没加平台守卫。

这两条测试本身没错、也确实该是 POSIX-only(Windows 上没有 FIFO 可以塞到输出路径上,
这个攻击面在那边不存在)。**问题是它们漏了 marker,而 Linux 永远不可能发现** ——
`os.mkfifo` 在这边解析得好好的,本地全绿说明不了任何事。

最扎心的是:**同一个波次在 `test_ledger_p0_provider_refs.py` 里做对了**
(`@pytest.mark.skipif(WINDOWS, reason="POSIX FIFO")`)。知识是有的,缺的只是强制。
而唯一发现它的东西,是一个跑 13 分钟的 Windows job。

## 二、所以不能只补 marker,得让这一类在 Linux 上可见

新增守卫:测试里凡是用到**在 Windows 上不存在的 `os.*` 名字**(mkfifo / mknod /
getuid / chown / fork / killpg …),必须带 Windows 守卫。

刻意收窄到"**不存在**的名字",因为那样漏守卫就是**必然的 AttributeError**,不是判断
题;`os.symlink` 需要权限、`os.link` 跨卷这类**行为差异**是另一个问题,不在这里瞎猜。

守卫自带真值表测试(过松的 GUARD_RE 会把所有文件都放行 —— 那才是真正要防的失效
模式),并且**把 marker 撤掉验证过它会红**,红的正是闸门点名的那两个模块。

## 三、install-smoke:真 Windows 上通过了

这次 dispatch 的 install-smoke job **在真 windows-latest 上全绿**,包括
"Uninstall never touches projects"。

上一轮我是用 Linux 的 pwsh 模拟验的;**现在是真机证据**。这一条从"模拟验过"升级成了
"验过"。

## 四、当前状态

- 本地全量(闸门同配置):**5584 passed, 0 failed**
- 真 Windows install-smoke:**通过**
- 真 Windows 全量套件(run #257,commit 7493554):**test 与 install-smoke 两个 job 全绿** —— 硬闸通过

## 五、这一轮真正的教训

前六轮我一直在做同一件事的不同变体:**先断言某件事验不了,再被证明是我没去试。**

- 「沙箱 ffmpeg 缺 drawtext,这是环境的锅」→ 换个构建就有了,还挖出一个产品 bug
- 「大小写折叠排序只有 Windows 能看」→ `PureWindowsPath` 在这儿就能看
- 「install-smoke 只能真机验」→ 一半能在 pwsh 里跑,而且抓到了 grep 抓不到的东西
- 「要开 PR 才能触发 CI」→ `workflow_dispatch` 一直都在

**每一次我给出的理由都成立,每一次结论都是错的。** 理由成立和结论正确是两回事,而我
这一路把前者当成了后者。这份报告留着,是希望下一个会话在写下"这个验不了"之前,先花
五分钟证明它。

---

# 第八轮:不再找 bug —— 改体验(人 + AI 两边)

## 一、AI 侧:一份 177 处测试钉着、却没人能读的契约

`_fail` 的 docstring 写着 `code` 是「agent 可以分支的稳定机器令牌」,全套测试里有
**177 处**断言具体的 code。但**始终注入的核心技能里,一个字都没提过这个信封**。
agent 拿到 `{"error": …, "code": "unknown_shot"}`,无从知道有哪些 code、哪些值得
分支。

普查之后更要紧的事实浮出来:**52 个专门 code,但 275 个失败点里有 250 个用的是默认
的 `error`** —— 也就是约 91% 的失败是「未分类」。而没有任何地方告诉 agent 这一点,
于是一个尽责的 agent 会去给 `"error"` 写分支逻辑,那是纯粹的浪费。

做了三件事:

1. **同一个事实必须给同一个 code。** `impact S099` 原来返回 `impact_error` ——
   一个**命令名形状**而非**事实形状**的 code,于是按 `unknown_shot` 分支的 agent
   悄悄漏掉它,而且丢失了 `_require_shot` 那条带两个补救命令的消息。改成接入既有
   属主 `_require_shot`(`select`/`redo`/`voice` 用的同一个)。
2. **写进技能库。** 核心技能加 §10(信封 + 「`error` 是未分类,读文本别分支」+ 指
   路),完整词表另开 `error-codes` 技能:按**你该做什么**分四类(改输入 / 修真相 /
   停下来问人 / 等一下再试),并明确区分它和降级链的 `content_rejected` /
   `rate_limited`(那些在 `manju tasks` 里,补救完全不同)。
3. **钉住文档不许撒谎。** 测试双向校验:文档里写的每个 code 必须真的被 CLI 发出
   (**反捏造**——文档里写一个没人发出的 code,等于让 agent 去等一个永远不会到的
   分支),CLI 发出的每个 code 也必须能在文档里查到。第一次跑就抓出两个漏写的
   (`impact_error`、`ingest_review_no_items`)。

**过程中被一条既有测试拦下**:核心技能每次 `manju auto` 都全文注入,有一条测试卡它
< 300 行,我加的表把它顶到 324。这个限制是对的(那是 agent 的 token 预算),所以**没
有放宽测试**,而是把词表挪进按需加载的技能——这本来就是技能库存在的意义。核心只留
「出错前就该知道」的那几行 + 指路,296 行。

## 二、AI 侧顺带发现:技能 frontmatter 坏了会**静默降级**

我新写的技能 description 里有 `code: "error"` —— 未加引号的 YAML 标量里出现
「冒号空格」,整块 frontmatter 解析失败。而加载器是**刻意宽容**的(`except
Exception: data = {}`),于是技能照常加载,`manju skills` 索引里安静地把 H1 标题印
在了 `when_to_use` 的位置。

**agent 的发现界面降级了,没有任何东西报错。**

宽容属于运行时(一个坏技能不该让 CLI 崩),响亮属于开发期。所以加了守卫:**磁盘上
每一个技能**的 frontmatter 必须真的能解析且含必需键。注意作用域——原有的 frontmatter
测试只跑 `TAXONOMY_IDS`(必备最小集),而超出该集合新增的技能**恰恰是没有任何检查
覆盖的那些**。扫了全部 19 个技能:除我自己那个外都干净。守卫**重新植入原 bug 验证
过会红**。

## 三、人侧:`manju status` 的待办列表

两个只有项目有深度之后才看得见的毛病:

1. **平铺 `todo[:6]`。** 十二个镜头通常缺的是同一样东西(十二行「配音」),于是那
   一条真正不同的(newtake 决策、broken 镜头)会被六个一模一样的邻居挤到线下。
   **最稀有的那行才是最值得读的,而它正是最容易消失的。**
2. 尾巴写着「共 12 项(**manju status --json 看全部**)」——让一个**人**去解析 JSON
   才能看完自己的待办清单。

现在:每个不同的 `key` 都保证先出现一次,尾巴按种类和镜头号点名剩下的。
`--json` 一个字节没动(agent 在那儿读全量 `todo`,机器契约在那条路上)。

改前改后:

```
待办  …共 12 项(manju status --json 看全部)
↓
待办  另有 6 项 — voice ×6(S007, S008, S009, S010, S011, S012)
```

## 四、验证

全量(闸门同配置):**5639 passed, 0 failed**。新增测试都先验证过红,其中三条是靠
**植入回归**证明的(`_split_command` 式的植入法):命令名形状 code、被挤掉的稀有待办、
坏 frontmatter。

## 五、第一次接触:60 个命令的墙,里面没有门

敲 `manju`(不带参数)会把完整帮助全印出来:**约 60 条命令、八个面板**。而那句一行
描述只点了一个命令 —— `build` —— 偏偏是新手**唯一还用不了**的那个(还没有东西可
build)。

更要命的是 `manju help-workflow`:它就是为「我想做 X,该按什么顺序敲哪些命令」而
存在的任务索引(10 条常见流程),但**你得先知道它存在才找得到它**。

修在 **epilog**(而不是顶部描述)是刻意的:墙滚过去之后,**留在屏幕上的是 epilog**。
顶部那行早就滚没了。

```
只记三个入口 / three doors —
manju status · 我在哪、下一步该干什么(任何时候先跑它)
manju create · 从一句话开始的七阶段创作漏斗(全新项目先 manju new)
manju help-workflow · 我想做 X,该按什么顺序敲哪些命令(10 条常见流程)
每条命令都有 --help;输出可读的命令大多同时带 --json 给 agent。
```

**第一版写坏了**:Rich 把帮助字符串当散文重排,单个换行会被吞掉,三个入口挤成一段
读不了的糊。空行能作为段落分隔活下来,所以每个门单独成段。测试里专门钉了这一条
(「三个门必须各占一行」),以及「Rich 标记不许漏成字面量」和「三个门都得是真能跑
的命令」——**404 的门比没有门更糟**。

全量:**5647 passed, 0 failed**。

## 六、帮助里的内部文档引用:指向一份仓库里没有的文件

`manju --help` 顶层带 **40 处 `§` 引用**,外加 `(goal item 2)` `(P3)` `(WP3)`
`(AI_IDE_18 WP7)` 等等。它们记录每条命令是从哪份计划文档来的 —— **下一个维护者需要
它们**。

但 CLAUDE.md 白纸黑字写着:**计划文档本身不在仓库里**。所以对着命令列表看的 owner,
这些是**指向谁也打不开的东西的指针**,还长得像应该有意义的样子。

做法不是去改 65 条 docstring,而是**在渲染时剥**:

- 唯一属主 `_strip_provenance`,挂在既有的 `_SuggestingGroup.get_command` 上;
- 只重写 `short_help`(命令列表那一行),`help` **原样保留** —— 所以
  `manju status --help` 里 `(§10)` 还在,维护者要找的地方还找得到;
- 子命令组继承同一个类,嵌套列表一并生效。

顶层 `§` 从 **40 → 8**。

**刻意保守,而且测试把「为什么保守」钉死了。** 我第一版贪心版本把
`(the decision is one line of text — §3)` 变成了 `(the decision is one line of
text —)`,把 `(§4, §5). The safety net` 变成了 `. The safety net`。**为了去掉噪音而
把句子弄坏,比噪音本身更糟。** 所以规则只有两条:整个括号**全是**出处 → 整个删;出处
**挂在真内容尾巴上** → 只删那个标记。织进句子中间的(`AI_IDE_16 §9 — round-trip…`)
一律不动,并有专门测试断言它们**不许**被动。

写测试时又被自己绊了一次:我先断言列表里「一个 `goal item` 都不许有」,而
`ingest-discard` 的句子中间就有一个 —— 那正是规则**故意**留下的。**改的是测试不是规则**:
断言改成「独立成括号的出处必须消失」+「§ 总数大幅下降」,和设计说的一致。

全量:**5664 passed, 0 failed**。

## 七、命令列表是给 owner 看的,而 owner 读中文

82 条顶层命令里 **56 条是纯英文**。也就是说 `manju --help` ——这个工具唯一的目录——
**对它唯一的使用者基本不可读**。

每条现在渲染中文;英文留在 docstring 里,也就还在 `manju <cmd> --help`,一键之遥、
一字未改。词条是既有英文首行的**严格翻译**——不新增任何声明、不臆造行为。

**两种写法试了、量了、否掉了,而且把否掉的理由写成了断言**,免得以后有人重来:

1. **每行「中文 / English」。** 看着「正确」,但每行都折成两三行,帮助页从 175 行涨
   到 200 多。**把墙加高,不等于把墙变得可读。**
2. **拿 `help.split("\n\n")[0]` 当摘要。** 有几条 docstring 开头就是六行一整段
   (`explain`),于是整段被灌进列表、把邻居全埋了。**取第一行,不是第一段。**

最终帮助页 **175 → 140 行**,每条命令一行。

**防腐杠杆**:测试从命令面**反推**词表 —— 新增一条纯英文顶层命令,套件立刻红,直到
给它补上中文。词表不可能悄悄落后于它所描述的界面。

全量:**5673 passed, 0 failed**。
