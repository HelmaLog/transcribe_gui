"""
Compress tab — video compression with FFmpeg presets.
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import queue

try:
    from tkinterdnd2 import DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

from backend import detect_hw_encoder, compress_probe, compress_video, estimate_output_size
from .base import Tab, BG, HL_GREEN


class CompressTab(Tab):

    def _build(self):
        p = self.frame
        self._cmp_log_queue = queue.Queue()
        self._cmp_stop_event = None
        self._cmp_probe_info = None
        self._cmp_encoder = 'libx264'
        self._cmp_start_time = None

        p.rowconfigure(9, weight=1)

        # ── 标题 ──────────────────────────────────────────────────────────────
        tk.Label(p, text="  视 频 压 缩  （X / Twitter 优化）",
                 bg=BG, fg="#555555",
                 font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 0))

        # ── 拖拽区 ────────────────────────────────────────────────────────────
        self._cmp_video_var = tk.StringVar()
        cmp_drop = tk.Frame(p, bg="#252525")
        cmp_drop.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 4))
        cmp_drop.columnconfigure(0, weight=1)

        self._cmp_drop_label = tk.Label(
            cmp_drop, text="🎬  拖拽视频到此处，或点击浏览",
            bg="#252525", fg="#666666", font=("Segoe UI", 10), pady=12, cursor="hand2",
        )
        self._cmp_drop_label.grid(row=0, column=0, sticky="ew")
        self._cmp_drop_label.bind("<Button-1>", lambda e: self._browse_cmp_video())

        self._cmp_video_entry = tk.Entry(
            cmp_drop, textvariable=self._cmp_video_var,
            bg="#252525", fg="#aaaaaa", insertbackground="white",
            relief="flat", font=("Segoe UI", 9), bd=0, justify="center",
        )
        self._cmp_video_entry.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 4))

        tk.Button(
            cmp_drop, text="浏览文件", command=self._browse_cmp_video,
            bg="#3a3a3a", fg="#cccccc", relief="flat", padx=12, pady=3,
            font=("Segoe UI", 9), cursor="hand2",
        ).grid(row=2, column=0, pady=(0, 10))

        if HAS_DND:
            for w in [cmp_drop, self._cmp_drop_label, self._cmp_video_entry]:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_cmp_drop)

        # ── 预设选择 ──────────────────────────────────────────────────────────
        preset_outer = tk.Frame(p, bg=BG)
        preset_outer.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 0))
        preset_outer.columnconfigure(0, weight=1)

        tk.Label(preset_outer, text="输出预设", bg=BG, fg="#777777",
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 3))

        self._cmp_preset_var = tk.StringVar(value="balanced")

        _presets = [
            ("fast",     "🚀 极速上传",  "720p · 30fps · 2.5 Mbps · 文件最小"),
            ("balanced", "⚖️ 均衡模式",  "1080p · 30fps · 8 Mbps · 推荐"),
            ("quality",  "💎 最高画质",  "原始分辨率 · 原始帧率 · 15 Mbps"),
            ("custom",   "🔧 自定义",    "手动设置分辨率 / 码率（帧率随源）"),
        ]
        for i, (key, label, desc) in enumerate(_presets):
            row_bg = "#252525"
            rf = tk.Frame(preset_outer, bg=row_bg, cursor="hand2")
            rf.grid(row=i + 1, column=0, sticky="ew", pady=1)
            rf.columnconfigure(2, weight=1)
            rf.bind("<Button-1>", lambda e, k=key: self._select_cmp_preset(k))

            rb = tk.Radiobutton(
                rf, variable=self._cmp_preset_var, value=key,
                bg=row_bg, fg="#cccccc", activebackground=row_bg,
                selectcolor=BG, bd=0, highlightthickness=0,
                command=self._on_cmp_preset_change,
            )
            rb.grid(row=0, column=0, padx=(10, 2), pady=6)

            tk.Label(rf, text=label, bg=row_bg, fg="#dddddd",
                     font=("Segoe UI", 10, "bold"), cursor="hand2",
                     ).grid(row=0, column=1, sticky="w", padx=4)
            tk.Label(rf, text=desc, bg=row_bg, fg="#666666",
                     font=("Segoe UI", 9), cursor="hand2",
                     ).grid(row=0, column=2, sticky="e", padx=(0, 14))

            for w in [rf]:
                w.bind("<Button-1>", lambda e, k=key: self._select_cmp_preset(k))

        # ── 自定义设置（默认隐藏）────────────────────────────────────────────
        self._cmp_custom_frame = tk.Frame(p, bg="#1c2535")
        self._cmp_custom_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=0)
        self._cmp_custom_frame.columnconfigure(0, weight=1)

        cust = tk.Frame(self._cmp_custom_frame, bg="#1c2535")
        cust.pack(fill="x", padx=12, pady=8)

        tk.Label(cust, text="分辨率:", bg="#1c2535", fg="#aaaaaa",
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._cmp_res_var = tk.StringVar(value="1920x1080")
        res_cb = ttk.Combobox(cust, textvariable=self._cmp_res_var, width=13,
                               values=["3840x2160", "2560x1440", "1920x1080",
                                       "1280x720", "854x480", "原始（不缩放）"])
        res_cb.grid(row=0, column=1, padx=(0, 18))
        res_cb.bind("<<ComboboxSelected>>", lambda e: self._update_cmp_estimate())

        tk.Label(cust, text="视频码率:", bg="#1c2535", fg="#aaaaaa",
                 font=("Segoe UI", 9)).grid(row=0, column=2, sticky="w", padx=(0, 6))
        self._cmp_vbitrate_var = tk.StringVar(value="8000")
        tk.Entry(cust, textvariable=self._cmp_vbitrate_var, width=7,
                 bg="#253050", fg="#cccccc", insertbackground="white",
                 relief="flat", font=("Segoe UI", 9)).grid(row=0, column=3)
        tk.Label(cust, text="kbps", bg="#1c2535", fg="#555555",
                 font=("Segoe UI", 9)).grid(row=0, column=4, padx=(3, 18))

        tk.Label(cust, text="音频码率:", bg="#1c2535", fg="#aaaaaa",
                 font=("Segoe UI", 9)).grid(row=0, column=5, sticky="w", padx=(0, 6))
        self._cmp_abitrate_var = tk.StringVar(value="192")
        tk.Entry(cust, textvariable=self._cmp_abitrate_var, width=6,
                 bg="#253050", fg="#cccccc", insertbackground="white",
                 relief="flat", font=("Segoe UI", 9)).grid(row=0, column=6)
        tk.Label(cust, text="kbps", bg="#1c2535", fg="#555555",
                 font=("Segoe UI", 9)).grid(row=0, column=7, padx=(3, 0))

        self._cmp_vbitrate_var.trace_add("write", lambda *_: self._update_cmp_estimate())
        self._cmp_abitrate_var.trace_add("write", lambda *_: self._update_cmp_estimate())

        # 初始隐藏
        self._cmp_custom_frame.grid_remove()

        # ── 信息栏 ────────────────────────────────────────────────────────────
        info_f = tk.Frame(p, bg=BG)
        info_f.grid(row=4, column=0, sticky="ew", padx=16, pady=(6, 2))
        info_f.columnconfigure(0, weight=1)

        self._cmp_info_var = tk.StringVar(value="请先选择视频文件")
        tk.Label(info_f, textvariable=self._cmp_info_var, bg=BG, fg="#888888",
                 font=("Segoe UI", 9), anchor="w").grid(row=0, column=0, sticky="ew")

        self._cmp_enc_var = tk.StringVar(value="编码器检测中…")
        tk.Label(info_f, textvariable=self._cmp_enc_var, bg=BG, fg="#555555",
                 font=("Segoe UI", 8), anchor="w").grid(row=1, column=0, sticky="ew")

        # ── 输出路径 ──────────────────────────────────────────────────────────
        out_f = tk.Frame(p, bg=BG)
        out_f.grid(row=5, column=0, sticky="ew", padx=16, pady=(2, 4))
        out_f.columnconfigure(1, weight=1)

        tk.Label(out_f, text="输出路径:", bg=BG, fg="#888888",
                 font=("Segoe UI", 9)).grid(row=0, column=0, padx=(0, 8), sticky="w")
        self._cmp_out_var = tk.StringVar()
        tk.Entry(out_f, textvariable=self._cmp_out_var,
                 bg="#252525", fg="#aaaaaa", insertbackground="white",
                 relief="flat", font=("Segoe UI", 9),
                 ).grid(row=0, column=1, sticky="ew")
        tk.Button(out_f, text="另存为", command=self._browse_cmp_output,
                  bg="#3a3a3a", fg="#cccccc", relief="flat", padx=10, pady=2,
                  font=("Segoe UI", 9), cursor="hand2",
                  ).grid(row=0, column=2, padx=(8, 0))

        # ── 进度条 ────────────────────────────────────────────────────────────
        prog_f = tk.Frame(p, bg=BG)
        prog_f.grid(row=6, column=0, sticky="ew", padx=16, pady=(4, 2))
        prog_f.columnconfigure(0, weight=1)

        self._cmp_progress = ttk.Progressbar(prog_f, length=300, mode="determinate")
        self._cmp_progress.grid(row=0, column=0, sticky="ew")
        self._cmp_prog_label = tk.Label(prog_f, text="", bg=BG, fg="#888888",
                                        font=("Segoe UI", 9), width=18, anchor="w")
        self._cmp_prog_label.grid(row=0, column=1, padx=(10, 0))

        # ── 操作按钮 ──────────────────────────────────────────────────────────
        btn_f = tk.Frame(p, bg=BG)
        btn_f.grid(row=7, column=0, pady=(4, 6))

        self._cmp_start_btn = tk.Button(
            btn_f, text="▶  开始压缩",
            command=self._start_compress,
            bg="#1e4a1e", fg="#aaddaa", activebackground="#2a6a2a",
            relief="flat", padx=22, pady=7,
            font=("Segoe UI", 10, "bold"), cursor="hand2",
        )
        self._cmp_start_btn.pack(side="left", padx=8)

        self._cmp_cancel_btn = tk.Button(
            btn_f, text="取  消",
            command=self._cancel_compress,
            bg="#3a3a3a", fg="#888888", activebackground="#4a4a4a",
            relief="flat", padx=18, pady=7,
            font=("Segoe UI", 10), cursor="hand2", state="disabled",
        )
        self._cmp_cancel_btn.pack(side="left", padx=8)

        # ── 日志 ──────────────────────────────────────────────────────────────
        self._cmp_log_box = scrolledtext.ScrolledText(
            p, bg="#141414", fg="#cccccc", insertbackground="white",
            relief="flat", font=("Consolas", 9), state="disabled", wrap="word",
        )
        self._cmp_log_box.grid(row=9, column=0, sticky="nsew", padx=16, pady=(0, 12))
        # 关键行（完成/✅）高亮成亮绿
        self._cmp_log_box.tag_configure("hl", foreground=HL_GREEN)

        # 初始化状态，后台检测编码器
        self._detect_cmp_encoder()
        self.app.after(100, self._poll_cmp_log)

    # ── Log polling ───────────────────────────────────────────────────────────

    def _poll_cmp_log(self):
        try:
            while True:
                msg = self._cmp_log_queue.get_nowait()
                self._cmp_log_box.configure(state="normal")
                is_hl = ("✅" in msg or "完成" in msg)
                self._cmp_log_box.insert("end", msg + "\n", ("hl",) if is_hl else ())
                self._cmp_log_box.see("end")
                self._cmp_log_box.configure(state="disabled")
        except queue.Empty:
            pass
        self.app.after(100, self._poll_cmp_log)

    def _cmp_log(self, msg):
        self._cmp_log_queue.put(msg)

    # ── Encoder detection ─────────────────────────────────────────────────────

    def _detect_cmp_encoder(self):
        def task():
            enc, desc = detect_hw_encoder()
            self._cmp_encoder = enc
            self.app.after(0, lambda: self._cmp_enc_var.set(f"编码器: {desc}"))
            self._cmp_log(f"🔍 编码器检测: {desc}")
        threading.Thread(target=task, daemon=True).start()

    # ── Video browsing / DnD ──────────────────────────────────────────────────

    def _browse_cmp_video(self):
        path = filedialog.askopenfilename(
            title="选择视频文件",
            filetypes=[
                ("视频文件", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.ts *.flv"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self._load_cmp_video(path)

    def _on_cmp_drop(self, event):
        path = event.data.strip().strip("{}")
        if path:
            self._load_cmp_video(path)

    def _load_cmp_video(self, path):
        self._cmp_video_var.set(path)
        self._cmp_out_var.set(self._cmp_suggest_output(path))
        self._cmp_info_var.set("⏳ 读取视频信息…")
        self._cmp_probe_info = None

        def probe_task():
            info, err = compress_probe(path)
            if err:
                self._cmp_log(f"⚠️ 读取视频失败: {err}")
                self.app.after(0, lambda: self._cmp_info_var.set(f"❌ {err}"))
            else:
                self._cmp_probe_info = info
                self.app.after(0, self._update_cmp_estimate)

        threading.Thread(target=probe_task, daemon=True).start()

    def _cmp_suggest_output(self, input_path):
        from backend import naming
        kind_map = {
            "fast": "压缩Fast", "balanced": "压缩",
            "quality": "压缩HQ", "custom": "压缩Custom",
        }
        kind = kind_map.get(self._cmp_preset_var.get(), "压缩")
        # 统一命名：时间戳_类型_短名.mp4（防覆盖在压缩执行时再做）
        return os.path.join(os.path.dirname(input_path),
                            naming.make_name(kind, input_path, ".mp4"))

    # ── Preset selection ──────────────────────────────────────────────────────

    def _select_cmp_preset(self, key):
        self._cmp_preset_var.set(key)
        self._on_cmp_preset_change()

    def _on_cmp_preset_change(self):
        preset = self._cmp_preset_var.get()
        if preset == "custom":
            self._cmp_custom_frame.grid()
        else:
            self._cmp_custom_frame.grid_remove()
        path = self._cmp_video_var.get()
        if path:
            self._cmp_out_var.set(self._cmp_suggest_output(path))
        self._update_cmp_estimate()

    def _update_cmp_estimate(self):
        info = self._cmp_probe_info
        if not info:
            return

        preset = self._cmp_preset_var.get()
        _params = {
            "fast":     (2500,  128),
            "balanced": (8000,  192),
            "quality":  (15000, 320),
        }
        if preset == "custom":
            try:
                vbr = max(100, int(self._cmp_vbitrate_var.get()))
            except ValueError:
                vbr = 8000
            try:
                abr = max(32, int(self._cmp_abitrate_var.get()))
            except ValueError:
                abr = 192
        else:
            vbr, abr = _params.get(preset, (8000, 192))

        duration   = info['duration']
        orig_bytes = info['size_bytes']
        est_bytes  = estimate_output_size(duration, vbr, abr)
        orig_mb    = orig_bytes / 1024 / 1024
        est_mb     = est_bytes  / 1024 / 1024
        dur_str    = f"{int(duration // 60)}:{int(duration % 60):02d}"

        if orig_bytes > 0:
            save_pct = max(0, int((1 - est_bytes / orig_bytes) * 100))
            size_str = f"原始: {orig_mb:.1f} MB  →  预计: {est_mb:.1f} MB（节省 {save_pct}%）"
        else:
            size_str = f"预计输出: {est_mb:.1f} MB"

        res_str = ""
        if info.get('width') and info.get('height'):
            fps_str = f"  {info['fps']} fps" if info.get('fps') else ""
            res_str = f"  |  {info['width']}×{info['height']}{fps_str}"

        # X 限制提示
        x_warn = ""
        if est_mb > 512:
            x_warn = "  ⚠️ 超出 X 普通用户 512MB 限制"
        elif est_mb > 2048:
            x_warn = "  ❌ 超出 X Premium 2GB 限制"

        self._cmp_info_var.set(
            f"时长: {dur_str}{res_str}  |  {size_str}{x_warn}"
        )

    # ── Output path browser ───────────────────────────────────────────────────

    def _browse_cmp_output(self):
        path = filedialog.asksaveasfilename(
            title="输出文件保存为",
            defaultextension=".mp4",
            filetypes=[("MP4 视频", "*.mp4"), ("所有文件", "*.*")],
        )
        if path:
            self._cmp_out_var.set(path)

    # ── Start / cancel compress ───────────────────────────────────────────────

    def _start_compress(self):
        if self._is_running:
            return

        input_path  = self._cmp_video_var.get().strip()
        output_path = self._cmp_out_var.get().strip()

        if not input_path:
            self._cmp_log("❌ 请先选择视频文件")
            return
        if not os.path.exists(input_path):
            self._cmp_log(f"❌ 文件不存在: {input_path}")
            return
        if not output_path:
            self._cmp_log("❌ 请设置输出路径")
            return
        # 防覆盖：同名已存在则自动追加 _2/_3，绝不冲掉上次的产物
        from backend import naming
        _resolved = naming.unique_path(os.path.dirname(output_path),
                                       os.path.basename(output_path))
        if _resolved != output_path:
            self._cmp_log(f"⚠ 同名文件已存在，改存为: {os.path.basename(_resolved)}")
        output_path = _resolved

        # 构造压缩参数
        preset = self._cmp_preset_var.get()
        # fps_cap：输出帧率上限（None=随源）。极速/均衡封顶 30fps，把 60fps 源的
        # 编码量砍半；最高画质/自定义保持源帧率。与 burn tab 同一套策略。
        _preset_map = {
            "fast":     {"scale": (1280, 720),  "vbitrate": 2500,  "abitrate": 128, "fps_cap": 30},
            "balanced": {"scale": (1920, 1080), "vbitrate": 8000,  "abitrate": 192, "fps_cap": 30},
            "quality":  {"scale": None,          "vbitrate": 15000, "abitrate": 320, "fps_cap": None},
        }
        if preset == "custom":
            try:
                vbr = max(100, int(self._cmp_vbitrate_var.get()))
            except ValueError:
                self._cmp_log("❌ 视频码率格式不正确，请输入整数"); return
            try:
                abr = max(32, int(self._cmp_abitrate_var.get()))
            except ValueError:
                self._cmp_log("❌ 音频码率格式不正确，请输入整数"); return
            res = self._cmp_res_var.get()
            if res.startswith("原始"):
                scale = None
            else:
                try:
                    w, h = res.split("x")
                    scale = (int(w), int(h))
                except Exception:
                    scale = None
            params = {"scale": scale, "vbitrate": vbr, "abitrate": abr, "fps_cap": None}
        else:
            params = _preset_map.get(preset, _preset_map["balanced"])

        probe    = self._cmp_probe_info
        duration = probe['duration'] if probe else 0
        encoder  = self._cmp_encoder or 'libx264'

        # 输出帧率：源帧率封顶到 fps_cap（仅在源更高时才降，不给低帧率源插帧）。
        # 拿不到源帧率时保守不降（fps=None）。
        src_fps = (probe or {}).get('fps')
        fps_cap = params.get("fps_cap")
        out_fps = fps_cap if (fps_cap and src_fps and src_fps > fps_cap + 0.01) else None

        config = {
            "input_path":  input_path,
            "output_path": output_path,
            "encoder":     encoder,
            "vbitrate":    params["vbitrate"],
            "abitrate":    params["abitrate"],
            "scale":       params["scale"],
            "fps":         out_fps,
            "duration":    duration,
        }

        # 启动压缩
        import time as _time
        self._is_running     = True
        self._cmp_stop_event = threading.Event()
        self._cmp_start_time = _time.time()
        self._cmp_progress["value"] = 0
        self._cmp_prog_label.configure(text="")
        self._cmp_start_btn.configure(state="disabled", text="压缩中…")
        self._cmp_cancel_btn.configure(state="normal", fg="#dddddd")

        def progress_cb(pct):
            self.app.after(0, lambda p=pct: self._update_cmp_progress(p))

        def task():
            result = compress_video(config, self._cmp_log, progress_cb, self._cmp_stop_event)

            def finish():
                self._is_running = False
                self._cmp_start_btn.configure(state="normal", text="▶  开始压缩")
                self._cmp_cancel_btn.configure(state="disabled", fg="#888888")
                if result:
                    self._cmp_progress["value"] = 100
                    self._cmp_prog_label.configure(text="✅ 完成", fg=HL_GREEN)
                    try:
                        os.startfile(os.path.dirname(os.path.abspath(result)))
                    except Exception:
                        pass
                else:
                    self._cmp_prog_label.configure(text="❌ 失败")

            self.app.after(0, finish)

        threading.Thread(target=task, daemon=True).start()

    def _update_cmp_progress(self, pct):
        import time as _time
        self._cmp_progress["value"] = pct
        if pct > 0.5 and self._cmp_start_time:
            elapsed = _time.time() - self._cmp_start_time
            eta     = elapsed / pct * (100 - pct)
            m, s    = int(eta // 60), int(eta % 60)
            self._cmp_prog_label.configure(text=f"{pct:.1f}%  剩余 {m}:{s:02d}", fg="#888888")
        else:
            self._cmp_prog_label.configure(text=f"{pct:.1f}%", fg="#888888")

    def _cancel_compress(self):
        if self._cmp_stop_event:
            self._cmp_stop_event.set()
        self._cmp_cancel_btn.configure(state="disabled", fg="#888888")

    # ── Config save ───────────────────────────────────────────────────────────

    def get_config(self):
        return {}
