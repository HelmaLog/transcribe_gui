"""
faster-whisper 字幕生成 + 双语翻译工具
依赖: pip install faster-whisper srt_equalizer srt tkinterdnd2 requests
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
import json
import re
from datetime import timedelta

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_MODELS = [
    "deepseek-ai/DeepSeek-V4-Flash",
    "deepseek-ai/DeepSeek-V3.2",
    "deepseek-ai/DeepSeek-V3.1-Terminus",
    "Pro/deepseek-ai/DeepSeek-V3.1-Terminus",
    "Qwen/Qwen3.6-35B-A3B",
    "Qwen/Qwen3.6-27B",
    "MiniMaxAI/MiniMax-M2.5",
]

DEFAULT_CONFIG = {
    "model_path": "",
    "device": "cuda",
    "compute_type": "int8",
    "language": "en",
    "max_chars": "42",
    "initial_prompt": "",
    "api_key": "",
    "translate_model": DEFAULT_MODELS[0],
    "custom_models": [],
    "translate_enabled": False,
    "batch_size": "15",
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                cfg = DEFAULT_CONFIG.copy()
                cfg.update(data)
                return cfg
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def translate_batch(subs_batch, api_key, model, log):
    """翻译一批字幕，返回{index: (zh, emoji)}字典"""
    import urllib.request
    import urllib.error

    lines = []
    for sub in subs_batch:
        lines.append(f"{sub.index}. {sub.content}")
    text = "\n".join(lines)

    prompt = f"""你是专业字幕翻译，请将以下英文字幕翻译为中文，并为每行选一个最贴切的表情符号。

要求：
- 严格保持行号一一对应，不合并不拆分
- 每行输出格式：行号. 表情 中文翻译
- 只输出翻译结果，不要解释

