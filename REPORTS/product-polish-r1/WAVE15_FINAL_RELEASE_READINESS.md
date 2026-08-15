# Product Polish R1 Wave 15 — 最终本地发布准备收口

日期：2026-08-14  
性质：**本地、零成本、发布前收口；不是跨平台正式发布认证**

## 结论

Wave 15 不再增加新的制作旅程、AI 能力或项目状态。它处理的是成熟个人软件最后仍会暴露的几个角落：

1. 用户在任何页面都能找到一个统一、键盘可达的 **帮助与支持（F1）**；
2. 帮助只显示有界、无密钥、无绝对路径的本地产品快照，不在后台运行诊断或收集支持包；
3. 本地数据、遥测、浏览器权限、严格零成本和未来受控云端边界有一份明确文档；
4. HTML 页面统一应用保守的浏览器权限与跨域资源策略；
5. `Ctrl/Cmd+K` 可以打开帮助，但仍然只导航，不能执行生成、选片、提案、锁片或付费动作；
6. 增加一个可重复、分阶段、带超时与独立日志的零成本本地 RC 编排器；
7. 清理多份历史交接文档的权威歧义，建立 `FINAL_HANDOFF.md`；
8. 将新增的 `manju.help-center/v1` 纳入 `CONTRACTS.yaml` 和必跑契约门；
9. 从实际 wheel 安装目标验证图标、包数据和 Windows App self-test，而不是只验证源码树。

本轮没有改变文本真相、append-only 媒体、Provider 账本、选片、镜头审批、Picture Lock 或构建身份。

## 1. 全局帮助与支持

### 入口

所有主要产品页面与未绑定工作区均有唯一入口：

```text
F1
应用栏 → 帮助
Ctrl/Cmd+K → 帮助与支持
```

对话框提供：

- Manju One 版本；
- 当前执行模式及人话解释；
- 项目是否已绑定、是否只读；
- 本地工作台、无产品遥测、项目真相与显式诊断保证；
- `Ctrl/Cmd+K`、`F1` 和页面内 `?` 快捷键；
- 任务中心、项目工作区、Doctor 入口；
- `manju doctor` / `manju doctor --windows`；
- `manju support-bundle`；
- Windows App 日志位置；
- 复制产品信息与复制支持包命令。

### 明确不做

帮助面板不会：

- 读取 API Key、token 或 Provider manifest 正文；
- 列出环境变量；
- 返回项目绝对路径、用户名、主机名或可执行文件路径；
- 读取媒体内容；
- 自动运行 Doctor；
- 自动生成或发送支持包；
- 写项目、运行 build、提交 Provider 或产生费用。

它是 `manju.help-center/v1` 的只读展示投影，不是项目、build、cache、Provider 或 Picture Lock 输入。

## 2. 本地数据与浏览器边界

新增 `docs/LOCAL_DATA_AND_PRIVACY.md`，明确：

- GUI 默认只通过 loopback HTTP 提供；
- 无产品使用遥测、远程分析脚本、CDN UI、远程字体或远程图标；
- 项目文件、用户级状态、日志和凭据引用各自属于什么；
- `strict_zero_cost` 会在 transport 前拒绝外部 Provider，并禁止读取云端凭据；
- 未来受控云端仍需要请求参数、预算、最大可能费用、人工确认和 submission identity；
- 支持包只在用户显式运行时生成，生成不等于发送；
- 卸载默认不删除项目和用户配置。

HTML 产品表面新增保守策略：

```text
Permissions-Policy
Cross-Origin-Resource-Policy: same-origin
X-Permitted-Cross-Domain-Policies: none
```

摄像头、麦克风、位置、支付、USB、串口、蓝牙等能力不会被普通工作台页面默默申请。

## 3. 本地发布候选编排器

新增：

```bash
python scripts/dev/product_release_candidate.py \
  --output REPORTS/product-polish-r1/local-rc
```

它会：

- 移除名称像密钥、密码、token、credential 或 private key 的环境变量；
- 强制 `MANJU_EXECUTION_MODE=strict_zero_cost`；
- 设置 `PIP_NO_INDEX=1`，禁止发布门自行访问包索引；
- 为每一阶段设置独立超时、stdout 和 stderr；
- 将浏览器、server、roundtrip 和 Windows 脚本测试隔离成有界进程；
- 运行性能、18 组视觉门、快速前往、帮助中心、Windows App self-test；
- 离线构建 wheel，并安装到隔离 target 后再次验证品牌图标和 Windows App self-test；
- 输出 JSON / Markdown 总结。

它不会更新 `REPORTS/LAST_GREEN.yaml`，也不会把本地 Linux 结果冒充成 Windows / Ubuntu 同 SHA 发布证据。

## 4. 本轮测试与验收

### 本地 RC

RC 运行于干净 Git worktree：

