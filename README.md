# Codex 异常中断自动恢复工具

这是一个运行在 Windows 上的托盘监测工具。它只读取 Codex 的本地日志，不读取账号、密钥、提示词正文或回复内容。

当它识别到可恢复异常时，会根据当前目标自动发送 `continue`，支持：

- VS Code 里的 Codex 插件
- Codex 桌面端
- Codex CLI

## 先说清楚三个容易混淆的点

1. VS Code 和桌面端：当前版本要想自动输入 `continue`，需要先校准输入框位置。  
   如果不校准，监测器仍然可以运行、记录历史，但不会安全地自动点进输入框并发送内容。

2. Codex CLI：不需要你专门再开一个“监测终端”。  
   你平时在哪个终端里正常输入 `codex`，那个终端就是被监测的对象。  
   `start_managed_codex_cli.bat` 只是一个“快速测试用”的启动脚本，方便你检查 CLI 监测链路是否正常。

3. `install_codex_cli_monitor.bat`：这是一次性安装脚本。  
   它会在你的用户目录下创建一个本地转发器，让你以后在任意项目目录里直接输入 `codex` 也能被监测到，不需要把工作目录固定在这个仓库里。

## 目录里的 3 个启动脚本

- `start_codex_monitor.bat`

  启动监测器本体，也就是托盘程序。日常先运行它。启动窗口会显示“已启动”，并提示你到 Windows 右下角的系统托盘操作；窗口不能关闭，可最小化。

- `install_codex_cli_monitor.bat`

  安装全局 Codex CLI 监测转发器。只需要执行一次，换机器后再重新安装。双击后窗口会保留，明确显示安装结果和下一步操作；看到“全局 CLI 监测转发器已安装”即表示成功。

- `start_managed_codex_cli.bat`

  打开一个用于验证的 Codex CLI 窗口。这个脚本主要用于测试，不是日常必须步骤。若曾运行旧版本脚本并看到“`codex.cmd` 不是内部或外部命令”，重新运行一次 `install_codex_cli_monitor.bat` 后再使用它。

## 安装依赖

需要 Windows + Python 3.13。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
```

## 日常使用

1. 启动监测器：

```powershell
.\start_codex_monitor.bat
```

2. 如果要监测 Codex CLI，先安装一次全局转发器：

```powershell
.\install_codex_cli_monitor.bat
```

3. 关闭旧终端，重新打开一个新终端，在任意项目目录里正常输入 `codex` 即可。

4. 如果要监测 VS Code 或桌面端，在托盘菜单里分别校准对应输入框。

## 托盘菜单里能做什么

- 校准 VS Code Codex 输入框
- 校准 Codex 桌面端输入框
- 安装全局 Codex CLI 监测
- 启动 CLI 快速测试窗口
- 测试发送 `continue` 到 VS Code
- 测试发送 `continue` 到桌面端
- 测试发送 `continue` 到 CLI
- 查看历史
- 暂停 / 恢复监测

## 监测器是怎么判断异常的

它会识别本地日志里比较明确的可恢复错误，例如：

- `429`
- `502`
- `503`
- `504`
- 超时
- 连接中断

只有能明确归属到唯一目标时，才会自动发送 `continue`。  
如果同时存在多个可能目标，工具会更保守，只记录历史，不会盲发。

## 如何验证这个工具是否可行

推荐按这个顺序测：

1. 启动监测器。
2. 如果要测 CLI，先运行一次 `install_codex_cli_monitor.bat`。
3. 打开一个新终端，在任意目录里输入 `codex`。
4. 触发一次真实的可恢复异常，比如 503。
5. 检查历史里是否出现：
   - `recoverable_error`
   - `continue_dispatched`

如果你是测 VS Code 或桌面端，先校准输入框，再做同样的异常测试。

## 配置文件

- `config.example.json` 是模板。
- 首次运行会生成本机的 `config.json` 和 `history.db`。
- `history.db` 和 `config.json` 不建议提交到 Git。

## 依赖

```text
Pillow
pystray
pywin32
pywinauto
websocket-client
```

## 常见问题

### 1. VS Code 不校准能不能自动恢复？

当前这版不行。  
不校准时，工具最多只能知道“有异常”，但不能安全确认该往哪个输入框里点、也不能保证不会误发到别的窗口，所以不会自动输入。

### 2. CLI 是不是必须另开一个专门的终端？

不是。  
你正常工作的那个终端就是监测对象。  
`start_managed_codex_cli.bat` 只是一个方便你测试的快捷入口。

### 3. 为什么会有一个 `C:\Users\ASUS\AppData\Local\CodexMonitor\bin\codex.cmd`？

那是安装全局 CLI 监测时生成的本地转发器。  
它的作用是把系统里原本的 `codex` 命令接管一下，然后在监测器在线时自动加上远程连接参数。  
它不会改变你的项目目录，只是让你在任何目录里输入 `codex` 都能被这套工具接住。

## 测试

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```
