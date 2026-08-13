# Manju 严格零成本本地运行手册

本手册不进行任何云模型或真实 Provider 调用。

## 1. 进入无 Key 的专用 shell

建议新开终端，并清除已知 provider Key。只检查变量名，不打印值。IDE 不得从用户其他配置文件复制 Key。

建议设置一个明确的本轮标志（最终变量名以仓库现有 owner 为准）：

```bash
export MANJU_EXECUTION_MODE=strict_zero_cost
export MANJU_NETWORK_POLICY=loopback_only
```

PowerShell：

```powershell
$env:MANJU_EXECUTION_MODE = "strict_zero_cost"
$env:MANJU_NETWORK_POLICY = "loopback_only"
```

如果仓库已有等价变量，复用已有名称；不要创建第二策略。

## 2. 本地 Proof Shot

### 2.1 生成确定性测试视频

优先复用测试 fixture。没有时可用 FFmpeg 生成短片；具体命令应由任务根据跨平台能力选择。媒体应包含可验证的帧变化、时长和分辨率，不含受版权保护素材。

### 2.2 启动 localhost fake async provider

要求：

- 只绑定 127.0.0.1 或 ::1；
- submit/poll/download 计数可查询；
- 可以注入 timeout、429/503、过期 URL、下载中断和错误媒体；
- 结果来自本地 fixture，不联网。

### 2.3 执行和恢复

```bash
manju providers check <LOCAL_FAKE_PROVIDER> --json
manju prompt <SHOT_ID> --check --json
manju build --dry-run --json
# 使用仓库现有 redo/director run 入口执行 localhost job
manju tasks --json
```

在 fake job 仍 running 时结束本地进程，重启后只继续 poll 原 job。断言 `submit_count == 1`。

### 2.4 媒体验证和 QC

- ffprobe 检查容器、时长、分辨率；
- SHA256 绑定 append-only take；
- QC packet/verdict 使用人工或确定性 fixture；
- 不调用云 VLM；
- 人工决定 KEEP/拒绝/select。

## 3. 本地关键帧 Best-of-2

1. 用 Pillow/FFmpeg 或用户已有图片创建两个不同 PNG/JPEG。
2. 通过现有 image take/import 路径登记 candidate_index 0/1，cost=0。
3. 为每张图生成 QC packet；人工填写或使用确定性 verdict fixture。
4. 人工采纳一张并提升为 ref。
5. 对 localhost fake video provider 做 dry-run，确认 exact image hash 进入 request identity。
6. 改选另一张，确认 request digest 改变。
7. 结论只针对 workflow/lineage，不评价真实生成质量。

## 4. 本地空间锚点

1. 父镜必须有 current-bound media 和 END observation。
2. 用 FFmpeg 从父镜 exact bytes 抽取/裁剪 anchor。
3. 保存 reference_only sidecar：source shot/take/hash/time/frame/profile/output hash。
4. 通过现有 refs truth patch 绑定子镜。
5. 重选父镜，确认旧 anchor stale。
6. 只在 localhost fake request 中验证 binding。

## 5. OpenChatCut 本地 finishing

1. 不配置任何 AI provider、转录、搜索或生成 Key。
2. 只连接 localhost MCP；非 loopback 地址拒绝。
3. 从当前 Picture Lock 导出 verified finishing handoff。
4. `begin_edit_session` 使用 manual approval。
5. 只做本地 trim、transition、caption、audio、effect、export。
6. 用户在 OpenChatCut 内第一次批准。
7. 导出 project/exchange/review render，计算 SHA256。
8. Manju 做 zero-write roundtrip/import-plan。
9. 用户第二次批准后才采用可表达的 canonical changes。
10. OpenChatCut 项目永远不是 Manju build input。

## 6. 零成本证据

每个阶段报告必须包含：

- HEAD/commit；
- network policy 状态；
- endpoint 是否 loopback；
- API key 检查结果（只写变量名是否存在，不写值）；
- submit/poll/download counts；
- media hashes；
- focused tests；
- 明确的“未验证”：真实 Provider、真实费用、真实模型质量。

## 7. 绝对禁止

- 使用免费额度或试用金；
- 填入任何云 Key；
- 真实 provider submit；
- 云 OCR/ASR/VLM/生成/搜索；
- 自动下载大模型权重；
- ambiguous submit 后自动重投；
- reviewer KEEP 自动 select；
- 覆盖媒体；
- 让 OpenChatCut/Workbench/graph 成为 canonical truth。
