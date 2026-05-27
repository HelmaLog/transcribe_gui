"""
backend.py — API calls, transcription, and download logic
"""

import os
import json
import re
import time
from datetime import timedelta

# ── ANSI cleanup ──────────────────────────────────────────────────────────────

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def _clean(msg):
    return _ANSI_RE.sub('', str(msg))


# ── Language display names ────────────────────────────────────────────────────

_LANG_NAMES = {
    'en': '英语', 'zh': '中文', 'zh-Hans': '中文（简体）', 'zh-Hant': '中文（繁体）',
    'ja': '日语', 'ko': '韩语', 'fr': '法语', 'de': '德语', 'es': '西班牙语',
    'ru': '俄语', 'ar': '阿拉伯语', 'pt': '葡萄牙语', 'it': '意大利语',
    'nl': '荷兰语', 'pl': '波兰语', 'tr': '土耳其语', 'vi': '越南语',
    'th': '泰语', 'id': '印尼语', 'hi': '印地语',
}

# ── Default model lists ───────────────────────────────────────────────────────

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

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "model_path": "",
    "device": "cuda",
    "compute_type": "int8",
    "language": "en",
    "max_chars_en": "42",
    "max_chars_zh": "20",
    "initial_prompt": "",
    "provider": "siliconflow",
    "api_key": "",
    "translate_model": DEFAULT_MODELS_SF[0],
    "custom_models": [],
    "ark_api_key": "",
    "ark_model": DEFAULT_MODELS_ARK[0],
    "ark_custom_models": [],
    "gemini_api_keys": [],
    "gemini_model": DEFAULT_MODELS_GEMINI[0],
    "gemini_custom_models": [],
    "gemini_threads": "1",
    "output_mode": "bilingual",
    "add_emoji": True,
    "translate_threads": "3",
    "batch_size": "15",
    "download_dir": "",
    "tweet_provider": "gemini",
    "tweet_model": DEFAULT_MODELS_GEMINI[0],
    "tweet_prompts": [
        {"name": "场景 1", "text": ""},
        {"name": "场景 2", "text": ""},
        {"name": "场景 3", "text": ""},
    ],
    "tweet_font_size": 11,
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                cfg = DEFAULT_CONFIG.copy()
                cfg.update(data)
                # Backward compat: gemini_api_keys was a newline-separated string
                if isinstance(cfg.get("gemini_api_keys"), str):
                    old = cfg["gemini_api_keys"]
                    cfg["gemini_api_keys"] = [k.strip() for k in old.splitlines() if k.strip()]
                # Backward compat: translate_enabled → output_mode
                if "translate_enabled" in cfg and "output_mode" not in cfg:
                    cfg["output_mode"] = "bilingual" if cfg.get("translate_enabled") else "english_only"
                # Backward compat: max_chars → max_chars_en
                if "max_chars" in cfg and "max_chars_en" not in cfg:
                    cfg["max_chars_en"] = cfg["max_chars"]
                # Backward compat: gemini_threads → translate_threads
                if "gemini_threads" in cfg and "translate_threads" not in cfg:
                    cfg["translate_threads"] = cfg["gemini_threads"]
                # Ensure tweet_prompts has exactly 3 items
                prompts = cfg.get("tweet_prompts", [])
                while len(prompts) < 3:
                    prompts.append({"name": f"场景 {len(prompts)+1}", "text": ""})
                cfg["tweet_prompts"] = prompts[:3]
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


# ── Subtitle helpers ──────────────────────────────────────────────────────────

def _flatten_youtube_subs(subs, srt_mod):
    """
    处理 YouTube 滚动字幕格式：提取每条唯一文字的首次出现时刻，
    以下一条新文字首次出现为结束，产生严格不重叠的干净字幕序列。
    """
    if not subs:
        return subs

    seen = set()
    order = []
    first_time = {}

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


