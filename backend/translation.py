"""
Translation functions: SiliconFlow, VolcEngine ARK, Google Gemini, and streaming chat.
"""

import json
import re
import time

from .config import DEFAULT_MODELS_GEMINI


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


def fetch_pioneer_models(api_key):
    """Return list of model IDs from GET /base-models?task_type=decoder&supports_inference=true.
    Returns None on error, [] on empty."""
    import urllib.request
    try:
        url = "https://api.pioneer.ai/base-models?task_type=decoder&supports_inference=true"
        req = urllib.request.Request(
            url,
            headers={"X-API-Key": api_key},
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, list):
            return [m["id"] for m in data if m.get("id")]
        if isinstance(data, dict):
            items = data.get("models") or data.get("data") or data.get("results") or []
            return [m["id"] for m in items if m.get("id")]
        return []
    except Exception:
        return None


def translate_batch_pioneer(subs_batch, api_key, model, log, add_emoji=True):
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
        "https://api.pioneer.ai/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST"
    )

    content = None
    for attempt in range(3):
        try:
            log("  → 等待 Pioneer 响应...")
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

    elif provider == "pioneer":
        if not api_key:
            return None, "未配置 Pioneer API Key，请在「转写」标签页中填写"
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
        req = urllib.request.Request(
            "https://api.pioneer.ai/v1/chat/completions",
            data=payload,
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
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode()
                return None, f"HTTP {e.code}: {body}"
            except Exception:
                return None, f"HTTP {e.code}: {e.reason}"
        except Exception as e:
            return None, str(e)

    return None, f"未知 provider: {provider}"
