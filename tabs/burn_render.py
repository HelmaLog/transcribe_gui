"""
Burn rendering helpers — pure functions with no tkinter dependency.

Subtitle/banner patch rendering (PIL), font lookup, emoji handling, ASS file
generation, ffmpeg banner filter, and small time-format helpers. Extracted from
burn.py so the UI module stays focused on the Tk widgets and burn pipeline.
"""

import os
import tempfile
from pathlib import Path
from datetime import timedelta

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    from pilmoji import Pilmoji
    HAS_PILMOJI = True
except ImportError:
    HAS_PILMOJI = False

_PILMOJI_SRC_CACHE: list = [None, False]  # [source_cls, already_probed]
_FONT_OBJ_CACHE:   dict  = {}             # (path_str, size) -> ImageFont


def _has_emoji(text: str) -> bool:
    """Quick check: does text contain any emoji codepoints?"""
    for ch in text:
        cp = ord(ch)
        if (0x2300 <= cp <= 0x27BF or    # misc technical / dingbats
                0x1F000 <= cp <= 0x1FAFF or  # main emoji / symbols block
                0xFE00 <= cp <= 0xFE0F):     # variation selectors (emoji modifier)
            return True
    return False


# ── Constants ──────────────────────────────────────────────────────────────────

_FONT_NAMES = [
    "Microsoft YaHei",
    "SimHei",
    "Source Han Sans CN Heavy",
    "Source Han Sans CN Bold",
    "Source Han Sans CN Medium",
    "Source Han Sans CN Regular",
    "Source Han Sans CN Light",
    "Source Han Sans CN ExtraLight",
    "Source Han Sans CN Normal",
    "FZDaHei-B02",
    "Arial Black",
    "Impact",
]

_FONT_FILES = {
    "Microsoft YaHei":              ["msyh.ttc", "msyh.ttf"],
    "SimHei":                       ["simhei.ttf"],
    "Source Han Sans CN Heavy":     ["SourceHanSansCN-Heavy.otf", "NotoSansCJKsc-Black.otf"],
    "Source Han Sans CN Bold":      ["SourceHanSansCN-Bold.otf"],
    "Source Han Sans CN Medium":    ["SourceHanSansCN-Medium.otf"],
    "Source Han Sans CN Regular":   ["SourceHanSansCN-Regular.otf"],
    "Source Han Sans CN Light":     ["SourceHanSansCN-Light.otf"],
    "Source Han Sans CN ExtraLight":["SourceHanSansCN-ExtraLight.otf"],
    "Source Han Sans CN Normal":    ["SourceHanSansCN-Normal.otf"],
    "FZDaHei-B02":                  ["FZDHB02.TTF", "FZDH_B02.TTF"],
    "Arial Black":                  ["ariblk.ttf"],
    "Impact":                       ["impact.ttf"],
}

_TEXT_COLORS = {"白": "#ffffff", "黑": "#000000", "深灰": "#333333"}
_BG_COLORS   = {"白": "#ffffff", "黑": "#000000", "黄": "#f5c518", "深灰": "#222222"}


# ── Module-level render helpers ────────────────────────────────────────────────

_LOCAL_FONTS_DIR = Path(__file__).parent.parent / "otf"


_FONT_PATH_CACHE: dict = {}

def _find_font_path(family: str) -> str:
    if family in _FONT_PATH_CACHE:
        return _FONT_PATH_CACHE[family]
    search_dirs = [_LOCAL_FONTS_DIR, Path("C:/Windows/Fonts")]
    result = ""
    for fname in _FONT_FILES.get(family, []):
        for d in search_dirs:
            p = d / fname
            if p.exists():
                result = str(p)
                break
        if result:
            break
    if not result:
        stem = family.lower().replace(" ", "")[:5]
        for d in search_dirs:
            for ext in ["*.ttc", "*.ttf", "*.otf"]:
                for f in d.glob(ext):
                    if stem in f.stem.lower():
                        result = str(f)
                        break
                if result:
                    break
            if result:
                break
    _FONT_PATH_CACHE[family] = result
    return result


# ── Emoji / mixed-font support ────────────────────────────────────────────────