def _build_translate_prompt(lines, add_emoji):
    body = chr(10).join(lines)
    common = (
        "- 严格保持行号一一对应，不合并不拆分\n"
        "- 忽略原文中的语气词、填充词和口语停顿（如 um、uh、you know、嗯、啊、呀等），输出简洁流畅的专业中文\n"
        "- 翻译结果不要加任何标点符号（不加逗号、句号、问号、感叹号等）\n"
        "- 只输出翻译结果，不要解释\n"
    )
    if add_emoji:
        return (
            "你是专业字幕翻译，请将以下英文字幕翻译为中文，并为每行选一个最贴切的表情符号。\n\n"
            "要求：\n"
            f"{common}"
            "- 每行输出格式：行号. 中文翻译|表情\n"
            "- 竖线 | 作为分隔符，表情紧跟竖线之后\n\n"
            f"字幕内容：\n{body}"
        )
    else:
        return (
            "你是专业字幕翻译，请将以下英文字幕翻译为中文。\n\n"
            "要求：\n"
            f"{common}"
            "- 每行输出格式：行号. 中文翻译\n\n"
            f"字幕内容：\n{body}"
        )


def _parse_translation(content):
    """解析翻译结果，返回 {行号: (中文文本, 表情)}。
    表情通过 | 分隔符提取，无表情时为空字符串。"""
    parsed = {}
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\.\s*(.+)$", line)
        if m:
            idx = int(m.group(1))
            rest = m.group(2).strip()
            if "|" in rest:
                text, emoji = rest.rsplit("|", 1)
                text = text.strip()
                emoji = emoji.strip()
            else:
                text = rest
                emoji = ""
            parsed[idx] = (text, emoji)
    return parsed


# ── Translation functions ─────────────────────────────────────────────────────

def translate_batch(subs_batch, api_key, model, log, add_emoji=True):
    import urllib.request

    lines = [f"{sub.index}. {sub.content}" for sub in subs_batch]
    prompt = _build_translate_prompt(lines, add_emoji)

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
                time.sleep(3)

    return _parse_translation(content) if content else {}


def translate_batch_ark(subs_batch, api_key, model, log, add_emoji=True):
    import urllib.request

    lines = [f"{sub.index}. {sub.content}" for sub in subs_batch]
    prompt = _build_translate_prompt(lines, add_emoji)

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
                time.sleep(3)

    return _parse_translation(content) if content else {}


def translate_batch_gemini(subs_batch, keys, start_key_idx, model, log, stop_event=None, add_emoji=True):
    import urllib.request
    import urllib.error

    def _wait(seconds):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if stop_event and stop_event.is_set():
                return True
            time.sleep(0.3)
        return False

    lines = [f"{sub.index}. {sub.content}" for sub in subs_batch]
    prompt = _build_translate_prompt(lines, add_emoji)

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2000},
    }).encode("utf-8")

    n = len(keys)
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
        if round_ == 0:
            log("⚠️ 所有 Key 均限速，等待 30s 后重试（点停止可立即中断）...")
            if _wait(30):
                return {}

    log("⚠️ 跳过本批（所有 Key 均已耗尽）")
    return {}


# ── Chat (for tweet assistant tab) ───────────────────────────────────────────

