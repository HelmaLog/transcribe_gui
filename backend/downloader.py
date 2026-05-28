"""
Video download logic using yt-dlp.
"""

import os
import re

from .common import _clean
from .config import _LANG_NAMES


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
            creationflags=subprocess.CREATE_NO_WINDOW,
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


def run_download(config, log, progress_cb=None):
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

    safe_title = re.sub(r'[\\/:*?"<>|]', '_', title).strip()[:40].rstrip()
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
            pct_str = _clean(d.get('_percent_str', '')).strip()
            if pct_str and pct_str != last_pct[0]:
                last_pct[0] = pct_str
                speed = _clean(d.get('_speed_str', '') or '').strip()
                eta = _clean(d.get('_eta_str', '') or '').strip()
                log(f"⬇️ {pct_str}  速度: {speed}  剩余: {eta}")
            # Call progress callback with numeric percentage
            if progress_cb:
                downloaded = d.get('downloaded_bytes', 0) or 0
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                if total > 0:
                    pct = min(99.0, downloaded / total * 100)
                    speed = _clean(d.get('_speed_str', '') or '').strip()
                    eta = _clean(d.get('_eta_str', '') or '').strip()
                    progress_cb(pct, speed, eta)
        elif d['status'] == 'finished':
            log(f"✅ 完成: {os.path.basename(d.get('filename', ''))}")
            if progress_cb:
                progress_cb(100.0, '', '')

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
