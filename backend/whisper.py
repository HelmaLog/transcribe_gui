"""
Whisper transcription logic.
"""

import os
import re
from datetime import timedelta

_FRAME_MS = 1000.0 / 30

# 字幕清洗：去掉不符合人类阅读习惯的特殊符号（转写/格式残留），例如：
#   尖括号 < > （含 << >>，常见转写噪声/伪引号）、音符 ♪♫♬♩、
#   markdown 残留 * `、竖线 |、波浪号/脱字符 ~ ^。
# 保留所有正常中英文标点：, . ! ? ; : ' " - … 及 ，。！？、；：""''《》（）「」 等，
# 不动括号 ()[]{}（属正常表达），不删带词括注（如 (laughs)）以免误伤。
_CAPTION_JUNK_RE = re.compile(r"[<>♪♫♬♩*`|~^]+")


def _clean_caption(text: str) -> str:
    """按人类阅读习惯清洗单条字幕文字；多行分别处理，丢弃清洗后变空的行。"""
    if not text:
        return text
    out_lines = []
    for line in text.splitlines():
        line = _CAPTION_JUNK_RE.sub("", line)
        # 折叠清洗后残留的多余空白，并去掉首尾空格
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        if line:
            out_lines.append(line)
    return "\n".join(out_lines)


def _snap_ms(ms):
    return round(round(ms / _FRAME_MS) * _FRAME_MS)


def _snap_srt_to_30fps(subs):
    """将字幕时间码对齐到 30fps 帧边界，解决 CapCut 渲染闪烁问题。"""
    for sub in subs:
        s = int(sub.start.total_seconds() * 1000)
        e = int(sub.end.total_seconds() * 1000)
        ns = _snap_ms(s)
        ne = _snap_ms(e)
        if ne <= ns:
            ne = ns + round(_FRAME_MS)
        sub.start = timedelta(milliseconds=ns)
        sub.end = timedelta(milliseconds=ne)
    return subs


def _fix_overlaps(subs) -> int:
    """
    确保相邻字幕不重叠：将前一条的 end 裁剪到下一条的 start。
    对齐到帧边界时相邻字幕可能被推到同一帧，此函数作为最终保险。
    返回修复的条数。
    """
    count = 0
    min_dur = timedelta(milliseconds=round(_FRAME_MS))
    for i in range(len(subs) - 1):
        if subs[i].end > subs[i + 1].start:
            new_end = subs[i + 1].start
            # 保证本条至少维持一帧时长
            subs[i].end = max(new_end, subs[i].start + min_dur)
            count += 1
    return count


def _close_gaps(subs, max_gap_s: float = 1.0) -> int:
    """
    闭合相邻字幕之间的小空隙：将每条的 end 延伸到下一条的 start，
    使字幕在时间上连续。这样烧录到视频后，字幕框不会在换气/句末这类
    自然小停顿处消失再出现，避免画面闪烁/跳动。

    仅向后延伸（不修改 start），且只在确有空隙时填补；与 _fix_overlaps
    配合后，被闭合的相邻字幕满足 end == 下一条 start，时间轴严丝合缝。

    max_gap_s：只闭合不超过该秒数的空隙。超过阈值的属于真实停顿
    （说话明显停了），保留空白让字幕自然消失更符合观感，硬留反而滞留违和。
    返回填补的空隙数。
    """
    count = 0
    max_gap = timedelta(seconds=max_gap_s)
    for i in range(len(subs) - 1):
        nxt_start = subs[i + 1].start
        gap = nxt_start - subs[i].end
        if timedelta(0) < gap <= max_gap:
            subs[i].end = nxt_start
            count += 1
    return count


from . import naming
from .config import DEFAULT_MODELS_GEMINI
from .translation import (
    translate_batch, translate_batch_ark, translate_batch_gemini, translate_batch_pioneer,
)


def _fmt_duration(sec: float) -> str:
    """把秒数格式化为「X分Y秒」/「Y秒」，用于展示字幕生成耗时。"""
    sec = max(0, int(round(sec)))
    if sec >= 60:
        return f"{sec // 60}分{sec % 60}秒"
    return f"{sec}秒"


