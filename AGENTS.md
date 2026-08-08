# 项目说明

## 结构

- `codex_monitor/`：Windows 托盘应用、日志监听、状态机、多目标恢复适配器和 UI 自动化。
- `codex_monitor/recovery.py`：VS Code、桌面端和 CLI 的恢复目标路由；协议不可用时 CLI 才退回唯一会话输入。
- `codex_monitor/cli_protocol.py`：本机 app-server 会话桥接，按 `threadId` 接收 CLI 异常并精确恢复。
- `codex_monitor/cli_wrapper.py`：一次性安装用户级 `codex` 转发器，保留当前工作目录。
- `tests/`：不依赖真实 Codex 服务的标准库单元测试。
- `config.example.json`：无密钥的多目标配置模板；运行时生成的 `config.json` 被 Git 忽略。
- `history.db`：游标、恢复状态与目标摘要，被 Git 忽略。
- `start_codex_monitor.bat`：启动托盘监测器。
- `install_codex_cli_monitor.bat`：安装全局 CLI 监测；新终端可在任意目录照常使用 `codex`。
- `start_managed_codex_cli.bat`：打开用于快速验证的 CLI 终端；它以独立参数启动转发器，避免 Windows `cmd` 错误解析带引号路径。

## 当前状态

- 监测器只读 `~/.codex/logs_2.sqlite`，覆盖 Codex HTTP、SSE 与 app-server 产生的可恢复错误。
- 恢复目标为 `vscode`、`desktop`、`cli`。只有异常可唯一关联到可用目标时，才会自动发送 `continue`。
- VS Code 和桌面端需要分别校准；桌面端会优先通过本机 app-server 恢复指定线程，协议失败才使用 UI Automation 兜底。
- 全局 CLI 监测通过 app-server 的 `threadId` 将异常和终端会话对应；多个已安装监测的 CLI 可以并行工作且只恢复报错会话。
- 未通过转发器启动的 CLI 自动发现会排除 VS Code 的 app-server 子进程；多会话或终端窗口不唯一时只记录历史。
- 托盘的“测试发送 continue”使用与真实异常相同的目标适配器，可在真实 503 前验证输入路径。
- 不调用中转 API，不读取或保存 API Key、认证文件、提示词和原始日志正文。

## 启动

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
.\start_codex_monitor.bat
```

## 验证

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```
