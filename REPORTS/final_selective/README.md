# Manju r16.11 · 从可靠 r16.10 重建的本轮冻结交付

上一轮选择性取回包未保留下来，不能算已交付。这一份重新以用户上传的 MANJU_CHANGES_FULL(1).zip 为基线开发、测试和封包，旧包保持。最终文件的实际大小与校验值见配套核验助手及下载审计，不引用历史承诺。

## 开始

完整解压到新目录，打开 START_HERE.html。仅浏览器使用不需要 Python 或账号。完整包含源码、wheel 与 Git 历史；上手包仅为同一离线工作台。IDE 打开 MANJU.code-workspace 或 APP/source，先读 AGENTS.md，运行 tools/ai_bootstrap.py --json，再按 docs/ai/OPERATIONS.md 执行当前任务。

## 只接回需要的修改

先保存收工包，再交给 IDE AI 建立工作副本。你可以继续在网页写；AI 返回时请给出带原稿的 STORY_RETURN.json，而不是直接恢复整个旧现场。

在首页“打开材料”选择它，进入“故事与人物 / 接回 AI 改稿”。有三份文字：当时交给 AI 的原稿、本地现在、外部新稿。默认只勾选未冲突文字，双方都改过的字段保留本地，不提供强行覆盖。按完整段落阅读后点“接回所选修改”。未选的文字、其他工作区、原素材和历史保留。

初次打开显示可接回数量、冲突和已一致项；文件管理会收起，把空间留给正文。大批改稿每页40项，选择总数包含其他页；可以清空后逐页选。接回一次后，故意没选的其他文字不会重新默认选上。继续写了新内容，旧预览失效，必须用当前稿重新比较。

人物/场景新增和删除、引用、知情范围、场序及新增历史使用一个保守的整组，默认不选。当前结构或待删除对象的文字被改过，就拒绝整组。整份组合结果通过原 Story 验证和大小限制后才一次应用，失败不留半份故事。它不是自动判断剧情依赖的语义引擎。

接回后可以立即撤回一次。继续创作后旧撤回点不能抹掉新输入。撤回不会复活已清除的型号确认、批准或选片。重要旧版本仍需独立收工备份。

## 给 AI 的准确命令

```text
python -m manju models ide-open CHECKOUT.zip --output AI_WORK
# AI 只修改 STORY.json 和 EDIT.json 的允许字段
python -m manju models ide-preview AI_WORK --html CHANGES.html
python -m manju models ide-story-return AI_WORK --expected-preview HASH --output STORY_RETURN.json
```

HASH 用刚才返回的 preview_sha256，文件又改过就重新预览。导出时复核原媒体、基线和历史，输出只创建新文件。新返回只包含故事，不包括 EDIT 镜头/返工/导演文字和媒体；`excluded_edit_fields` 会列出尚未带回的其他字段。它们仍使用原 EDIT.json 三方回程。`ide-return` 整包返回继续存在，但不要把整包恢复说成局部接回。

脚本处理独立故事文件可用：

```text
python -m manju models story-return-preview CURRENT_STORY.json STORY_RETURN.json
python -m manju models story-return-apply CURRENT_STORY.json STORY_RETURN.json --take scene/S02/action --expected-preview HASH --output NEW_STORY.json
```

多个字段重复 --take；`structure` 为整组键。没有同一故事基线的新建、跨作品切换，请继续用明确的整本导入，不伪造基线。JSON必须是完整已验证故事；单行字段不接收换行，正文使用LF，不悄悄规范化丢字。

## 保存与保密

已接回故事使用原 Desk/v2 收工包和可选本机续作，不迁移作品格式，原 r16.10 可完整恢复。未接受的 STORY_RETURN.json 不加入收工缓冲，请保留原文件或点“另存待处理改稿”；它含完整作者基线、候选、私密设定和历史，不只是画面上看到的差异。

改稿JSON不含媒体，不是作品备份。文件摘要是完整性检查，不是数字签名、模型身份或作者批准。导入数据不能执行脚本，不自动上传。用户在外部IDE主动提供的内容是否发送给模型服务，仍取决于自己的客户端和账号。

## 先用示例体验

在首页恢复 `EXAMPLES/SELECTIVE/LOCAL_LATER_CHECKOUT.zip`，再打开同目录 `STORY_RETURN.json`。这时本地第一场和第二场对白都有后来输入；默认只接回外部第二场动作，冲突对白保留。试着撤回、再次接回，然后保存。

`CHECKOUT_FOR_AI.zip` 是原来交出去的基线，`SELECTED_CHECKOUT.zip` 是实际演练结果，`READ_CHANGES.html` 是导出基线与候选的只读对照。内部ZIP不用解压。这些是虚构故事与真实合成图视频，不是商业模型作品。

## 下载与最终核验

完整包已含上手页面，不必下载两份。配套下载助手可以按内容检查实际完整/上手ZIP；文件改名不妨碍识别。大包受限时使用同版五分段，不解压、不排序，在助手中合成后再选回实际保存文件核对。

本次冻结版是当前范围内经过验证的交付，不承诺未来模型/操作系统变化永不需要适配。未运行Windows实机安装/退出/升级回滚或全部商业IDE/生成服务，也没有全项目回归或整片重渲染；具体测试与跳过见 VALIDATION.md。原12模型能力档、质量名单和图像路线本轮不改，不将旧排名当最新信息。

本机续作仍须主动开启，独立备份仍重要。用新目录，不覆盖旧版本、实际影片工程或唯一原素材；附件不是永久存储，请先保存到自己的硬盘并用助手核验。
