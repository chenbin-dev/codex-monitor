# 项目说明

## 结构

- `codex_monitor/`：Windows 托盘应用、日志监听、状态机和 UI 自动恢复。
- `tests/`：不依赖真实 Codex 或中转服务的标准库单元测试。
- `config.example.json`：不含密钥的配置模板，运行时生成的 `config.json` 会被忽略。
- `history.db`：运行时历史与重试状态，已被忽略。
- `start_codex_monitor.bat`：Windows 一键启动脚本，优先使用项目内虚拟环境。

## 当前状态

- 已实现基于日志驱动的异常识别、每线程单恢复任务状态机、DPI 感知窗口校准、ProseMirror 输入框定位、Win64 `SendInput` 输入和托盘历史。
- 自动恢复要求完成校准，并且目标 Codex 输入框可被 Windows UI Automation 读取为空。
- 不调用中转 API，不读取或保存 API Key。

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