def chat_completion_stream(messages, system_prompt, provider, api_key, model, on_chunk):
    """
    Streaming chat. Calls on_chunk(text) for each text chunk received.
    messages: list of {"role": "user"|"assistant", "content": str}
    api_key: list of keys for Gemini, str for others
    Returns: (full_text, error_str)
    """
    import urllib.request
    import urllib.error

    full_text = ""

    if provider == "gemini":
        keys = api_key if isinstance(api_key, list) else ([api_key] if api_key else [])
        if not keys:
            return None, "未配置 Gemini API Key，请在「转写」标签页中添加"

        contents = [
            {
                "role": "user" if m["role"] == "user" else "model",
                "parts": [{"text": m["content"]}],
            }
            for m in messages
        ]
        payload_dict = {
            "contents": contents,
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096},
        }
        if system_prompt:
            payload_dict["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        payload = json.dumps(payload_dict).encode("utf-8")

        for key in keys:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:streamGenerateContent?alt=sse&key={key}"
            )
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    for raw_line in resp:
                        line = raw_line.decode("utf-8").rstrip("\r\n")
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            text = chunk["candidates"][0]["content"]["parts"][0]["text"]
                            full_text += text
                            on_chunk(text)
                        except (KeyError, IndexError, json.JSONDecodeError):
                            pass
                return full_text, None
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    continue
                try:
                    detail = json.loads(e.read().decode())
                    msg = detail.get("error", {}).get("message", str(e))
                except Exception:
                    msg = str(e)
                return None, f"HTTP {e.code}: {msg}"
            except Exception as e:
                return None, str(e)
        return None, "所有 Gemini Key 均被限速，请稍后重试"

    elif provider in ("siliconflow", "volcengine"):
        if not api_key:
            name = "硅基流动" if provider == "siliconflow" else "火山引擎 ARK"
            return None, f"未配置{name} API Key，请在「转写」标签页中填写"
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.extend(messages)
        payload = json.dumps({
            "model": model,
            "messages": msgs,
            "temperature": 0.7,
            "max_tokens": 4096,
            "stream": True,
        }).encode("utf-8")
        if provider == "siliconflow":
            url = "https://api.siliconflow.cn/v1/chat/completions"
        else:
            url = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").rstrip("\r\n")
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        text = chunk["choices"][0]["delta"].get("content", "")
                        if text:
                            full_text += text
                            on_chunk(text)
                    except (KeyError, IndexError, json.JSONDecodeError):
                        pass
            return full_text, None
        except Exception as e:
            return None, str(e)

    return None, f"未知 provider: {provider}"