```text
HEAD：3ad9a3f2c64ae8a7654f4323ae090e1427bc12d6
Tree：6315ee74270898bba6bfefa5b5d49a430601c514
工作树：clean
结果：PASS
必须阶段失败：0
```

独立 pytest 阶段合计：

```text
236 passed
```

其中包括：

| 阶段 | 通过 |
|---|---:|
| `product-polish-core-tests` | 146 |
| `quick-open-tests` | 9 |
| `help-center-tests` | 4 |
| `contract-registry-tests` | 17 |
| `zero-cost-tests` | 14 |
| `roundtrip-baseline-absent-tests` | 4 |
| `roundtrip-needs-baseline-tests` | 8 |
| `roundtrip-fcpxml-tests` | 12 |
| `gui-project-action-tests` | 14 |
| `windows-install-tests` | 8 |


其它通过项：

- `python -m compileall`；
- `tests/test_fp_contracts.py` 契约双向一致；
- 12 / 100 / 300 镜性能预算；
- 6 个核心页面 × 桌面 / 390px / 400% 等效，共 **18 组**视觉验收；
- 快速前往真实 renderer + shipped JavaScript 验收；
- 帮助中心 3 个场景真实 renderer + shipped JavaScript 验收；
- Windows App source self-test；
- 离线 wheel 构建；
- 隔离 wheel 安装与安装后 self-test。

### 性能观察

当前 Linux 沙箱、预热后五次中位数：

| 镜头数 | Cockpit | 快速前往索引 | Review HTML | Storyboard HTML |
|---:|---:|---:|---:|---:|
| 12 | 40.571 ms | 0.577 ms | 26.880 ms | 22.858 ms |
| 100 | 285.609 ms | 3.875 ms | 212.141 ms | 151.321 ms |
| 300 | 836.816 ms | 11.713 ms | 611.421 ms | 456.469 ms |

Cockpit 本地预算：

```text
12 镜 ≤ 150ms
100 镜 ≤ 600ms
300 镜 ≤ 1400ms
```

全部通过。这是本地观察，不是所有 Windows 机器的性能保证。

### 视觉与交互

18 / 18 页面-视口组合通过：

- 工作区、创作、分镜、审片、剪辑、导出；
- 1440×900、390×844、320×800（400% 等效）；
- 无页面级横向溢出；
- 无重复 ID；
- 无可见 `undefined`；
- 每页一个 skip link、一个可聚焦 `main`；
- 每页唯一 `Ctrl/Cmd+K` 与 `F1` 入口；
- 无浏览器 page error。

帮助中心额外验证：

- F1 打开；
- 默认焦点进入关闭按钮；
- Escape 与关闭按钮恢复原触发点；
- 项目已绑定、390px 和未绑定工作区三种场景；
- 每次显式打开只读取一次 `/api/app/about`；
- 无横向溢出；
- 索引失败可重试；
- 没有危险 mutation API。

## 5. 证据路径

```text
REPORTS/product-polish-r1/wave15-tests/release-candidate.json
REPORTS/product-polish-r1/wave15-tests/release-candidate.md
REPORTS/product-polish-r1/wave15-tests/performance.json
REPORTS/product-polish-r1/wave15-tests/visual-acceptance.json
REPORTS/product-polish-r1/wave15-tests/command-palette-acceptance.json
REPORTS/product-polish-r1/wave15-tests/help-center-acceptance.json
REPORTS/product-polish-r1/wave15-tests/logs/
REPORTS/product-polish-r1/screenshots/wave15/
```

## 6. 零成本与安全边界

本轮统计：

```text
真实 Provider 调用：0
外部模型/API：0
凭据读取：0
付费调用：0
免费额度调用：0
```

没有新增：

- 项目 schema 真相字段；
- 数据库表；
- 新制作旅程；
- 内置 LLM；
- Manju MCP；
- 自动选片；
- 自动镜头审批；
- 自动 Picture Lock；
- React / Electron；
- 云端 UI 资源。

## 7. 没有冒充完成的发布门

本地 RC 明确不替代：

- 全仓 dependency-complete pytest；
- live localhost 浏览器 E2E；
- 真实 Windows 11 App 模式 Dogfood；
- Windows hard gate；
- 同一最终 SHA 的 Ubuntu / Windows 双绿；
- 真实 Provider、计费、取消和 AI 视频质量。

当前环境没有 `ruff`，因此记录为 `unavailable`，没有写成通过。

## 8. 产品完成后的原则

Product Polish R1 到此停止扩展产品流程。下一步只做真实平台发布收口：

1. Windows 11 开始菜单安装、双击复用、重开、安全退出；
2. 更新、回滚与运行中卸载零删除拒绝；
3. 最终 SHA 的 Windows hard gate；
4. 同一 SHA 的 Ubuntu full suite；
5. 更新 `LAST_GREEN.yaml`；
6. 有预算后另做一镜、一候选、硬上限的真实 Provider Proof Shot。

在这些证据完成前，不再增加新旅程、新模型或大规模结构重写。
