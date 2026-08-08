"""托盘菜单打开的只读历史窗口。"""

from __future__ import annotations

import datetime as dt
import threading

from .history import HistoryStore


def show_history_async(store: HistoryStore) -> None:
    """Tkinter 事件循环独立运行，避免阻塞 pystray 的托盘回调。"""

    def worker() -> None:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title("Codex 监测历史")
        root.geometry("920x460")
        columns = ("time", "target", "type", "detail", "thread")
        tree = ttk.Treeview(root, columns=columns, show="headings")
        for column, title, width in (
            ("time", "时间", 160),
            ("target", "目标", 100),
            ("type", "类型", 150),
            ("detail", "结果", 350),
            ("thread", "线程", 150),
        ):
            tree.heading(column, text=title)
            tree.column(column, width=width, anchor="w")
        for row in reversed(list(store.recent_events())):
            timestamp = dt.datetime.fromtimestamp(row["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
            thread = (row["thread_id"] or "-")[:18]
            tree.insert(
                "",
                "end",
                values=(timestamp, row.get("target_id") or "-", row["event_type"], row["detail"], thread),
            )
        tree.pack(fill="both", expand=True, padx=10, pady=10)
        root.mainloop()

    threading.Thread(target=worker, name="codex-history", daemon=True).start()
