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

DEFAULT_MODELS_SF = [
    "deepseek-ai/DeepSeek-V4-Flash",
    "deepseek-ai/DeepSeek-V3.2",
    "deepseek-ai/DeepSeek-V3.1-Terminus",
    "Pro/deepseek-ai/DeepSeek-V3.1-Terminus",
    "Qwen/Qwen3.6-35B-A3B",
    "Qwen/Qwen3.6-27B",
    "MiniMaxAI/MiniMax-M2.5",
]

DEFAULT_MODELS_ARK = [
    "deepseek-v3-2-251201",
    "deepseek-r1-250528",
    "doubao-pro-32k-241215",
    "doubao-pro-256k-241115",
]

DEFAULT_CONFIG = {
    "model_path": "",
    "device": "cuda",
    "compute_type": "int8",
    "language": "en",
    "max_chars": "42",
    "initial_prompt": "",
    "provider": "siliconflow",
    "api_key": "",
    "translate_model": DEFAULT_MODELS_SF[0],
    "custom_models": [],
    "ark_api_key": "",
    "ark_model": DEFAULT_MODELS_ARK[0],
    "ark_custom_models": [],
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


def _parse_translation(content):
    """解析翻译响应，返回 {index: (zh, emoji)} 字典"""
    parsed = {}
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\.\s*(.+)$", line)
        if m:
            idx = int(m.group(1))
            rest = m.group(2).strip()
            emoji = ""
            text_part = rest
            for i, ch in enumerate(rest):
                if ord(ch) > 127:
                    emoji = ch
                    text_part = rest[i+1:].strip()
                    break
                else:
                    break
            text_part = text_part.rstrip("，。！？；：、…·")
            parsed[idx] = (text_part, emoji)
    return parsed


def translate_batch(subs_batch, api_key, model, log):
    """硅基流动翻译，返回 {index: (zh, emoji)} 字典"""
    import urllib.request

    lines = [f"{sub.index}. {sub.content}" for sub in subs_batch]
    prompt = f"""你是专业字幕翻译，请将以下英文字幕翻译为中文，并为每行选一个最贴切的表情符号。

要求：
- 严格保持行号一一对应，不合并不拆分
- 每行输出格式：行号. 表情 中文翻译
- 只输出翻译结果，不要解释

字幕内容：
{chr(10).join(lines)}"""

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 2000,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.siliconflow.cn/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST"
    )

    content = None
    for attempt in range(3):
        try:
            log("  → 等待 API 响应...")
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                content = result["choices"][0]["message"]["content"].strip()
            log("  ← 收到响应：")
            for line in content.splitlines():
                if line.strip():
                    log(f"    {line}")
            break
        except Exception as e:
            log(f"⚠️ 第{attempt+1}次请求失败: {e}，{'重试中...' if attempt < 2 else '跳过本批'}")
            if attempt < 2:
                import time
                time.sleep(3)

    return _parse_translation(content) if content else {}


def translate_batch_ark(subs_batch, api_key, model, log):
    """火山引擎 ARK 翻译，返回 {index: (zh, emoji)} 字典"""
    import urllib.request

    lines = [f"{sub.index}. {sub.content}" for sub in subs_batch]
    prompt = f"""你是专业字幕翻译，请将以下英文字幕翻译为中文，并为每行选一个最贴切的表情符号。

要求：
- 严格保持行号一一对应，不合并不拆分
- 每行输出格式：行号. 表情 中文翻译
- 只输出翻译结果，不要解释

字幕内容：
{chr(10).join(lines)}"""

    payload = json.dumps({
        "model": model,
        "stream": False,
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}]
            }
        ],
        "temperature": 0.3,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://ark.cn-beijing.volces.com/api/v3/responses",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST"
    )

    content = None
    for attempt in range(3):
        try:
            log("  → 等待 ARK 响应...")
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            for item in result.get("output", []):
                if item.get("type") == "message":
                    for c in item.get("content", []):
                        if c.get("type") == "output_text":
                            content = c["text"].strip()
                            break
                if content:
                    break
            if content:
                log("  ← 收到响应：")
                for line in content.splitlines():
                    if line.strip():
                        log(f"    {line}")
                break
        except Exception as e:
            log(f"⚠️ 第{attempt+1}次请求失败: {e}，{'重试中...' if attempt < 2 else '跳过本批'}")
            if attempt < 2:
                import time
                time.sleep(3)

    return _parse_translation(content) if content else {}


