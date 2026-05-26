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

DEFAULT_MODELS_GEMINI = [
    "gemini-2.5-flash",
    "gemini-3.5-flash",
    "gemini-2.5-pro",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
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
    "gemini_api_keys": "",
    "gemini_model": DEFAULT_MODELS_GEMINI[0],
    "gemini_custom_models": [],
    "gemini_threads": "1",
    "translate_enabled": False,
    "batch_size": "15",
    "download_dir": "",
}

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

def _clean(msg):
    return _ANSI_RE.sub('', str(msg))

# Common language code → display name
_LANG_NAMES = {
    'en': '英语', 'zh': '中文', 'zh-Hans': '中文（简体）', 'zh-Hant': '中文（繁体）',
    'ja': '日语', 'ko': '韩语', 'fr': '法语', 'de': '德语', 'es': '西班牙语',
    'ru': '俄语', 'ar': '阿拉伯语', 'pt': '葡萄牙语', 'it': '意大利语',
    'nl': '荷兰语', 'pl': '波兰语', 'tr': '土耳其语', 'vi': '越南语',
    'th': '泰语', 'id': '印尼语', 'hi': '印地语',
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


def _flatten_youtube_subs(subs, srt_mod):
    """
    处理 YouTube 滚动字幕格式：
    提取每条唯一文字的首次出现时刻，以下一条新文字首次出现为结束，
    产生严格不重叠的干净字幕序列。
    """
    if not subs:
        return subs

    seen = set()
    order = []       # 按首次出现顺序排列的唯一行文本
    first_time = {}  # text → 首次出现的 start 时间

    for sub in subs:
        for ln in sub.content.splitlines():
            ln = ln.strip()
            if ln and ln not in seen:
                seen.add(ln)
                first_time[ln] = sub.start
                order.append(ln)

    result = []
    for i, text in enumerate(order):
        start = first_time[text]
        end = first_time[order[i + 1]] if i + 1 < len(order) else subs[-1].end
        if end > start:
            result.append(srt_mod.Subtitle(len(result) + 1, start, end, text))

    return result


def _parse_translation(content):
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


def translate_batch_gemini(subs_batch, keys, start_key_idx, model, log, stop_event=None):
    import urllib.request
    import urllib.error
    import time

    def _wait(seconds):
        """可被 stop_event 打断的 sleep，返回 True 表示被停止。"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if stop_event and stop_event.is_set():
                return True
            time.sleep(0.3)
        return False

    lines = [f"{sub.index}. {sub.content}" for sub in subs_batch]
    prompt = f"""你是专业字幕翻译，请将以下英文字幕翻译为中文，并为每行选一个最贴切的表情符号。

要求：
- 严格保持行号一一对应，不合并不拆分
- 每行输出格式：行号. 表情 中文翻译
- 只输出翻译结果，不要解释

字幕内容：
{chr(10).join(lines)}"""

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2000},
    }).encode("utf-8")

    n = len(keys)
    # 最多尝试：遍历所有 key 2 轮，每轮全部 429 才等待一次
    for round_ in range(2):
        for ki in range(n):
            if stop_event and stop_event.is_set():
                return {}
            key = keys[(start_key_idx + ki) % n]
            key_label = f"Key{(start_key_idx + ki) % n + 1}"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            try:
                log(f"  → [{key_label}] 等待 Gemini 响应...")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                content = result["candidates"][0]["content"]["parts"][0]["text"].strip()
                log("  ← 收到响应：")
                for line in content.splitlines():
                    if line.strip():
                        log(f"    {line}")
                return _parse_translation(content)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    log(f"  ⚠️ [{key_label}] 限速 (429)，切换下一个 Key...")
                else:
                    log(f"  ⚠️ [{key_label}] 请求失败: {e}")
            except Exception as e:
                log(f"  ⚠️ [{key_label}] 请求失败: {e}")
        # 所有 key 本轮均失败，等待后重试
        if round_ == 0:
            log("⚠️ 所有 Key 均限速，等待 30s 后重试（点停止可立即中断）...")
            if _wait(30):
                return {}

    log("⚠️ 跳过本批（所有 Key 均已耗尽）")
    return {}


def run_transcribe(config, log, stop_event=None):
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
        if provider == "siliconflow":
            api_key = config["api_key"]
            translate_model = config["translate_model"]
            translate_fn = translate_batch
        elif provider == "volcengine":
            api_key = config.get("ark_api_key", "")
            translate_model = config.get("ark_model", "")
            translate_fn = translate_batch_ark
        else:
            raw_keys = config.get("gemini_api_keys", "")
            gemini_keys = [k.strip() for k in raw_keys.splitlines() if k.strip()]
            api_key = gemini_keys[0] if gemini_keys else ""
            translate_model = config.get("gemini_model", DEFAULT_MODELS_GEMINI[0])
            translate_fn = None  # Gemini uses its own call path below
        batch_size = config["batch_size"]

        use_existing_srt = bool(srt_path and os.path.exists(srt_path))

        if use_existing_srt:
            log(f"使用已有英文字幕: {os.path.basename(srt_path)}")
            with open(srt_path, "r", encoding="utf-8") as f:
                raw_subs = list(srt.parse(f.read()))
            log(f"已加载 {len(raw_subs)} 条字幕，处理 YouTube 滚动字幕格式...")
            raw_subs = _flatten_youtube_subs(raw_subs, srt)
            log(f"展开后 {len(raw_subs)} 条，开始按 {max_chars} 字符切分...")
            split_subs = []
            for sub in raw_subs:
                split_subs.extend(srt_equalizer.split_subtitle(sub, max_chars))
            for i, sub in enumerate(split_subs, 1):
                sub.index = i
            log(f"切分完成，共 {len(split_subs)} 条")

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
                provider_name = {"siliconflow": "硅基流动", "volcengine": "火山引擎 ARK"}.get(provider, "Google Gemini")

                if provider == "gemini":
                    concurrency = max(1, int(config.get("gemini_threads", 1)))
                else:
                    concurrency = 3

                log(f"开始翻译，{provider_name} / {translate_model}，每批 {batch_size} 行，共 {len(batches)} 批，{concurrency} 路并发...")
                translations = {}
                stopped = False

                with ThreadPoolExecutor(max_workers=concurrency) as executor:
                    if provider == "gemini":
                        future_to_bi = {
                            executor.submit(translate_batch_gemini, batch, gemini_keys, bi % len(gemini_keys), translate_model, log, stop_event): bi
                            for bi, batch in enumerate(batches)
                        }
                    else:
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
                        if stop_event and stop_event.is_set():
                            for f in future_to_bi:
                                f.cancel()
                            stopped = True
                            break

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

                suffix = "_部分双语" if stopped else "_双语"
                bi_path = base_path + suffix + ".srt"
                with open(bi_path, "w", encoding="utf-8") as f:
                    f.write(srt.compose(bilingual_subs))
                if stopped:
                    log(f"⏹ 已停止，部分双语字幕已保存（{len(translations)}/{len(split_subs)} 行）: {bi_path}")
                else:
                    log(f"✅ 双语字幕已保存: {bi_path}")

        if stop_event and stop_event.is_set():
            log("⏹ 任务已停止")
        else:
            log("🎉 全部完成！")

    except Exception as e:
        import traceback
        log(f"❌ 错误: {e}")
        log(traceback.format_exc())


def query_video_info(url):
    """
    Returns (info_dict, error_str). Runs in background thread.
    info_dict contains: title, uploader, duration, heights, manual_subs, auto_subs, has_ffmpeg
    """
    import shutil
    try:
        import yt_dlp
    except ImportError:
        return None, "未安装 yt-dlp，请运行: pip install yt-dlp"

    has_ffmpeg = bool(shutil.which('ffmpeg'))
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
            raw = ydl.extract_info(url, download=False)
    except Exception as e:
        return None, _clean(str(e))

    title = raw.get('title', 'video')
    duration = raw.get('duration', 0) or 0
    uploader = raw.get('uploader', '')

    # Collect available video heights (from formats that carry video)
    heights = sorted(
        {f['height'] for f in raw.get('formats', [])
         if f.get('vcodec', 'none') != 'none' and f.get('height')},
        reverse=True
    )

    # Subtitles: {lang_code: {'name': str, 'type': 'manual'|'auto'}}
    subs = {}
    for lang, fmts in raw.get('subtitles', {}).items():
        name = _LANG_NAMES.get(lang, lang)
        if fmts and isinstance(fmts, list) and fmts[0].get('name'):
            name = fmts[0]['name']
        subs[lang] = {'name': name, 'type': 'manual'}
    for lang, fmts in raw.get('automatic_captions', {}).items():
        if lang not in subs:
            name = _LANG_NAMES.get(lang, lang)
            if fmts and isinstance(fmts, list) and fmts[0].get('name'):
                name = fmts[0]['name']
            subs[lang] = {'name': name, 'type': 'auto'}

    return {
        'title': title,
        'uploader': uploader,
        'duration': duration,
        'heights': heights,
        'subs': subs,
        'has_ffmpeg': has_ffmpeg,
    }, None


def run_download(config, log):
    """
    config keys: url, save_dir, title, format_str, subtitle_langs, audio_only
    subtitle_langs: list of lang codes, empty = no subtitles
    """
    try:
        import yt_dlp
    except ImportError:
        log("❌ 未安装 yt-dlp，请运行: pip install yt-dlp")
        return None

    url = config['url']
    save_dir = config['save_dir']
    title = config['title']
    format_str = config['format_str']
    subtitle_langs = config.get('subtitle_langs', [])
    audio_only = config.get('audio_only', False)
    also_audio = config.get('also_audio', False)

    try:
        os.makedirs(save_dir, exist_ok=True)
    except Exception as e:
        log(f"❌ 无法创建目录: {e}")
        return None

    safe_title = re.sub(r'[\\/:*?"<>|]', '_', title).strip()[:40]
    video_dir = os.path.join(save_dir, safe_title)
    os.makedirs(video_dir, exist_ok=True)
    log(f"📁 保存至: {video_dir}")

    impersonate_target = None
    if subtitle_langs:
        try:
            import curl_cffi  # noqa: F401
            try:
                from yt_dlp.networking.impersonate import ImpersonateTarget
                impersonate_target = ImpersonateTarget('chrome')
                log("🔒 已启用浏览器伪装（curl_cffi Chrome），有助于下载中文字幕")
            except Exception:
                pass
        except ImportError:
            log("⚠️  未安装 curl_cffi，中文字幕可能因 429 失败")
            log("💡  一次性修复: pip install curl_cffi")

    class YDLLogger:
        def debug(self, msg):
            # 过滤 [debug] 和 [download] 进度行（进度由 progress_hook 统一处理）
            if msg.startswith('[debug]') or msg.startswith('[download]'):
                return
            log(_clean(msg))
        def info(self, msg):
            log(_clean(msg))
        def warning(self, msg):
            cleaned = _clean(msg)
            log(f"⚠️ {cleaned}")
            if 'impersonation' in cleaned:
                log("💡 提示: 安装 curl_cffi 可修复中文字幕 429 问题 → pip install curl_cffi")
        def error(self, msg):
            log(f"❌ {_clean(msg)}")

    last_pct = [""]

    def progress_hook(d):
        if d['status'] == 'downloading':
            pct = _clean(d.get('_percent_str', '')).strip()
            if pct and pct != last_pct[0]:
                last_pct[0] = pct
                speed = _clean(d.get('_speed_str', '')).strip()
                eta = _clean(d.get('_eta_str', '')).strip()
                log(f"⬇️ {pct}  速度: {speed}  剩余: {eta}")
        elif d['status'] == 'finished':
            log(f"✅ 完成: {os.path.basename(d.get('filename', ''))}")

    postprocessors = []
    if subtitle_langs:
        postprocessors.append({'key': 'FFmpegSubtitlesConvertor', 'format': 'srt'})
    if audio_only or also_audio:
        postprocessors.append({'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'})

    ydl_opts = {
        'format': format_str,
        'outtmpl': os.path.join(video_dir, '%(title)s.%(ext)s'),
        'writesubtitles': bool(subtitle_langs),
        'writeautomaticsub': bool(subtitle_langs),
        'subtitleslangs': subtitle_langs if subtitle_langs else [],
        'merge_output_format': 'mp4' if not audio_only else None,
        'keepvideo': also_audio,
        'postprocessors': postprocessors,
        'progress_hooks': [progress_hook],
        'logger': YDLLogger(),
        'no_warnings': True,
        'ignoreerrors': True,
        'sleep_interval_subtitles': 2,
        'retries': 5,
    }
    if impersonate_target is not None:
        ydl_opts['impersonate'] = impersonate_target

    log("⬇️ 开始下载...")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        err_str = _clean(str(e))
        if 'Impersonate target' in str(e) and 'impersonate' in ydl_opts:
            log("⚠️ 伪装目标不可用，改用普通模式重试...")
            ydl_opts.pop('impersonate')
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
            except Exception as e2:
                log(f"❌ 下载失败: {_clean(str(e2))}")
                import traceback
                log(traceback.format_exc())
                return video_dir
        else:
            log(f"❌ 下载失败: {err_str}")
            import traceback
            log(traceback.format_exc())
            return video_dir

    all_files = os.listdir(video_dir)
    video_exts = ('.mp4', '.mkv', '.webm', '.mp3', '.m4a', '.opus')
    media_files = [f for f in all_files if os.path.splitext(f)[1].lower() in video_exts]
    if media_files:
        log(f"🎉 全部完成！文件保存至: {video_dir}")
    else:
        log("⚠️ 媒体文件未找到，字幕或其他文件可能已下载，请检查日志")
    return video_dir


# ── 格式选择弹窗 ──────────────────────────────────────────────────────────────

class FormatDialog(tk.Toplevel):
    """
    Modal dialog that shows available resolutions and subtitles for a YouTube video.
    Calls on_confirm(result_dict) when user confirms, destroys itself on cancel.
    """

    # Preferred language order for subtitle display
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
        # Center over parent, enforce max size before showing
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

        # ── 视频信息 ──
        title = info['title']
        short_title = title if len(title) <= 60 else title[:57] + "..."
        tk.Label(p, text=short_title, bg="#1e1e1e", fg="#ffffff",
                 font=("Segoe UI", 10, "bold"), wraplength=480, justify="left",
                 anchor="w").grid(row=0, column=0, columnspan=2,
                                  sticky="w", padx=16, pady=(14, 0))

        dur = info['duration']
        meta = f"👤 {info['uploader']}   ⏱ {int(dur//60)}:{int(dur%60):02d}" if info['uploader'] else f"⏱ {int(dur//60)}:{int(dur%60):02d}"
        self._lbl(p, meta, "#666666", 9).grid(row=1, column=0, columnspan=2,
                                               sticky="w", padx=16, pady=(2, 4))

        # ── ffmpeg 警告 ──
        if not info['has_ffmpeg']:
            tk.Label(p, text="⚠️  未检测到 ffmpeg — 高分辨率视频需要 ffmpeg 才能合并音视频流。\n    建议安装 ffmpeg 后重试，或选择「仅音频」。",
                     bg="#2a1f00", fg="#ffcc44", font=("Segoe UI", 9),
                     justify="left", anchor="w", padx=10, pady=6
                     ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 0))

        # ── 下载类型 ──
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

        # ── 分辨率 ──
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

        # ── 字幕 ──
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

        # Build subtitle checkboxes in a scrollable-ish frame (canvas + frame for many subs)
        sub_outer = tk.Frame(p, bg="#1e1e1e")
        sub_outer.grid(row=12, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 4))

        self._sub_vars = {}  # lang_code -> BooleanVar

        # Only show Chinese and English subtitle options
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

        # ── 按钮 ──
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

        if not want_video and not want_audio:
            messagebox.showwarning("请选择", "请至少勾选「视频」或「音频」之一", parent=self)
            return

        height_val = self._height_var.get()
        subtitle_langs = [lang for lang, var in self._sub_vars.items() if var.get()]

        if not want_video:
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
        }
        self.destroy()
        self._on_confirm(result)

    def _cancel(self):
        self.destroy()
        self._on_cancel()


# ── 主窗口 ────────────────────────────────────────────────────────────────────

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
        self._last_dl_dir = ""
        self._stop_event = None
        self._saved_config = load_config()
        self._apply_style()
        self._build()
        self._poll_log()
        self._poll_dl_log()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _apply_style(self):
        style = ttk.Style(self)
        style.theme_use('default')
        style.configure('TNotebook', background='#1e1e1e', borderwidth=0)
        style.configure('TNotebook.Tab', background='#2a2a2a', foreground='#888888',
                        padding=[16, 6], font=('Segoe UI', 9))
        style.map('TNotebook.Tab',
                  background=[('selected', '#1e1e1e')],
                  foreground=[('selected', '#ffffff')])

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
            "gemini_api_keys": self._gemini_key_text.get("1.0", "end").strip(),
            "gemini_model": self.trans_model_var.get().strip() if provider == "gemini" else self._saved_config.get("gemini_model", DEFAULT_MODELS_GEMINI[0]),
            "gemini_custom_models": gemini_custom,
            "gemini_threads": self.gemini_threads_var.get().strip(),
            "translate_enabled": self.translate_var.get(),
            "batch_size": self.batch_var.get().strip(),
            "download_dir": self.dl_dir_var.get().strip(),
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

    def _build(self):
        cfg = self._saved_config
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        nb = ttk.Notebook(self)
        nb.grid(row=0, column=0, sticky="nsew")

        tab_t = tk.Frame(nb, bg="#1e1e1e")
        tab_d = tk.Frame(nb, bg="#1e1e1e")
        tab_t.columnconfigure(0, weight=1)
        tab_d.columnconfigure(0, weight=1)
        nb.add(tab_t, text="  转 写  ")
        nb.add(tab_d, text="  下 载  ")

        self._build_transcribe(tab_t, cfg)
        self._build_download(tab_d, cfg)

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

        self._lbl(p, "初始提示词（可选）").grid(row=10, column=0, sticky="w", padx=16, pady=(2, 0))
        self.prompt_var = tk.StringVar(value=cfg["initial_prompt"])
        tk.Entry(p, textvariable=self.prompt_var, bg="#2d2d2d", fg="#ffffff",
                 insertbackground="white", relief="flat", font=("Segoe UI", 10), bd=4
                 ).grid(row=11, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)

        sep = tk.Frame(p, bg="#333333", height=1)
        sep.grid(row=12, column=0, sticky="ew", padx=16, pady=(8, 0))

        f_trans_title = tk.Frame(p, bg="#1e1e1e")
        f_trans_title.grid(row=13, column=0, sticky="ew", padx=16, pady=(6, 0))
        tk.Label(f_trans_title, text="  翻 译", bg="#1e1e1e", fg="#555555",
                 font=("Segoe UI", 8)).pack(side="left")
        self.translate_var = tk.BooleanVar(value=cfg.get("translate_enabled", False))
        tk.Checkbutton(f_trans_title, text="启用双语翻译", variable=self.translate_var,
                       bg="#1e1e1e", fg="#aaaaaa", selectcolor="#2d2d2d",
                       activebackground="#1e1e1e", font=("Segoe UI", 9)
                       ).pack(side="left", padx=(16, 0))

        f_provider = tk.Frame(p, bg="#1e1e1e")
        f_provider.grid(row=14, column=0, sticky="w", padx=16, pady=(4, 0))
        tk.Label(f_provider, text="翻译服务", bg="#1e1e1e", fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left")
        self.provider_var = tk.StringVar(value=cfg.get("provider", "siliconflow"))
        for val, txt in [("siliconflow", "硅基流动"), ("volcengine", "火山引擎 ARK"), ("gemini", "Google Gemini")]:
            tk.Radiobutton(f_provider, text=txt, variable=self.provider_var, value=val,
                           bg="#1e1e1e", fg="#aaaaaa", selectcolor="#2d2d2d",
                           activebackground="#1e1e1e", font=("Segoe UI", 9),
                           command=self._on_provider_change
                           ).pack(side="left", padx=(12, 0))

        self._sf_key_lbl = self._lbl(p, "硅基流动 API Key")
        self._sf_key_lbl.grid(row=15, column=0, sticky="w", padx=16, pady=(6, 0))
        self.sf_key_var = tk.StringVar(value=cfg.get("api_key", ""))
        self._sf_key_entry = tk.Entry(p, textvariable=self.sf_key_var, bg="#2d2d2d", fg="#ffffff",
                                      insertbackground="white", relief="flat",
                                      font=("Segoe UI", 10), bd=4, show="*")
        self._sf_key_entry.grid(row=16, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)

        self._ark_key_lbl = self._lbl(p, "火山引擎 ARK API Key")
        self._ark_key_lbl.grid(row=15, column=0, sticky="w", padx=16, pady=(6, 0))
        self.ark_key_var = tk.StringVar(value=cfg.get("ark_api_key", ""))
        self._ark_key_entry = tk.Entry(p, textvariable=self.ark_key_var, bg="#2d2d2d", fg="#ffffff",
                                       insertbackground="white", relief="flat",
                                       font=("Segoe UI", 10), bd=4, show="*")
        self._ark_key_entry.grid(row=16, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)

        self._gemini_key_lbl = self._lbl(p, "Google Gemini API Key（每行一个，多个 Key 自动轮询）")
        self._gemini_key_lbl.grid(row=15, column=0, sticky="w", padx=16, pady=(6, 0))
        # Container: Text + threads row, all in column=0
        self._gemini_key_frame = tk.Frame(p, bg="#1e1e1e")
        self._gemini_key_frame.grid(row=16, column=0, sticky="ew", padx=16, pady=(2, 4))
        self._gemini_key_frame.columnconfigure(0, weight=1)
        self._gemini_key_text = tk.Text(self._gemini_key_frame, bg="#2d2d2d", fg="#ffffff",
                                        insertbackground="white", relief="flat",
                                        font=("Segoe UI", 10), bd=4, height=3)
        self._gemini_key_text.insert("1.0", cfg.get("gemini_api_keys", ""))
        self._gemini_key_text.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Label(self._gemini_key_frame, text="线程数", bg="#1e1e1e", fg="#888888",
                 font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.gemini_threads_var = tk.StringVar(value=cfg.get("gemini_threads", "1"))
        tk.Entry(self._gemini_key_frame, textvariable=self.gemini_threads_var, width=5,
                 bg="#2d2d2d", fg="#ffffff", insertbackground="white", relief="flat",
                 font=("Segoe UI", 10), bd=4).grid(row=1, column=1, sticky="w",
                                                    padx=(8, 0), pady=(5, 0), ipady=3)

        self._lbl(p, "翻译模型（可手动输入新模型后回车保存）").grid(
            row=17, column=0, sticky="w", padx=16, pady=(2, 0))
        self.trans_model_var = tk.StringVar()
        self.trans_combo = ttk.Combobox(p, textvariable=self.trans_model_var,
                                         font=("Segoe UI", 10))
        self.trans_combo.grid(row=18, column=0, sticky="ew", padx=16, pady=(2, 4), ipady=4)
        self.trans_combo.bind("<Return>", self._add_custom_model)

        f_batch = tk.Frame(p, bg="#1e1e1e")
        f_batch.grid(row=19, column=0, sticky="w", padx=16, pady=(2, 4))
        tk.Label(f_batch, text="每批翻译行数", bg="#1e1e1e", fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left")
        self.batch_var = tk.StringVar(value=cfg.get("batch_size", "15"))
        tk.Entry(f_batch, textvariable=self.batch_var, width=6, bg="#2d2d2d", fg="#ffffff",
                 insertbackground="white", relief="flat", font=("Segoe UI", 10),
                 bd=4).pack(side="left", padx=(8, 0), ipady=3)

        f_btn_row = tk.Frame(p, bg="#1e1e1e")
        f_btn_row.grid(row=20, column=0, pady=12)
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

        p.rowconfigure(21, weight=1)
        self.log_box = scrolledtext.ScrolledText(p, bg="#111111", fg="#cccccc",
                                                  font=("Consolas", 9), relief="flat",
                                                  state="disabled", height=10)
        self.log_box.grid(row=21, column=0, sticky="nsew", padx=16, pady=(0, 16))

        self._on_provider_change()

    def _build_download(self, p, cfg):
        p.columnconfigure(0, weight=1)
        p.rowconfigure(6, weight=1)

        # ── 区域标题 ──
        tk.Label(p, text="  下 载", bg="#1e1e1e", fg="#555555",
                 font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w",
                                            padx=16, pady=(12, 0))

        # ── 保存目录（含浏览按钮） ──
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

        # ── 视频链接 ──
        self._lbl(p, "视频链接（支持 YouTube、X/Twitter、B站等）").grid(
            row=3, column=0, sticky="w", padx=16, pady=(10, 0))
        self.dl_url_var = tk.StringVar()
        tk.Entry(p, textvariable=self.dl_url_var, bg="#2d2d2d", fg="#ffffff",
                 insertbackground="white", relief="flat",
                 font=("Segoe UI", 10), bd=4).grid(
            row=4, column=0, sticky="ew", padx=16, pady=(2, 0), ipady=4)

        # ── 操作按钮 ──
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

        # ── 日志 ──
        self.dl_log_box = scrolledtext.ScrolledText(
            p, bg="#111111", fg="#cccccc", font=("Consolas", 9),
            relief="flat", state="disabled", height=10)
        self.dl_log_box.grid(row=6, column=0, sticky="nsew", padx=16, pady=(0, 16))

    # ── 转写标签页事件 ──────────────────────────────────────────────────────────

    def _on_provider_change(self):
        provider = self.provider_var.get()
        cfg = self._saved_config
        self._sf_key_lbl.grid_remove()
        self._sf_key_entry.grid_remove()
        self._ark_key_lbl.grid_remove()
        self._ark_key_entry.grid_remove()
        self._gemini_key_lbl.grid_remove()
        self._gemini_key_frame.grid_remove()
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
            self._gemini_key_frame.grid()
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
            key = {"siliconflow": "custom_models", "volcengine": "ark_custom_models"}.get(self.provider_var.get(), "gemini_custom_models")
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
            "gemini_api_keys": self._gemini_key_text.get("1.0", "end").strip(),
            "gemini_threads": self.gemini_threads_var.get().strip(),
            "translate_model": self.trans_model_var.get().strip() if provider == "siliconflow" else "",
            "ark_model": self.trans_model_var.get().strip() if provider == "volcengine" else "",
            "gemini_model": self.trans_model_var.get().strip() if provider == "gemini" else "",
            "batch_size": batch_size,
        }

        self._do_save_config()
        self._is_running = True
        self._stop_event = threading.Event()
        self.btn.configure(state="disabled", text="处理中...")
        self.stop_btn.configure(state="normal")
        label = os.path.basename(srt_file) if srt_file else os.path.basename(video)
        self._log(f"▶ {label}")

        stop_event = self._stop_event

        def task():
            run_transcribe(config, self._log, stop_event)
            self._is_running = False
            self.btn.configure(state="normal", text="▶  开始")
            self.stop_btn.configure(state="disabled", text="⏹  停止")

        threading.Thread(target=task, daemon=True).start()

    def _stop(self):
        if self._stop_event:
            self._stop_event.set()
        self.stop_btn.configure(state="disabled", text="停止中...")

    # ── 下载标签页事件 ──────────────────────────────────────────────────────────

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
        # Pre-set the video subfolder path so the button works immediately
        _safe = re.sub(r'[\\/:*?"<>|]', '_', title).strip()[:40]
        self._last_dl_dir = os.path.normpath(os.path.join(save_dir, _safe))

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
        }

        def task():
            result = run_download(dl_config, self._dl_log)
            if result:
                self._last_dl_dir = result
            self._dl_is_running = False
            self.dl_btn.configure(state="normal", text="🔍  查询并选择格式")

        threading.Thread(target=task, daemon=True).start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