# ── Main transcription logic ──────────────────────────────────────────────────

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
        initial_prompt = config["initial_prompt"]
        save_path = config["save_path"]
        output_mode = config.get("output_mode", "bilingual")
        # 向前兼容旧配置
        if "output_mode" not in config and "translate_enabled" in config:
            output_mode = "bilingual" if config["translate_enabled"] else "english_only"
        max_chars_en = int(config.get("max_chars_en", config.get("max_chars", 42)))
        max_chars_zh = int(config.get("max_chars_zh", 20))
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
            raw_keys = config.get("gemini_api_keys", [])
            if isinstance(raw_keys, str):
                gemini_keys = [k.strip() for k in raw_keys.splitlines() if k.strip()]
            else:
                gemini_keys = [k.strip() for k in raw_keys if k.strip()]
            api_key = gemini_keys[0] if gemini_keys else ""
            translate_model = config.get("gemini_model", DEFAULT_MODELS_GEMINI[0])
            translate_fn = None

        batch_size = config["batch_size"]
        use_existing_srt = bool(srt_path and os.path.exists(srt_path))

        if use_existing_srt:
            log(f"使用已有英文字幕: {os.path.basename(srt_path)}")
            with open(srt_path, "r", encoding="utf-8") as f:
                raw_subs = list(srt.parse(f.read()))
            log(f"已加载 {len(raw_subs)} 条字幕，处理 YouTube 滚动字幕格式...")
            raw_subs = _flatten_youtube_subs(raw_subs, srt)
            log(f"展开后 {len(raw_subs)} 条")
            if output_mode == "chinese_only":
                log("中文模式：保留原始段落，翻译后按中文字数切分")
                for i, sub in enumerate(raw_subs, 1):
                    sub.index = i
                split_subs = raw_subs
            else:
                log(f"开始按 {max_chars_en} 字符切分...")
                split_subs = []
                for sub in raw_subs:
                    split_subs.extend(srt_equalizer.split_subtitle(sub, max_chars_en))
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

            log(f"识别完成，共 {len(subs)} 段")
            if output_mode == "chinese_only":
                log("中文模式：保留原始段落，翻译后按中文字数切分")
                for i, sub in enumerate(subs, 1):
                    sub.index = i
                split_subs = subs
            else:
                log(f"开始按 {max_chars_en} 字符切分...")
                split_subs = []
                for sub in subs:
                    split_subs.extend(srt_equalizer.split_subtitle(sub, max_chars_en))
                for i, sub in enumerate(split_subs, 1):
                    sub.index = i
                log(f"切分完成，共 {len(split_subs)} 条")

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

        # ── 保存英文字幕 ──
        # 从视频转写时始终保存；使用现有 SRT 且模式为"只生成英文"时也保存
        if not use_existing_srt or output_mode == "english_only":
            en_path = base_path + "_英文.srt"
            with open(en_path, "w", encoding="utf-8") as f:
                f.write(srt.compose(split_subs))
            log(f"✅ 英文字幕已保存: {en_path}")

        # ── 翻译并保存中文 / 双语字幕 ──
        if output_mode != "english_only":
            if not api_key.strip():
                log("❌ 未填写 API Key，跳过翻译")
            else:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                add_emoji = config.get("add_emoji", True)
                batches = [split_subs[i:i+batch_size] for i in range(0, len(split_subs), batch_size)]
                provider_name = {
                    "siliconflow": "硅基流动",
                    "volcengine": "火山引擎 ARK",
                }.get(provider, "Google Gemini")
                mode_name = "中文" if output_mode == "chinese_only" else "双语"
                emoji_tag = "" if add_emoji else "（无表情）"

                concurrency = max(1, int(config.get("translate_threads", 3)))
                log(f"开始翻译（{mode_name}{emoji_tag}），{provider_name} / {translate_model}，"
                    f"每批 {batch_size} 行，共 {len(batches)} 批，{concurrency} 路并发...")
                translations = {}
                stopped = False

                with ThreadPoolExecutor(max_workers=concurrency) as executor:
                    if provider == "gemini":
                        future_to_bi = {
                            executor.submit(
                                translate_batch_gemini, batch, gemini_keys,
                                bi % len(gemini_keys), translate_model, log, stop_event, add_emoji
                            ): bi
                            for bi, batch in enumerate(batches)
                        }
                    else:
                        future_to_bi = {
                            executor.submit(translate_fn, batch, api_key, translate_model, log, add_emoji): bi
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

                output_subs = []
                if output_mode == "chinese_only":
                    # 中文模式：翻译后按 max_chars_zh 切分，每段都附上相同 emoji
                    for sub in split_subs:
                        if sub.index in translations:
                            zh_text, emoji = translations[sub.index]
                        else:
                            zh_text, emoji = sub.content, ""  # 翻译失败保留原文
                        temp = srt.Subtitle(0, sub.start, sub.end, zh_text)
                        parts = srt_equalizer.split_subtitle(temp, max_chars_zh)
                        if emoji:
                            for part in parts:
                                part.content = part.content.rstrip() + " " + emoji
                        output_subs.extend(parts)
                    for i, sub in enumerate(output_subs, 1):
                        sub.index = i
                else:
                    # 双语模式：英文已提前切分，中文直接合并（emoji 跟在中文末尾）
                    for sub in split_subs:
                        if sub.index in translations:
                            zh_text, emoji = translations[sub.index]
                            zh_line = f"{zh_text} {emoji}" if emoji else zh_text
                            content = f"{zh_line}\n{sub.content}"
                        else:
                            content = sub.content  # 翻译失败保留原文
                        output_subs.append(srt.Subtitle(
                            index=sub.index, start=sub.start, end=sub.end, content=content,
                        ))

                if output_mode == "chinese_only":
                    suffix = "_部分中文" if stopped else "_中文"
                else:
                    suffix = "_部分双语" if stopped else "_双语"
                out_path = base_path + suffix + ".srt"
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(srt.compose(output_subs))
                if stopped:
                    log(f"⏹ 已停止，部分{mode_name}字幕已保存（{len(translations)}/{len(split_subs)} 行）: {out_path}")
                else:
                    log(f"✅ {mode_name}字幕已保存: {out_path}")

        if stop_event and stop_event.is_set():
            log("⏹ 任务已停止")
        else:
            log("🎉 全部完成！")

        return save_dir

    except Exception as e:
        import traceback
        log(f"❌ 错误: {e}")
        log(traceback.format_exc())


# ── Video info query ──────────────────────────────────────────────────────────

def query_video_info(url):
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

    heights = sorted(
        {f['height'] for f in raw.get('formats', [])
         if f.get('vcodec', 'none') != 'none' and f.get('height')},
        reverse=True
    )

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


# ── Download helpers ──────────────────────────────────────────────────────────

def _ffmpeg_convert_vtt(vtt_path, log):
    """用 FFmpeg 将单个 .vtt 转成 .srt，成功后删除原文件。返回是否成功。"""
    import shutil
    import subprocess

    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        log("⚠️ 未找到 ffmpeg，无法将 VTT 转为 SRT")
        return False

    srt_path = os.path.splitext(vtt_path)[0] + '.srt'
    try:
        result = subprocess.run(
            [ffmpeg, '-y', '-i', vtt_path, srt_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and os.path.exists(srt_path):
            os.remove(vtt_path)
            return True
        # FFmpeg 失败时把 stderr 末尾 200 字符记录到日志
        stderr_tail = result.stderr.strip()[-200:] if result.stderr else ''
        log(f"⚠️ VTT→SRT 转换失败: {stderr_tail}")
        return False
    except subprocess.TimeoutExpired:
        log("⚠️ VTT→SRT 转换超时")
        return False
    except Exception as e:
        log(f"⚠️ VTT→SRT 转换异常: {e}")
        return False


# ── Download ──────────────────────────────────────────────────────────────────

def run_download(config, log):
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
    subtitle_only = config.get('subtitle_only', False)

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
    if not subtitle_only and (audio_only or also_audio):
        postprocessors.append({'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'})

    if subtitle_only:
        ydl_opts = {
            'outtmpl': os.path.join(video_dir, '%(title)s.%(ext)s'),
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': subtitle_langs,
            'skip_download': True,
            'postprocessors': postprocessors,
            'progress_hooks': [progress_hook],
            'logger': YDLLogger(),
            'no_warnings': True,
            'ignoreerrors': True,
            'sleep_interval_subtitles': 2,
            'retries': 5,
        }
        log("📄 仅下载字幕（跳过视频）...")
    else:
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
        log("⬇️ 开始下载...")
    if impersonate_target is not None:
        ydl_opts['impersonate'] = impersonate_target
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
    if subtitle_only:
        # 将残留的 .vtt 转为 .srt（skip_download=True 时 postprocessor 不触发）
        vtt_files = [f for f in all_files if os.path.splitext(f)[1].lower() == '.vtt']
        for vtt_file in vtt_files:
            vtt_path = os.path.join(video_dir, vtt_file)
            log(f"🔄 转换字幕格式: {vtt_file} → SRT")
            _ffmpeg_convert_vtt(vtt_path, log)

        # 重新扫描（转换后文件列表已变化）
        all_files = os.listdir(video_dir)
        sub_exts = ('.srt', '.vtt', '.ass', '.ssa')
        sub_files = [f for f in all_files if os.path.splitext(f)[1].lower() in sub_exts]
        if sub_files:
            log(f"🎉 字幕下载完成！共 {len(sub_files)} 个文件，保存至: {video_dir}")
        else:
            log("⚠️ 字幕文件未找到，该视频可能没有可用字幕，请检查日志")
    else:
        video_exts = ('.mp4', '.mkv', '.webm', '.mp3', '.m4a', '.opus')
        media_files = [f for f in all_files if os.path.splitext(f)[1].lower() in video_exts]
        if media_files:
            log(f"🎉 全部完成！文件保存至: {video_dir}")
        else:
            log("⚠️ 媒体文件未找到，字幕或其他文件可能已下载，请检查日志")
    return video_dir
