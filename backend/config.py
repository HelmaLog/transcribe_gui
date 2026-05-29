import os
import json

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

DEFAULT_MODELS_PIONEER = []  # populated at runtime via GET /base-models

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

DEFAULT_CONFIG = {
    "model_path": "",
    "device": "cuda",
    "compute_type": "int8",
    "language": "en",
    "max_chars_en": "42",
    "max_chars_zh": "20",
    "initial_prompt": "The following is a clear transcript with proper punctuation and capitalization.",
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
    "pioneer_api_key": "",
    "pioneer_model": "",
    "pioneer_custom_models": [],
    "output_mode": "bilingual",
    "snap_to_30fps": True,
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
    "tweet_line_spacing": 4,
    "tweet_username": "qinqincr",
    "tweet_sessions": [],
    "tweet_active_session": 0,
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
                # Ensure at least one prompt
                prompts = cfg.get("tweet_prompts", [])
                if not prompts:
                    prompts = [{"name": "场景 1", "text": ""}]
                cfg["tweet_prompts"] = prompts
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
