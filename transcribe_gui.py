"""
faster-whisper 字幕生成 + 双语翻译工具
依赖: pip install faster-whisper srt_equalizer srt tkinterdnd2 requests yt-dlp
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
from tkinter import ttk, filedialog, scrolledtext, messagebox
import threading
import queue
import re

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

from backend import (
    load_config, save_config,
    DEFAULT_MODELS_SF, DEFAULT_MODELS_ARK, DEFAULT_MODELS_GEMINI, DEFAULT_CONFIG,
    translate_batch, translate_batch_ark, translate_batch_gemini,
    chat_completion_stream,
    run_transcribe, query_video_info, run_download,
)


# ── Windows dark title bar ────────────────────────────────────────────────────

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
            ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        pass


# ── Gemini key list widget ────────────────────────────────────────────────────

class GeminiKeyListWidget(tk.Frame):
    """Masked list of Gemini API keys with add/delete buttons."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg="#1e1e1e", **kwargs)
        self._keys = []
        self.columnconfigure(0, weight=1)
        self._build()

    @staticmethod
    def _mask(key):
        if len(key) <= 8:
            return "•" * len(key)
        return key[:4] + "•" * (len(key) - 8) + key[-4:]

    def _build(self):
        # Listbox row
        f_list = tk.Frame(self, bg="#1e1e1e")
        f_list.grid(row=0, column=0, sticky="ew")
        f_list.columnconfigure(0, weight=1)

        self._listbox = tk.Listbox(
            f_list, bg="#2d2d2d", fg="#aaaaaa",
            selectbackground="#0078d4", selectforeground="#ffffff",
            relief="flat", font=("Consolas", 9), height=3,
            activestyle="none", bd=4,
        )
        self._listbox.grid(row=0, column=0, sticky="ew", ipady=2)

        sb = tk.Scrollbar(f_list, orient="vertical", command=self._listbox.yview,
                          bg="#2d2d2d", troughcolor="#1e1e1e", width=10)
        sb.grid(row=0, column=1, sticky="ns")
        self._listbox.configure(yscrollcommand=sb.set)

        # Button row
        f_btn = tk.Frame(self, bg="#1e1e1e")
        f_btn.grid(row=1, column=0, sticky="w", pady=(4, 0))

        tk.Button(
            f_btn, text="+ 添加密钥", command=self._add_key,
            bg="#3a3a3a", fg="#cccccc", relief="flat", padx=10,
            font=("Segoe UI", 9), cursor="hand2", activebackground="#4a4a4a",
        ).pack(side="left")
        tk.Button(
            f_btn, text="删除选中", command=self._delete_selected,
            bg="#3a3a3a", fg="#cccccc", relief="flat", padx=10,
            font=("Segoe UI", 9), cursor="hand2", activebackground="#4a4a4a",
        ).pack(side="left", padx=(8, 0))


    def _add_key(self):
        dlg = tk.Toplevel(self)
        dlg.title("添加 Gemini API Key")
        dlg.configure(bg="#1e1e1e")
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text="请输入 API Key：", bg="#1e1e1e", fg="#888888",
                 font=("Segoe UI", 9)).pack(padx=24, pady=(18, 4), anchor="w")

        key_var = tk.StringVar()
        e = tk.Entry(
            dlg, textvariable=key_var, show="•",
            bg="#2d2d2d", fg="#ffffff", insertbackground="white",
            relief="flat", font=("Segoe UI", 10), bd=4, width=42,
        )
        e.pack(padx=24, pady=(0, 4), ipady=5)
        e.focus_set()

        def confirm(*_):
            key = key_var.get().strip()
            if key and key not in self._keys:
                self._keys.append(key)
                self._listbox.insert("end", self._mask(key))
            dlg.destroy()

        e.bind("<Return>", confirm)

        f_btn = tk.Frame(dlg, bg="#1e1e1e")
        f_btn.pack(pady=14)
        tk.Button(f_btn, text="取消", command=dlg.destroy,
                  bg="#3a3a3a", fg="#cccccc", relief="flat", padx=18, pady=6,
                  font=("Segoe UI", 9), cursor="hand2",
                  activebackground="#4a4a4a").pack(side="left", padx=(0, 10))
        tk.Button(f_btn, text="添加", command=confirm,
                  bg="#0078d4", fg="#ffffff", relief="flat", padx=18, pady=6,
                  font=("Segoe UI", 9, "bold"), cursor="hand2",
                  activebackground="#005fa3").pack(side="left")

        # Center over parent
        dlg.update_idletasks()
        x = self.winfo_rootx() + self.winfo_width() // 2 - dlg.winfo_width() // 2
        y = self.winfo_rooty() + self.winfo_height() // 2 - dlg.winfo_height() // 2
        dlg.geometry(f"+{x}+{y}")

    def _delete_selected(self):
        sel = self._listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        self._listbox.delete(idx)
        self._keys.pop(idx)

    def get_keys(self):
        return list(self._keys)

    def set_keys(self, keys):
        self._keys = list(keys)
        self._listbox.delete(0, "end")
        for k in self._keys:
            self._listbox.insert("end", self._mask(k))


# ── Format selection dialog ───────────────────────────────────────────────────

