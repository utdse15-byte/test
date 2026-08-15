# Product Polish R1 Wave 14 — Windows App 生命周期

**日期：** 2026-08-14  
**功能提交：** `ddf72928be2da82cb1c780b65c559345f5c8380d`  
**Git Tree：** `ce85e181354507603f58efe474e2443b483cbe8e`  
**资金边界：** 零真实 Provider、零凭据读取、零付费、零免费额度调用

## 结论

Wave 14 没有增加新的创作流程或 AI 能力，而是把 Manju 已有的本地 GUI 收口成一条成熟、可恢复的 Windows 个人应用生命周期：

```text
安装并自检
  → 开始菜单双击
  → pythonw 无控制台启动同一 GUI
  → 重复双击复用同一本地会话
  → 启动失败有 UTF-8 日志和原生提示
  → 更新 / 回滚同步已拥有的入口
  → 运行中卸载先零删除拒绝
```

开始菜单入口只是现有 `manju gui --app --port 0` 的薄壳。它没有引入 Electron、WebView2 SDK、第二套 server、第二套任务队列、数据库、项目 truth、Provider 路径或费用语义。

## 1. 点击启动成为正常安装默认值

`install-manju.ps1` 现在默认创建当前用户的：

```text
开始菜单 → Manju 工作台
```

入口目标为当前版本化 venv 的：

```text
pythonw.exe -m manju.gui.windows_app
```

因此日常启动不依赖当前工作目录，也不会使用 `manju.cmd` 控制台窗口。需要纯 CLI 安装时仍可显式使用：

```powershell
install-manju.ps1 -NoShortcut
```

旧自动化中的 `-CreateShortcut` 继续兼容。

## 2. 桌面入口在激活版本前完成零网络自检

安装器在原子切换 `current.txt` 之前验证：

- `manju --version`；
- `pythonw.exe` 存在；
- 安装后的 `manju.gui.windows_app` 模块存在；
- wheel 内 `.ico` 与 SVG 存在且格式正确；
- 固定入口仍映射到 `manju gui --app --port 0`；
- `manju doctor` 没有崩溃。

`windows_app --self-test` 不绑定端口、不打开浏览器、不读 Provider 凭据、不访问网络、不执行付费动作。任一检查失败都发生在版本指针切换前，旧版本保持有效。

最终 wheel 重新构建并安装到隔离目录后验证：

```text
wheel SHA-256（本次构建）: da045e69a72a57714799069738e6eabc8a0a16e9c48d8599dc90ffe8052301fb
说明：wheel 是 ZIP 容器；不同构建时间的 archive metadata 可能改变整体哈希，这里不把 wheel 哈希声明为跨构建稳定身份。
.ico size:      18,786 bytes
.ico header:    00000100
installed self-test: PASS
```

## 3. 重复双击复用同一本地会话，而不是复制服务

启动后，GUI 在 socket 绑定成功之后发布可删除的：

```text
%LOCALAPPDATA%\Manju\App\workspace-session.json
```

记录包含：

- 固定 schema；
- exact loopback URL；
- server PID；
- 随机 token；
- 当前 executable identity；
-版本与启动时间。

第二次启动会读取 `/api/app/status`，并同时核对：

```text
product == manju
protocol == manju-gui-app-status.1
pid == session.pid
shutdown_state ∈ open / closing / stuck
```

行为：

- `open`：重新打开同一本地会话；浏览器可能创建新的视图，但不会复制 server；
- `closing`：提示正在安全退出，不启动第二个 server；
- `stuck`：打开原会话供用户检查任务和日志；
- HTTP 暂时无响应但记录进程仍活着：fail closed；
- PID 已退出或明确属于另一个 executable：丢弃陈旧会话并启动新 server。

浏览器 shell handoff 失败时会显示精确 loopback 地址，但已经绑定的 server 继续作为唯一 owner，不会因为“窗口没开”再提交一份任务。

## 4. 连续双击竞态委托给现有跨平台锁 owner

启动器没有实现第五套 `msvcrt` / `fcntl` advisory lock。`_LauncherLock` 委托给现有 `runtime.buildlock.BuildLock`，复用其：

- hard-link / `O_EXCL` 原子获得；
- holder PID、hostname 和唯一 token；
- Windows 安全的进程存活探测；
- heartbeat；
- 陈旧锁接管；
- release owner 校验。

