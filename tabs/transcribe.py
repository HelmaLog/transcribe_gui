"""
Transcribe tab — Whisper transcription + translation.
"""

import glob as _glob
import os
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from tkinterdnd2 import DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

from backend import (
    DEFAULT_MODELS_SF, DEFAULT_MODELS_ARK, DEFAULT_MODELS_GEMINI, DEFAULT_MODELS_PIONEER,
    fetch_pioneer_models, fetch_sf_models, fetch_ark_models, fetch_gemini_models,
    test_ark_model,
    run_transcribe,
)


def _strip_model_marker(name: str) -> str:
    for suffix in (" ✓", " ✗"):
        if name.endswith(suffix):
            return name[:-2]
    return name
from .base import Tab, BG


# Whisper 初始提示词默认值：一句带标点、大小写规范的英文，作为"上文"喂给模型，
# 促使识别结果带标点符号与正确大小写（本工具源语音通常为英文）。可在界面里改/清空。
_DEFAULT_INITIAL_PROMPT = (
    "The following is a clear transcript with proper punctuation and capitalization."
)

_VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m4v", ".wmv")


def _find_oldest_video(folder: str) -> str:
    """返回目录中修改时间最早的视频文件路径（无则空串）。
    用现有 SRT 翻译时源视频通常比生成的字幕更早，取最老的即原始视频。"""
    try:
        vids = [os.path.join(folder, f) for f in os.listdir(folder)
                if f.lower().endswith(_VIDEO_EXTS)
                and os.path.isfile(os.path.join(folder, f))]
    except OSError:
        return ""
    if not vids:
        return ""
    return min(vids, key=os.path.getmtime)


# ── Gemini key list widget ────────────────────────────────────────────────────

