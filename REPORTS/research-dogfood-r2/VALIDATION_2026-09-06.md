# Manju One R2 最终验证说明

核验时间：2026-09-06 09:44:32 UTC。

**这是有实测证据的研究/修复候选，不是完成全部 Windows 发布验收的正式版。**
原包未修改，所有工作在独立恢复的分支中完成。没有替用户安装、覆盖运行中的版本或修改用户的真实影片工程。

## 1. 代码身份与原包接管

原包基线：`ee1e524a8640a121101603d3da9a2acb78a49c50`。最终运行时代码：`62ccbb96894ee7a9c22c1329b2d4f0cbd5524515`；其 `src` 子树为 `9bfc068654d5534642e2ca33b29c6548f6832e16`。
交付 HEAD 还包含文档与验收工具收口，准确 SHA/tree 以顶层 `PACKAGE_MANIFEST.json` 为准；封装会再次确认运行时代码与测试源码未在全范围测试后改动。

原 ZIP 的 1470 条 SHA-256 校验通过，1399 个 tracked 文件与恢复树在 CRLF/LF 归一后语义一致。旧状态与交接正文完整保存在 `BASE_STATE_2026-08-15.md`、`BASE_HANDOFF_2026-08-15.md`，同时仍在原 ZIP 和 Git 历史中。
`REPORTS/LAST_GREEN.yaml` 原样保留在历史双平台实测提交 `f076aee`，本轮没有给它盖新章。

## 2. 全范围对照与相关回归

| 范围 | 总数 | 通过 | 失败 | 错误 | 跳过 |
|---|---:|---:|---:|---:|---:|
| 原包独立基线 | 6373 | 6335 | 16 | 0 | 22 |
| 首轮改版，后续已修订 | 6396 | 6355 | 19 | 0 | 22 |
| 最终运行时代码 | 6403 | 6366 | 15 | 0 | 22 |
| 最终相关回归范围 | 174 | 174 | 0 | 0 | 0 |

这些测试范围重叠，不能相加当作独立覆盖率。最终保留了全部 6373 个原范围用例，新增 30 个用例；新增用例位于三个 `test_dogfood_r2_*.py` 文件。
最终失败均在原包基线中出现；这不等于完整发布已通过。

全范围采用六个互不重复的模块分片进程，不是 xdist。缺少 Hypothesis 的三个模块未收集、未计入上述总数：
`test_crash_safety_campaign.py`、`test_fp_timebase.py`、`test_properties.py`。
末轮分片进程的总预算为 1800 秒，上一轮为 1500 秒；没有放宽任何产品测试的 30 秒/60 秒断言或超时。
最终各分片完整退出，没有被强制中断。原始命令、分片结果与 JUnit 都在交付根目录 `EVIDENCE/`。

测试夹具会按各自目的清除或设置执行模式，因此不能声称每个旧单元测试都在同一种 strict 模式下运行。
真实 HTTP/浏览器组合验收和额外 CLI 制作演练则明确使用 `strict_zero_cost`；没有使用真实生成服务、用户凭据、免费额度、付费请求或模型下载。公开资料研究使用了网页检索。

### 首轮失败如何处理

首轮改版的额外问题包括决定索引漏项、纯素材说明模块未纳入导出器分类，以及一次真实 SIGINT 测试的时序失败。
前两项分别修正对应索引与分类，没有删测试。SIGINT 的同环境独立复跑，原版与新版各 1 项都通过（3.08/3.07 秒）；先前失败仍保留，不据此宣称这个时序风险已经消失。
原包中网络下载上限测试误拦截 `urlopen`，实际实现使用自建的安全 opener；测试现改为拦截真实调用入口，并确认凭据安全重定向处理器仍在，上限断言和产品实现都未弱化。

原包失败、最终不再失败的用例：
```text
tests.test_generic_cloud::test_default_transport_caps_response_size
```

### 最终未通过项目

```text
tests.test_job_cancel::test_gui_cancel_running_build_stops_before_next_shot
tests.test_content_qc::test_must_show_violation_caught_by_machine
tests.test_content_qc::test_must_show_satisfied_passes
tests.test_card_visual_fixes::test_short_dialogue_stays_on_one_line[1080-1920]
tests.test_card_visual_fixes::test_short_dialogue_stays_on_one_line[1920-1080]
tests.test_card_visual_fixes::test_long_dialogue_still_wraps
tests.test_card_visual_fixes::test_card_paints_the_full_frame[1080-1920-caption]
tests.test_card_visual_fixes::test_card_paints_the_full_frame[1080-1920-chapter]
tests.test_card_visual_fixes::test_card_paints_the_full_frame[1920-1080-caption]
tests.test_card_visual_fixes::test_card_paints_the_full_frame[1920-1080-chapter]
tests.test_card_visual_fixes::test_gradient_actually_reaches_the_pixels
tests.test_card_visual_fixes::test_edge_pushed_text_keeps_a_margin
tests.test_round5::test_html_card_video
tests.test_round5::test_caption_card_prefers_html
tests.test_round_a::test_kenburns_generic_ref_advisory
```