锁位于每用户 App 运行态，不是项目 `.manju` truth、build 输入、Provider identity 或费用证据。Windows 文件扫描器导致 release 暂时失败时，应用退出不会因此崩溃；锁证据留给既有 stale-lock 语义恢复。

现有 Windows 不变量门继续要求 `msvcrt` 实现只存在于原有四个 owner，本轮没有放宽 allowlist。

## 5. windowless 失败不再静默

每次开始菜单启动写 UTF-8 日志：

```text
%LOCALAPPDATA%\Manju\Logs\app-launch-YYYYMMDD-HHMMSS-PID.log
```

特性：

- 默认只留最近 12 份；
- `LOCALAPPDATA` 不可写时尝试系统临时目录；
- 两处都不可写时原生 MessageBox 报错并停止；
- import、Typer、GUI server 或浏览器 handoff 异常均留下 traceback；
- 原生错误明确说明项目未修改、日志位置和 `manju doctor --windows`。

`pythonw` 没有控制台，因此这条可见失败路径是桌面入口的必要产品边界。

## 6. 统一应用图标

新增唯一品牌资源 owner：

```text
src/manju/gui/assets/manju-app.svg
src/manju/gui/assets/manju-app.ico
src/manju/gui/brand.py
```

同一图标用于：

- 开始菜单 `.lnk`；
- 浏览器 tab favicon；
-未来安装元数据。

资源完全随 wheel 本地分发，无 CDN、无字体文件、无远程请求。ICO 包含 16、24、32、48、64、128、256 七种尺寸。

## 7. 快捷方式具备所有权和恢复语义

新增共享 PowerShell owner：

```text
scripts/windows/manju-shortcut.ps1
```

所有权为四态：

```text
absent / owned / foreign / unknown
```

只有同时满足以下条件才视为 Manju 自己的入口：

- target 位于 `%LOCALAPPDATA%\Manju`；
- 参数是新 `-m manju.gui.windows_app`，或可识别的旧 `manju.cmd gui`。

策略：

- `foreign`：不覆盖、不删除；
- `unknown`：不猜测所有权；显式删除请求 fail closed；
- 新入口先在同目录临时 `.lnk` 完整生成并复核 target、arguments、icon，再替换正式入口；
- COM、磁盘或图标错误不会截断上一份已知可用入口。

## 8. 更新和回滚同步入口，但不伪造应用失败

`update-manju.ps1` 会保留用户当前偏好：

- 已有 owned 入口：更新到新版本；
- 用户没有入口：更新后仍没有；
- `-CreateShortcut`：显式创建或修复；
- `-NoShortcut`：只移除 owned 入口。

版本指针已经安全切换之后，如果 Windows shell 集成刷新失败：

- 应用更新 / 回滚仍然保持成功；
- 上一入口仍指向保留在磁盘上的旧版本；
- 脚本给出明确修复命令；
- 不把一个健康的版本切换误报成整个应用安装失败。

## 9. 卸载优先保护正在运行的工作

`uninstall-manju.ps1` 在删除任何文件之前检查：

- 有效 App session；
- 安装根下仍在运行的 `python` / `pythonw` / `manju` 进程。

若进程仍活着：

```text
零删除拒绝
→ 要求使用应用栏“退出”或先结束 CLI
→ 再重新运行卸载
```

对于有效 session，如果进程存在但 executable path 暂时无法读取，也按“仍在运行”处理；只有明确证明进程已退出或属于别的程序才继续。

卸载只删除：

```text
App / bin / Cache / owned shortcut
Logs 仅在 -Logs 时删除
```

永远不删除：

```text
*.manju 项目
~/.manju 配置、Provider、路由、最近项目、素材库、GUI 状态
foreign shortcut
```

快捷方式所有权检查或清理失败时，会在删除 App 目录之前中止，避免留下指向已删除版本的死入口。

## 10. Doctor 只报告它真正知道的事实

`manju doctor --windows` 新增 Windows App 行，但只做 credential-free 本地文件快照。跨平台 Python 无法权威解析 WScript `.lnk`，因此只报告：

```text
shortcut_detected = true / false
shortcut_verified = windows-only
```