def _get_pilmoji_source():
    """Return first working pilmoji source class (cached). None if unavailable."""
    if _PILMOJI_SRC_CACHE[1]:
        return _PILMOJI_SRC_CACHE[0]
    _PILMOJI_SRC_CACHE[1] = True
    if not HAS_PILMOJI or not HAS_PIL:
        return None
    candidates = []
    for name in ("AppleEmojiSource", "TwitterEmojiSource", "GoogleEmojiSource"):
        try:
            import importlib
            mod = importlib.import_module("pilmoji.source")
            candidates.append(getattr(mod, name))
        except Exception:
            pass
    try:
        test_font = ImageFont.load_default()
    except Exception:
        test_font = None
    for src_cls in candidates:
        try:
            dummy = Image.new("RGBA", (4, 4))
            with Pilmoji(dummy, source=src_cls) as pm:
                pm.getsize("😊", font=test_font)
            _PILMOJI_SRC_CACHE[0] = src_cls
            return src_cls
        except Exception:
            continue
    return None


_EMOJI_RANGES = (
    (0x1F300, 0x1FAFF),  # Misc Symbols & Pictographs → Emoticons → Transport…
    (0x2300,  0x23FF),   # Misc Technical (clocks, etc.)
    (0x2600,  0x27BF),   # Misc Symbols, Dingbats
    (0x2B00,  0x2BFF),   # Misc Symbols & Arrows
    (0xFE00,  0xFE0F),   # Variation selectors (emoji presentation)
    (0x200D,  0x200D),   # Zero-width joiner (compound emoji)
    (0x20E3,  0x20E3),   # Combining enclosing keycap
    (0x3297,  0x3299),   # Circled ideographs used as emoji
)

def _is_emoji(c: str) -> bool:
    cp = ord(c)
    return any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)


def _emoji_runs(text: str):
    """Split text into [(is_emoji, segment), ...] runs."""
    if not text:
        return []
    runs: list = []
    cur_emoji = _is_emoji(text[0])
    cur = text[0]
    for ch in text[1:]:
        ie = _is_emoji(ch)
        if ie == cur_emoji:
            cur += ch
        else:
            runs.append((cur_emoji, cur))
            cur_emoji, cur = ie, ch
    runs.append((cur_emoji, cur))
    return runs


_EMOJI_FONT_CACHE: dict = {}

def _get_emoji_font(size: int):
    """Load Segoe UI Emoji at given size (cached). Returns None if unavailable."""
    if size in _EMOJI_FONT_CACHE:
        return _EMOJI_FONT_CACHE[size]
    emoji_path = Path("C:/Windows/Fonts/seguiemj.ttf")
    font = None
    if HAS_PIL and emoji_path.exists():
        try:
            font = ImageFont.truetype(str(emoji_path), size)
        except Exception:
            font = None
    _EMOJI_FONT_CACHE[size] = font
    return font


