# Codex 异常中断自动续跑工具

这是一个 Windows 托盘工具。它只读 `~/.codex/logs_2.sqlite`，在检测到可恢复的 Codex 异常中断后，会自动激活已校准的 VS Code 窗口，并向空的 Codex 输入框发送 `continue`。

## 行为

- 监听 429、502/503/504、传输超时、流中断、模型容量不足等可恢复异常。
- 每个 Codex 线程独立等待 30 秒，避免和 Codex 自身重试打架。
- 同一线程的多条错误日志只会合并为一个恢复任务，不会重复输入 `continue`。
- 连续 3 次实际发送后仍未恢复时，改为每 5 分钟继续尝试，直到同线程出现完成日志。
- 鉴权、余额、模型不存在、审批等不可恢复错误只写历史，不自动输入 `continue`。
- 目标窗口不可操作、无法读取输入框，或输入框已有草稿时，本次事件会跳过，不清空草稿。
- 不会还原已最大化的 VS Code 窗口；优先通过 Codex 的 ProseMirror 输入框定位，校准坐标仅作为后备。
- 不调用中转 API，不读取或保存 API Key。

## 安装

需要 Windows 和 Python 3.13。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
```

## 启动

安装完成后，直接双击 `start_codex_monitor.bat`，或者在项目根目录执行：

```powershell
.\start_codex_monitor.bat
```

如果你想手动启动，也可以继续用：

```powershell
.\.venv\Scripts\python -m codex_monitor
```

## 首次使用

程序第一次启动后，会在项目根目录生成 `config.json` 和 `history.db`。

1. 在系统托盘里找到工具图标。
2. 点“校准 Codex 输入框”。
3. 按提示关闭窗口后，在目标 Codex 输入框里点一下。
4. 校准后请检查 `config.json` 里的 `target_window.title_contains`，确保它是唯一且稳定的 VS Code 工作区标题片段。

## 托盘菜单

- 状态：显示当前监测状态和待处理会话数。
- 校准 Codex 输入框：记录固定工作区输入框的位置。
- 查看历史：查看异常、跳过、派发和恢复记录。
- 暂停监测：暂停或恢复所有日志处理和自动操作。
- 退出：安全停止后台监测线程。

## 验证

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```

测试覆盖异常来源过滤、可恢复/不可恢复分类、重试状态机和日志游标续读。
