# Codex 健康监测工具 - 实施设计

> 状态：多目标恢复版本（2026-08-08）

## 目标

当 Codex 因可恢复服务异常中断时，工具只读本机日志，将异常路由到唯一可确认的 VS Code、Codex 桌面端或 Codex CLI 会话，并自动发送 `continue`。

## 架构

```text
~/.codex/logs_2.sqlite (只读)
    -> 增量日志扫描与异常分类
    -> 每个 thread 的去重/延迟/观察状态机
    -> RecoveryRegistry 唯一目标路由
    -> VS Code UI | Desktop 协议探测 + UI | CLI 终端
    -> SendInput Unicode continue
```

## 目标路由

- VS Code：已校准且唯一匹配的编辑器窗口，打开 Codex 侧栏后确认空输入框。
- 桌面端：通过本机 app-server 的 `thread/resume` 与 `turn/start` 恢复指定线程；协议调用失败时使用独立校准后的 UI Automation。
- CLI：扫描 `codex.exe`，排除 `app-server` 进程；只有唯一 CLI 进程和唯一含 Codex 标识的终端窗口时才自动输入。
- 多个候选目标或无法关联异常时：仅记入历史，不自动发送。

## 状态规则

- 可恢复：429、502、503、504、传输超时、流中断、模型容量不足。
- 不可恢复：认证、余额、模型不存在、审批等，仅写历史。
- 每个线程先等待 30 秒，发送后观察 30 秒；三次实际发送后转为每 5 分钟重试。
- 同一异常生命周期内仅在状态机允许时发送一次，不会因重复日志重复输入。

## 安全边界

- 不读取 `auth.json`，不调用中转 API，不保存 API Key。
- `history.db` 只保存分类摘要、目标 ID、线程标识和动作结果，不保存日志正文、提示词或响应。
- 无法安全定位目标或检测到多个候选时不进行键盘输入。

## 使用与验证

托盘中的“测试发送 continue”不写入模拟日志，但使用与真实异常完全相同的目标选择和输入路径。详见 [README.md](README.md)。
