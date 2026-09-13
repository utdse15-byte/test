# 用 AI 制作作品，不只是让 AI 改代码

## 第一次在 IDE 打开

完整包解压到新目录，打开根部 `MANJU.code-workspace`（支持该格式的 IDE），或手动打开 `APP/source/`。给 AI：

> 从 AGENTS.md 开始接管，先执行只读自检并读最新项目状态。我要用你协助制作作品，不是默认改软件。确认源文件和本次目标后操作；改动保留可恢复的原件，不自动生成付费内容、批准或锁片。

任意 AI 不会因为读了文件就永久内化全部项目。入口文件负责定位，状态快照负责跨会话，CLI 和结构校验负责实际操作。没有 AGENTS 支持的工具，把这份文件明确附给它。Claude Code 使用根 CLAUDE.md 引用同一入口，不复制另一套规则。

## 源码与 Git 历史

直接解压的 `APP/source/` 是完整源码快照，不自带 `.git`。完整历史独立保存在旁边的 `APP/repository.bundle`。仅制作作品不要求先恢复源码仓库；需要改软件、分支与比较历史时，在完整包根目录执行：

```text
git clone APP/repository.bundle MANJU_DEV
cd MANJU_DEV
python tools/ai_bootstrap.py --json
```

`MANJU_DEV` 必须是新的不存在目录；然后让 IDE 打开它。不要把 `git status` 在普通解压目录失败误报为源码损坏。不能在旧应用目录里解压覆盖来升级。

## 环境

只读 `python tools/ai_bootstrap.py --json` 无第三方依赖（Python 3.11+）。它不自动安装。实际制作 CLI 需要安装本包 wheel 或源码及依赖；不要在系统 Python 里乱装。

开发环境示例（需你已有 Python，安装依赖可能访问包索引，先确认）：

```text
python -m venv .venv
# Windows 用 .venv\Scripts\python.exe；其他系统用 .venv/bin/python
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m manju --version
.venv\Scripts\python.exe -m manju models ide-open --help
```

完整包已有 `APP/INSTALL_WINDOWS.cmd` 的私有安装路径。无需为离线工作台安装开发环境；直接用 START_HERE.html。FFmpeg 只在实际媒体处理时需要，不是读 JSON 的前提。

## 从浏览器交给 AI，再回来

1. 在顶部保存收工 ZIP，选回核验。它包含实际媒体与故事；未保存的页面输入不是 IDE 文件。
2. 安装当前版本后，在终端运行（路径可用绝对路径，含空格加引号）：

```text
python -m manju models ide-open CHECKOUT.zip --output AI_WORK
python -m manju models ide-preview AI_WORK
```

3. 让 AI 阅读 AI_WORK/AGENTS.md。修改 STORY.json 的人物、场景与关系；修改 EDIT.json 的 values 完成镜头、导演或返工文字调整。原元数据、旧简报和 REFERENCES 媒体不改。原始已有观察不重写，不能靠改记录伪造“看过”。
4. 再执行 ide-preview，核对 changed_fields、story_changed、brief_status。使用**这次输出**的 preview_sha256：

```text
python -m manju models ide-return AI_WORK --expected-preview HASH --output RETURNED_CHECKOUT.zip
```

5. 你在工作台用“打开材料”载入返回包，核验、预览、明确确认。保存过的原包仍在。返回是整份候选，不是实时同步，不自动替代你后来在浏览器写的新内容。

浏览器又有新稿时：先另存当前收工包；在第09区加载 AI_WORK/EDIT.json 可按原机制三方比较，只取需要的文字。故事整体导入要另行比对、确认，不声称自动三方合并。确认完成后，以新收工包重新 ide-open 开始下一轮。

此接口现在支持人物/故事/场景与镜头、返工、导演文本。新增实际候选/图片、审片/锁片、第三方生成任务仍走既有专用入口，不改 REFERENCES 文件来替换素材。AI_WORK 中可以另写 NOTES.md 供下次接手，它不进入作品数据或自动批准。

## 主影片工程的完整制作路线

以下命令在用户选定的影片工程目录执行，不在应用源码目录执行；目录需已有 project.yaml。先读实际状态，再按需查 --help，不要猜参数。

| 工作 | 读取的 Skill / 文档 | 实际入口 |
| --- | --- | --- |
| 接管、缺口、上次操作 | skills/manju/SKILL.md | manju status --json；manju events -n 30 --json；manju production status --json |
| 立项与故事场景 | creation-funnel、scene-design | manju create --help；manju series --help |
| 人物/设定连续性 | character-consistency、series-bible | manju assets --help；manju refs --help；manju appearances --help |
| 镜头和提示编译 | shot-design、prompt-craft | manju prompt --help；manju director --help |
| 生成计划和风险 | skills/manju/SKILL.md；docs/CLI.md | manju build --dry-run；命中 ask_before 停下 |
| 外部素材回收 | review-take-and-route-repair | manju ingest --help；manju import --help；明确人工评审 |
| 字幕、声音与剪辑 | subtitle-standards、audio-finishing | manju transcribe --help；manju voice --help；manju export --help |
| 修改后的验证 | 原合同、锁与来源文件 | manju check；manju qc；git diff（工程使用 Git 时） |
| 交付与恢复 | docs/CLI.md；原包装说明 | manju pack --help；manju unpack --help |

manju 的生成provider、外部网页操作与商用额度并非全部已接通或实测。命令存在不是商业执行成功。不能用本地占位片当最终画质证据，不能把预演通过当 Picture Lock。

## 维护软件时

读 DEVELOPMENT_GUARDS.md；src 为运行时，tools/workbench.* 为离线页源码；生成页由 `python tools/rebuilt-delivery/build_workbench.py` 构建，不能只改生成后的 HTML。至少跑本次相关测试，记录没有执行的范围。全项目和 Windows 发布门按现有流程，不因局部成功改 LAST_GREEN。

## IDE 入口依据

2026-09-13 核对官方文档：Codex 与 Cursor 支持 AGENTS.md；Claude Code 使用 CLAUDE.md，可通过 @AGENTS.md 引用。支持程度和读取范围取决于实际客户端设置。本轮未连接这些商业 IDE 产品端到端测试。

- https://developers.openai.com/codex/guides/agents-md
- https://code.claude.com/docs/en/memory
- https://cursor.com/docs/rules

入口文件是上下文和工作约定，不是权限系统。需由用户自己的 IDE 读文件、运行命令并回报结果；不会替它配置忽略确认、读取密钥或自动执行付费服务。
