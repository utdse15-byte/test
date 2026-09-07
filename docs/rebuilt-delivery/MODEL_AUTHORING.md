# 新模型离线适配

## 范围

本模块为创作规划和外部交接，不是 execution provider，不联网，不调用生成模型，不改写影片选片状态。
`compatible` 只表示在这份有日期的有限能力档中没有已知冲突，不表示所有供应商参数完备、账户可用、计费明确或画质达标。
8 份档分开记录 MiniMax H3 / H3-Max、Gemini Omni 1.1 Flash、Veo 3.1 Preview / Fast Preview、Runway Keyframes / Edit Studio、Luma Ray3 Modify。
这是部分模式的保守集合，不宣称覆盖供应商全部能力。`perform` 等没有经过本轮核验的任务可以返回空列表，不临时编造适配。

## 用法

以下命令在一个新的工作目录中运行。`python -m manju` 与安装后的 `manju` 等价。

```text
manju models example --output request.json
manju models catalog --output catalog.json
manju models plan request.json
manju models approve request.json --profile veo-3.1-generate-preview --mode text --reviewer 自己的名字 --human-confirmed --acknowledge-warnings --output approval.json
manju models bundle request.json approval.json --asset-root . --output new-handoff
manju models verify new-handoff
```

先检查 plan 的每个告警，再明确批准。不提供 `--human-confirmed` 或告警确认时拒绝批准。
所有输出需要新名字；改请求、素材、档位或能力目录会使之前的批准失效。
可用 `--catalog custom.json` 读取自定义离线档，严格校验 schema；不会下载来源网页或执行配置。
`manju models diff old.json new.json` 显示新增、删除和变化的档位。

## 素材与参数

`assets` 记录 id、role、相对路径、SHA-256、字节数；需要视频时长约束的模式还要求 `duration_ms`。
参考视频、待编辑视频、首帧、尾帧和角色图片分别是不同的 role，不能互相假装。
路径采用正斜线，不接受链接、路径穿越、Windows 保留名或大小写冲突。
同一物理文件可以承担多种逻辑角色，但哈希和字节数必须一致。
封包时读取真实本地文件并复核字节；规划阶段只验证声明，不能凭声明替代编码、图片尺寸和供应商安全检查。
`preserve` / `change` 精确文本冲突会被拒绝；语义矛盾仍需要人工判断，程序不会假装理解全部自然语言。

## 不改变历史交接格式

R2 的 provider-handoff 对完整文件清单严格校验，在旧包中增加目录本身就会破坏原身份。
因此本轮创建独立 companion bundle，不向旧包里插入 `MODEL_HANDOFF/`，也不静默改写旧 manifest。
旧包继续由原来的 `manju handoff verify` 验证；新包使用 `manju models verify`。

## 批准与完整性边界

批准是本地人工声明，不是身份认证、密码学签名、付款授权，也不是 Picture Lock。
哈希能发现内容发生变化，但不能证明模型来源或谁完成了审批。
`stage=final` 要求已批准草稿哈希；R4 中这是声明字段。后续审片/晋升流程负责检查对应的审片记录和实际文件。
封包使用独占的新目录名，完整性校验后才返回成功。断电可能留下不完整目录，验证器会拒绝该目录，不能将其当成成功包。
历史批准按当时快照复核，提交给外部平台前还要查看能力档的复核期限。未知参数保持“未核验”，不推断为支持。
