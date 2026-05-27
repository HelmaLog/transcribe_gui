import tkinter as tk
from tkinter import ttk

BG = "#1e1e1e"


class Tab:
    """Base class for all tabs."""

    def __init__(self, app, frame: tk.Frame):
        self.app = app
        self.frame = frame
        self._is_running = False  # checked by App._on_close
        self._build()

    def _build(self):
        raise NotImplementedError

    def get_config(self) -> dict:
        """Return config dict for saving. Override in subclasses."""
        return {}

    # ── Common UI helpers ─────────────────────────────────────────────────────
    def _lbl(self, parent, text):
        return tk.Label(parent, text=text, bg=BG, fg="#888888",
                        font=("Segoe UI", 9), anchor="w")

    def _entry_row(self, parent, row, label, var, browse_cmd=None, browse_label="浏览"):
        self._lbl(parent, label).grid(row=row*2, column=0, columnspan=3,
                                      sticky="w", padx=16, pady=(8, 0))
        f = tk.Frame(parent, bg=BG)
        f.grid(row=row*2+1, column=0, columnspan=3, sticky="ew", padx=16, pady=(2, 0))
        f.columnconfigure(0, weight=1)
        entry = tk.Entry(f, textvariable=var, bg="#252525", fg="#aaaaaa",
                         insertbackground="white", relief="flat",
                         font=("Segoe UI", 10), bd=4)
        entry.grid(row=0, column=0, sticky="ew", ipady=4)
        if browse_cmd:
            tk.Button(f, text=browse_label, command=browse_cmd,
                      bg="#3a3a3a", fg="#cccccc", relief="flat", padx=12,
                      font=("Segoe UI", 9), cursor="hand2",
                      activebackground="#4a4a4a"
                      ).grid(row=0, column=1, padx=(6, 0))
        return entry