def _is_youtube_rolling(subs) -> bool:
    """
    YouTube 自动生成的滚动字幕有两种常见特征，命中其一即判定为滚动格式：
      1) 时间范围大量重叠（相邻字幕 start < 上一条 end）；
      2) 相邻字幕文字大量重复（上一行被带入下一条逐行往下滚），
         此类时间戳通常首尾相接、严格不重叠，仅靠特征 1 无法识别。
    取前 30 对相邻字幕做采样，任一特征命中过半即认为是滚动格式。
    普通 SRT 相邻字幕文字互不相同、时间也不重叠，两项都不会过半，避免误判。
    """
    if len(subs) < 5:
        return False
    sample = min(len(subs) - 1, 30)

    overlaps = sum(1 for i in range(1, sample + 1) if subs[i].start < subs[i - 1].end)
    if overlaps / sample > 0.5:
        return True

    def _lines(sub):
        return {ln.strip() for ln in sub.content.splitlines() if ln.strip()}

    repeats = 0
    for i in range(1, sample + 1):
        if _lines(subs[i]) & _lines(subs[i - 1]):
            repeats += 1
    return repeats / sample > 0.5


def _flatten_youtube_subs(subs, srt_mod):
    """
    处理 YouTube 滚动字幕格式：提取每条唯一文字的首次出现时刻，
    以下一条新文字首次出现为结束，产生严格不重叠的干净字幕序列。
    普通 SRT 文件不经过此函数——相同文字会被去重导致字幕丢失。
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


def run_transcribe(config, log, stop_event=None):
    try:
        import srt
        import time
        from srt_equalizer import srt_equalizer

        t_start = time.monotonic()
        video_path = config.get("video_path", "")
        srt_path = config.get("srt_path", "")
        model_path = config.get("model_path", "")
        device = config["device"]
        compute_type = config["compute_type"]
        # 束宽（beam_size）：解码时每步保留的候选路径数。1=贪心、最快；越大越准、越慢。
        beam_size = int(config.get("beam_size", 1) or 1)
        # CPU 线程数：0 = 由 CTranslate2 自动按物理核心数选取（通常即最优）。
        cpu_threads = int(config.get("cpu_threads", 0) or 0)
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
        elif provider == "pioneer":
            api_key = config.get("pioneer_api_key", "")
            translate_model = config.get("pioneer_model", "")
            translate_fn = translate_batch_pioneer
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
        primary_srt = ""   # 实际保存、用于烧录的首选字幕（翻译结果优先于英文）
        # 字幕来源/生成方式描述，用于日志与界面展示
        if use_existing_srt:
            gen_method = "已有英文字幕"
        else:
            _thr = f"{cpu_threads}线程" if cpu_threads else "线程自动"
            gen_method = (f"Whisper 转写 · {device} · {compute_type} · "
                          f"束宽{beam_size} · {_thr}")

        if use_existing_srt:
            log(f"使用已有英文字幕: {os.path.basename(srt_path)}")
            with open(srt_path, "r", encoding="utf-8") as f:
                raw_subs = list(srt.parse(f.read()))
            log(f"已加载 {len(raw_subs)} 条字幕")
            if _is_youtube_rolling(raw_subs):
                log("检测到 YouTube 滚动字幕格式，正在展开...")
                raw_subs = _flatten_youtube_subs(raw_subs, srt)
                log(f"展开后 {len(raw_subs)} 条")
            else:
                log("普通 SRT 格式，跳过滚动字幕处理")
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

            name_source = srt_path   # 短标题取自字幕文件名（naming 会剥离旧标记）
            ref_dir = os.path.dirname(os.path.abspath(srt_path))
        else:
            from faster_whisper import WhisperModel

            log(f"加载模型: {model_path}")
            model = WhisperModel(model_path, device=device,
                                 compute_type=compute_type, cpu_threads=cpu_threads)
            if device == "cpu":
                import os as _os
                _logical = _os.cpu_count() or 0
                _used = cpu_threads if cpu_threads else "自动（由引擎按物理核心选取）"
                log(f"模型加载完成 · CPU 线程={_used}（本机逻辑核心 {_logical}）")
            else:
                log("模型加载完成")

            log(f"开始转写: {os.path.basename(video_path)}（{gen_method}）")
            segments, info = model.transcribe(
                video_path,
                language=language if language else None,
                beam_size=beam_size,
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

            name_source = video_path   # 短标题取自视频文件名
            ref_dir = os.path.dirname(video_path)

        if save_path:
            save_dir = os.path.dirname(save_path)
            if not save_dir:
                save_dir = ref_dir
        else:
            save_dir = ref_dir

        # 同一次任务里英文/双语/中文字幕共用同一时间戳，便于在文件夹里成组排列
        from datetime import datetime as _datetime
        name_ts = _datetime.now()

        # ── 清洗特殊符号 ──
        # 必须放在「闭合空隙」之前：先清洗、并丢弃整条只剩符号（清洗后变空）的字幕，
        # 再让闭合空隙跨过这些被删条目把时间轴接上，避免留下空白/不连续字幕。
        _cleaned = 0
        for sub in split_subs:
            new_c = _clean_caption(sub.content)
            if new_c != sub.content:
                _cleaned += 1
            sub.content = new_c
        _before = len(split_subs)
        split_subs = [s for s in split_subs if s.content.strip()]
        _dropped = _before - len(split_subs)
        for i, sub in enumerate(split_subs, 1):
            sub.index = i
        if _cleaned:
            log(f"🧹 已清理 {_cleaned} 条字幕中的特殊符号"
                + (f"，并删除 {_dropped} 条仅含符号的空字幕" if _dropped else ""))

        snap = config.get("snap_to_30fps", False)
        if snap:
            _snap_srt_to_30fps(split_subs)
        n = _fix_overlaps(split_subs)
        if n:
            log(f"⚠️ 修复了英文字幕中 {n} 处时间戳重叠")
        gap_max = float(config.get("close_gap_max_s", 1.0))
        g = _close_gaps(split_subs, gap_max)
        if g:
            log(f"🔗 已闭合英文字幕中 {g} 处≤{gap_max:g}s 空隙，字幕连续无闪烁")

        # ── 保存英文字幕 ──
        # 从视频转写时始终保存；使用现有 SRT 且模式为"只生成英文"时也保存
        if not use_existing_srt or output_mode == "english_only":
            en_path = naming.make_path(save_dir, "英文", name_source, ".srt", name_ts)
            with open(en_path, "w", encoding="utf-8") as f:
                f.write(srt.compose(split_subs))
            primary_srt = en_path
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
                            zh_text = _clean_caption(zh_text)  # 译文也清洗特殊符号
                        else:
                            zh_text, emoji = sub.content, ""  # 翻译失败保留原文
                        if not zh_text.strip():
                            continue  # 译文清洗后为空则跳过，不产出空字幕
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
                            zh_text = _clean_caption(zh_text)  # 译文也清洗特殊符号
                            zh_line = f"{zh_text} {emoji}".strip() if emoji else zh_text
                            # 译文清洗后若为空，只留英文行，避免出现空中文行+换行
                            content = f"{zh_line}\n{sub.content}" if zh_line else sub.content
                        else:
                            content = sub.content  # 翻译失败保留原文
                        output_subs.append(srt.Subtitle(
                            index=sub.index, start=sub.start, end=sub.end, content=content,
                        ))

                if output_mode == "chinese_only":
                    kind = "部分中文" if stopped else "中文"
                else:
                    kind = "部分双语" if stopped else "双语"
                if snap:
                    _snap_srt_to_30fps(output_subs)
                n = _fix_overlaps(output_subs)
                if n:
                    log(f"⚠️ 修复了{mode_name}字幕中 {n} 处时间戳重叠")
                gap_max = float(config.get("close_gap_max_s", 1.0))
                g = _close_gaps(output_subs, gap_max)
                if g:
                    log(f"🔗 已闭合{mode_name}字幕中 {g} 处≤{gap_max:g}s 空隙，字幕连续无闪烁")
                out_path = naming.make_path(save_dir, kind, name_source, ".srt", name_ts)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(srt.compose(output_subs))
                primary_srt = out_path   # 翻译结果优先用于烧录
                if stopped:
                    log(f"⏹ 已停止，部分{mode_name}字幕已保存（{len(translations)}/{len(split_subs)} 行）: {out_path}")
                else:
                    log(f"✅ {mode_name}字幕已保存: {out_path}")

        elapsed = time.monotonic() - t_start
        log(f"⏱ 字幕生成耗时: {_fmt_duration(elapsed)}（方式: {gen_method}）")

        if stop_event and stop_event.is_set():
            log("⏹ 任务已停止")
        else:
            log("🎉 全部完成！")

        return save_dir, primary_srt

    except Exception as e:
        import traceback
        log(f"❌ 错误: {e}")
        log(traceback.format_exc())