def _measure_line(line: str, font_main, font_emoji) -> tuple:
    """Return (width, height) for a mixed-font line."""
    w = h = 0
    for is_emoji, seg in _emoji_runs(line):
        f = font_emoji if (is_emoji and font_emoji) else font_main
        try:
            bb = f.getbbox(seg)
            w += max(0, bb[2] - bb[0])
            h  = max(h, max(0, bb[3] - bb[1]))
        except Exception:
            fallback_sz = getattr(f, "size", 14)
            w += len(seg) * max(1, fallback_sz // 2)
            h  = max(h, fallback_sz)
    return max(1, w), max(1, h)


def _draw_mixed_line(draw, x: int, y: int, line: str,
                     font_main, font_emoji, fill: tuple):
    """Draw a mixed-font line onto `draw` starting at (x, y)."""
    for is_emoji, seg in _emoji_runs(line):
        f = font_emoji if (is_emoji and font_emoji) else font_main
        draw.text((x, y), seg, fill=fill, font=f)
        try:
            bb = f.getbbox(seg)
            x += max(0, bb[2] - bb[0])
        except Exception:
            x += len(seg) * max(1, getattr(f, "size", 14) // 2)


# ── Colour helpers ─────────────────────────────────────────────────────────────

def _hex_to_rgb(hex_c: str) -> tuple:
    h = hex_c.lstrip("#")
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _resolve_color(name_or_hex: str, color_map: dict) -> str:
    return color_map.get(name_or_hex, name_or_hex)


def _make_subtitle_patch(text: str, sp: dict,
                          frame_w: int, frame_h: int):
    """Render subtitle as a small RGBA patch. Returns (patch, (paste_x, paste_y))."""
    if not HAS_PIL or not text or not text.strip():
        return None, None

    fp = _find_font_path(sp.get("font_family", "Microsoft YaHei"))
    sz = sp.get("font_size", 28)
    _font_key = (str(fp), sz)
    font = _FONT_OBJ_CACHE.get(_font_key)
    if font is None:
        try:
            font = ImageFont.truetype(fp, sz) if fp else ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()
        _FONT_OBJ_CACHE[_font_key] = font

    lines = text.strip().split("\n")
    lsp   = sp.get("line_spacing", 8)
    pad_x = sp.get("pad_x", 16)
    pad_y = sp.get("pad_y", 8)

    # Only use pilmoji when the text actually contains emoji characters.
    # For plain text, PIL is called directly — avoids network/init overhead.
    _pilmoji_src = _get_pilmoji_source()
    _use_pilmoji = _pilmoji_src is not None and _has_emoji(text)
    if _use_pilmoji:
        try:
            dummy = Image.new("RGBA", (1, 1))
            line_bbs = []
            with Pilmoji(dummy, source=_pilmoji_src) as pm:
                for line in lines:
                    w, h = pm.getsize(line, font=font)
                    line_bbs.append((max(1, w), max(1, h)))
        except Exception:
            _use_pilmoji = False

    if not _use_pilmoji:
        emoji_font = _get_emoji_font(sz)
        line_bbs = [_measure_line(line, font, emoji_font) for line in lines]

    max_w   = max(w for w, _ in line_bbs) if line_bbs else sz
    total_h = sum(h for _, h in line_bbs) + lsp * max(0, len(lines)-1)
    box_w   = max_w + pad_x * 2
    box_h   = total_h + pad_y * 2

    patch = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(patch)

    bg_hex = _resolve_color(sp.get("bg_color", "黑"), _BG_COLORS)
    br, bg_, bb_ = _hex_to_rgb(bg_hex)
    a = int(sp.get("bg_alpha", 80) / 100 * 255)
    r = sp.get("corner_radius", 8)
    try:
        draw.rounded_rectangle([0, 0, box_w, box_h], radius=r,
                               fill=(br, bg_, bb_, a))
    except AttributeError:
        draw.rectangle([0, 0, box_w, box_h], fill=(br, bg_, bb_, a))

    tc = _hex_to_rgb(_resolve_color(sp.get("text_color", "白"), _TEXT_COLORS))

    # PIL 的 getbbox 返回 (x0, y0, x1, y1)，其中 y0 是绘制锚点到可见字形顶部的内部空隙。
    # 若不补偿，顶部 padding 视觉上会比底部多出 y0 像素。
    try:
        _y_top_offset = max(0, font.getbbox("Ag")[1])
    except Exception:
        _y_top_offset = 0
    ty = pad_y - _y_top_offset + sp.get("voffset", 0)

    if _use_pilmoji:
        try:
            with Pilmoji(patch, source=_pilmoji_src) as pm:
                for i, line in enumerate(lines):
                    lw = line_bbs[i][0]
                    tx = (box_w - lw) // 2
                    pm.text((tx, ty), line, fill=tc, font=font)
                    ty += line_bbs[i][1] + lsp
        except Exception:
            _use_pilmoji = False
            ty = pad_y

    if not _use_pilmoji:
        emoji_font = _get_emoji_font(sz)
        for i, line in enumerate(lines):
            lw = line_bbs[i][0]
            tx = (box_w - lw) // 2
            _draw_mixed_line(draw, tx, ty, line, font, emoji_font, tc)
            ty += line_bbs[i][1] + lsp

    px = max(0, (frame_w - box_w) // 2)
    py = max(0, frame_h - box_h - int(frame_h * 0.05))
    return patch, (px, py)


def _make_banner_patch(bp: dict, frame_w: int, frame_h: int):
    """Render banner as an RGBA patch. Returns (patch, (paste_x, paste_y))."""
    if not HAS_PIL:
        return None, None
    text = bp.get("text", "").strip()
    if not text:
        return None, None

    fp = _find_font_path(bp.get("font_family", "Microsoft YaHei"))
    sz = bp.get("font_size", 32)
    try:
        font = ImageFont.truetype(fp, sz) if fp else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    height   = bp.get("height", 60)
    position = bp.get("position", "top")
    align    = bp.get("align", "center")
    tc_rgb   = _hex_to_rgb(bp.get("text_color", "#ffffff"))
    no_bg    = bool(bp.get("no_bg", False))
    border_w = int(bp.get("border_w", 0)) if bp.get("border", False) else 0
    bd_rgb   = _hex_to_rgb(bp.get("border_color", "#000000"))

    if no_bg:
        patch = Image.new("RGBA", (frame_w, height), (0, 0, 0, 0))
    else:
        patch = Image.new("RGBA", (frame_w, height),
                          _hex_to_rgb(bp.get("bg_color", "#1a1a2e")) + (255,))
    draw  = ImageDraw.Draw(patch)

    # y_top_offset: gap between PIL draw origin and actual visible glyph top
    try:
        _y_top_offset = max(0, font.getbbox("Ag")[1])
    except Exception:
        _y_top_offset = 0

    _pilmoji_src = _get_pilmoji_source()
    tw, th = sz * max(1, len(text)) // 2, sz
    if _pilmoji_src:
        try:
            dummy = Image.new("RGBA", (1, 1))
            with Pilmoji(dummy, source=_pilmoji_src) as pm:
                tw, th = pm.getsize(text, font=font)
        except Exception:
            _pilmoji_src = None
    if not _pilmoji_src:
        try:
            bb = font.getbbox(text)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
        except Exception:
            pass

    # Visually center: account for y_top_offset so top/bottom gap are equal
    ty = (height - th) // 2 - _y_top_offset + bp.get("voffset", 0)
    if align == "left":
        tx = 16 + border_w          # 给左侧描边留出空间
    elif align == "right":
        tx = frame_w - tw - 16 - border_w
    else:
        tx = (frame_w - tw) // 2    # 居中时描边对称外扩，无需补偿

    _stroke = {}
    if border_w > 0:
        _stroke = dict(stroke_width=border_w, stroke_fill=bd_rgb + (255,))

    if _pilmoji_src:
        try:
            with Pilmoji(patch, source=_pilmoji_src) as pm:
                pm.text((tx, ty), text, fill=tc_rgb + (255,), font=font, **_stroke)
        except Exception:
            draw.text((tx, ty + _y_top_offset), text, fill=tc_rgb + (255,),
                      font=font, **_stroke)
    else:
        draw.text((tx, ty), text, fill=tc_rgb + (255,), font=font, **_stroke)

    py = 0 if position == "top" else frame_h - height
    return patch, (0, py)


def render_subtitle_on_frame(img: "Image.Image", text: str, sp: dict) -> "Image.Image":
    """Draw subtitle box on a PIL image. Used for preview only."""
    if not HAS_PIL or not text or not text.strip():
        return img
    patch, pos = _make_subtitle_patch(text, sp, img.width, img.height)
    if patch is None:
        return img
    out = img.convert("RGBA")
    out.paste(patch, pos, patch)
    return out.convert("RGB")


def render_banner_on_frame(img: "Image.Image", bp: dict) -> "Image.Image":
    """Draw banner on a PIL image."""
    if not HAS_PIL:
        return img
    if not bp.get("text", "").strip():
        return img
    img = img.convert("RGBA")
    patch, pos = _make_banner_patch(bp, img.width, img.height)
    if patch is not None:
        img.paste(patch, pos, patch)   # 用 alpha 作 mask，支持透明背景
    return img.convert("RGB")


def _subs_at(subs, t_sec: float) -> str:
    t = timedelta(seconds=t_sec)
    for sub in subs:
        if sub.start <= t <= sub.end:
            return sub.content
    return ""


def _hex_to_ass_color(hex_c: str, alpha: int = 0) -> str:
    """#RRGGBB → ASS &HAABBGGRR  (alpha: 0=opaque, 255=transparent)."""
    h = hex_c.lstrip("#")
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}"


def _td_to_ass_time(td) -> str:
    total_cs = int(td.total_seconds() * 100)
    cs = total_cs % 100
    s  = (total_cs // 100) % 60
    m  = (total_cs // 6000) % 60
    h  = total_cs // 360000
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ffmpeg_path(path: str) -> str:
    """Convert path to forward-slash form for use inside single-quoted ffmpeg filter values."""
    return path.replace("\\", "/")


def _write_ass(subs, sp: dict, out_w: int, out_h: int) -> str:
    """Convert SRT subtitles + style params into a temp ASS file. Returns file path."""
    font  = sp.get("font_family", "Microsoft YaHei")
    size  = sp.get("font_size",   28)
    bold  = 1 if sp.get("bold") else 0
    lsp   = sp.get("line_spacing", 8)
    padx  = sp.get("pad_x", 16)
    pady  = sp.get("pad_y", 8)

    tc_hex       = _resolve_color(sp.get("text_color", "白"), _TEXT_COLORS)
    bc_hex       = _resolve_color(sp.get("bg_color",   "黑"), _BG_COLORS)
    bg_alpha_pct = sp.get("bg_alpha", 80)
    bg_alpha_ass = int((1 - bg_alpha_pct / 100) * 255)

    text_color_ass = _hex_to_ass_color(tc_hex, 0)
    bg_color_ass   = _hex_to_ass_color(bc_hex, bg_alpha_ass)
    outline        = max(1, min(pady, padx // 2))
    margin_v       = max(10, int(out_h * 0.04))

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {out_w}\n"
        f"PlayResY: {out_h}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},{text_color_ass},{text_color_ass},"
        f"{bg_color_ass},{bg_color_ass},{bold},0,0,0,100,100,{lsp},0,3,"
        f"{outline},0,2,10,10,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events = []
    for sub in subs:
        s = _td_to_ass_time(sub.start)
        e = _td_to_ass_time(sub.end)
        t = sub.content.strip().replace("\n", "\\N")
        events.append(f"Dialogue: 0,{s},{e},Default,,0,0,0,,{t}\n")

    fd, path = tempfile.mkstemp(suffix=".ass")
    with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
        f.write(header)
        f.writelines(events)
    return path


def _banner_filter(bp: dict, out_w: int, out_h: int) -> str:
    """Build ffmpeg drawbox+drawtext filter string for the fixed banner."""
    text = bp.get("text", "").strip()
    if not text:
        return ""

    height   = bp.get("height",    60)
    position = bp.get("position",  "top")
    align    = bp.get("align",     "center")
    font_sz  = bp.get("font_size", 32)
    tc_hex   = bp.get("text_color", "#ffffff").lstrip("#")
    bc_hex   = bp.get("bg_color",  "#1a1a2e").lstrip("#")
    no_bg    = bool(bp.get("no_bg", False))
    border_w = int(bp.get("border_w", 0)) if bp.get("border", False) else 0
    bd_hex   = bp.get("border_color", "#000000").lstrip("#")

    box_y  = "0"        if position == "top" else f"ih-{height}"
    text_y = (f"({height}-text_h)/2"
              if position == "top"
              else f"ih-{height}+({height}-text_h)/2")

    if align == "left":
        text_x = "16"
    elif align == "right":
        text_x = "w-text_w-16"
    else:
        text_x = "(w-text_w)/2"

    t  = text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    fp = _find_font_path(bp.get("font_family", "Microsoft YaHei"))
    font_arg = f":fontfile='{_ffmpeg_path(fp)}'" if fp else ""

    box   = (f"drawbox=x=0:y={box_y}:w=iw:h={height}"
             f":color=0x{bc_hex}ff@1.0:t=fill")
    dtext = (f"drawtext=text='{t}':fontsize={font_sz}{font_arg}"
             f":fontcolor=0x{tc_hex}:x={text_x}:y={text_y}")
    if border_w > 0:
        dtext += f":borderw={border_w}:bordercolor=0x{bd_hex}"
    return dtext if no_bg else f"{box},{dtext}"


# ── Time-format helpers ────────────────────────────────────────────────────────

def _td_to_str(td) -> str:
    total = int(td.total_seconds() * 1000)
    ms = total % 1000
    s  = (total // 1000) % 60
    m  = (total // 60000) % 60
    h  = total // 3600000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ms_to_hms(ms: int) -> str:
    ms = max(0, int(ms))
    s  = (ms // 1000) % 60
    m  = (ms // 60000) % 60
    h  = ms // 3600000
    return f"{h:02d}:{m:02d}:{s:02d}"
