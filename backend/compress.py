"""
Video compression functions using FFmpeg.
"""

import os
import re


def detect_hw_encoder():
    """检测可用的硬件视频编码器，返回 (encoder_name, description)"""
    import shutil
    import subprocess

    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return 'libx264', '软件编码 (libx264)'

    candidates = [
        ('h264_nvenc', 'NVIDIA NVENC'),
        ('h264_qsv',   'Intel QSV'),
        ('h264_amf',   'AMD AMF'),
    ]
    for enc, desc in candidates:
        try:
            result = subprocess.run(
                [ffmpeg, '-f', 'lavfi', '-i', 'nullsrc=s=128x128:d=0.1',
                 '-vf', 'format=yuv420p',
                 '-c:v', enc, '-frames:v', '1', '-f', 'null', '-'],
                capture_output=True, timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                return enc, f'硬件加速 ({desc})'
        except Exception:
            pass
    return 'libx264', '软件编码 (libx264)'


def compress_probe(path):
    """用 ffprobe 读取视频信息，返回 (info_dict, None) 或 (None, error_str)"""
    import shutil
    import subprocess
    import json as _json

    ffmpeg_path = shutil.which('ffmpeg')
    ffprobe = shutil.which('ffprobe')
    if not ffprobe and ffmpeg_path:
        candidate = os.path.join(os.path.dirname(ffmpeg_path), 'ffprobe.exe')
        if os.path.exists(candidate):
            ffprobe = candidate
    if not ffprobe:
        return None, "未找到 ffprobe，请确认 FFmpeg 已安装并在 PATH 中"

    try:
        result = subprocess.run(
            [ffprobe, '-v', 'quiet', '-print_format', 'json',
             '-show_streams', '-show_format', path],
            capture_output=True, text=True, timeout=20,
            encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            return None, f"ffprobe 错误: {result.stderr[:300]}"

        data = _json.loads(result.stdout)
        fmt = data.get('format', {})
        duration = float(fmt.get('duration', 0) or 0)
        size_bytes = int(fmt.get('size', 0) or 0)

        width = height = None
        fps = None
        for stream in data.get('streams', []):
            if stream.get('codec_type') == 'video':
                width = stream.get('width')
                height = stream.get('height')
                r = stream.get('r_frame_rate', '')
                if '/' in r:
                    n, d = r.split('/')
                    fps = round(int(n) / int(d), 2) if int(d) else None
                break

        return {
            'duration': duration,
            'size_bytes': size_bytes,
            'width': width,
            'height': height,
            'fps': fps,
        }, None
    except subprocess.TimeoutExpired:
        return None, "ffprobe 超时"
    except Exception as e:
        return None, str(e)


def estimate_output_size(duration_s, vbitrate_kbps, abitrate_kbps):
    """预估输出文件大小（字节）"""
    return int((vbitrate_kbps + abitrate_kbps) * 1000 / 8 * duration_s)


def compress_video(config, log, progress_cb, stop_event=None):
    """
    调用 FFmpeg 压缩视频，实时回报进度。

    config 字段:
      input_path, output_path, encoder, vbitrate (kbps),
      abitrate (kbps), scale ((w,h) 或 None), duration (秒)

    progress_cb(pct: float) 传入 0–100
    返回 output_path（成功）或 None（失败/取消）
    """
    import shutil
    import subprocess

    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        log("❌ 未找到 ffmpeg，请安装 FFmpeg 并将其加入系统 PATH")
        return None

    input_path  = config['input_path']
    output_path = config['output_path']
    encoder     = config.get('encoder', 'libx264')
    vbitrate    = int(config['vbitrate'])
    abitrate    = int(config['abitrate'])
    scale       = config.get('scale')        # (w, h) or None
    duration    = float(config.get('duration', 0))

    # ── 构造缩放滤镜 ──────────────────────────────────────────────────────────
    if scale:
        w, h = scale
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    else:
        vf = None

    # ── 构造 FFmpeg 命令 ──────────────────────────────────────────────────────
    cmd = [ffmpeg, '-y', '-i', input_path]

    # 视频编码器
    cmd += ['-c:v', encoder]
    if encoder == 'libx264':
        cmd += ['-preset', 'fast', '-profile:v', 'high']
    elif encoder == 'h264_nvenc':
        cmd += ['-preset', 'p4', '-profile:v', 'high']
    elif encoder == 'h264_qsv':
        cmd += ['-preset', 'fast']
    elif encoder == 'h264_amf':
        cmd += ['-quality', 'speed']

    cmd += ['-b:v', f'{vbitrate}k',
            '-maxrate', f'{vbitrate}k',
            '-bufsize', f'{vbitrate * 2}k']
    if vf:
        cmd += ['-vf', vf]

    # 音频编码器
    cmd += ['-c:a', 'aac', '-b:a', f'{abitrate}k', '-ac', '2']

    # MP4 流式起始（方便边下边看）
    cmd += ['-movflags', '+faststart']
    cmd.append(output_path)

    log(f"🎬 开始压缩: {os.path.basename(input_path)}")
    log(f"   编码器: {encoder}  |  视频: {vbitrate} kbps  |  音频: {abitrate} kbps")
    if scale:
        log(f"   目标分辨率: {scale[0]}×{scale[1]}")
    log(f"   输出: {output_path}")

    # ── 执行并解析进度 ────────────────────────────────────────────────────────
    _time_re = re.compile(r'time=(\d+):(\d+):(\d+\.?\d*)')

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        for line in proc.stderr:
            if stop_event and stop_event.is_set():
                proc.terminate()
                log("⏹ 压缩已取消")
                return None
            m = _time_re.search(line)
            if m and duration > 0:
                h, mn, s = m.groups()
                cur = int(h) * 3600 + int(mn) * 60 + float(s)
                pct = min(99.0, cur / duration * 100)
                progress_cb(pct)

        proc.wait()

        if stop_event and stop_event.is_set():
            log("⏹ 压缩已取消")
            return None

        if proc.returncode == 0 and os.path.exists(output_path):
            out_mb = os.path.getsize(output_path) / 1024 / 1024
            log(f"✅ 压缩完成！输出大小: {out_mb:.1f} MB")
            log(f"   路径: {output_path}")
            progress_cb(100.0)
            return output_path
        else:
            log(f"❌ FFmpeg 返回错误码: {proc.returncode}")
            return None

    except Exception as e:
        import traceback
        log(f"❌ 压缩异常: {e}")
        log(traceback.format_exc())
        return None
