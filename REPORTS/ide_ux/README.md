# Manju · 让 AI 接得住，让写作松一口气

维护候选版 **0.2.0+r16.9**，基于完整 r16.8。普通离线台、原素材和旧收工包格式保留。本轮不是商业 IDE 安装器，不含模型账号、Python、FFmpeg 或所有依赖。

## 先用哪一个包

**本地 IDE 协助制作：下载完整包 MANJU_IDE_FULL.zip。** 解压到新目录，打开 `MANJU.code-workspace`，或直接打开 `APP/source/`。上手小包只有页面和示例，没有应用源码和 CLI。

给 IDE 的 AI 这段话即可开始：

> 从 AGENTS.md 开始，先执行只读自检并读最新项目状态。我主要要你协助制作作品，不是默认改软件。接下来我会给你保存好的收工 ZIP 或实际影片工程目录。先确认对象和操作路径，再在保留原件的工作副本上修改，预览后给我新的返回包。不自动生成付费内容、不批准候选、不锁片。

完整包根 AGENTS.md 会指向 APP/source/AGENTS.md；CLAUDE.md 引用同一入口，避免多份规则冲突。`APP/source/docs/ai/OPERATIONS.md` 是完整操作说明。普通目录读取权限不是安全沙箱，规则文件不能保证任何 AI 永久内化或完全遵守。

**只看界面：下载 MANJU_IDE_START.zip。** 完整解压后打开 `START_HERE.html`，不需要 Python 或账号。两包页面相同，不必两份都下载。

## 写作变得更直观

故事现在有独立的「故事与人物」入口，不与单镜头规划挤在一页。场景编辑分成「写这一场」「人物与参考」「制作版本」三步；全局作品设置、完整 JSON 进出和兼容操作收进可展开区。

「镜头与素材」改成单列。输入字号、留白、按钮高度都提升，不靠缩小文字腾地方。切换只是显示变化，不重建表单，不修改已保存指纹，原媒体播放、原图检查和快捷保存继续使用原实现。

需要看全时仍可切回完整长页。焦点定位能展开对应场景步骤，键盘左右键切步骤，窄屏有独立布局。全部原 210 个带编号的输入/操作控件仍各保留一份。

## 从网页交给 AI：你只需先保存一次

在工作台顶部「保存收工包」，下载后选回核验。把这个实际 ZIP 的路径给 AI；它使用已经安装本轮 Manju 的 Python 环境执行：

```text
python -m manju models ide-open CHECKOUT.zip --output AI_WORK
python -m manju models ide-preview AI_WORK
```

新的 AI_WORK 是普通目录，里面有可编辑 `STORY.json`、可编辑 `EDIT.json` 的 values、只读原包、实际参考媒体与用途表、AI 接手说明和 NOTES.md。

AI 可写人物、关系、场景、动作和镜头/导演/返工文字；原素材、原始基线和旧简报/旧观察保持。故事改动会让有关旧简报需要复核，不以相同编号冒充相同内容。

改完再次 preview，用这次结果里的 `preview_sha256` 执行：

```text
python -m manju models ide-return AI_WORK --expected-preview HASH --output RETURNED_CHECKOUT.zip
```

返回的是**新候选包**，不会覆盖原件，也不会直接操作浏览器。你在首页打开它，核验、预览并明确确认。若浏览器后来又写了新稿，先另存；文字可用第09区的既有三方比较取回，故事需独立比较确认。这不是实时双向同步。

完全没有修改的返回，原 ZIP 字节保持；有修改时保留内部原媒体而重新生成包。返回通过结构/哈希检查，不代表模型的剧情判断正确，也不代表你已经审片。

## 主影片的后续全流程

原影片工程有自己的 project.yaml、故事文本、镜头 YAML 与共享 Bible，不是 APP/source。AI 根据现有 Skill 和 JSON CLI 依次进行状态检查、规划、素材回收、人工评审、字幕/声音/导出等工作。本轮补齐实际命令导航并修正旧指南中不存在的 `events --tail` 用法，没有新造一个绕过原引擎的控制面。

新增 IDE 返回接口不负责新增实际候选、编造观察、批准/锁片或开通商业接口。这些仍走既有专用入口和明确授权。没有自动安装大型依赖、付费调用或读取凭据。

## Git 历史在哪里

完整源码快照 `APP/source` 没有 `.git`；历史在 `APP/repository.bundle`。需要维护代码时，从完整包根目录执行 `git clone APP/repository.bundle MANJU_DEV`，然后让 IDE 打开新目录 MANJU_DEV。仅制作作品不要求恢复源码 Git。

不要在旧目录直接解压覆盖，不要因为 git status 在普通源码快照失败就新建一个空历史冒充恢复。

## 示例

在首页打开 `EXAMPLES/IDE/CHECKOUT_FOR_AI.zip` 看原稿，或打开 `EXAMPLES/IDE/RETURNED_CHECKOUT.zip` 看改稿后的候选。内层 ZIP 不用解压；`IDE_PREVIEW.json` 是实际命令的差异报告。示例均为虚构故事与合成媒体，编辑是脚本模拟，不是商业模型生成或你的批准。

保留了 `EXAMPLES/STORY/STORY_CHECKOUT.zip` 的原故事示例和 `EXAMPLES/CHECKOUT.zip` 通用入口。

## 下载与保存

完整包、上手包配有本轮专用 `MANJU_IDE_DOWNLOAD_HELPER.zip`。解压打开 `OPEN_DOWNLOAD_HELPER.html`，选回实际 ZIP 核对大小和 SHA-256。大包不顺利可取五个 PART ZIP，在助手里一次选择，无需解压或排序；合成后保存，再选回核验。

助手不联网、不上传；通过表示字节完整，不是发布者数字签名或 Windows 认证。附件不是永久备份，请保存到自己硬盘并另留副本。

## 尚未验收的范围

未安装实际 Codex/Claude Code/Cursor 客户端做端到端，不宣称任意 AI 自动理解全部项目。未保存的网页内容不可被磁盘 AI 读取。普通 HTML 无依赖；操作 CLI 需 Python 3.11+ 和本包依赖，媒体处理另需 FFmpeg。Windows 原生双击、完整依赖安装、升级回滚与全项目回归尚未认证。

本轮浏览器使用原始页面的隔离文档和真实文件、真实 CLI；不是模拟 Windows 文件导航。未重新渲染整部影片、未重排模型质量榜，原12视频能力档、质量名单和精修路线均保持。
