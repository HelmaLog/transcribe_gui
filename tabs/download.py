"""
Download tab — video/audio/subtitle downloader using yt-dlp.
"""

import os
import re
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

from backend import query_video_info, run_download
from .base import Tab, BG


# ── Format selection dialog ───────────────────────────────────────────────────

class FormatDialog(tk.Toplevel):
    _LANG_ORDER = ['en', 'zh-Hans', 'zh-Hant', 'zh', 'ja', 'ko', 'fr', 'de',
                   'es', 'ru', 'pt', 'ar', 'it']

    def __init__(self, parent, info, on_confirm, on_cancel):
        super().__init__(parent)
        self.title("选择下载格式")
        self.configure(bg=BG)
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
        return tk.Label(parent, text=text, bg=BG, fg=color,
                        font=("Segoe UI", size), anchor="w")

    def _section(self, parent, row, text):
        tk.Label(parent, text=text, bg=BG, fg="#555555",
                 font=("Segoe UI", 8)).grid(row=row, column=0, columnspan=2,
                                            sticky="w", padx=16, pady=(12, 2))
        tk.Frame(parent, bg="#333333", height=1).grid(
            row=row+1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 6))

    def _build(self):
        info = self._info
        p = self

        title = info['title']
        short_title = title if len(title) <= 60 else title[:57] + "..."
        tk.Label(p, text=short_title, bg=BG, fg="#ffffff",
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
        f_type = tk.Frame(p, bg=BG)
        f_type.grid(row=5, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 4))
        self._video_var = tk.BooleanVar(value=True)
        self._audio_var = tk.BooleanVar(value=False)
        tk.Checkbutton(f_type, text="视频（MP4）", variable=self._video_var,
                       bg=BG, fg="#cccccc", selectcolor="#252525",
                       activebackground=BG, font=("Segoe UI", 10),
                       command=self._on_content_change).pack(side="left", padx=(0, 24))
        tk.Checkbutton(f_type, text="音频（MP3）", variable=self._audio_var,
                       bg=BG, fg="#cccccc", selectcolor="#252525",
                       activebackground=BG, font=("Segoe UI", 10)
                       ).pack(side="left")

        self._section(p, 6, "  分 辨 率  （视频+音频时可用）")
        self._height_var = tk.StringVar(value="best")
        self._height_frame = tk.Frame(p, bg=BG)
        self._height_frame.grid(row=8, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 4))

        heights = info['heights']
        choices = [("best", "最高可用")]
        for h in heights:
            choices.append((str(h), f"{h}p"))

        self._height_radios = []
        for val, lbl in choices:
            rb = tk.Radiobutton(self._height_frame, text=lbl, variable=self._height_var, value=val,
                                bg=BG, fg="#cccccc", selectcolor="#252525",
                                activebackground=BG, font=("Segoe UI", 10))
            rb.pack(side="left", padx=(0, 12))
            self._height_radios.append(rb)

        if not heights:
            self._lbl(self._height_frame, "（无可用视频流）", "#555555").pack(side="left")

        self._section(p, 9, "  字 幕")
        subs = info['subs']

        f_sub_header = tk.Frame(p, bg=BG)
        f_sub_header.grid(row=11, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 2))
        self._sub_all_var = tk.BooleanVar(value=False)
        tk.Checkbutton(f_sub_header, text="全选", variable=self._sub_all_var,
                       bg=BG, fg="#aaaaaa", selectcolor="#252525",
                       activebackground=BG, font=("Segoe UI", 9),
                       command=self._toggle_all_subs).pack(side="left")

        if not subs:
            self._lbl(f_sub_header, "  （该视频没有字幕）", "#555555").pack(side="left")

        sub_outer = tk.Frame(p, bg=BG)
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
            row_f = tk.Frame(sub_outer, bg=BG)
            row_f.grid(row=i // cols, column=i % cols, sticky="w", padx=(0, 20), pady=1)
            cb = tk.Checkbutton(row_f, text=f"{meta['name']} ({lang})",
                                variable=var, bg=BG, fg="#cccccc",
                                selectcolor="#252525", activebackground=BG,
                                font=("Segoe UI", 9))
            cb.pack(side="left")
            tk.Label(row_f, text=type_tag, bg=BG, fg=type_color,
                     font=("Segoe UI", 8)).pack(side="left", padx=(2, 0))

        sep = tk.Frame(p, bg="#333333", height=1)
        sep.grid(row=13, column=0, columnspan=2, sticky="ew", padx=16, pady=(10, 0))
        f_btn = tk.Frame(p, bg=BG)
        f_btn.grid(row=14, column=0, columnspan=2, pady=12)
        tk.Button(f_btn, text="取消", command=self._cancel,
                  bg="#3a3a3a", fg="#cccccc", relief="flat", padx=20, pady=7,
                  font=("Segoe UI", 10), cursor="hand2",
                  activebackground="#4a4a4a").pack(side="left", padx=(0, 12))
        tk.Button(f_btn, text="⬇  确认下载", command=self._confirm,
                  bg="#1e4a1e", fg="#aaddaa", relief="flat", padx=20, pady=7,
                  font=("Segoe UI", 10, "bold"), cursor="hand2",
                  activebackground="#2a6a2a").pack(side="left")

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


# ── Download Tab ──────────────────────────────────────────────────────────────

class DownloadTab(Tab):

    def _build(self):
        cfg = self.app._saved_config
        p = self.frame
        self._dl_log_queue = queue.Queue()
        self._last_dl_dir = ""

        p.columnconfigure(0, weight=1)

        tk.Label(p, text="  下 载", bg=BG, fg="#555555",
                 font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w",
                                            padx=16, pady=(12, 0))

        self._lbl(p, "视频保存目录（每个视频自动创建独立子文件夹）").grid(
            row=1, column=0, sticky="w", padx=16, pady=(8, 0))
        f_dir = tk.Frame(p, bg=BG)
        f_dir.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 0))
        f_dir.columnconfigure(0, weight=1)
        self.dl_dir_var = tk.StringVar(value=cfg.get("download_dir", ""))
        tk.Entry(f_dir, textvariable=self.dl_dir_var, bg="#252525", fg="#aaaaaa",
                 insertbackground="white", relief="flat",
                 font=("Segoe UI", 10), bd=4).grid(row=0, column=0, sticky="ew", ipady=4)
        tk.Button(f_dir, text="浏览", command=self._browse_dl_dir,
                  bg="#3a3a3a", fg="#cccccc", relief="flat", padx=12,
                  font=("Segoe UI", 9), cursor="hand2",
                  activebackground="#4a4a4a").grid(row=0, column=1, padx=(6, 0))

        self._lbl(p, "视频链接（支持 YouTube、X/Twitter、B站等）").grid(
            row=3, column=0, sticky="w", padx=16, pady=(10, 0))
        self.dl_url_var = tk.StringVar()
        self._dl_url_entry = tk.Entry(p, textvariable=self.dl_url_var, bg="#252525", fg="#aaaaaa",
                                      insertbackground="white", relief="flat",
                                      font=("Segoe UI", 10), bd=4)
        self._dl_url_entry.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 0), ipady=4)

        f_btn = tk.Frame(p, bg=BG)
        f_btn.grid(row=5, column=0, pady=14)
        self.dl_btn = tk.Button(
            f_btn, text="🔍  查询并选择格式", command=self._start_download,
            bg="#1e4a1e", fg="#aaddaa", relief="flat",
            font=("Segoe UI", 10, "bold"), padx=20, pady=7,
            cursor="hand2", activebackground="#2a6a2a")
        self.dl_btn.pack(side="left")
        tk.Button(
            f_btn, text="📁 打开文件夹", command=self._open_dl_folder,
            bg="#3a3a3a", fg="#cccccc", relief="flat",
            font=("Segoe UI", 10), padx=20, pady=7,
            cursor="hand2", activebackground="#4a4a4a").pack(side="left", padx=(10, 0))

        # ── Progress bar row ──────────────────────────────────────────────────
        prog_f = tk.Frame(p, bg=BG)
        prog_f.grid(row=6, column=0, sticky="ew", padx=16, pady=(4, 2))
        prog_f.columnconfigure(0, weight=1)

        self._dl_progress = ttk.Progressbar(prog_f, length=300, mode="determinate")
        self._dl_progress.grid(row=0, column=0, sticky="ew")
        self._dl_speed_label = tk.Label(prog_f, text="", bg=BG, fg="#888888",
                                        font=("Segoe UI", 9), width=22, anchor="w")
        self._dl_speed_label.grid(row=0, column=1, padx=(10, 0))

        self._dl_task_var = tk.StringVar(value="")
        tk.Label(p, textvariable=self._dl_task_var, bg=BG, fg="#666666",
                 font=("Segoe UI", 9), anchor="w"
                 ).grid(row=7, column=0, sticky="ew", padx=16, pady=(0, 2))

        p.rowconfigure(8, weight=1)
        self.dl_log_box = scrolledtext.ScrolledText(
            p, bg="#141414", fg="#cccccc", font=("Consolas", 9),
            relief="flat", state="disabled", height=10)
        self.dl_log_box.grid(row=8, column=0, sticky="nsew", padx=16, pady=(0, 16))

        self.app.after(100, self._poll_dl_log)

    # ── Log polling ───────────────────────────────────────────────────────────

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
        self.app.after(100, self._poll_dl_log)

    def _dl_log(self, msg):
        self._dl_log_queue.put(msg)

    # ── Progress update ───────────────────────────────────────────────────────

    def _update_dl_progress(self, pct, speed="", eta=""):
        self._dl_progress["value"] = pct
        parts = []
        if speed:
            parts.append(f"速度: {speed}")
        if eta:
            parts.append(f"剩余: {eta}")
        self._dl_speed_label.configure(text="  ".join(parts))

    # ── Focus URL (called when switching to this tab) ─────────────────────────

    def focus_url(self):
        self._dl_url_entry.focus_set()

    # ── Folder browser ────────────────────────────────────────────────────────

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

    # ── Download flow ─────────────────────────────────────────────────────────

    def _start_download(self):
        url = self.dl_url_var.get().strip()
        save_dir = self.dl_dir_var.get().strip()

        if not url:
            self._dl_log("❌ 请输入 YouTube 链接")
            return
        if not save_dir:
            self._dl_log("❌ 请选择视频保存目录")
            return

        self._is_running = True
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
                self.app.after(200, check_info)
                return

            if err:
                self._dl_log(f"❌ 查询失败: {err}")
                self._is_running = False
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
                self._is_running = False
                self.dl_btn.configure(state="normal", text="🔍  查询并选择格式")
                self._dl_log("取消下载")

            FormatDialog(self.app, info, on_confirm, on_cancel)

        self.app.after(200, check_info)

    def _begin_download(self, url, save_dir, title, fmt_opts):
        self.dl_btn.configure(state="disabled", text="下载中...")
        _safe = re.sub(r'[\\/:*?"<>|]', '_', title).strip()[:40].rstrip()
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

        # Set task label and reset progress
        self._dl_task_var.set(f"正在下载: {title[:40]}")
        self._dl_progress["value"] = 0
        self._dl_speed_label.configure(text="")

        def progress_cb(pct, speed, eta):
            self.app.after(0, lambda: self._update_dl_progress(pct, speed, eta))

        def task():
            result = run_download(dl_config, self._dl_log, progress_cb)
            if result:
                self._last_dl_dir = result

            def finish():
                self._is_running = False
                self.dl_btn.configure(state="normal", text="🔍  查询并选择格式")
                self._dl_task_var.set("")
                if result:
                    self._dl_progress["value"] = 100
                    self._dl_speed_label.configure(text="✅ 完成")
                else:
                    self._dl_speed_label.configure(text="")

            self.app.after(0, finish)

        threading.Thread(target=task, daemon=True).start()

    # ── Config save ───────────────────────────────────────────────────────────

    def get_config(self):
        return {
            "download_dir": self.dl_dir_var.get().strip(),
        }
