"""
字幕生成 & 双语翻译 — main entry point
"""

import os
import sys


def _setup_cuda_paths():
    import site
    site_packages = site.getsitepackages()[0]
    nvidia_base = os.path.join(site_packages, "nvidia")
    if not os.path.exists(nvidia_base):
        return
    dirs_to_add = []
    for pkg in ["cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"]:
        bin_dir = os.path.join(nvidia_base, pkg, "bin")
        if os.path.exists(bin_dir):
            dirs_to_add.append(bin_dir)
    if dirs_to_add:
        os.environ["PATH"] = os.pathsep.join(dirs_to_add) + os.pathsep + os.environ.get("PATH", "")


_setup_cuda_paths()

import tkinter as tk
from tkinter import ttk, messagebox

try:
    from tkinterdnd2 import TkinterDnD
    _TkBase = TkinterDnD.Tk
except ImportError:
    _TkBase = tk.Tk

from backend import load_config, save_config
from tabs.transcribe import TranscribeTab
from tabs.download   import DownloadTab
from tabs.tweet      import TweetTab
from tabs.compress   import CompressTab
from tabs.burn       import BurnTab

BG = "#1e1e1e"


def _apply_dark_titlebar(window):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if hwnd == 0:
            hwnd = window.winfo_id()
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


class App(_TkBase):
    def __init__(self):
        super().__init__()
        self.title("字幕生成 & 双语翻译")
        # 窗口图标：与快捷方式一致用 app.ico（任务栏运行时图标）
        try:
            _ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
            if os.path.exists(_ico):
                self.iconbitmap(default=_ico)
        except Exception:
            pass
        self.resizable(True, True)
        self.configure(bg=BG)
        self.minsize(600, 550)
        self._saved_config = load_config()
        self._apply_style()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, lambda: _apply_dark_titlebar(self))
        geo = self._saved_config.get("window_geometry", "")
        if geo:
            try:
                self.geometry(geo)
            except Exception:
                pass
        # Restore last active tab (default: 烧录 = index 2)
        tab_idx = int(self._saved_config.get("active_tab", 2))
        try:
            self._nb.select(tab_idx)
        except Exception:
            self._nb.select(2)
        # Ensure the window actually appears in front, not hidden in taskbar
        self.after(100, self._bring_to_front)

    def _bring_to_front(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    # ── Style ─────────────────────────────────────────────────────────────────
    def _apply_style(self):
        style = ttk.Style(self)
        style.theme_use('default')
        style.configure('TNotebook', background=BG, borderwidth=0)
        style.configure('TNotebook.Tab', background='#2a2a2a', foreground='#888888',
                        padding=[16, 6], font=('Segoe UI', 9))
        style.map('TNotebook.Tab',
                  background=[('selected', BG)],
                  foreground=[('selected', '#ffffff')])
        style.configure('TCombobox',
                        fieldbackground='#2d2d2d', background='#3a3a3a',
                        foreground='#cccccc', arrowcolor='#777777',
                        selectbackground='#0a5a9a', selectforeground='#ffffff',
                        insertcolor='#ffffff')
        style.map('TCombobox',
                  fieldbackground=[('readonly', '#2d2d2d'), ('disabled', '#1a1a1a')],
                  foreground=[('readonly', '#cccccc'), ('disabled', '#555555')],
                  background=[('active', '#484848'), ('pressed', '#383838')])
        self.option_add('*TCombobox*Listbox.background', '#252525')
        self.option_add('*TCombobox*Listbox.foreground', '#c8c8c8')
        self.option_add('*TCombobox*Listbox.selectBackground', '#0a5a9a')
        self.option_add('*TCombobox*Listbox.selectForeground', '#ffffff')
        self.option_add('*TCombobox*Listbox.relief', 'flat')
        self.option_add('*TCombobox*Listbox.borderWidth', '0')
        self.option_add('*TCombobox*Scrollbar.width', 8)
        self.option_add('*TCombobox*Scrollbar.background', '#3c3c3c')
        self.option_add('*TCombobox*Scrollbar.troughColor', '#1a1a1a')
        self.option_add('*TCombobox*Scrollbar.relief', 'flat')

    # ── Build ─────────────────────────────────────────────────────────────────
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._nb = ttk.Notebook(self)
        self._nb.grid(row=0, column=0, sticky="nsew")
        self._nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        _tab_defs = [
            ("  下 载  ", DownloadTab),
            ("  转 写  ", TranscribeTab),
            ("  烧 录  ", BurnTab),
            ("  推 文  ", TweetTab),
            ("  压 缩  ", CompressTab),
        ]
        self.tabs: list = []
        for text, TabClass in _tab_defs:
            frame = tk.Frame(self._nb, bg=BG)
            frame.columnconfigure(0, weight=1)
            self._nb.add(frame, text=text)
            self.tabs.append(TabClass(self, frame))

        (self.download_tab, self.transcribe_tab, self.burn_tab,
         self.tweet_tab, self.compress_tab) = self.tabs

    # ── Shared helpers ────────────────────────────────────────────────────────
    def get_api_key(self, provider: str):
        """Central API key accessor — used by TweetTab and TranscribeTab."""
        return self.transcribe_tab.get_api_key(provider)

    def _do_save_config(self):
        cfg = {}
        for tab in self.tabs:
            cfg.update(tab.get_config())
        cfg["window_geometry"] = self.geometry()
        cfg["active_tab"]      = self._nb.index("current")
        save_config(cfg)

    def _on_close(self):
        if any(getattr(t, '_is_running', False) for t in self.tabs):
            if not messagebox.askyesno("进行中", "任务还在进行中，确定要退出吗？"):
                return
        self._do_save_config()
        self.destroy()

    def _on_tab_changed(self, event):
        idx = self._nb.index("current")
        if idx == 0:
            self.after(50, self.download_tab.focus_url)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # 必须在创建窗口前设置 AppUserModelID，否则 Windows 任务栏会把进程归到
    # python/pyw 解释器名下、显示解释器图标，而不是我们的 app.ico。
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "QinQin.TranscribeGUI")
        except Exception:
            pass
    app = App()
    app.mainloop()
