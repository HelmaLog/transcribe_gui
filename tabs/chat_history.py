"""「聊天记录」tab:只提供一个按钮,点击后在浏览器打开排版好的会话记录。

不在 GUI 内渲染文字(tkinter 字体小、不清晰),也不在启动时解析日志(避免拖慢启动)——
所有解析/渲染都在点击按钮时才发生,见 backend/chat_log.py。
"""
import threading
import webbrowser
import tkinter as tk

from backend import chat_log
from .base import Tab, BG, HL_GREEN


class ChatHistoryTab(Tab):

    def _build(self):
        p = self.frame
        p.rowconfigure(0, weight=1)
        p.columnconfigure(0, weight=1)

        box = tk.Frame(p, bg=BG)
        box.grid(row=0, column=0)

        tk.Label(box, text="📜 聊天记录", bg=BG, fg=HL_GREEN,
                 font=("Segoe UI", 16, "bold")).pack(pady=(0, 8))
        tk.Label(box, text="历次对话与我的记忆,在浏览器中查看(排版清晰、可搜索)",
                 bg=BG, fg="#888888", font=("Segoe UI", 10)).pack(pady=(0, 20))

        self._btn = tk.Button(
            box, text="🌐  在浏览器打开", command=self._open_browser,
            bg="#1e4a1e", fg="#aaddaa", relief="flat",
            font=("Segoe UI", 12, "bold"), padx=28, pady=10,
            cursor="hand2", activebackground="#2a6a2a")
        self._btn.pack()

        self._hint = tk.Label(box, text="", bg=BG, fg="#666666",
                              font=("Segoe UI", 9))
        self._hint.pack(pady=(14, 0))

    def _open_browser(self):
        self._btn.configure(state="disabled", text="⏳  生成中…")
        self._hint.configure(text="正在解析会话日志,请稍候…")

        def work():
            try:
                index = chat_log.generate_html()
                webbrowser.open(index.as_uri())
                msg = f"已在浏览器打开:{index}"
            except Exception as e:
                msg = f"打开失败:{e}"
            self.app.after(0, lambda: self._done(msg))

        threading.Thread(target=work, daemon=True).start()

    def _done(self, msg):
        self._btn.configure(state="normal", text="🌐  在浏览器打开")
        self._hint.configure(text=msg)