def run_transcribe(config, log):
    try:
        import srt
        from srt_equalizer import srt_equalizer
        from datetime import date
        import re as _re

        video_path = config.get("video_path", "")
        srt_path = config.get("srt_path", "")
        model_path = config.get("model_path", "")
        device = config["device"]
        compute_type = config["compute_type"]
        language = config["language"]
        max_chars = config["max_chars"]
        initial_prompt = config["initial_prompt"]
        save_path = config["save_path"]
        translate_enabled = config["translate_enabled"]
        provider = config.get("provider", "siliconflow")
        api_key = config["api_key"] if provider == "siliconflow" else config.get("ark_api_key", "")
        translate_model = config["translate_model"] if provider == "siliconflow" else config.get("ark_model", "")
        batch_size = config["batch_size"]
        translate_fn = translate_batch if provider == "siliconflow" else translate_batch_ark

        use_existing_srt = bool(srt_path and os.path.exists(srt_path))

        if use_existing_srt:
            log(f"使用已有英文字幕: {os.path.basename(srt_path)}")
            with open(srt_path, "r", encoding="utf-8") as f:
                split_subs = list(srt.parse(f.read()))
            log(f"已加载 {len(split_subs)} 条字幕")

            srt_stem = os.path.splitext(os.path.basename(srt_path))[0]
            if srt_stem.endswith("_英文"):
                srt_stem = srt_stem[:-3]
            short_name = srt_stem
            ref_dir = os.path.dirname(os.path.abspath(srt_path))
        else:
            from faster_whisper import WhisperModel

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

            video_stem = os.path.splitext(os.path.basename(video_path))[0]
            clean_name = _re.sub(r"[^\w\s]", " ", video_stem).strip()
            words = clean_name.split()[:8]
            short_name = " ".join(words) + f"_{date.today().strftime('%Y-%m-%d')}"
            ref_dir = os.path.dirname(video_path)

        if save_path:
            save_dir = os.path.dirname(save_path)
            if not save_dir:
                save_dir = ref_dir
        else:
            save_dir = ref_dir

        base_path = os.path.join(save_dir, short_name)

        if not use_existing_srt:
            en_path = base_path + "_英文.srt"
            with open(en_path, "w", encoding="utf-8") as f:
                f.write(srt.compose(split_subs))
            log(f"✅ 英文字幕已保存: {en_path}")

        if translate_enabled:
            if not api_key.strip():
                log("❌ 未填写API Key，跳过翻译")
            else:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                batches = [split_subs[i:i+batch_size] for i in range(0, len(split_subs), batch_size)]
                provider_name = "硅基流动" if provider == "siliconflow" else "火山引擎 ARK"
                log(f"开始翻译，{provider_name} / {translate_model}，每批 {batch_size} 行，共 {len(batches)} 批，3 路并发...")
                translations = {}
                with ThreadPoolExecutor(max_workers=3) as executor:
                    future_to_bi = {
                        executor.submit(translate_fn, batch, api_key, translate_model, log): bi
                        for bi, batch in enumerate(batches)
                    }
                    for future in as_completed(future_to_bi):
                        bi = future_to_bi[future]
                        result = future.result()
                        translations.update(result)
                        done = sum(1 for f in future_to_bi if f.done())
                        log(f"✅ 第 {bi+1}/{len(batches)} 批完成，共 {len(result)} 行（已完成 {done}/{len(batches)}）")

                bilingual_subs = []
                for sub in split_subs:
                    if sub.index in translations:
                        zh, emoji = translations[sub.index]
                        content = f"{zh} {emoji}\n{sub.content}" if emoji else f"{zh}\n{sub.content}"
                    else:
                        content = sub.content
                    bilingual_subs.append(srt.Subtitle(
                        index=sub.index, start=sub.start, end=sub.end, content=content,
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
        provider = self.provider_var.get()
        sf_custom = self._saved_config.get("custom_models", [])
        ark_custom = self._saved_config.get("ark_custom_models", [])
        cur = self.trans_model_var.get().strip()
        if provider == "siliconflow":
            if cur and cur not in DEFAULT_MODELS_SF and cur not in sf_custom:
                sf_custom.append(cur)
        else:
            if cur and cur not in DEFAULT_MODELS_ARK and cur not in ark_custom:
                ark_custom.append(cur)
        save_config({
            "model_path": self.model_var.get().strip(),
            "device": self.device_var.get(),
            "compute_type": self.compute_var.get(),
            "language": self.lang_var.get().strip(),
            "max_chars": self.chars_var.get().strip(),
            "initial_prompt": self.prompt_var.get(),
            "provider": provider,
            "api_key": self.sf_key_var.get().strip(),
            "translate_model": self.trans_model_var.get().strip() if provider == "siliconflow" else self._saved_config.get("translate_model", DEFAULT_MODELS_SF[0]),
            "custom_models": sf_custom,
            "ark_api_key": self.ark_key_var.get().strip(),
            "ark_model": self.trans_model_var.get().strip() if provider == "volcengine" else self._saved_config.get("ark_model", DEFAULT_MODELS_ARK[0]),
            "ark_custom_models": ark_custom,
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

        # 模型路径  (row=1 → grid rows 2,3)
        self.model_var = tk.StringVar(value=cfg["model_path"])
        self._entry_row(self, 1, "Whisper 模型路径", self.model_var, self._browse_model)

        # 保存路径  (row=2 → grid rows 4,5)
        self.save_var = tk.StringVar()
        self._entry_row(self, 2, "SRT 保存路径（空=与源文件同目录）", self.save_var, self._browse_save, "另存为")

        # 现有英文 SRT（可选）  (row=3 → grid rows 6,7)
        self.srt_var = tk.StringVar()
        srt_entry = self._entry_row(self, 3, "现有英文 SRT（可选，提供后跳过本地识别）", self.srt_var, self._browse_srt)
        if HAS_DND:
            srt_entry.drop_target_register(DND_FILES)
            srt_entry.dnd_bind("<<Drop>>", self._on_drop_srt)

        # 选项行
        f_opts = tk.Frame(self, bg="#1e1e1e")
        f_opts.grid(row=9, column=0, sticky="ew", padx=16, pady=8)

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
        self._lbl(self, "初始提示词（可选）").grid(row=10, column=0, sticky="w", padx=16, pady=(2, 0))
        self.prompt_var = tk.StringVar(value=cfg["initial_prompt"])
        tk.Entry(self, textvariable=self.prompt_var, bg="#2d2d2d", fg="#ffffff",
                 insertbackground="white", relief="flat", font=("Segoe UI", 10), bd=4
                 ).grid(row=11, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)

        # ── 翻译区 ──────────────────────────────
        sep = tk.Frame(self, bg="#333333", height=1)
        sep.grid(row=12, column=0, sticky="ew", padx=16, pady=(8, 0))

        f_trans_title = tk.Frame(self, bg="#1e1e1e")
        f_trans_title.grid(row=13, column=0, sticky="ew", padx=16, pady=(6, 0))
        tk.Label(f_trans_title, text="  翻 译", bg="#1e1e1e", fg="#555555",
                 font=("Segoe UI", 8)).pack(side="left")
        self.translate_var = tk.BooleanVar(value=cfg.get("translate_enabled", False))
        tk.Checkbutton(f_trans_title, text="启用双语翻译", variable=self.translate_var,
                       bg="#1e1e1e", fg="#aaaaaa", selectcolor="#2d2d2d",
                       activebackground="#1e1e1e", font=("Segoe UI", 9)
                       ).pack(side="left", padx=(16, 0))

        # 翻译服务选择
        f_provider = tk.Frame(self, bg="#1e1e1e")
        f_provider.grid(row=14, column=0, sticky="w", padx=16, pady=(4, 0))
        tk.Label(f_provider, text="翻译服务", bg="#1e1e1e", fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left")
        self.provider_var = tk.StringVar(value=cfg.get("provider", "siliconflow"))
        for val, txt in [("siliconflow", "硅基流动"), ("volcengine", "火山引擎 ARK")]:
            tk.Radiobutton(f_provider, text=txt, variable=self.provider_var, value=val,
                           bg="#1e1e1e", fg="#aaaaaa", selectcolor="#2d2d2d",
                           activebackground="#1e1e1e", font=("Segoe UI", 9),
                           command=self._on_provider_change
                           ).pack(side="left", padx=(12, 0))

        # 硅基流动 API Key（row 15, 16）
        self._sf_key_lbl = self._lbl(self, "硅基流动 API Key")
        self._sf_key_lbl.grid(row=15, column=0, sticky="w", padx=16, pady=(6, 0))
        self.sf_key_var = tk.StringVar(value=cfg.get("api_key", ""))
        self._sf_key_entry = tk.Entry(self, textvariable=self.sf_key_var, bg="#2d2d2d", fg="#ffffff",
                                      insertbackground="white", relief="flat",
                                      font=("Segoe UI", 10), bd=4, show="*")
        self._sf_key_entry.grid(row=16, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)

        # 火山引擎 ARK API Key（row 15, 16 — 同位置，按 provider 切换）
        self._ark_key_lbl = self._lbl(self, "火山引擎 ARK API Key")
        self._ark_key_lbl.grid(row=15, column=0, sticky="w", padx=16, pady=(6, 0))
        self.ark_key_var = tk.StringVar(value=cfg.get("ark_api_key", ""))
        self._ark_key_entry = tk.Entry(self, textvariable=self.ark_key_var, bg="#2d2d2d", fg="#ffffff",
                                       insertbackground="white", relief="flat",
                                       font=("Segoe UI", 10), bd=4, show="*")
        self._ark_key_entry.grid(row=16, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)

        # 翻译模型
        self._lbl(self, "翻译模型（可手动输入新模型后回车保存）").grid(
            row=17, column=0, sticky="w", padx=16, pady=(2, 0))
        self.trans_model_var = tk.StringVar()
        self.trans_combo = ttk.Combobox(self, textvariable=self.trans_model_var,
                                         font=("Segoe UI", 10))
        self.trans_combo.grid(row=18, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)
        self.trans_combo.bind("<Return>", self._add_custom_model)

        # 每批行数
        f_batch = tk.Frame(self, bg="#1e1e1e")
        f_batch.grid(row=19, column=0, sticky="w", padx=16, pady=(2, 4))
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
        self.btn.grid(row=20, column=0, pady=12)

        # 日志
        self.rowconfigure(21, weight=1)
        self.log_box = scrolledtext.ScrolledText(self, bg="#111111", fg="#cccccc",
                                                  font=("Consolas", 9), relief="flat",
                                                  state="disabled", height=10)
        self.log_box.grid(row=21, column=0, sticky="nsew", padx=16, pady=(0, 16))

        # 初始化 provider 显示状态
        self._on_provider_change()

    def _on_provider_change(self):
        provider = self.provider_var.get()
        cfg = self._saved_config
        if provider == "siliconflow":
            self._ark_key_lbl.grid_remove()
            self._ark_key_entry.grid_remove()
            self._sf_key_lbl.grid()
            self._sf_key_entry.grid()
            models = DEFAULT_MODELS_SF + cfg.get("custom_models", [])
            saved_model = cfg.get("translate_model", DEFAULT_MODELS_SF[0])
        else:
            self._sf_key_lbl.grid_remove()
            self._sf_key_entry.grid_remove()
            self._ark_key_lbl.grid()
            self._ark_key_entry.grid()
            models = DEFAULT_MODELS_ARK + cfg.get("ark_custom_models", [])
            saved_model = cfg.get("ark_model", DEFAULT_MODELS_ARK[0])
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
            key = "custom_models" if self.provider_var.get() == "siliconflow" else "ark_custom_models"
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
            max_chars = int(self.chars_var.get())
        except ValueError:
            self._log("❌ 字符数请填整数")
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
            "max_chars": max_chars,
            "initial_prompt": self.prompt_var.get(),
            "save_path": self.save_var.get().strip(),
            "translate_enabled": self.translate_var.get(),
            "provider": provider,
            "api_key": self.sf_key_var.get().strip(),
            "ark_api_key": self.ark_key_var.get().strip(),
            "translate_model": self.trans_model_var.get().strip() if provider == "siliconflow" else "",
            "ark_model": self.trans_model_var.get().strip() if provider == "volcengine" else "",
            "batch_size": batch_size,
        }

        self._do_save_config()
        self._is_running = True
        self.btn.configure(state="disabled", text="处理中...")
        label = os.path.basename(srt_file) if srt_file else os.path.basename(video)
        self._log(f"▶ {label}")

        def task():
            run_transcribe(config, self._log)
            self._is_running = False
            self.btn.configure(state="normal", text="▶  开始")

        threading.Thread(target=task, daemon=True).start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
