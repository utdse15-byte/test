# Windows App 模式

Manju 的 Windows 桌面入口仍然使用同一个本地 GUI、同一个确定性核心和同一份项目真相。
这里没有 Electron、WebView2 SDK、云壳、第二套任务队列或第二份项目状态。开始菜单入口只是
一个无控制台、可恢复的 `pythonw` 启动器。

## 安装

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\install-manju.ps1 -AddToPath
```

安装默认创建当前用户的：

```text
开始菜单 → Manju 工作台
```

它指向当前已激活版本的：

```text
pythonw.exe -m manju.gui.windows_app
```

因此日常启动不会长期显示控制台窗口。只需要 CLI 时：

```powershell
.\scripts\windows\install-manju.ps1 -NoShortcut
```

旧自动化仍可使用 `-CreateShortcut`；它与 `-NoShortcut` 不能同时使用。

同名快捷方式如果不属于 `%LOCALAPPDATA%\Manju` 的版本化安装，安装、更新和卸载都会保留它，
不会覆盖或删除。

## 安装前自检

在切换 `current.txt` 之前，安装器会完成：

```text
manju --version
python -m manju.gui.windows_app --self-test
manju doctor
```

桌面入口自检只验证：

- 安装后的 CLI 包含 `gui`；
- 包内 `.ico` 存在；
- 桌面入口固定使用 `gui --app --port 0`；
- `pythonw.exe` 存在。

它不会绑定端口、打开浏览器、读取 Provider 凭据、访问网络或产生费用。任何一项失败都发生在
原子指针切换之前，旧版本继续有效。

## 启动、重开与安全退出

启动器会：

1. 检查 `%LOCALAPPDATA%\Manju\App\workspace-session.json`；
2. 只接受精确 loopback `http://127.0.0.1|localhost|::1:<port>`；
3. 读取 `/api/app/status`，并核对 Manju 协议标记和服务进程 PID；
4. `open` 时重新打开同一个本地会话（浏览器可能创建新的视图，但不会复制服务）；
5. `closing` 时提示正在安全退出，并拒绝启动第二个服务；
6. `stuck` 时重开原会话供用户查看任务和日志；
7. 状态接口暂时无响应但记录中的进程仍活着时，fail closed，不复制服务；
8. 只有记录失效且进程已不在时，才删除会话文件并启动新的
   `manju gui --app --port 0`；
9. 使用每用户启动锁收住连续双击发生在会话登记之前的竞态。

会话文件和锁都位于 `%LOCALAPPDATA%\Manju\App`，是可删除运行态，不是项目真相、build
输入、Provider 身份、选片状态或费用证据。

直接点击 Edge/Chrome 窗口的 **X** 可能只关闭窗口，本地服务仍然运行。再次从开始菜单启动会
重开该会话。真正结束 Manju 请使用应用栏的 **退出**，让现有任务协调器先处理正在写项目的
工作。

如果现有服务仍在运行但系统无法打开浏览器窗口，启动器会给出精确 loopback 地址，并明确拒绝
启动第二个服务。用户可以把该地址粘贴到浏览器，而不会复制任务或重复写入。

## 启动失败与日志

窗口化入口没有控制台，因此每次启动都会写 UTF-8 日志：

```text
%LOCALAPPDATA%\Manju\Logs\app-launch-YYYYMMDD-HHMMSS-PID.log
```

默认只保留最近 12 份。`LOCALAPPDATA` 不可写时会尝试系统临时目录；两个位置都不可写时，
启动器会显示原生错误并停止，不会静默失败。

启动异常会显示 Windows 原生错误框，明确说明：

- 项目没有被修改；
- 日志路径；
- 建议运行 `manju doctor --windows`。

启动器本身不会读取 Provider 凭据，也不会调用 Provider。

在应用窗口内按 `F1` 可以查看当前版本、本地安全模式、常用快捷键和日志目录，并复制
`manju doctor --windows` / `manju support-bundle` 命令。帮助面板不会自动运行诊断、读取
密钥或发送日志；是否生成、检查和分享支持包始终由用户决定。完整边界见
`LOCAL_DATA_AND_PRIVACY.md`。

## 快捷方式的更新与所有权

快捷方式先在同目录的临时 `.lnk` 中完整生成并复核，再替换正式入口。COM、磁盘或图标错误发生
时，上一份已知可用的入口会保留。

更新与回滚：

```powershell
.\scripts\windows\update-manju.ps1
.\scripts\windows\update-manju.ps1 -Rollback
```

更新默认保留用户当前偏好：已有 Manju 入口就刷新到新版本；没有就继续没有。

```powershell
.\scripts\windows\update-manju.ps1 -CreateShortcut
.\scripts\windows\update-manju.ps1 -NoShortcut
```

版本指针切换已经成功后，如果 shell 快捷方式刷新失败，更新或回滚仍然保持成功，并给出明确的
修复命令。它不会把一个健康的版本切换误报成应用安装失败。

## 卸载

```powershell
.\scripts\windows\uninstall-manju.ps1
```

若版本化 Manju 进程仍在运行，卸载会在删除任何文件前拒绝，并要求先使用应用内 **退出**。
成功卸载：

- 删除版本化 App、bin、Cache；
- 删除确认属于本安装的开始菜单入口；
- 默认保留 Logs，只有 `-Logs` 才删除；
- 绝不删除 `*.manju` 项目；
- 绝不删除 `~/.manju` 的 Provider、路由、最近项目、素材库或 GUI 状态；
- 绝不删除同名但不属于 Manju 安装的快捷方式。

快捷方式清理或所有权检查失败时，卸载会在删除应用文件前中止；项目和配置始终不在卸载范围内。

## 体检

```powershell
manju doctor --windows
```

体检只做本地文件系统快照，不打开浏览器、不联网、不读密钥。跨平台 Python 代码只能诚实报告
“检测到 `.lnk` 文件”；快捷方式目标、参数、图标和所有权由 Windows PowerShell 安装门验证，
不会在非 Windows 环境中猜测为通过。

## 验证边界

Linux/macOS 可以验证：

- 会话文件和精确 token 清理；
- loopback、协议标记和 PID 核对；
- `open / closing / stuck / unreachable` 的防重复语义；
- 双击启动锁；
- UTF-8 日志、回退和保留上限；
- wheel 中的 SVG/ICO；
- installer/update/uninstall 的静态安全边界。

以下事实必须由 Windows hard gate 或真实 Windows 11 Dogfood 证明：

- `pythonw.exe` 真正无控制台启动；
- WScript `.lnk` 的创建、图标、目标和所有权读取；
- 更新/回滚后的快捷方式目标；
- 运行中卸载拒绝；
- Edge/Chrome App 模式真实键鼠旅程；
- 安装、更新、回滚、卸载在同一发布 SHA 上全部通过。