已确认的类别：Chromium 文件截图链路的超时/降级；两个内容 QC 旧用例在没有显式选片时要求构建成功，与当前人工选片门冲突；本地生成取消响应未满足 30 秒要求。
不能把这几类统一包装成“纯环境问题”。尤其本地卡片生成仍可进入 60 秒的 HTML 截图子进程，取消响应存在真实未闭环风险。
也不能把 `ProviderCanceled` 直接拿来伪造本地取消：该异常关联远端任务/潜在计费语义，错误复用会制造假的远端未决状态。

## 3. 浏览器和服务端实际使用检查

使用真实 Chromium 执行 DOM 与 JavaScript，真实本地 HTTP 服务处理写入和读取。24 组页面/视口为 12 个页面 × 1440/390 宽度，检查文档横向溢出与脚本错误，并保存截图。
走通第一条音效保存、删除全部后重建；第一张信息卡保存、删除后重建；双窗口旧版本保存收到 409、磁盘新稿与两份浏览器草稿都保留；F1 读取实际帮助数据、Ctrl+K 搜索。

直接浏览器访问 localhost 被沙箱的 `ERR_BLOCKED_BY_ADMINISTRATOR` 阻止。因此验收将真实服务返回的页面放入浏览器，再把 fetch 转交真实 HTTP 服务；没有伪造响应，但这**不等于**完整浏览器网络、Origin/CSP、安装包或 Windows E2E。
长轮询使用接口允许的 1 秒 timeout，仅为测试可控结束。没有改产品安全策略。
早期桌面首页截图捕捉到了加载占位，后续工具改为等待真实首页主操作卡片；最终 `composed-delivery/` 两个首页均明确记录 `home_ready=true`。较早证据保留为过程记录，不覆盖最终验收。

## 4. 媒体、导出、历史包和安装产物

**素材交接回收：**真实 PNG 首尾帧 → 创建/校验制作包 → 本地合成 MP4 返回 → `ingest --handoff --no-auto-select`。
新增 1 个候选，原来的 4 个 take/sidecar 文件字节不变；新候选没有自动选用，边车保留制作包身份。中文图片扩展名在实际包中为 `.png`。

**六秒制作演练：**三个各两秒的合成镜头，在一次性夹具内显式选择；无生成的 `build --gen off` → 中文字幕与提示音 → 渲染 → 同内容缓存复用 → 调整混音后重渲染。
得到 640×360 的六秒真实 A/V，完整解码通过；6 个既有 take/sidecar 文件不变，旧成片哈希不变，新混音产生新成片路径。
SRT/ASS/VTT/EDL/FCPXML 实际写出并保留；FCPXML 被解析，导出损失报告 schema 被检查。**没有**声称这些文件已在真实剪映、Premiere 或 Final Cut 中导入验收，也没有声称完成画质/叙事/人工锁片批准。

**真正的历史代码兼容：**从 Git 恢复 `2484c65` 的原 R1 渲染器，再用原包 `ee1e524` 的 R2 渲染器，分别制作 `portable_video` 与 `minimax_h3`，共 4 个历史包；最终代码全部验证通过，所有包字节未改。它补充了单元测试的历史分支模拟。

**wheel：**最终 wheel 在不访问 package index 的条件下构建；通过独立目标目录安装、`python -I` 导入和 CLI 校验，240 个包文件与最终源码逐字节一致。
wheel SHA-256 为 `99308469698b9b3413b1767f08eb06f568e1ce27ec72451ebbd8802ec09b838a`。
它只含 Manju 包，不包含 Python、FFmpeg、浏览器或第三方依赖；不能称作全新电脑的一键离线安装器。

## 5. 性能与静态检查

沿用原有中位数预算，没有放宽门槛：

| 一次性项目规模 | Cockpit 中位数 | 原预算 |
|---|---:|---:|
| 12 镜 | 18.298 ms | 150 ms |
| 100 镜 | 125.546 ms | 600 ms |
| 300 镜 | 379.599 ms | 1400 ms |

具体样本与运行负载在 `EVIDENCE/performance/`；这不是用户电脑性能保证。
Python 编译和实际前端脚本的 Node 语法检查通过。Ruff 未安装，不能把编译成功冒充 lint 通过。
环境为 Debian 13 / Python 3.13.5；FFmpeg 为 7.1.5，**不是**项目固定的 6.1.1。

## 6. 仍不能盖章的边界

真实 Windows 11 的 pythonw、COM 快捷方式、启动复用、关闭再开、更新/回滚、运行中卸载拒绝没有在本环境执行。
同一个最终提交、齐备依赖、固定 FFmpeg 的 Ubuntu full suite 与 Windows hard gate 仍需实际证据。
真实提供商、真实素材质量与费用、远端取消是否停止计费没有验证。

本轮没有为测试变绿绕过人工选片，没有自动审批或 Picture Lock，没有在引擎内增加 LLM、MCP、React/Electron 或云端资源。

## 7. 证据入口

交付根目录 `EVIDENCE/regression-summary.json` 是紧凑统计，`regression-comparison.json` 保留逐用例状态；三个全范围目录保留原日志/JUnit。
`composed-delivery/`、`edit-export-delivery/`、`historical-handoffs/` 是最终实际操作证据。
`final-wheel-*` 为最终安装产物验证，`performance/` 为原预算测量。
红灯、早期失败、被工具中断的 focused 尝试均被标为过程证据，不能当作最终成功结果。
最终包的文件清单、Git 树、完整历史与恢复验证见顶层 manifest、SHA256SUMS 和恢复验证记录。