class FormatDialog(tk.Toplevel):
    _LANG_ORDER = ['en', 'zh-Hans', 'zh-Hant', 'zh', 'ja', 'ko', 'fr', 'de',
                   'es', 'ru', 'pt', 'ar', 'it']

    def __init__(self, parent, info, on_confirm, on_cancel):
        super().__init__(parent)
        self.title("选择下载格式")
        self.configure(bg="#1e1e1e")
        self.resizable(True, True)
        self.grab_set()
        self._info = info
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        max_w = min(560, sw - 80)
        max_h = min(480, sh - 120)
        self.maxsize(max_w, max_h)
        w = min(self.winfo_width(), max_w)
        h = min(self.winfo_height(), max_h)
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _lbl(self, parent, text, color="#888888", size=9):
        return tk.Label(parent, text=text, bg="#1e1e1e", fg=color,
                        font=("Segoe UI", size), anchor="w")

    def _section(self, parent, row, text):
        tk.Label(parent, text=text, bg="#1e1e1e", fg="#555555",
                 font=("Segoe UI", 8)).grid(row=row, column=0, columnspan=2,
                                             sticky="w", padx=16, pady=(12, 2))
        tk.Frame(parent, bg="#333333", height=1).grid(
            row=row+1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 6))

    def _build(self):
        info = self._info
        p = self

        title = info['title']
        short_title = title if len(title) <= 60 else title[:57] + "..."
        tk.Label(p, text=short_title, bg="#1e1e1e", fg="#ffffff",
                 font=("Segoe UI", 10, "bold"), wraplength=480, justify="left",
                 anchor="w").grid(row=0, column=0, columnspan=2,
                                  sticky="w", padx=16, pady=(14, 0))

        dur = info['duration']
        meta = (f"👤 {info['uploader']}   ⏱ {int(dur//60)}:{int(dur%60):02d}"
                if info['uploader'] else f"⏱ {int(dur//60)}:{int(dur%60):02d}")
        self._lbl(p, meta, "#666666", 9).grid(row=1, column=0, columnspan=2,
                                               sticky="w", padx=16, pady=(2, 4))

        if not info['has_ffmpeg']:
            tk.Label(p, text="⚠️  未检测到 ffmpeg — 高分辨率视频需要 ffmpeg 才能合并音视频流。\n"
                             "    建议安装 ffmpeg 后重试，或选择「仅音频」。",
                     bg="#2a1f00", fg="#ffcc44", font=("Segoe UI", 9),
                     justify="left", anchor="w", padx=10, pady=6
                     ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 0))

        self._section(p, 3, "  下 载 内 容")
        f_type = tk.Frame(p, bg="#1e1e1e")
        f_type.grid(row=5, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 4))
        self._video_var = tk.BooleanVar(value=True)
        self._audio_var = tk.BooleanVar(value=False)
        tk.Checkbutton(f_type, text="视频（MP4）", variable=self._video_var,
                       bg="#1e1e1e", fg="#cccccc", selectcolor="#2d2d2d",
                       activebackground="#1e1e1e", font=("Segoe UI", 10),
                       command=self._on_content_change).pack(side="left", padx=(0, 24))
        tk.Checkbutton(f_type, text="音频（MP3）", variable=self._audio_var,
                       bg="#1e1e1e", fg="#cccccc", selectcolor="#2d2d2d",
                       activebackground="#1e1e1e", font=("Segoe UI", 10)
                       ).pack(side="left")

        self._section(p, 6, "  分 辨 率  （视频+音频时可用）")
        self._height_var = tk.StringVar(value="best")
        self._height_frame = tk.Frame(p, bg="#1e1e1e")
        self._height_frame.grid(row=8, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 4))

        heights = info['heights']
        choices = [("best", "最高可用")]
        for h in heights:
            choices.append((str(h), f"{h}p"))

        self._height_radios = []
        for val, lbl in choices:
            rb = tk.Radiobutton(self._height_frame, text=lbl, variable=self._height_var, value=val,
                                bg="#1e1e1e", fg="#cccccc", selectcolor="#2d2d2d",
                                activebackground="#1e1e1e", font=("Segoe UI", 10))
            rb.pack(side="left", padx=(0, 12))
            self._height_radios.append(rb)

        if not heights:
            self._lbl(self._height_frame, "（无可用视频流）", "#555555").pack(side="left")

        self._section(p, 9, "  字 幕")
        subs = info['subs']

        f_sub_header = tk.Frame(p, bg="#1e1e1e")
        f_sub_header.grid(row=11, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 2))
        self._sub_all_var = tk.BooleanVar(value=False)
        tk.Checkbutton(f_sub_header, text="全选", variable=self._sub_all_var,
                       bg="#1e1e1e", fg="#aaaaaa", selectcolor="#2d2d2d",
                       activebackground="#1e1e1e", font=("Segoe UI", 9),
                       command=self._toggle_all_subs).pack(side="left")

        if not subs:
            self._lbl(f_sub_header, "  （该视频没有字幕）", "#555555").pack(side="left")

        sub_outer = tk.Frame(p, bg="#1e1e1e")
        sub_outer.grid(row=12, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 4))

        self._sub_vars = {}
        _SHOW_LANGS = {'en', 'zh', 'zh-Hans', 'zh-Hant'}
        _ORDER = ['en', 'zh-Hans', 'zh-Hant', 'zh']
        filtered = {k: v for k, v in subs.items() if k in _SHOW_LANGS}
        sorted_langs = sorted(filtered.keys(),
                              key=lambda l: _ORDER.index(l) if l in _ORDER else 99)

        if not filtered and subs:
            self._lbl(f_sub_header, "  （无中英文字幕）", "#555555").pack(side="left")

        cols = 2
        for i, lang in enumerate(sorted_langs):
            meta = filtered[lang]
            var = tk.BooleanVar(value=False)
            self._sub_vars[lang] = var
            type_tag = "[手动]" if meta['type'] == 'manual' else "[自动]"
            type_color = "#7ec8a0" if meta['type'] == 'manual' else "#888888"
            row_f = tk.Frame(sub_outer, bg="#1e1e1e")
            row_f.grid(row=i // cols, column=i % cols, sticky="w", padx=(0, 20), pady=1)
            cb = tk.Checkbutton(row_f, text=f"{meta['name']} ({lang})",
                                variable=var, bg="#1e1e1e", fg="#cccccc",
                                selectcolor="#2d2d2d", activebackground="#1e1e1e",
                                font=("Segoe UI", 9))
            cb.pack(side="left")
            tk.Label(row_f, text=type_tag, bg="#1e1e1e", fg=type_color,
                     font=("Segoe UI", 8)).pack(side="left", padx=(2, 0))

        sep = tk.Frame(p, bg="#333333", height=1)
        sep.grid(row=13, column=0, columnspan=2, sticky="ew", padx=16, pady=(10, 0))
        f_btn = tk.Frame(p, bg="#1e1e1e")
        f_btn.grid(row=14, column=0, columnspan=2, pady=12)
        tk.Button(f_btn, text="取消", command=self._cancel,
                  bg="#3a3a3a", fg="#cccccc", relief="flat", padx=20, pady=7,
                  font=("Segoe UI", 10), cursor="hand2",
                  activebackground="#4a4a4a").pack(side="left", padx=(0, 12))
        tk.Button(f_btn, text="⬇  确认下载", command=self._confirm,
                  bg="#0078d4", fg="#ffffff", relief="flat", padx=20, pady=7,
                  font=("Segoe UI", 10, "bold"), cursor="hand2",
                  activebackground="#005fa3").pack(side="left")

        self._on_content_change()

    def _on_content_change(self):
        state = "normal" if self._video_var.get() else "disabled"
        for rb in self._height_radios:
            rb.configure(state=state)

    def _toggle_all_subs(self):
        val = self._sub_all_var.get()
        for var in self._sub_vars.values():
            var.set(val)

    def _confirm(self):
        want_video = self._video_var.get()
        want_audio = self._audio_var.get()
        subtitle_langs = [lang for lang, var in self._sub_vars.items() if var.get()]
        subtitle_only = (not want_video and not want_audio and bool(subtitle_langs))

        if not want_video and not want_audio and not subtitle_langs:
            messagebox.showwarning("请选择", "请至少勾选「视频」、「音频」或「字幕」之一", parent=self)
            return

        height_val = self._height_var.get()

        if subtitle_only:
            fmt = "bestaudio/best"   # skip_download=True 时格式无意义，填占位
            audio_only = False
            also_audio = False
        elif not want_video:
            fmt = "bestaudio/best"
            audio_only = True
            also_audio = False
        elif height_val == "best":
            fmt = "bestvideo+bestaudio/best"
            audio_only = False
            also_audio = want_audio
        else:
            h = height_val
            fmt = f"bestvideo[height<={h}]+bestaudio/bestvideo[height<={h}]/best[height<={h}]"
            audio_only = False
            also_audio = want_audio

        result = {
            'format_str': fmt,
            'subtitle_langs': subtitle_langs,
            'audio_only': audio_only,
            'also_audio': also_audio,
            'subtitle_only': subtitle_only,
        }
        self.destroy()
        self._on_confirm(result)

    def _cancel(self):
        self.destroy()
        self._on_cancel()


# ── Main application ──────────────────────────────────────────────────────────

class App(TkinterDnD.Tk if HAS_DND else tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("字幕生成 & 双语翻译")
        self.resizable(True, True)
        self.configure(bg="#1e1e1e")
        self.minsize(600, 550)
        self._log_queue = queue.Queue()
        self._dl_log_queue = queue.Queue()
        self._is_running = False
        self._dl_is_running = False
        self._tweet_is_running = False
        self._last_dl_dir = ""
        self._stop_event = None
        self._saved_config = load_config()
        self._apply_style()
        self._build()
        self._poll_log()
        self._poll_dl_log()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, lambda: _apply_dark_titlebar(self))

    def _apply_style(self):
        style = ttk.Style(self)
        style.theme_use('default')

        # Notebook tabs
        style.configure('TNotebook', background='#1e1e1e', borderwidth=0)
        style.configure('TNotebook.Tab', background='#2a2a2a', foreground='#888888',
                        padding=[16, 6], font=('Segoe UI', 9))
        style.map('TNotebook.Tab',
                  background=[('selected', '#1e1e1e')],
                  foreground=[('selected', '#ffffff')])

        # Dark combobox — entry field + arrow button
        style.configure('TCombobox',
                        fieldbackground='#2d2d2d',
                        background='#3a3a3a',
                        foreground='#cccccc',
                        arrowcolor='#777777',
                        selectbackground='#0a5a9a',
                        selectforeground='#ffffff',
                        insertcolor='#ffffff')
        style.map('TCombobox',
                  fieldbackground=[('readonly', '#2d2d2d'), ('disabled', '#1a1a1a')],
                  foreground=[('readonly', '#cccccc'), ('disabled', '#555555')],
                  background=[('active', '#484848'), ('pressed', '#383838')])

        # Dark dropdown popup (Listbox + Scrollbar created by Tk internally)
        self.option_add('*TCombobox*Listbox.background', '#252525')
        self.option_add('*TCombobox*Listbox.foreground', '#c8c8c8')
        self.option_add('*TCombobox*Listbox.selectBackground', '#0a5a9a')
        self.option_add('*TCombobox*Listbox.selectForeground', '#ffffff')
        self.option_add('*TCombobox*Listbox.relief', 'flat')
        self.option_add('*TCombobox*Listbox.borderWidth', '0')
        # Narrow, dark scrollbar inside the dropdown
        self.option_add('*TCombobox*Scrollbar.width', 8)
        self.option_add('*TCombobox*Scrollbar.background', '#3c3c3c')
        self.option_add('*TCombobox*Scrollbar.activeBackground', '#505050')
        self.option_add('*TCombobox*Scrollbar.troughColor', '#1a1a1a')
        self.option_add('*TCombobox*Scrollbar.relief', 'flat')
        self.option_add('*TCombobox*Scrollbar.borderWidth', '0')
        self.option_add('*TCombobox*Scrollbar.elementBorderWidth', '0')

    def _on_close(self):
        if self._is_running or self._dl_is_running:
            if not messagebox.askyesno("进行中", "任务还在进行中，确定要退出吗？"):
                return
        self._do_save_config()
        self.destroy()

    def _do_save_config(self):
        provider = self.provider_var.get()
        sf_custom = self._saved_config.get("custom_models", [])
        ark_custom = self._saved_config.get("ark_custom_models", [])
        gemini_custom = self._saved_config.get("gemini_custom_models", [])
        cur = self.trans_model_var.get().strip()
        if provider == "siliconflow":
            if cur and cur not in DEFAULT_MODELS_SF and cur not in sf_custom:
                sf_custom.append(cur)
        elif provider == "volcengine":
            if cur and cur not in DEFAULT_MODELS_ARK and cur not in ark_custom:
                ark_custom.append(cur)
        else:
            if cur and cur not in DEFAULT_MODELS_GEMINI and cur not in gemini_custom:
                gemini_custom.append(cur)

        tweet_prompts = []
        for i in range(3):
            name = self._tweet_prompt_name_vars[i].get().strip() or f"场景 {i+1}"
            text = self._tweet_prompt_texts[i].get("1.0", "end").strip()
            tweet_prompts.append({"name": name, "text": text})

        save_config({
            "model_path": self.model_var.get().strip(),
            "device": self.device_var.get(),
            "compute_type": self.compute_var.get(),
            "language": self.lang_var.get().strip(),
            "max_chars_en": self.chars_var.get().strip(),
            "max_chars_zh": self.chars_zh_var.get().strip(),
            "initial_prompt": self.prompt_var.get(),
            "provider": provider,
            "api_key": self.sf_key_var.get().strip(),
            "translate_model": (self.trans_model_var.get().strip()
                                if provider == "siliconflow"
                                else self._saved_config.get("translate_model", DEFAULT_MODELS_SF[0])),
            "custom_models": sf_custom,
            "ark_api_key": self.ark_key_var.get().strip(),
            "ark_model": (self.trans_model_var.get().strip()
                          if provider == "volcengine"
                          else self._saved_config.get("ark_model", DEFAULT_MODELS_ARK[0])),
            "ark_custom_models": ark_custom,
            "gemini_api_keys": self._gemini_key_widget.get_keys(),
            "gemini_model": (self.trans_model_var.get().strip()
                             if provider == "gemini"
                             else self._saved_config.get("gemini_model", DEFAULT_MODELS_GEMINI[0])),
            "gemini_custom_models": gemini_custom,
            "translate_threads": self.threads_var.get().strip(),
            "add_emoji": self.emoji_var.get(),
            "output_mode": self.output_mode_var.get(),
            "batch_size": self.batch_var.get().strip(),
            "download_dir": self.dl_dir_var.get().strip(),
            "tweet_provider": self.tweet_provider_var.get(),
            "tweet_model": self.tweet_model_var.get().strip(),
            "tweet_prompts": tweet_prompts,
            "tweet_font_size": self._tweet_font_size.get(),
        })

    def _poll_log(self):
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self.log_box.configure(state="normal")
                self.log_box.insert("end", msg + "\n")
                self.log_box.see("end")
                self.log_box.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    def _poll_dl_log(self):
        try:
            while True:
                msg = self._dl_log_queue.get_nowait()
                self.dl_log_box.configure(state="normal")
                self.dl_log_box.insert("end", msg + "\n")
                self.dl_log_box.see("end")
                self.dl_log_box.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._poll_dl_log)

    def _lbl(self, parent, text):
        return tk.Label(parent, text=text, bg="#1e1e1e", fg="#888888",
                        font=("Segoe UI", 9), anchor="w")

    def _entry_row(self, parent, row, label, var, browse_cmd=None, browse_label="浏览"):
        self._lbl(parent, label).grid(row=row*2, column=0, columnspan=3,
                                       sticky="w", padx=16, pady=(8, 0))
        f = tk.Frame(parent, bg="#1e1e1e")
        f.grid(row=row*2+1, column=0, columnspan=3, sticky="ew", padx=16, pady=2)
        f.columnconfigure(0, weight=1)
        e = tk.Entry(f, textvariable=var, bg="#2d2d2d", fg="#ffffff",
                     insertbackground="white", relief="flat",
                     font=("Segoe UI", 10), bd=4)
        e.grid(row=0, column=0, sticky="ew", ipady=4)
        if browse_cmd:
            tk.Button(f, text=browse_label, command=browse_cmd,
                      bg="#3a3a3a", fg="#cccccc", relief="flat", padx=10,
                      font=("Segoe UI", 9), cursor="hand2",
                      activebackground="#4a4a4a").grid(row=0, column=1, padx=(6, 0))
        return e

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self):
        cfg = self._saved_config
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._nb = ttk.Notebook(self)
        nb = self._nb
        nb.grid(row=0, column=0, sticky="nsew")
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        tab_t = tk.Frame(nb, bg="#1e1e1e")
        tab_d = tk.Frame(nb, bg="#1e1e1e")
        tab_w = tk.Frame(nb, bg="#1e1e1e")
        for tab in (tab_t, tab_d, tab_w):
            tab.columnconfigure(0, weight=1)
        nb.add(tab_t, text="  转 写  ")
        nb.add(tab_d, text="  下 载  ")
        nb.add(tab_w, text="  推 文  ")

        self._build_transcribe(tab_t, cfg)
        self._build_download(tab_d, cfg)
        self._build_tweet(tab_w, cfg)

    # ── Transcribe tab ────────────────────────────────────────────────────────

    def _build_transcribe(self, p, cfg):
        tk.Label(p, text="  转 写", bg="#1e1e1e", fg="#555555",
                 font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 0))

        self.video_var = tk.StringVar()
        drop_frame = tk.Frame(p, bg="#252525")
        drop_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 4))
        drop_frame.columnconfigure(0, weight=1)
        self.drop_label = tk.Label(drop_frame, text="🎬  拖拽视频到此处，或点击浏览",
                                    bg="#252525", fg="#666666", font=("Segoe UI", 10),
                                    pady=14, cursor="hand2")
        self.drop_label.grid(row=0, column=0, sticky="ew")
        self.video_entry = tk.Entry(drop_frame, textvariable=self.video_var,
                                     bg="#252525", fg="#aaaaaa", insertbackground="white",
                                     relief="flat", font=("Segoe UI", 9), bd=0, justify="center")
        self.video_entry.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 4))
        tk.Button(drop_frame, text="浏览文件", command=self._browse_video,
                  bg="#3a3a3a", fg="#cccccc", relief="flat", padx=12, pady=3,
                  font=("Segoe UI", 9), cursor="hand2"
                  ).grid(row=2, column=0, pady=(0, 10))
        if HAS_DND:
            for w in [drop_frame, self.drop_label, self.video_entry]:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)

        self.model_var = tk.StringVar(value=cfg["model_path"])
        self._entry_row(p, 1, "Whisper 模型路径", self.model_var, self._browse_model)

        self.save_var = tk.StringVar()
        self._entry_row(p, 2, "SRT 保存路径（空=与源文件同目录）", self.save_var, self._browse_save, "另存为")

        self.srt_var = tk.StringVar()
        srt_entry = self._entry_row(p, 3, "现有英文 SRT（可选，提供后跳过本地识别）", self.srt_var, self._browse_srt)
        if HAS_DND:
            srt_entry.drop_target_register(DND_FILES)
            srt_entry.dnd_bind("<<Drop>>", self._on_drop_srt)

        f_opts = tk.Frame(p, bg="#1e1e1e")
        f_opts.grid(row=9, column=0, sticky="ew", padx=16, pady=8)

        def olbl(t):
            tk.Label(f_opts, text=t, bg="#1e1e1e", fg="#888888",
                     font=("Segoe UI", 9)).pack(side="left")

        def ocombo(var, vals, w):
            ttk.Combobox(f_opts, textvariable=var, values=vals, width=w,
                         state="readonly").pack(side="left", padx=(4, 14))

        def oentry(var, w):
            tk.Entry(f_opts, textvariable=var, width=w, bg="#2d2d2d", fg="#ffffff",
                     insertbackground="white", relief="flat", font=("Segoe UI", 10),
                     bd=4).pack(side="left", padx=(4, 14), ipady=3)

        olbl("设备")
        self.device_var = tk.StringVar(value=cfg["device"])
        ocombo(self.device_var, ["cuda", "cpu"], 7)
        olbl("精度")
        self.compute_var = tk.StringVar(value=cfg["compute_type"])
        ocombo(self.compute_var, ["float16", "float32", "int8"], 9)
        olbl("语言")
        self.lang_var = tk.StringVar(value=cfg["language"])
        oentry(self.lang_var, 5)
        olbl("英文切分")
        self.chars_var = tk.StringVar(value=cfg.get("max_chars_en", cfg.get("max_chars", "42")))
        oentry(self.chars_var, 5)
        olbl("中文切分")
        self.chars_zh_var = tk.StringVar(value=cfg.get("max_chars_zh", "20"))
        oentry(self.chars_zh_var, 5)

        self._lbl(p, "初始提示词（可选）").grid(row=10, column=0, sticky="w", padx=16, pady=(2, 0))
        self.prompt_var = tk.StringVar(value=cfg["initial_prompt"])
        tk.Entry(p, textvariable=self.prompt_var, bg="#2d2d2d", fg="#ffffff",
                 insertbackground="white", relief="flat", font=("Segoe UI", 10), bd=4
                 ).grid(row=11, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)

        tk.Frame(p, bg="#333333", height=1).grid(row=12, column=0, sticky="ew", padx=16, pady=(8, 0))

        f_trans_title = tk.Frame(p, bg="#1e1e1e")
        f_trans_title.grid(row=13, column=0, sticky="ew", padx=16, pady=(6, 0))
        tk.Label(f_trans_title, text="  翻 译", bg="#1e1e1e", fg="#555555",
                 font=("Segoe UI", 8)).pack(side="left")
        self.output_mode_var = tk.StringVar(value=cfg.get("output_mode", "bilingual"))
        for i, (val, txt) in enumerate([
            ("english_only", "只生成英文"),
            ("bilingual",    "双语（中英）"),
            ("chinese_only", "只生成中文"),
        ]):
            tk.Radiobutton(
                f_trans_title, text=txt, variable=self.output_mode_var, value=val,
                bg="#1e1e1e", fg="#aaaaaa", selectcolor="#2d2d2d",
                activebackground="#1e1e1e", font=("Segoe UI", 9),
                command=self._on_output_mode_change,
            ).pack(side="left", padx=(16 if i == 0 else 6, 0))

        # ── 翻译选项区（选"只生成英文"时自动折叠）──
        self._trans_opts = tk.Frame(p, bg="#1e1e1e")
        self._trans_opts.grid(row=14, column=0, sticky="ew")
        self._trans_opts.columnconfigure(0, weight=1)
        tof = self._trans_opts

        f_provider = tk.Frame(tof, bg="#1e1e1e")
        f_provider.grid(row=0, column=0, sticky="w", padx=16, pady=(4, 0))
        tk.Label(f_provider, text="翻译服务", bg="#1e1e1e", fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left")
        self.provider_var = tk.StringVar(value=cfg.get("provider", "siliconflow"))
        for val, txt in [("siliconflow", "硅基流动"), ("volcengine", "火山引擎 ARK"), ("gemini", "Google Gemini")]:
            tk.Radiobutton(f_provider, text=txt, variable=self.provider_var, value=val,
                           bg="#1e1e1e", fg="#aaaaaa", selectcolor="#2d2d2d",
                           activebackground="#1e1e1e", font=("Segoe UI", 9),
                           command=self._on_provider_change
                           ).pack(side="left", padx=(12, 0))

        # SF key
        self._sf_key_lbl = self._lbl(tof, "硅基流动 API Key")
        self._sf_key_lbl.grid(row=1, column=0, sticky="w", padx=16, pady=(6, 0))
        self.sf_key_var = tk.StringVar(value=cfg.get("api_key", ""))
        self._sf_key_entry = tk.Entry(tof, textvariable=self.sf_key_var, bg="#2d2d2d", fg="#ffffff",
                                      insertbackground="white", relief="flat",
                                      font=("Segoe UI", 10), bd=4, show="•")
        self._sf_key_entry.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)

        # ARK key
        self._ark_key_lbl = self._lbl(tof, "火山引擎 ARK API Key")
        self._ark_key_lbl.grid(row=1, column=0, sticky="w", padx=16, pady=(6, 0))
        self.ark_key_var = tk.StringVar(value=cfg.get("ark_api_key", ""))
        self._ark_key_entry = tk.Entry(tof, textvariable=self.ark_key_var, bg="#2d2d2d", fg="#ffffff",
                                       insertbackground="white", relief="flat",
                                       font=("Segoe UI", 10), bd=4, show="•")
        self._ark_key_entry.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)

        # Gemini key widget
        self._gemini_key_lbl = self._lbl(tof, "Google Gemini API Key（多 Key 自动轮询）")
        self._gemini_key_lbl.grid(row=1, column=0, sticky="w", padx=16, pady=(6, 0))
        self._gemini_key_widget = GeminiKeyListWidget(tof)
        self._gemini_key_widget.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 4))
        self._gemini_key_widget.set_keys(cfg.get("gemini_api_keys", []))

        # Model combo
        self._lbl(tof, "翻译模型（可手动输入新模型后回车保存）").grid(
            row=3, column=0, sticky="w", padx=16, pady=(2, 0))
        self.trans_model_var = tk.StringVar()
        self.trans_combo = ttk.Combobox(tof, textvariable=self.trans_model_var,
                                        font=("Segoe UI", 10))
        self.trans_combo.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)
        self.trans_combo.bind("<Return>", self._add_custom_model)

        f_batch = tk.Frame(tof, bg="#1e1e1e")
        f_batch.grid(row=5, column=0, sticky="w", padx=16, pady=(2, 6))
        tk.Label(f_batch, text="每批翻译行数", bg="#1e1e1e", fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left")
        self.batch_var = tk.StringVar(value=cfg.get("batch_size", "15"))
        tk.Entry(f_batch, textvariable=self.batch_var, width=6, bg="#2d2d2d", fg="#ffffff",
                 insertbackground="white", relief="flat", font=("Segoe UI", 10),
                 bd=4).pack(side="left", padx=(8, 0), ipady=3)
        tk.Label(f_batch, text="并发数", bg="#1e1e1e", fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left", padx=(18, 0))
        self.threads_var = tk.StringVar(value=cfg.get("translate_threads", "3"))
        tk.Entry(f_batch, textvariable=self.threads_var, width=4, bg="#2d2d2d", fg="#ffffff",
                 insertbackground="white", relief="flat", font=("Segoe UI", 10),
                 bd=4).pack(side="left", padx=(8, 0), ipady=3)
        self.emoji_var = tk.BooleanVar(value=cfg.get("add_emoji", True))
        tk.Checkbutton(f_batch, text="添加表情", variable=self.emoji_var,
                       bg="#1e1e1e", fg="#888888", selectcolor="#2d2d2d",
                       activebackground="#1e1e1e", font=("Segoe UI", 9),
                       ).pack(side="left", padx=(18, 0))

        # ── 按钮行 ──
        f_btn_row = tk.Frame(p, bg="#1e1e1e")
        f_btn_row.grid(row=15, column=0, pady=10)
        self.btn = tk.Button(f_btn_row, text="▶  开始", command=self._start,
                             bg="#0078d4", fg="#ffffff", relief="flat",
                             font=("Segoe UI", 11, "bold"), padx=24, pady=8,
                             cursor="hand2", activebackground="#005fa3")
        self.btn.pack(side="left")
        self.stop_btn = tk.Button(f_btn_row, text="⏹  停止", command=self._stop,
                                  bg="#555555", fg="#aaaaaa", relief="flat",
                                  font=("Segoe UI", 11, "bold"), padx=24, pady=8,
                                  cursor="hand2", activebackground="#666666", state="disabled")
        self.stop_btn.pack(side="left", padx=(10, 0))
        self._open_folder_btn = tk.Button(
            f_btn_row, text="📂  打开目录", command=self._open_output_folder,
            bg="#3a3a3a", fg="#aaaaaa", relief="flat",
            font=("Segoe UI", 11), padx=18, pady=8,
            cursor="hand2", activebackground="#4a4a4a", state="disabled")
        self._open_folder_btn.pack(side="left", padx=(10, 0))

        p.rowconfigure(16, weight=1)
        self.log_box = scrolledtext.ScrolledText(p, bg="#111111", fg="#cccccc",
                                                  font=("Consolas", 9), relief="flat",
                                                  state="disabled", height=10)
        self.log_box.grid(row=16, column=0, sticky="nsew", padx=16, pady=(0, 16))

        self._last_output_dir = ""
        self._on_provider_change()
        self._on_output_mode_change()

    # ── Download tab ──────────────────────────────────────────────────────────

    def _build_download(self, p, cfg):
        p.columnconfigure(0, weight=1)
        p.rowconfigure(6, weight=1)

        tk.Label(p, text="  下 载", bg="#1e1e1e", fg="#555555",
                 font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w",
                                            padx=16, pady=(12, 0))

        self._lbl(p, "视频保存目录（每个视频自动创建独立子文件夹）").grid(
            row=1, column=0, sticky="w", padx=16, pady=(8, 0))
        f_dir = tk.Frame(p, bg="#1e1e1e")
        f_dir.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 0))
        f_dir.columnconfigure(0, weight=1)
        self.dl_dir_var = tk.StringVar(value=cfg.get("download_dir", ""))
        tk.Entry(f_dir, textvariable=self.dl_dir_var, bg="#2d2d2d", fg="#ffffff",
                 insertbackground="white", relief="flat",
                 font=("Segoe UI", 10), bd=4).grid(row=0, column=0, sticky="ew", ipady=4)
        tk.Button(f_dir, text="浏览", command=self._browse_dl_dir,
                  bg="#3a3a3a", fg="#cccccc", relief="flat", padx=12,
                  font=("Segoe UI", 9), cursor="hand2",
                  activebackground="#4a4a4a").grid(row=0, column=1, padx=(6, 0))

        self._lbl(p, "视频链接（支持 YouTube、X/Twitter、B站等）").grid(
            row=3, column=0, sticky="w", padx=16, pady=(10, 0))
        self.dl_url_var = tk.StringVar()
        self._dl_url_entry = tk.Entry(p, textvariable=self.dl_url_var, bg="#2d2d2d", fg="#ffffff",
                                      insertbackground="white", relief="flat",
                                      font=("Segoe UI", 10), bd=4)
        self._dl_url_entry.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 0), ipady=4)

        f_btn = tk.Frame(p, bg="#1e1e1e")
        f_btn.grid(row=5, column=0, pady=14)
        self.dl_btn = tk.Button(
            f_btn, text="🔍  查询并选择格式", command=self._start_download,
            bg="#0078d4", fg="#ffffff", relief="flat",
            font=("Segoe UI", 10, "bold"), padx=20, pady=7,
            cursor="hand2", activebackground="#005fa3")
        self.dl_btn.pack(side="left")
        tk.Button(
            f_btn, text="📁 打开文件夹", command=self._open_dl_folder,
            bg="#3a3a3a", fg="#cccccc", relief="flat",
            font=("Segoe UI", 10), padx=20, pady=7,
            cursor="hand2", activebackground="#4a4a4a").pack(side="left", padx=(10, 0))

        self.dl_log_box = scrolledtext.ScrolledText(
            p, bg="#111111", fg="#cccccc", font=("Consolas", 9),
            relief="flat", state="disabled", height=10)
        self.dl_log_box.grid(row=6, column=0, sticky="nsew", padx=16, pady=(0, 16))

    # ── Tweet tab ─────────────────────────────────────────────────────────────

    def _build_tweet(self, p, cfg):
        p.columnconfigure(0, weight=1)
        p.rowconfigure(0, weight=1)

        fs = cfg.get("tweet_font_size", 11)
        self._tweet_font_size = tk.IntVar(value=fs)

        prompts = cfg.get("tweet_prompts", DEFAULT_CONFIG["tweet_prompts"])

        # PanedWindow: top = prompt config (draggable), bottom = chat
        pw = tk.PanedWindow(p, orient=tk.VERTICAL,
                            sashwidth=5, sashpad=0, sashrelief="flat",
                            sashcursor="sb_v_double_arrow",
                            bg="#252525", bd=0, relief="flat")
        pw.grid(row=0, column=0, sticky="nsew")

        # ── Top pane: Prompt config ──
        top = tk.Frame(pw, bg="#1e1e1e")
        top.columnconfigure(0, weight=1)
        top.rowconfigure(1, weight=1)

        tk.Label(top, text="  提示词配置", bg="#1e1e1e", fg="#444444",
                 font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w", padx=16, pady=(10, 0))

        pnb = ttk.Notebook(top)
        pnb.grid(row=1, column=0, sticky="nsew", padx=16, pady=(4, 10))
        self._tweet_prompt_nb = pnb
        self._tweet_prompt_name_vars = []
        self._tweet_prompt_texts = []

        for i, pd in enumerate(prompts):
            tab = tk.Frame(pnb, bg="#161616")
            tab.columnconfigure(1, weight=1)
            tab.rowconfigure(1, weight=1)
            pnb.add(tab, text=f"  {pd['name']}  ")

            tk.Label(tab, text="名称", bg="#161616", fg="#555555",
                     font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w",
                                                 padx=(14, 6), pady=(10, 6))
            name_var = tk.StringVar(value=pd["name"])
            self._tweet_prompt_name_vars.append(name_var)
            tk.Entry(tab, textvariable=name_var, bg="#222222", fg="#bbbbbb",
                     insertbackground="#888888", relief="flat",
                     font=("Segoe UI", 10), bd=3, highlightthickness=0).grid(
                row=0, column=1, sticky="ew", padx=(0, 14), pady=(10, 6), ipady=4)

            pt = tk.Text(tab, bg="#111111", fg="#cccccc", insertbackground="#888888",
                         relief="flat", font=("Segoe UI", 10), bd=0,
                         wrap="word", padx=12, pady=10, height=4,
                         selectbackground="#1e3a5a", highlightthickness=0)
            pt.insert("1.0", pd["text"])
            pt.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=14, pady=(0, 0))
            self._tweet_prompt_texts.append(pt)

            idx = i
            tk.Button(tab, text="保 存", command=lambda i=idx: self._save_tweet_prompt(i),
                      bg="#161616", fg="#505050", relief="flat", padx=14, pady=5,
                      font=("Segoe UI", 9), cursor="hand2",
                      activebackground="#242424", activeforeground="#aaaaaa",
                      bd=0).grid(row=2, column=1, sticky="e", padx=(0, 14), pady=(6, 10))

        pw.add(top, minsize=60, stretch="never")

        # ── Bottom pane: Chat ──
        bottom = tk.Frame(pw, bg="#1e1e1e")
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(1, weight=1)

        # Chat header row
        f_hdr = tk.Frame(bottom, bg="#1e1e1e")
        f_hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 0))

        # Provider radios
        self.tweet_provider_var = tk.StringVar(value=cfg.get("tweet_provider", "gemini"))
        for val, txt in [("gemini", "Gemini"), ("siliconflow", "硅基"), ("volcengine", "ARK")]:
            tk.Radiobutton(f_hdr, text=txt, variable=self.tweet_provider_var, value=val,
                           bg="#1e1e1e", fg="#666666", selectcolor="#1e1e1e",
                           activebackground="#1e1e1e", activeforeground="#aaaaaa",
                           font=("Segoe UI", 9),
                           command=self._on_tweet_provider_change).pack(side="left", padx=(0, 2))

        self.tweet_model_var = tk.StringVar(value=cfg.get("tweet_model", DEFAULT_MODELS_GEMINI[0]))
        self.tweet_model_combo = ttk.Combobox(f_hdr, textvariable=self.tweet_model_var,
                                               font=("Segoe UI", 9), width=20)
        self.tweet_model_combo.pack(side="left", padx=(8, 0))

        # Right side: font controls + new conversation
        f_right = tk.Frame(f_hdr, bg="#1e1e1e")
        f_right.pack(side="right")

        _bkw = dict(bg="#1e1e1e", relief="flat", font=("Segoe UI", 9), cursor="hand2",
                    activebackground="#252525", bd=0, padx=4, pady=2)
        tk.Button(f_right, text="A−", fg="#484848",
                  command=lambda: self._update_tweet_font(-1), **_bkw).pack(side="left")
        tk.Label(f_right, textvariable=self._tweet_font_size, bg="#1e1e1e", fg="#3e3e3e",
                 font=("Segoe UI", 9), width=2, anchor="center").pack(side="left")
        tk.Button(f_right, text="A+", fg="#484848",
                  command=lambda: self._update_tweet_font(1), **_bkw).pack(side="left")
        # "新对话" moved to send row

        # Chat display: tk.Text + thin dark native scrollbar
        f_chat = tk.Frame(bottom, bg="#0c0c0c",
                          highlightbackground="#1e1e1e", highlightthickness=1)
        f_chat.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 0))
        f_chat.columnconfigure(0, weight=1)
        f_chat.rowconfigure(0, weight=1)

        self._tweet_chat = tk.Text(
            f_chat, bg="#0c0c0c", fg="#d8d8d8",
            insertbackground="white", relief="flat", font=("Segoe UI", fs), bd=0,
            wrap="word", padx=16, pady=14, spacing1=1, spacing3=1,
            selectbackground="#1e3a5a", selectforeground="#ffffff",
            cursor="arrow", highlightthickness=0)
        self._tweet_chat.grid(row=0, column=0, sticky="nsew")
        self._tweet_chat.configure(state="disabled")

        # Thin 7px dark scrollbar
        chat_sb = tk.Scrollbar(
            f_chat, orient="vertical", command=self._tweet_chat.yview,
            bg="#282828", activebackground="#383838", troughcolor="#0c0c0c",
            relief="flat", bd=0, width=7, elementborderwidth=0, highlightthickness=0)
        chat_sb.grid(row=0, column=1, sticky="ns")
        self._tweet_chat.configure(yscrollcommand=chat_sb.set)
        self._apply_tweet_tags()

        # Input box with subtle border
        f_input_wrap = tk.Frame(bottom, bg="#1a1a1a",
                                highlightbackground="#2a2a2a", highlightthickness=1)
        f_input_wrap.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 0))
        f_input_wrap.columnconfigure(0, weight=1)

        self._tweet_input = tk.Text(
            f_input_wrap, bg="#141414", fg="#d0d0d0",
            insertbackground="#7a7a7a", relief="flat", font=("Segoe UI", 10),
            bd=0, height=4, wrap="word", padx=14, pady=10, highlightthickness=0)
        self._tweet_input.grid(row=0, column=0, sticky="ew")
        # Enter = send, Ctrl+Enter = newline
        self._tweet_input.bind("<Return>",
                                lambda e: (self._send_tweet(), "break")[-1])
        self._tweet_input.bind("<Control-Return>",
                                lambda e: (self._tweet_input.insert("insert", "\n"), "break")[-1])

        # Send row: right-aligned  [新对话]  [发 送]
        f_send = tk.Frame(bottom, bg="#1e1e1e")
        f_send.grid(row=3, column=0, sticky="e", padx=16, pady=(8, 14))
        tk.Button(f_send, text="新对话", command=self._new_tweet_conversation,
                  bg="#1e1e1e", fg="#555555", relief="flat", padx=14, pady=7,
                  font=("Segoe UI", 9), cursor="hand2",
                  activebackground="#252525", activeforeground="#999999",
                  bd=0).pack(side="left", padx=(0, 8))
        self.tweet_send_btn = tk.Button(
            f_send, text="发 送", command=self._send_tweet,
            bg="#0a6dd4", fg="#ffffff", relief="flat", padx=28, pady=7,
            font=("Segoe UI", 10, "bold"), cursor="hand2",
            activebackground="#0060bb", bd=0)
        self.tweet_send_btn.pack(side="left")

        pw.add(bottom, minsize=220, stretch="always")

        # Init
        self._tweet_history = []
        self._on_tweet_provider_change()

    # ── Transcribe tab events ─────────────────────────────────────────────────

    def _on_output_mode_change(self):
        pass  # 翻译配置区常驻，不折叠，避免切换时界面跳动

    def _on_provider_change(self):
        provider = self.provider_var.get()
        cfg = self._saved_config
        self._sf_key_lbl.grid_remove()
        self._sf_key_entry.grid_remove()
        self._ark_key_lbl.grid_remove()
        self._ark_key_entry.grid_remove()
        self._gemini_key_lbl.grid_remove()
        self._gemini_key_widget.grid_remove()
        if provider == "siliconflow":
            self._sf_key_lbl.grid()
            self._sf_key_entry.grid()
            models = DEFAULT_MODELS_SF + cfg.get("custom_models", [])
            saved_model = cfg.get("translate_model", DEFAULT_MODELS_SF[0])
        elif provider == "volcengine":
            self._ark_key_lbl.grid()
            self._ark_key_entry.grid()
            models = DEFAULT_MODELS_ARK + cfg.get("ark_custom_models", [])
            saved_model = cfg.get("ark_model", DEFAULT_MODELS_ARK[0])
        else:
            self._gemini_key_lbl.grid()
            self._gemini_key_widget.grid()
            models = DEFAULT_MODELS_GEMINI + cfg.get("gemini_custom_models", [])
            saved_model = cfg.get("gemini_model", DEFAULT_MODELS_GEMINI[0])
        self.trans_combo["values"] = models
        self.trans_model_var.set(saved_model if saved_model in models else models[0])

    def _add_custom_model(self, event=None):
        val = self.trans_model_var.get().strip()
        if not val:
            return
        current = list(self.trans_combo["values"])
        if val not in current:
            current.append(val)
            self.trans_combo["values"] = current
            cfg = self._saved_config
            key = {"siliconflow": "custom_models", "volcengine": "ark_custom_models"}.get(
                self.provider_var.get(), "gemini_custom_models")
            custom = cfg.get(key, [])
            if val not in custom:
                custom.append(val)
            cfg[key] = custom
            self._log(f"已添加模型: {val}")

    def _on_drop(self, event):
        path = event.data.strip().strip("{}")
        self.video_var.set(path)
        self.drop_label.configure(fg="#aaaaaa")

    def _on_drop_srt(self, event):
        path = event.data.strip().strip("{}")
        self.srt_var.set(path)

    def _browse_video(self):
        path = filedialog.askopenfilename(
            filetypes=[("视频/音频", "*.mp4 *.mkv *.mov *.avi *.mp3 *.wav *.m4a"), ("所有文件", "*.*")])
        if path:
            self.video_var.set(path)
            self.drop_label.configure(fg="#aaaaaa")

    def _browse_model(self):
        path = filedialog.askdirectory(title="选择Whisper模型文件夹")
        if path:
            self.model_var.set(path)

    def _browse_save(self):
        video = self.video_var.get().strip()
        init = os.path.splitext(video)[0] + ".srt" if video else ""
        path = filedialog.asksaveasfilename(
            defaultextension=".srt",
            initialfile=os.path.basename(init) if init else "",
            filetypes=[("SRT字幕", "*.srt")])
        if path:
            self.save_var.set(path)

    def _browse_srt(self):
        path = filedialog.askopenfilename(
            filetypes=[("SRT字幕", "*.srt"), ("所有文件", "*.*")])
        if path:
            self.srt_var.set(path)

    def _log(self, msg):
        self._log_queue.put(msg)

    def _start(self):
        video = self.video_var.get().strip()
        srt_file = self.srt_var.get().strip()
        model = self.model_var.get().strip()

        if not video and not srt_file:
            self._log("❌ 请先选择视频文件，或提供已有英文 SRT")
            return
        if srt_file and not os.path.exists(srt_file):
            self._log("❌ SRT 文件不存在，请重新选择")
            return
        if not srt_file and not model:
            self._log("❌ 请先选择 Whisper 模型路径")
            return

        try:
            max_chars_en = int(self.chars_var.get())
        except ValueError:
            self._log("❌ 英文切分字符数请填整数")
            return
        try:
            max_chars_zh = int(self.chars_zh_var.get())
        except ValueError:
            self._log("❌ 中文切分字符数请填整数")
            return
        try:
            batch_size = int(self.batch_var.get())
        except ValueError:
            batch_size = 15

        provider = self.provider_var.get()
        config = {
            "video_path": video,
            "srt_path": srt_file,
            "model_path": model,
            "device": self.device_var.get(),
            "compute_type": self.compute_var.get(),
            "language": self.lang_var.get().strip(),
            "max_chars_en": max_chars_en,
            "max_chars_zh": max_chars_zh,
            "initial_prompt": self.prompt_var.get(),
            "save_path": self.save_var.get().strip(),
            "output_mode": self.output_mode_var.get(),
            "provider": provider,
            "api_key": self.sf_key_var.get().strip(),
            "ark_api_key": self.ark_key_var.get().strip(),
            "gemini_api_keys": self._gemini_key_widget.get_keys(),
            "translate_model": self.trans_model_var.get().strip() if provider == "siliconflow" else "",
            "ark_model": self.trans_model_var.get().strip() if provider == "volcengine" else "",
            "gemini_model": self.trans_model_var.get().strip() if provider == "gemini" else "",
            "batch_size": batch_size,
            "translate_threads": self.threads_var.get().strip(),
            "add_emoji": self.emoji_var.get(),
        }

        self._do_save_config()
        self._is_running = True
        self._stop_event = threading.Event()
        self.btn.configure(state="disabled", text="处理中...")
        self.stop_btn.configure(state="normal")
        self._open_folder_btn.configure(state="disabled")
        label = os.path.basename(srt_file) if srt_file else os.path.basename(video)
        self._log(f"▶ {label}")

        stop_event = self._stop_event

        def task():
            result_dir = run_transcribe(config, self._log, stop_event)
            self._is_running = False
            if result_dir:
                self._last_output_dir = result_dir
                self._open_folder_btn.configure(state="normal")
            self.btn.configure(state="normal", text="▶  开始")
            self.stop_btn.configure(state="disabled", text="⏹  停止")

        threading.Thread(target=task, daemon=True).start()

    def _stop(self):
        if self._stop_event:
            self._stop_event.set()
        self.stop_btn.configure(state="disabled", text="停止中...")

    def _open_output_folder(self):
        folder = self._last_output_dir
        if not folder or not os.path.isdir(folder):
            self._log("❌ 输出目录不存在，请先执行一次转写")
            return
        self._log(f"📂 打开: {folder}")
        os.startfile(folder)

    # ── Download tab events ───────────────────────────────────────────────────

    def _on_tab_changed(self, event):
        idx = self._nb.index("current")
        if idx == 1:   # 下载 tab
            self.after(50, self._dl_url_entry.focus_set)

    def _browse_dl_dir(self):
        path = filedialog.askdirectory(title="选择视频保存目录")
        if path:
            self.dl_dir_var.set(path)

    def _open_dl_folder(self):
        folder = self._last_dl_dir or self.dl_dir_var.get().strip()
        if not folder:
            self._dl_log("❌ 请先完成一次下载或手动填写目录")
            return
        folder = os.path.normpath(folder)
        if os.path.isdir(folder):
            self._dl_log(f"📂 打开: {folder}")
            os.startfile(folder)
        else:
            self._dl_log(f"❌ 文件夹不存在: {folder}")

    def _dl_log(self, msg):
        self._dl_log_queue.put(msg)

    def _start_download(self):
        url = self.dl_url_var.get().strip()
        save_dir = self.dl_dir_var.get().strip()

        if not url:
            self._dl_log("❌ 请输入 YouTube 链接")
            return
        if not save_dir:
            self._dl_log("❌ 请选择视频保存目录")
            return

        self._do_save_config()
        self._dl_is_running = True
        self.dl_btn.configure(state="disabled", text="查询中...")
        self._dl_log(f"🔍 正在查询视频信息: {url}")

        info_q = queue.Queue()

        def query_task():
            info, err = query_video_info(url)
            info_q.put((info, err))

        threading.Thread(target=query_task, daemon=True).start()

        def check_info():
            try:
                info, err = info_q.get_nowait()
            except queue.Empty:
                self.after(200, check_info)
                return

            if err:
                self._dl_log(f"❌ 查询失败: {err}")
                self._dl_is_running = False
                self.dl_btn.configure(state="normal", text="🔍  查询并选择格式")
                return

            self._dl_log(f"📺 {info['title']}")
            dur = info['duration']
            if info['uploader']:
                self._dl_log(f"👤 {info['uploader']}  ⏱ {int(dur//60)}:{int(dur%60):02d}")
            self._dl_log(f"📐 可用分辨率: {', '.join(str(h)+'p' for h in info['heights']) or '无视频流'}")
            self._dl_log(f"💬 字幕: {len(info['subs'])} 种语言可用")

            def on_confirm(fmt_opts):
                self._begin_download(url, save_dir, info['title'], fmt_opts)

            def on_cancel():
                self._dl_is_running = False
                self.dl_btn.configure(state="normal", text="🔍  查询并选择格式")
                self._dl_log("取消下载")

            FormatDialog(self, info, on_confirm, on_cancel)

        self.after(200, check_info)

    def _begin_download(self, url, save_dir, title, fmt_opts):
        self.dl_btn.configure(state="disabled", text="下载中...")
        _safe = re.sub(r'[\\/:*?"<>|]', '_', title).strip()[:40]
        self._last_dl_dir = os.path.normpath(os.path.join(save_dir, _safe))

        if fmt_opts.get('subtitle_only'):
            content_desc = "仅字幕"
        else:
            parts = []
            if fmt_opts.get('audio_only'):
                parts.append("音频(MP3)")
            else:
                parts.append("视频(MP4)")
                if fmt_opts.get('also_audio'):
                    parts.append("+ 音频(MP3)")
            content_desc = " ".join(parts)
        sub_desc = ", ".join(fmt_opts['subtitle_langs']) if fmt_opts['subtitle_langs'] else "无字幕"
        self._dl_log(f"▶ 开始下载  [{content_desc}]  字幕: {sub_desc}")

        dl_config = {
            'url': url,
            'save_dir': save_dir,
            'title': title,
            'format_str': fmt_opts['format_str'],
            'subtitle_langs': fmt_opts['subtitle_langs'],
            'audio_only': fmt_opts['audio_only'],
            'also_audio': fmt_opts.get('also_audio', False),
            'subtitle_only': fmt_opts.get('subtitle_only', False),
        }

        def task():
            result = run_download(dl_config, self._dl_log)
            if result:
                self._last_dl_dir = result
            self._dl_is_running = False
            self.dl_btn.configure(state="normal", text="🔍  查询并选择格式")

        threading.Thread(target=task, daemon=True).start()

    # ── Tweet tab events ──────────────────────────────────────────────────────

    def _apply_tweet_tags(self):
        fs = self._tweet_font_size.get()
        f = "Segoe UI"
        self._tweet_chat.tag_configure(
            "user_hdr", foreground="#4a9fd4",
            font=(f, fs - 1, "bold"), spacing1=16, spacing3=3)
        self._tweet_chat.tag_configure(
            "ai_hdr", foreground="#5aaa5a",
            font=(f, fs - 1, "bold"), spacing1=16, spacing3=3)
        self._tweet_chat.tag_configure(
            "user_text", foreground="#c0d8e8",
            font=(f, fs), spacing3=4, lmargin1=4, lmargin2=4)
        self._tweet_chat.tag_configure(
            "ai_text", foreground="#dedede",
            font=(f, fs), spacing3=4, lmargin1=4, lmargin2=4)
        self._tweet_chat.tag_configure(
            "err_text", foreground="#d05050",
            font=(f, fs), spacing3=4)
        self._tweet_chat.tag_configure(
            "sep", foreground="#1e1e1e",
            font=(f, 6), spacing1=4, spacing3=10)

    def _update_tweet_font(self, delta):
        cur = self._tweet_font_size.get()
        new = max(8, min(24, cur + delta))
        if new == cur:
            return
        self._tweet_font_size.set(new)
        self._tweet_chat.configure(font=("Segoe UI", new))
        self._apply_tweet_tags()

    def _on_tweet_provider_change(self):
        provider = self.tweet_provider_var.get()
        cfg = self._saved_config
        if provider == "gemini":
            models = DEFAULT_MODELS_GEMINI + cfg.get("gemini_custom_models", [])
        elif provider == "siliconflow":
            models = DEFAULT_MODELS_SF + cfg.get("custom_models", [])
        else:
            models = DEFAULT_MODELS_ARK + cfg.get("ark_custom_models", [])
        self.tweet_model_combo["values"] = models
        cur = self.tweet_model_var.get()
        if cur not in models:
            self.tweet_model_var.set(models[0])

    def _save_tweet_prompt(self, idx):
        name = self._tweet_prompt_name_vars[idx].get().strip() or f"场景 {idx+1}"
        text = self._tweet_prompt_texts[idx].get("1.0", "end").strip()
        prompts = self._saved_config.get("tweet_prompts", DEFAULT_CONFIG["tweet_prompts"])
        prompts[idx] = {"name": name, "text": text}
        self._saved_config["tweet_prompts"] = prompts
        self._tweet_prompt_nb.tab(idx, text=f"  {name}  ")
        self._do_save_config()

    def _new_tweet_conversation(self):
        self._tweet_history = []
        self._tweet_chat.configure(state="normal")
        self._tweet_chat.delete("1.0", "end")
        self._tweet_chat.configure(state="disabled")

    def _get_tweet_api_key(self):
        provider = self.tweet_provider_var.get()
        if provider == "gemini":
            return self._gemini_key_widget.get_keys()
        elif provider == "siliconflow":
            return self.sf_key_var.get().strip()
        else:
            return self.ark_key_var.get().strip()

    def _get_tweet_system_prompt(self):
        try:
            idx = self._tweet_prompt_nb.index("current")
            return self._tweet_prompt_texts[idx].get("1.0", "end").strip()
        except Exception:
            return ""

    def _append_tweet(self, text, tag):
        self._tweet_chat.configure(state="normal")
        self._tweet_chat.insert("end", text, tag)
        self._tweet_chat.see("end")
        self._tweet_chat.configure(state="disabled")

    def _stream_tweet_chunk(self, text):
        self._tweet_chat.configure(state="normal")
        self._tweet_chat.insert("end", text, "ai_text")
        self._tweet_chat.see("end")
        self._tweet_chat.configure(state="disabled")

    def _send_tweet(self):
        if self._tweet_is_running:
            return
        msg = self._tweet_input.get("1.0", "end").strip()
        if not msg:
            return
        self._tweet_input.delete("1.0", "end")

        self._tweet_history.append({"role": "user", "content": msg})
        self._append_tweet("你\n", "user_hdr")
        self._append_tweet(msg + "\n", "user_text")

        provider = self.tweet_provider_var.get()
        model = self.tweet_model_var.get()
        api_key = self._get_tweet_api_key()
        system_prompt = self._get_tweet_system_prompt()
        history = list(self._tweet_history)

        self._tweet_is_running = True
        self.tweet_send_btn.configure(state="disabled", text="···")
        self._append_tweet("AI\n", "ai_hdr")

        def on_chunk(text):
            self.after(0, lambda t=text: self._stream_tweet_chunk(t))

        def task():
            full_text, err = chat_completion_stream(
                history, system_prompt, provider, api_key, model, on_chunk
            )
            if err:
                self.after(0, lambda: self._append_tweet(f"❌ {err}\n", "err_text"))
            else:
                if full_text:
                    self._tweet_history.append({"role": "assistant", "content": full_text})
                self.after(0, lambda: self._stream_tweet_chunk("\n"))
            sep = "─" * 52 + "\n"
            self.after(0, lambda: self._append_tweet(sep, "sep"))
            self._tweet_is_running = False
            self.after(0, lambda: self.tweet_send_btn.configure(state="normal", text="发 送"))

        threading.Thread(target=task, daemon=True).start()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