字幕内容：
{text}"""

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 2000,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.siliconflow.cn/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST"
    )

    content = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                content = result["choices"][0]["message"]["content"].strip()
                break
        except Exception as e:
            log(f"⚠️ 第{attempt+1}次请求失败: {e}，{'重试中...' if attempt < 2 else '跳过本批'}")
            if attempt < 2:
                import time
                time.sleep(3)
    if content is None:
        return {}

    # 解析输出
    parsed = {}
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\.\s*(.+)$", line)
        if m:
            idx = int(m.group(1))
            rest = m.group(2).strip()
            # 提取表情（第一个字符如果是emoji）
            emoji = ""
            text_part = rest
            # 检查开头是否有表情符号
            for i, ch in enumerate(rest):
                if ord(ch) > 127:
                    emoji = ch
                    text_part = rest[i+1:].strip()
                    break
                else:
                    break
            parsed[idx] = (text_part, emoji)

    return parsed


def run_transcribe(config, log):
    try:
        from faster_whisper import WhisperModel
        import srt
        from srt_equalizer import srt_equalizer

        video_path = config["video_path"]
        model_path = config["model_path"]
        device = config["device"]
        compute_type = config["compute_type"]
        language = config["language"]
        max_chars = config["max_chars"]
        initial_prompt = config["initial_prompt"]
        save_path = config["save_path"]
        translate_enabled = config["translate_enabled"]
        api_key = config["api_key"]
        translate_model = config["translate_model"]
        batch_size = config["batch_size"]

        log(f"加载模型: {model_path}")
        model = WhisperModel(model_path, device=device, compute_type=compute_type)
        log("模型加载完成")

        log(f"开始转写: {os.path.basename(video_path)}")
        segments, info = model.transcribe(
            video_path,
            language=language if language else None,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(
                threshold=0.5,
                min_speech_duration_ms=0,
                max_speech_duration_s=3.0,
                min_silence_duration_ms=500,
                speech_pad_ms=400,
            ),
            word_timestamps=True,
            condition_on_previous_text=False,
            suppress_tokens=[-1],
            initial_prompt=initial_prompt if initial_prompt.strip() else None,
        )
        log(f"检测语言: {info.language}，时长: {info.duration:.1f}s")
        log("正在识别，请稍候...")

        subs = []
        for i, seg in enumerate(segments, 1):
            subs.append(srt.Subtitle(
                index=i,
                start=timedelta(seconds=seg.start),
                end=timedelta(seconds=seg.end),
                content=seg.text.strip(),
            ))
            if i % 10 == 0:
                log(f"已识别 {i} 段，进度: {seg.start:.1f}s / {info.duration:.1f}s")

        log(f"识别完成，共 {len(subs)} 段，开始切分...")
        split_subs = []
        for sub in subs:
            split_subs.extend(srt_equalizer.split_subtitle(sub, max_chars))
        for i, sub in enumerate(split_subs, 1):
            sub.index = i

        # 生成简短文件名：取视频名前8个单词 + 日期
        from datetime import date
        import re as _re
        video_stem = os.path.splitext(os.path.basename(video_path))[0]
        clean_name = _re.sub(r"[^\w\s]", " ", video_stem).strip()
        words = clean_name.split()[:8]
        short_name = " ".join(words) + f"_{date.today().strftime('%Y-%m-%d')}"

        # 确定保存目录
        if save_path:
            save_dir = os.path.dirname(save_path)
            if not save_dir:
                save_dir = os.path.dirname(video_path)
        else:
            save_dir = os.path.dirname(video_path)

        base_path = os.path.join(save_dir, short_name)

        # 先保存英文SRT
        en_path = base_path + "_英文.srt"
        with open(en_path, "w", encoding="utf-8") as f:
            f.write(srt.compose(split_subs))
        log(f"✅ 英文字幕已保存: {en_path}")

        # 翻译
        if translate_enabled:
            if not api_key.strip():
                log("❌ 未填写API Key，跳过翻译")
            else:
                log(f"开始翻译，模型: {translate_model}，每批 {batch_size} 行...")
                translations = {}
                batches = [split_subs[i:i+batch_size] for i in range(0, len(split_subs), batch_size)]
                for bi, batch in enumerate(batches):
                    log(f"翻译第 {bi+1}/{len(batches)} 批...")
                    result = translate_batch(batch, api_key, translate_model, log)
                    translations.update(result)
                    if result:
                        log(f"✅ 第 {bi+1} 批翻译成功，共 {len(result)} 行")

                # 生成双语SRT
                bilingual_subs = []
                for sub in split_subs:
                    if sub.index in translations:
                        zh, emoji = translations[sub.index]
                        content = f"{zh} {emoji}\n{sub.content}" if emoji else f"{zh}\n{sub.content}"
                    else:
                        content = sub.content
                    bilingual_subs.append(srt.Subtitle(
                        index=sub.index,
                        start=sub.start,
                        end=sub.end,
                        content=content,
                    ))

                bi_path = base_path + "_双语.srt"
                with open(bi_path, "w", encoding="utf-8") as f:
                    f.write(srt.compose(bilingual_subs))
                log(f"✅ 双语字幕已保存: {bi_path}")

        log("🎉 全部完成！")

    except Exception as e:
        import traceback
        log(f"❌ 错误: {e}")
        log(traceback.format_exc())


class App(TkinterDnD.Tk if HAS_DND else tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("字幕生成 & 双语翻译")
        self.resizable(True, True)
        self.configure(bg="#1e1e1e")
        self.minsize(600, 550)
        self._log_queue = queue.Queue()
        self._is_running = False
        self._saved_config = load_config()
        self._build()
        self._poll_log()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        if self._is_running:
            if not messagebox.askyesno("进行中", "任务还在进行中，确定要退出吗？"):
                return
        self._do_save_config()
        self.destroy()

    def _do_save_config(self):
        custom = self._saved_config.get("custom_models", [])
        cur = self.trans_model_var.get().strip()
        if cur and cur not in DEFAULT_MODELS and cur not in custom:
            custom.append(cur)
        save_config({
            "model_path": self.model_var.get().strip(),
            "device": self.device_var.get(),
            "compute_type": self.compute_var.get(),
            "language": self.lang_var.get().strip(),
            "max_chars": self.chars_var.get().strip(),
            "initial_prompt": self.prompt_var.get(),
            "api_key": self.api_key_var.get().strip(),
            "translate_model": self.trans_model_var.get().strip(),
            "custom_models": custom,
            "translate_enabled": self.translate_var.get(),
            "batch_size": self.batch_var.get().strip(),
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

    def _build(self):
        cfg = self._saved_config
        self.columnconfigure(0, weight=1)

        # ── 转写区 ──────────────────────────────
        tk.Label(self, text="  转 写", bg="#1e1e1e", fg="#555555",
                 font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 0))

        # 拖拽区
        self.video_var = tk.StringVar()
        drop_frame = tk.Frame(self, bg="#252525")
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

        # 模型路径
        self.model_var = tk.StringVar(value=cfg["model_path"])
        self._entry_row(self, 1, "Whisper 模型路径", self.model_var, self._browse_model)

        # 保存路径
        self.save_var = tk.StringVar()
        self._entry_row(self, 2, "SRT 保存路径（空=视频同目录）", self.save_var, self._browse_save, "另存为")

        # 选项行
        f_opts = tk.Frame(self, bg="#1e1e1e")
        f_opts.grid(row=7, column=0, sticky="ew", padx=16, pady=8)

        def olbl(t): tk.Label(f_opts, text=t, bg="#1e1e1e", fg="#888888",
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
        olbl("最大字符数")
        self.chars_var = tk.StringVar(value=cfg["max_chars"])
        oentry(self.chars_var, 5)

        # 初始提示词
        self._lbl(self, "初始提示词（可选）").grid(row=8, column=0, sticky="w", padx=16, pady=(2, 0))
        self.prompt_var = tk.StringVar(value=cfg["initial_prompt"])
        tk.Entry(self, textvariable=self.prompt_var, bg="#2d2d2d", fg="#ffffff",
                 insertbackground="white", relief="flat", font=("Segoe UI", 10), bd=4
                 ).grid(row=9, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)

        # ── 翻译区 ──────────────────────────────
        sep = tk.Frame(self, bg="#333333", height=1)
        sep.grid(row=10, column=0, sticky="ew", padx=16, pady=(8, 0))

        f_trans_title = tk.Frame(self, bg="#1e1e1e")
        f_trans_title.grid(row=11, column=0, sticky="ew", padx=16, pady=(6, 0))
        tk.Label(f_trans_title, text="  翻 译", bg="#1e1e1e", fg="#555555",
                 font=("Segoe UI", 8)).pack(side="left")
        self.translate_var = tk.BooleanVar(value=cfg.get("translate_enabled", False))
        tk.Checkbutton(f_trans_title, text="启用双语翻译", variable=self.translate_var,
                       bg="#1e1e1e", fg="#aaaaaa", selectcolor="#2d2d2d",
                       activebackground="#1e1e1e", font=("Segoe UI", 9)
                       ).pack(side="left", padx=(16, 0))

        # API Key
        self._lbl(self, "硅基流动 API Key").grid(row=12, column=0, sticky="w", padx=16, pady=(6, 0))
        self.api_key_var = tk.StringVar(value=cfg["api_key"])
        tk.Entry(self, textvariable=self.api_key_var, bg="#2d2d2d", fg="#ffffff",
                 insertbackground="white", relief="flat", font=("Segoe UI", 10),
                 bd=4, show="*").grid(row=13, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)

        # 翻译模型选择
        self._lbl(self, "翻译模型（可手动输入新模型后回车保存）").grid(
            row=14, column=0, sticky="w", padx=16, pady=(2, 0))
        all_models = DEFAULT_MODELS + cfg.get("custom_models", [])
        self.trans_model_var = tk.StringVar(value=cfg["translate_model"])
        self.trans_combo = ttk.Combobox(self, textvariable=self.trans_model_var,
                                         values=all_models, font=("Segoe UI", 10))
        self.trans_combo.grid(row=15, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)
        self.trans_combo.bind("<Return>", self._add_custom_model)

        # 每批行数
        f_batch = tk.Frame(self, bg="#1e1e1e")
        f_batch.grid(row=16, column=0, sticky="w", padx=16, pady=(2, 4))
        tk.Label(f_batch, text="每批翻译行数", bg="#1e1e1e", fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left")
        self.batch_var = tk.StringVar(value=cfg.get("batch_size", "15"))
        tk.Entry(f_batch, textvariable=self.batch_var, width=6, bg="#2d2d2d", fg="#ffffff",
                 insertbackground="white", relief="flat", font=("Segoe UI", 10),
                 bd=4).pack(side="left", padx=(8, 0), ipady=3)

        # ── 开始按钮 ─────────────────────────────
        self.btn = tk.Button(self, text="▶  开始", command=self._start,
                             bg="#0078d4", fg="#ffffff", relief="flat",
                             font=("Segoe UI", 11, "bold"), padx=24, pady=8,
                             cursor="hand2", activebackground="#005fa3")
        self.btn.grid(row=17, column=0, pady=12)

        # 日志
        self.rowconfigure(18, weight=1)
        self.log_box = scrolledtext.ScrolledText(self, bg="#111111", fg="#cccccc",
                                                  font=("Consolas", 9), relief="flat",
                                                  state="disabled", height=10)
        self.log_box.grid(row=18, column=0, sticky="nsew", padx=16, pady=(0, 16))

    def _add_custom_model(self, event=None):
        val = self.trans_model_var.get().strip()
        if not val:
            return
        current = list(self.trans_combo["values"])
        if val not in current:
            current.append(val)
            self.trans_combo["values"] = current
            cfg = self._saved_config
            custom = cfg.get("custom_models", [])
            if val not in custom:
                custom.append(val)
            cfg["custom_models"] = custom
            self._log(f"已添加模型: {val}")

    def _on_drop(self, event):
        path = event.data.strip().strip("{}")
        self.video_var.set(path)
        self.drop_label.configure(fg="#aaaaaa")

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

    def _log(self, msg):
        self._log_queue.put(msg)

    def _start(self):
        video = self.video_var.get().strip()
        model = self.model_var.get().strip()
        if not video:
            self._log("❌ 请先选择视频文件")
            return
        if not model:
            self._log("❌ 请先选择模型路径")
            return
        try:
            max_chars = int(self.chars_var.get())
        except ValueError:
            self._log("❌ 字符数请填整数")
            return
        try:
            batch_size = int(self.batch_var.get())
        except ValueError:
            batch_size = 15

        config = {
            "video_path": video,
            "model_path": model,
            "device": self.device_var.get(),
            "compute_type": self.compute_var.get(),
            "language": self.lang_var.get().strip(),
            "max_chars": max_chars,
            "initial_prompt": self.prompt_var.get(),
            "save_path": self.save_var.get().strip(),
            "translate_enabled": self.translate_var.get(),
            "api_key": self.api_key_var.get().strip(),
            "translate_model": self.trans_model_var.get().strip(),
            "batch_size": batch_size,
        }

        self._do_save_config()
        self._is_running = True
        self.btn.configure(state="disabled", text="处理中...")
        self._log(f"▶ {os.path.basename(video)}")

        def task():
            run_transcribe(config, self._log)
            self._is_running = False
            self.btn.configure(state="normal", text="▶  开始")

        threading.Thread(target=task, daemon=True).start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