class GeminiKeyListWidget(tk.Frame):
    """Masked list of Gemini API keys with add/delete buttons."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=BG, **kwargs)
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
        f_list = tk.Frame(self, bg=BG)
        f_list.grid(row=0, column=0, sticky="ew")
        f_list.columnconfigure(0, weight=1)

        self._listbox = tk.Listbox(
            f_list, bg="#252525", fg="#aaaaaa",
            selectbackground="#0078d4", selectforeground="#ffffff",
            relief="flat", font=("Consolas", 9), height=3,
            activestyle="none", bd=4,
        )
        self._listbox.grid(row=0, column=0, sticky="ew", ipady=2)

        sb = tk.Scrollbar(f_list, orient="vertical", command=self._listbox.yview,
                          bg="#3a3a3a", troughcolor=BG, width=10)
        sb.grid(row=0, column=1, sticky="ns")
        self._listbox.configure(yscrollcommand=sb.set)

        # Button row
        f_btn = tk.Frame(self, bg=BG)
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
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text="请输入 API Key：", bg=BG, fg="#888888",
                 font=("Segoe UI", 9)).pack(padx=24, pady=(18, 4), anchor="w")

        key_var = tk.StringVar()
        e = tk.Entry(
            dlg, textvariable=key_var, show="•",
            bg="#252525", fg="#aaaaaa", insertbackground="white",
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

        f_btn = tk.Frame(dlg, bg=BG)
        f_btn.pack(pady=14)
        tk.Button(f_btn, text="取消", command=dlg.destroy,
                  bg="#3a3a3a", fg="#cccccc", relief="flat", padx=18, pady=6,
                  font=("Segoe UI", 9), cursor="hand2",
                  activebackground="#4a4a4a").pack(side="left", padx=(0, 10))
        tk.Button(f_btn, text="添加", command=confirm,
                  bg="#1e4a1e", fg="#aaddaa", relief="flat", padx=18, pady=6,
                  font=("Segoe UI", 9, "bold"), cursor="hand2",
                  activebackground="#2a6a2a").pack(side="left")

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


# ── Transcribe Tab ────────────────────────────────────────────────────────────

class TranscribeTab(Tab):

    def _build(self):
        cfg = self.app._saved_config
        p = self.frame
        self._log_queue = queue.Queue()
        self._stop_event = None
        self._last_output_dir = ""

        tk.Label(p, text="  转 写", bg=BG, fg="#555555",
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
        self._save_lbl = tk.Label(p, text="SRT 保存路径  ▸ 点击另存为...",
                                  bg=BG, fg="#888888", font=("Segoe UI", 9),
                                  anchor="w", cursor="hand2")
        self._save_lbl.grid(row=4, column=0, sticky="w", padx=16, pady=(8, 0))
        self._save_lbl.bind("<Button-1>", lambda e: self._on_save_label_click())

        self._save_frame = tk.Frame(p, bg=BG)
        self._save_frame.grid(row=5, column=0, columnspan=3, sticky="ew", padx=16, pady=(2, 0))
        self._save_frame.columnconfigure(0, weight=1)
        tk.Entry(self._save_frame, textvariable=self.save_var, bg="#252525", fg="#aaaaaa",
                 insertbackground="white", relief="flat", font=("Segoe UI", 10), bd=4,
                 ).grid(row=0, column=0, sticky="ew", ipady=4)
        tk.Button(self._save_frame, text="另存为", command=self._browse_save,
                  bg="#3a3a3a", fg="#cccccc", relief="flat", padx=12,
                  font=("Segoe UI", 9), cursor="hand2", activebackground="#4a4a4a",
                  ).grid(row=0, column=1, padx=(6, 0))
        self._save_frame.grid_remove()

        self.srt_var = tk.StringVar()
        srt_entry = self._entry_row(p, 3, "现有英文 SRT（可选，提供后跳过本地识别）", self.srt_var, self._browse_srt)
        if HAS_DND:
            srt_entry.drop_target_register(DND_FILES)
            srt_entry.dnd_bind("<<Drop>>", self._on_drop_srt)

        f_opts = tk.Frame(p, bg=BG)
        f_opts.grid(row=9, column=0, sticky="ew", padx=16, pady=8)

        def olbl(t):
            tk.Label(f_opts, text=t, bg=BG, fg="#888888",
                     font=("Segoe UI", 9)).pack(side="left")

        def ocombo(var, vals, w):
            ttk.Combobox(f_opts, textvariable=var, values=vals, width=w,
                         state="readonly").pack(side="left", padx=(4, 14))

        def oentry(var, w):
            tk.Entry(f_opts, textvariable=var, width=w, bg="#252525", fg="#aaaaaa",
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

        # 初始提示词（可选）：默认折叠，避免占用空间；展开后可编辑/清空。
        prompt_hdr = tk.Frame(p, bg=BG)
        prompt_hdr.grid(row=10, column=0, sticky="ew", padx=16, pady=(2, 0))
        prompt_hdr.columnconfigure(0, weight=1)
        self._lbl(prompt_hdr, "初始提示词（可选，给 Whisper 的风格/上下文提示）"
                  ).grid(row=0, column=0, sticky="w")
        self._prompt_toggle_btn = tk.Button(
            prompt_hdr, text="▶ 展开", command=self._toggle_prompt,
            bg=BG, fg="#555555", relief="flat",
            font=("Segoe UI", 8), padx=6, pady=0, cursor="hand2",
            activebackground="#2a2a2a", activeforeground="#888888")
        self._prompt_toggle_btn.grid(row=0, column=1, sticky="e")

        self.prompt_var = tk.StringVar(
            value=cfg.get("initial_prompt") or _DEFAULT_INITIAL_PROMPT)
        self._prompt_entry = tk.Entry(
            p, textvariable=self.prompt_var, bg="#252525", fg="#aaaaaa",
            insertbackground="white", relief="flat", font=("Segoe UI", 10), bd=4)
        self._prompt_entry.grid(row=11, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)
        self._prompt_expanded = False
        self._prompt_entry.grid_remove()

        tk.Frame(p, bg="#333333", height=1).grid(row=12, column=0, sticky="ew", padx=16, pady=(8, 0))

        f_trans_title = tk.Frame(p, bg=BG)
        f_trans_title.grid(row=13, column=0, sticky="ew", padx=16, pady=(6, 0))
        tk.Label(f_trans_title, text="  翻 译", bg=BG, fg="#555555",
                 font=("Segoe UI", 8)).pack(side="left")
        self.output_mode_var = tk.StringVar(value=cfg.get("output_mode", "bilingual"))
        for i, (val, txt) in enumerate([
            ("english_only", "只生成英文"),
            ("bilingual",    "双语（中英）"),
            ("chinese_only", "只生成中文"),
        ]):
            tk.Radiobutton(
                f_trans_title, text=txt, variable=self.output_mode_var, value=val,
                bg=BG, fg="#aaaaaa", selectcolor="#252525",
                activebackground=BG, font=("Segoe UI", 9),
                command=self._on_output_mode_change,
            ).pack(side="left", padx=(16 if i == 0 else 6, 0))

        # ── 翻译选项区 ──
        self._trans_opts = tk.Frame(p, bg=BG)
        self._trans_opts.grid(row=14, column=0, sticky="ew")
        self._trans_opts.columnconfigure(0, weight=1)
        tof = self._trans_opts

        f_provider = tk.Frame(tof, bg=BG)
        f_provider.grid(row=0, column=0, sticky="w", padx=16, pady=(4, 0))
        tk.Label(f_provider, text="翻译服务", bg=BG, fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left")
        self.provider_var = tk.StringVar(value=cfg.get("provider", "siliconflow"))
        for val, txt in [
            ("siliconflow", "硅基流动"), ("volcengine", "火山引擎 ARK"),
            ("gemini", "Google Gemini"), ("pioneer", "Pioneer"),
        ]:
            tk.Radiobutton(f_provider, text=txt, variable=self.provider_var, value=val,
                           bg=BG, fg="#aaaaaa", selectcolor="#252525",
                           activebackground=BG, font=("Segoe UI", 9),
                           command=self._on_provider_change
                           ).pack(side="left", padx=(12, 0))

        # SF key
        self._sf_key_lbl = self._lbl(tof, "硅基流动 API Key")
        self._sf_key_lbl.grid(row=1, column=0, sticky="w", padx=16, pady=(6, 0))
        self.sf_key_var = tk.StringVar(value=cfg.get("api_key", ""))
        self._sf_key_entry = tk.Entry(tof, textvariable=self.sf_key_var, bg="#252525", fg="#aaaaaa",
                                      insertbackground="white", relief="flat",
                                      font=("Segoe UI", 10), bd=4, show="•")
        self._sf_key_entry.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)

        # ARK key
        self._ark_key_lbl = self._lbl(tof, "火山引擎 ARK API Key")
        self._ark_key_lbl.grid(row=1, column=0, sticky="w", padx=16, pady=(6, 0))
        self.ark_key_var = tk.StringVar(value=cfg.get("ark_api_key", ""))
        self._ark_key_entry = tk.Entry(tof, textvariable=self.ark_key_var, bg="#252525", fg="#aaaaaa",
                                       insertbackground="white", relief="flat",
                                       font=("Segoe UI", 10), bd=4, show="•")
        self._ark_key_entry.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)

        # Gemini key widget
        self._gemini_key_lbl = self._lbl(tof, "Google Gemini API Key（多 Key 自动轮询）")
        self._gemini_key_lbl.grid(row=1, column=0, sticky="w", padx=16, pady=(6, 0))
        self._gemini_key_widget = GeminiKeyListWidget(tof)
        self._gemini_key_widget.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 4))
        self._gemini_key_widget.set_keys(cfg.get("gemini_api_keys", []))

        # Pioneer key
        self._pioneer_key_lbl = self._lbl(tof, "Pioneer API Key")
        self._pioneer_key_lbl.grid(row=1, column=0, sticky="w", padx=16, pady=(6, 0))
        self.pioneer_key_var = tk.StringVar(value=cfg.get("pioneer_api_key", ""))
        self._pioneer_key_entry = tk.Entry(tof, textvariable=self.pioneer_key_var,
                                           bg="#252525", fg="#aaaaaa",
                                           insertbackground="white", relief="flat",
                                           font=("Segoe UI", 10), bd=4, show="•")
        self._pioneer_key_entry.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)

        # Model label row (with optional fetch button for Pioneer)
        f_model_hdr = tk.Frame(tof, bg=BG)
        f_model_hdr.grid(row=3, column=0, sticky="ew", padx=16, pady=(2, 0))
        self._lbl(f_model_hdr, "翻译模型（可手动输入新模型后回车保存）").pack(side="left")
        _fbtn_kw = dict(relief="flat", padx=10, pady=1, font=("Segoe UI", 9),
                        cursor="hand2", bg="#3a3a3a", fg="#cccccc", activebackground="#4a4a4a")
        self._sf_fetch_btn = tk.Button(
            f_model_hdr, text="获取模型列表", command=self._fetch_sf_models_trans, **_fbtn_kw)
        self._sf_fetch_btn.pack(side="left", padx=(10, 0))
        self._ark_fetch_btn = tk.Button(
            f_model_hdr, text="获取模型列表", command=self._fetch_ark_models_trans, **_fbtn_kw)
        self._ark_fetch_btn.pack(side="left", padx=(10, 0))
        self._gemini_fetch_btn = tk.Button(
            f_model_hdr, text="获取模型列表", command=self._fetch_gemini_models_trans, **_fbtn_kw)
        self._gemini_fetch_btn.pack(side="left", padx=(10, 0))
        self._pioneer_fetch_btn = tk.Button(
            f_model_hdr, text="获取模型列表", command=self._fetch_pioneer_models, **_fbtn_kw)
        self._pioneer_fetch_btn.pack(side="left", padx=(10, 0))

        self.trans_model_var = tk.StringVar()
        self.trans_combo = ttk.Combobox(tof, textvariable=self.trans_model_var,
                                        font=("Segoe UI", 10), height=16)
        self.trans_combo.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)
        self.trans_combo.bind("<Return>", self._add_custom_model)

        f_batch = tk.Frame(tof, bg=BG)
        f_batch.grid(row=5, column=0, sticky="w", padx=16, pady=(2, 6))
        tk.Label(f_batch, text="每批翻译行数", bg=BG, fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left")
        self.batch_var = tk.StringVar(value=cfg.get("batch_size", "15"))
        tk.Entry(f_batch, textvariable=self.batch_var, width=6, bg="#252525", fg="#aaaaaa",
                 insertbackground="white", relief="flat", font=("Segoe UI", 10),
                 bd=4).pack(side="left", padx=(8, 0), ipady=3)
        tk.Label(f_batch, text="并发数", bg=BG, fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left", padx=(18, 0))
        self.threads_var = tk.StringVar(value=cfg.get("translate_threads", "3"))
        tk.Entry(f_batch, textvariable=self.threads_var, width=4, bg="#252525", fg="#aaaaaa",
                 insertbackground="white", relief="flat", font=("Segoe UI", 10),
                 bd=4).pack(side="left", padx=(8, 0), ipady=3)
        self.emoji_var = tk.BooleanVar(value=cfg.get("add_emoji", True))
        tk.Checkbutton(f_batch, text="添加表情", variable=self.emoji_var,
                       bg=BG, fg="#888888", selectcolor="#252525",
                       activebackground=BG, font=("Segoe UI", 9),
                       ).pack(side="left", padx=(18, 0))
        self.snap_30fps_var = tk.BooleanVar(value=cfg.get("snap_to_30fps", True))
        tk.Checkbutton(f_batch, text="30fps 对齐（CapCut）", variable=self.snap_30fps_var,
                       bg=BG, fg="#888888", selectcolor="#252525",
                       activebackground=BG, font=("Segoe UI", 9),
                       ).pack(side="left", padx=(18, 0))

        # ── 按钮行 ──
        f_btn_row = tk.Frame(p, bg=BG)
        f_btn_row.grid(row=15, column=0, pady=10)
        self.btn = tk.Button(f_btn_row, text="▶  开始", command=self._start,
                             bg="#1e4a1e", fg="#aaddaa", relief="flat",
                             font=("Segoe UI", 11, "bold"), padx=24, pady=8,
                             cursor="hand2", activebackground="#2a6a2a")
        self.btn.pack(side="left")
        self.stop_btn = tk.Button(f_btn_row, text="⏹  停止", command=self._stop,
                                  bg="#3a3a3a", fg="#888888", relief="flat",
                                  font=("Segoe UI", 11, "bold"), padx=24, pady=8,
                                  cursor="hand2", activebackground="#4a4a4a", state="disabled")
        self.stop_btn.pack(side="left", padx=(10, 0))
        self._open_folder_btn = tk.Button(
            f_btn_row, text="📂  打开目录", command=self._open_output_folder,
            bg="#3a3a3a", fg="#aaaaaa", relief="flat",
            font=("Segoe UI", 11), padx=18, pady=8,
            cursor="hand2", activebackground="#4a4a4a", state="disabled")
        self._open_folder_btn.pack(side="left", padx=(10, 0))

        p.rowconfigure(16, weight=1)
        self.log_box = scrolledtext.ScrolledText(p, bg="#141414", fg="#cccccc",
                                                 font=("Consolas", 9), relief="flat",
                                                 state="disabled", height=10)
        self.log_box.grid(row=16, column=0, sticky="nsew", padx=16, pady=(0, 16))

        self._on_provider_change()
        self._on_output_mode_change()
        self.app.after(100, self._poll_log)

    # ── Log polling ───────────────────────────────────────────────────────────

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
        self.app.after(100, self._poll_log)

    def _log(self, msg):
        self._log_queue.put(msg)

    # ── Provider / output mode changes ───────────────────────────────────────

    def _toggle_prompt(self):
        self._prompt_expanded = not self._prompt_expanded
        if self._prompt_expanded:
            self._prompt_entry.grid()
            self._prompt_toggle_btn.configure(text="▼ 收起")
        else:
            self._prompt_entry.grid_remove()
            self._prompt_toggle_btn.configure(text="▶ 展开")

    def _on_output_mode_change(self):
        pass  # 翻译配置区常驻，不折叠，避免切换时界面跳动

    def _on_provider_change(self):
        provider = self.provider_var.get()
        cfg = self.app._saved_config
        self._sf_key_lbl.grid_remove()
        self._sf_key_entry.grid_remove()
        self._ark_key_lbl.grid_remove()
        self._ark_key_entry.grid_remove()
        self._gemini_key_lbl.grid_remove()
        self._gemini_key_widget.grid_remove()
        self._pioneer_key_lbl.grid_remove()
        self._pioneer_key_entry.grid_remove()
        self._pioneer_fetch_btn.pack_forget()
        self._sf_fetch_btn.pack_forget()
        self._ark_fetch_btn.pack_forget()
        self._gemini_fetch_btn.pack_forget()
        if provider == "siliconflow":
            self._sf_key_lbl.grid()
            self._sf_key_entry.grid()
            self._sf_fetch_btn.pack(side="left", padx=(10, 0))
            custom = cfg.get("custom_models", [])
            models = custom if custom else DEFAULT_MODELS_SF
            saved_model = cfg.get("translate_model", models[0] if models else "")
        elif provider == "volcengine":
            self._ark_key_lbl.grid()
            self._ark_key_entry.grid()
            self._ark_fetch_btn.pack(side="left", padx=(10, 0))
            custom = cfg.get("ark_custom_models", [])
            models = custom if custom else DEFAULT_MODELS_ARK
            saved_model = cfg.get("ark_model", models[0] if models else "")
        elif provider == "pioneer":
            self._pioneer_key_lbl.grid()
            self._pioneer_key_entry.grid()
            self._pioneer_fetch_btn.pack(side="left", padx=(10, 0))
            models = cfg.get("pioneer_custom_models", DEFAULT_MODELS_PIONEER)
            saved_model = cfg.get("pioneer_model", models[0] if models else "")
        else:  # gemini
            self._gemini_key_lbl.grid()
            self._gemini_key_widget.grid()
            self._gemini_fetch_btn.pack(side="left", padx=(10, 0))
            custom = cfg.get("gemini_custom_models", [])
            models = custom if custom else DEFAULT_MODELS_GEMINI
            saved_model = cfg.get("gemini_model", models[0] if models else "")
        self.trans_combo["values"] = models
        self.trans_model_var.set(saved_model if saved_model in models else (models[0] if models else ""))

    def _add_custom_model(self, event=None):
        val = self.trans_model_var.get().strip()
        if not val:
            return
        current = list(self.trans_combo["values"])
        if val not in current:
            current.append(val)
            self.trans_combo["values"] = current
            cfg = self.app._saved_config
            key = {
                "siliconflow": "custom_models",
                "volcengine": "ark_custom_models",
                "pioneer": "pioneer_custom_models",
            }.get(self.provider_var.get(), "gemini_custom_models")
            custom = cfg.get(key, [])
            if val not in custom:
                custom.append(val)
            cfg[key] = custom
            self._log(f"已添加模型: {val}")

    def _fetch_pioneer_models(self):
        key = self.pioneer_key_var.get().strip()
        if not key:
            self._log("❌ 请先填写 Pioneer API Key")
            return
        self._log("正在获取 Pioneer 模型列表...")

        def _do_fetch():
            models = fetch_pioneer_models(key)
            if models is None:
                self.app.after(0, lambda: self._log("❌ 获取失败，请检查 API Key 或网络连接"))
            elif not models:
                self.app.after(0, lambda: self._log("⚠️ 未找到可用的 LLM 模型"))
            else:
                def _update():
                    cfg = self.app._saved_config
                    models = sorted(models)
                    cfg["pioneer_custom_models"] = models
                    if not cfg.get("pioneer_model") or cfg["pioneer_model"] not in models:
                        cfg["pioneer_model"] = models[0]
                    self.trans_combo["values"] = models
                    self.trans_model_var.set(cfg["pioneer_model"])
                    self.app._do_save_config()
                    self._log(f"✅ 获取到 {len(models)} 个模型（已保存）")
                self.app.after(0, _update)

        threading.Thread(target=_do_fetch, daemon=True).start()

    def _fetch_sf_models_trans(self):
        key = self.sf_key_var.get().strip()
        if not key:
            self._log("❌ 请先填写硅基流动 API Key")
            return
        self._log("正在获取硅基流动模型列表...")
        self._sf_fetch_btn.configure(text="获取中...", state="disabled")

        def _do():
            models = fetch_sf_models(key)

            def _update():
                self._sf_fetch_btn.configure(text="获取模型列表", state="normal")
                if models is None:
                    self._log("❌ 获取失败，请检查 API Key 或网络")
                elif not models:
                    self._log("⚠️ 未找到可用模型")
                else:
                    cfg = self.app._saved_config
                    models = sorted(models)
                    cfg["custom_models"] = models
                    if not cfg.get("translate_model") or cfg["translate_model"] not in models:
                        cfg["translate_model"] = models[0]
                    self.app._do_save_config()
                    self._on_provider_change()
                    self._log(f"✅ 获取到 {len(models)} 个模型（已保存）")

            self.app.after(0, _update)

        threading.Thread(target=_do, daemon=True).start()

    def _fetch_ark_models_trans(self):
        key = self.ark_key_var.get().strip()
        if not key:
            self._log("❌ 请先填写火山引擎 ARK API Key")
            return
        self._log("正在获取 ARK 模型列表（含可用性检测，请稍候）...")
        self._ark_fetch_btn.configure(text="获取中...", state="disabled")

        def _do():
            raw = fetch_ark_models(key)
            if raw is None:
                def _fail():
                    self._ark_fetch_btn.configure(text="获取模型列表", state="normal")
                    self._log("❌ 获取失败，请检查 API Key 或网络")
                self.app.after(0, _fail)
                return
            if not raw:
                def _empty():
                    self._ark_fetch_btn.configure(text="获取模型列表", state="normal")
                    self._log("⚠️ 未找到可用模型")
                self.app.after(0, _empty)
                return

            results = {}
            with ThreadPoolExecutor(max_workers=min(8, len(raw))) as pool:
                futures = {pool.submit(test_ark_model, key, m): m for m in raw}
                for f in as_completed(futures):
                    m = futures[f]
                    results[m] = f.result()

            marked = []
            for m in raw:
                ok = results.get(m)
                marked.append(f"{m} ✓" if ok is True else f"{m} ✗" if ok is False else m)
            marked.sort(key=lambda n: (0 if n.endswith(" ✓") else 1, n))

            def _update():
                self._ark_fetch_btn.configure(text="获取模型列表", state="normal")
                cfg = self.app._saved_config
                cfg["ark_custom_models"] = marked
                avail = sum(1 for n in marked if n.endswith(" ✓"))
                saved = cfg.get("ark_model", "")
                if not saved or saved not in marked:
                    first_ok = next((n for n in marked if n.endswith(" ✓")), marked[0] if marked else "")
                    cfg["ark_model"] = first_ok
                self.app._do_save_config()
                self._on_provider_change()
                self._log(f"✅ 获取到 {len(marked)} 个模型（{avail} 个可用，已保存）")

            self.app.after(0, _update)

        threading.Thread(target=_do, daemon=True).start()

    def _fetch_gemini_models_trans(self):
        keys = self._gemini_key_widget.get_keys()
        if not keys:
            self._log("❌ 请先添加 Gemini API Key")
            return
        self._log("正在获取 Gemini 模型列表...")
        self._gemini_fetch_btn.configure(text="获取中...", state="disabled")

        def _do():
            models = fetch_gemini_models(keys)

            def _update():
                self._gemini_fetch_btn.configure(text="获取模型列表", state="normal")
                if models is None:
                    self._log("❌ 获取失败，请检查 API Key 或网络")
                elif not models:
                    self._log("⚠️ 未找到可用的 Gemini 模型")
                else:
                    cfg = self.app._saved_config
                    models = sorted(models)
                    cfg["gemini_custom_models"] = models
                    if not cfg.get("gemini_model") or cfg["gemini_model"] not in models:
                        cfg["gemini_model"] = models[0]
                    self.app._do_save_config()
                    self._on_provider_change()
                    self._log(f"✅ 获取到 {len(models)} 个 Gemini 模型（已保存）")

            self.app.after(0, _update)

        threading.Thread(target=_do, daemon=True).start()

    # ── File browsing / DnD ───────────────────────────────────────────────────

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

    def _on_save_label_click(self):
        self._save_frame.grid()
        self._save_lbl.configure(text="SRT 保存路径  ▾")
        self._browse_save()
        if not self.save_var.get():
            self._save_frame.grid_remove()
            self._save_lbl.configure(text="SRT 保存路径  ▸ 点击另存为...")

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

    # ── Start / Stop ──────────────────────────────────────────────────────────

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
            "pioneer_api_key": self.pioneer_key_var.get().strip(),
            "translate_model": self.trans_model_var.get().strip() if provider == "siliconflow" else "",
            "ark_model": _strip_model_marker(self.trans_model_var.get().strip()) if provider == "volcengine" else "",
            "gemini_model": self.trans_model_var.get().strip() if provider == "gemini" else "",
            "pioneer_model": self.trans_model_var.get().strip() if provider == "pioneer" else "",
            "batch_size": batch_size,
            "translate_threads": self.threads_var.get().strip(),
            "add_emoji": self.emoji_var.get(),
            "snap_to_30fps": self.snap_30fps_var.get(),
        }

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
                self.app.after(0, lambda: self._handoff_to_burn(video, result_dir))
            self.btn.configure(state="normal", text="▶  开始")
            self.stop_btn.configure(state="disabled", text="⏹  停止")

        threading.Thread(target=task, daemon=True).start()

    def _stop(self):
        if self._stop_event:
            self._stop_event.set()
        self.stop_btn.configure(state="disabled", text="停止中...")

    def _handoff_to_burn(self, video_path: str, result_dir: str):
        """Switch to BurnTab and fill in video + SRT paths after transcription."""
        # Find the best SRT: bilingual > chinese > english, newest first
        srt_found = ""
        for pattern in ("双语_*.srt", "部分双语_*.srt", "中文_*.srt", "部分中文_*.srt", "英文_*.srt"):
            matches = _glob.glob(os.path.join(result_dir, pattern))
            if matches:
                srt_found = max(matches, key=os.path.getmtime)
                break

        # 用现有 SRT 翻译时没有选视频（video_path 为空）：
        # 自动取字幕所在目录中最早的视频作为默认视频地址。
        if not video_path:
            folder = os.path.dirname(srt_found) if srt_found else result_dir
            video_path = _find_oldest_video(folder)
            if video_path:
                self._log(f"🎬 已自动选用目录中最早的视频: {os.path.basename(video_path)}")

        burn_tab = getattr(self.app, "burn_tab", None)
        if burn_tab is None:
            return
        burn_tab.set_files(video_path, srt_found)
        # Switch notebook to the burn tab (index 2)
        try:
            nb = self.app._nb
            nb.select(self.app.tabs.index(burn_tab))
        except Exception:
            pass

    def _open_output_folder(self):
        folder = self._last_output_dir
        if not folder or not os.path.isdir(folder):
            self._log("❌ 输出目录不存在，请先执行一次转写")
            return
        self._log(f"📂 打开: {folder}")
        os.startfile(folder)

    # ── API key accessor (used by App.get_api_key) ────────────────────────────

    def get_api_key(self, provider):
        if provider == "gemini":
            return self._gemini_key_widget.get_keys()
        elif provider == "siliconflow":
            return self.sf_key_var.get().strip()
        elif provider == "pioneer":
            return self.pioneer_key_var.get().strip()
        else:
            return self.ark_key_var.get().strip()

    # ── Config save ───────────────────────────────────────────────────────────

    def get_config(self):
        provider = self.provider_var.get()
        cfg = self.app._saved_config
        sf_custom      = list(cfg.get("custom_models", []))
        ark_custom     = list(cfg.get("ark_custom_models", []))
        gem_custom     = list(cfg.get("gemini_custom_models", []))
        pioneer_custom = list(cfg.get("pioneer_custom_models", []))
        cur = self.trans_model_var.get().strip()
        if provider == "siliconflow":
            if cur and cur not in DEFAULT_MODELS_SF and cur not in sf_custom:
                sf_custom.append(cur)
            translate_model = cur
            ark_model    = cfg.get("ark_model", DEFAULT_MODELS_ARK[0])
            gemini_model = cfg.get("gemini_model", DEFAULT_MODELS_GEMINI[0])
            pioneer_model = cfg.get("pioneer_model", "")
        elif provider == "volcengine":
            clean = _strip_model_marker(cur)
            if clean and clean not in DEFAULT_MODELS_ARK and cur not in ark_custom:
                ark_custom.append(cur)
            translate_model = cfg.get("translate_model", DEFAULT_MODELS_SF[0])
            ark_model    = clean
            gemini_model = cfg.get("gemini_model", DEFAULT_MODELS_GEMINI[0])
            pioneer_model = cfg.get("pioneer_model", "")
        elif provider == "pioneer":
            if cur and cur not in pioneer_custom:
                pioneer_custom.append(cur)
            translate_model = cfg.get("translate_model", DEFAULT_MODELS_SF[0])
            ark_model    = cfg.get("ark_model", DEFAULT_MODELS_ARK[0])
            gemini_model = cfg.get("gemini_model", DEFAULT_MODELS_GEMINI[0])
            pioneer_model = cur
        else:
            if cur and cur not in DEFAULT_MODELS_GEMINI and cur not in gem_custom:
                gem_custom.append(cur)
            translate_model = cfg.get("translate_model", DEFAULT_MODELS_SF[0])
            ark_model    = cfg.get("ark_model", DEFAULT_MODELS_ARK[0])
            gemini_model = cur
            pioneer_model = cfg.get("pioneer_model", "")
        return {
            "model_path":          self.model_var.get().strip(),
            "device":              self.device_var.get(),
            "compute_type":        self.compute_var.get(),
            "language":            self.lang_var.get().strip(),
            "max_chars_en":        self.chars_var.get().strip(),
            "max_chars_zh":        self.chars_zh_var.get().strip(),
            "initial_prompt":      self.prompt_var.get(),
            "provider":            provider,
            "api_key":             self.sf_key_var.get().strip(),
            "translate_model":     translate_model,
            "custom_models":       sf_custom,
            "ark_api_key":         self.ark_key_var.get().strip(),
            "ark_model":           ark_model,
            "ark_custom_models":   ark_custom,
            "gemini_api_keys":     self._gemini_key_widget.get_keys(),
            "gemini_model":        gemini_model,
            "gemini_custom_models": gem_custom,
            "pioneer_api_key":     self.pioneer_key_var.get().strip(),
            "pioneer_model":       pioneer_model,
            "pioneer_custom_models": pioneer_custom,
            "translate_threads":   self.threads_var.get().strip(),
            "add_emoji":           self.emoji_var.get(),
            "output_mode":         self.output_mode_var.get(),
            "batch_size":          self.batch_var.get().strip(),
            "snap_to_30fps":       self.snap_30fps_var.get(),
        }