真正的 target、arguments、icon 和 ownership 验证仍属于 real Windows PowerShell hard gate。Doctor 不把“同名文件存在”冒充成入口已经正确。

## 11. Windows CI 门加固

`windows-ci.yml` 现在在版本化安装 smoke 中额外验证：

1. installed `windows_app --self-test`；
2. 开始菜单入口存在；
3. target 为 `pythonw.exe`；
4. arguments 精确为 `-m manju.gui.windows_app`；
5. target 位于 per-user AppRoot；
6. icon 文件存在；
7. update 后 shortcut target 跟随新版本；
8. rollback 后 target 回到旧版本；
9. 有 session 的运行进程使卸载零删除拒绝；
10. 无 session 但安装根下仍运行的 Python 同样使卸载拒绝；
11. 最终卸载删除 owned 入口且保留项目。

当前沙箱没有 PowerShell 和真实 Windows，因此这些 workflow 断言已经写入，但没有被本轮冒充为真实 Windows 通过。

## 12. 验证结果

### Pytest（互不重复计数）

```text
314 passed
9 skipped
0 failed
```

其中 9 项是当前非 Windows 主机没有 PowerShell，因此 Windows 脚本行为测试被诚实跳过。

### 静态与包验证

```text
compileall                              PASS
windows-ci.yml YAML parse               PASS
rendered common.js / pages.js node check PASS
git diff --check                        PASS
source windows_app --self-test          PASS
wheel build + isolated target install   PASS
visual acceptance evidence bundled      PASS
installed windows_app --self-test       PASS
```

当前环境没有 `ruff`，没有联网安装，也没有把它描述成已通过。

对应证据：

```text
REPORTS/product-polish-r1/wave14-tests/static-final.log
REPORTS/product-polish-r1/wave14-tests/source-self-test.log
REPORTS/product-polish-r1/wave14-tests/package-install-final.log
```

### 离线真实 renderer 视觉门

复跑已有六个核心表面：

```text
工作区 / 创作 / 分镜 / 审片 / 剪辑 / 导出
× desktop / narrow / 400%-equivalent
= 18 / 18 通过
```

结果：

```text
横向溢出 0
重复 ID 0
visible undefined 0
browser pageerror 0
```

这是 `page.set_content` 离线真实 renderer 门，不是 live localhost HTTP E2E。

视觉证据：

```text
REPORTS/product-polish-r1/wave14-tests/visual-gate/visual-acceptance.json
REPORTS/product-polish-r1/wave14-tests/visual-gate/visual-acceptance.log
```

## 13. 零成本和架构边界

本轮：

```text
真实 Provider 调用：0
外部模型/API：0
凭据读取：0
付费调用：0
免费额度调用：0
```

没有新增：

- 项目 schema；
- 数据库表；
- 第二套 GUI server；
- 第二套任务队列；
- 自动选片；
- 自动审批；
- 自动 Picture Lock；
- 内置 LLM；
- Manju MCP；
- React；
- Electron；
- 云端资源。

## 14. 尚未宣称完成的发布门

本轮没有宣称：

- `pythonw.exe` 在真实 Windows 上确实无控制台闪窗；
- WScript COM `.lnk` 创建和替换已在真实 Windows 跑通；
- Edge/Chrome App 模式真实键鼠 Dogfood；
- 实际 Windows 更新 / 回滚 / 卸载；
- Windows hard gate；
- 同一 SHA Ubuntu / Windows 双绿；
- 全仓完整 pytest；
- 真实 Provider、计费或 AI 视频质量。

`REPORTS/LAST_GREEN.yaml` 因此仍保持此前真实双平台测量的 SHA，不会用本地 Linux 证据伪造发布完成。

## 15. 下一步判断

下一步不应再扩展产品功能。最高价值路线是：

```text
在真实 Windows 11 上安装当前包
→ 双击开始菜单并验证无控制台
→ 连续双击验证同会话
→ 关闭浏览器窗口再重开
→ 应用内安全退出
→ 更新 / 回滚检查 shortcut target
→ 运行中卸载拒绝
→ 完整 Windows hard gate
→ 同一 SHA Ubuntu 门
```

只有这些真实平台事实完成后，才应更新 `LAST_GREEN`，并决定是否开始纯机械的大文件拆分。
