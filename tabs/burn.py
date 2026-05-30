"""
Burn tab — hardcode subtitles into video.
"""

import os
import shutil
import tempfile
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, colorchooser, messagebox
from datetime import timedelta

try:
    from tkinterdnd2 import DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

try:
    import vlc
    HAS_VLC = True
except Exception:
    HAS_VLC = False

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

try:
    import av
    HAS_AV = True
except ImportError:
    HAS_AV = False

from .base import Tab, BG, HL_GREEN

from .burn_render import (
    _FONT_NAMES, _TEXT_COLORS, _BG_COLORS, _resolve_color, _hex_to_rgb,
    _is_emoji, _ffmpeg_path, _td_to_ass_time, _write_ass, _banner_filter,
    _make_subtitle_patch, _make_banner_patch,
    render_subtitle_on_frame, render_banner_on_frame, _td_to_str, _ms_to_hms,
)

# ── Constants ──────────────────────────────────────────────────────────────────

_ENCODER_CACHE = [None]


def _detect_encoder() -> str:
    if _ENCODER_CACHE[0] is not None:
        return _ENCODER_CACHE[0]
    if HAS_AV:
        for enc in ["h264_nvenc", "h264_qsv", "h264_amf"]:
            try:
                import tempfile
                fd, tmp = tempfile.mkstemp(suffix=".mp4")
                os.close(fd)
                try:
                    with av.open(tmp, "w") as _c:
                        _s = _c.add_stream(enc, rate=30)
                        _s.width = 16
                        _s.height = 16
                        _s.pix_fmt = "yuv420p"
                        _f = av.VideoFrame(16, 16, "yuv420p")
                        _f.pts = 0
                        for _p in _s.encode(_f):
                            _c.mux(_p)
                        for _p in _s.encode():
                            _c.mux(_p)
                    _ENCODER_CACHE[0] = enc
                    return enc
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            except Exception:
                continue
    _ENCODER_CACHE[0] = "libx264"
    return _ENCODER_CACHE[0]


def _verify_av_encoder(encoder: str, fps_rate, out_w: int, out_h: int,
                        vbitrate: int) -> bool:
    """Return True if the encoder can be opened successfully by PyAV.

    必须用实际输出尺寸校验：硬件编码器（尤其 AMD AMF）会拒绝过小的画面
    （16×16 直接 Init 失败），用真实分辨率才不会把可用的硬编误判为不可用。
    """
    w = max(64, int(out_w) & ~1)
    h = max(64, int(out_h) & ~1)
    fd, tmp = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        with av.open(tmp, "w") as c:
            s = c.add_stream(encoder, rate=fps_rate)
            s.width, s.height, s.pix_fmt = w, h, "yuv420p"
            f = av.VideoFrame(w, h, "yuv420p")
            f.pts = 0
            for p in s.encode(f):
                c.mux(p)
            for p in s.encode():
                c.mux(p)
        return True
    except Exception:
        return False
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _verify_encoder_cli(ffmpeg_exe: str, encoder: str,
                        out_w: int, out_h: int) -> bool:
    """用系统 ffmpeg CLI 在真实输出尺寸下校验编码器能否打开。

    圆角/直角路径的实际编码都由系统 ffmpeg.exe 完成，所以校验也应用同一套
    工具、同一尺寸——而不是用 PyAV（其内置 ffmpeg 与系统版本可能不一致）。
    """
    import subprocess
    w = max(64, int(out_w) & ~1)
    h = max(64, int(out_h) & ~1)
    try:
        r = subprocess.run(
            [ffmpeg_exe, "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", f"nullsrc=s={w}x{h}:d=0.1",
             "-c:v", encoder, "-frames:v", "1", "-f", "null", "-"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def _set_av_encoder_opts(stream, encoder: str) -> None:
    if encoder == "h264_nvenc":
        stream.options = {"preset": "p4", "tune": "hq", "rc": "vbr"}
    elif encoder == "libx264":
        stream.options = {"preset": "medium", "threads": "0"}
    elif encoder == "h264_qsv":
        stream.options = {"preset": "medium"}


# ── Flow layout frame ─────────────────────────────────────────────────────────

class _ItemFlowFrame(tk.Frame):
    """Left-to-right wrapping flow layout for small item frames.
    Items fill each row; when the next item would overflow, it wraps."""
    _GAP_X = 10
    _GAP_Y = 4

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self._cells: list = []
        self._reflowing = False
        self.bind("<Configure>", lambda e: self.after_idle(self._reflow))

    def add(self, cell: "tk.Frame"):
        self._cells.append(cell)

    def _reflow(self):
        if self._reflowing:
            return
        w = self.winfo_width()
        if w <= 4 or not self._cells:
            return
        self._reflowing = True
        try:
            for c in self._cells:
                c.update_idletasks()
            x, y, row_h = 0, 0, 0
            for cell in self._cells:
                cw = cell.winfo_reqwidth()
                ch = cell.winfo_reqheight()
                if x > 0 and x + cw > w:
                    x = 0
                    y += row_h + self._GAP_Y
                    row_h = 0
                cell.place(x=x, y=y)
                x += cw + self._GAP_X
                row_h = max(row_h, ch)
            new_h = max(1, y + row_h)
            if self.winfo_height() != new_h:
                self.configure(height=new_h)
        finally:
            self._reflowing = False


# ── BurnTab ────────────────────────────────────────────────────────────────────

class BurnTab(Tab):

    def _build(self):
        self._subs = []
        self._tmp_srt = ""
        self._player = None
        self._instance = None
        self._dragging_seek = False
        self._poll_id = None
        self._duration_ms = 0
        self._current_iid = None
        self._loading = False
        self._loaded_video_path = ""
        self._video_aspect = 0.0
        self._loop_start_ms  = 0
        self._loop_end_ms    = 0
        self._loop_poll_id   = None
        self._sub_looping    = False
        self._seg_looping      = False
        self._seg_loop_start   = 0
        self._seg_loop_end     = 0
        self._seg_loop_poll_id = None
        self._reloading_subs = False
        self._edit_was_playing = False
        self._last_srt_scan_folder = ""
        self._preview_debounce_id = None
        self._preview_gen = 0       # 代次计数器，防止旧线程覆盖新结果
        self._preview_photo = None
        self._preview_snap_tmp = ""
        self._preview_t_sec = None  # 字幕点击时缓存精确时间，防止 VLC 异步 seek 丢帧
        self._style_expanded = False
        self._banner_expanded = False
        self._burn_opts_expanded = False
        self._burn_thread = None
        self._burn_start_time = None
        self._is_running = False
        self._segments = []          # [(start_ms, end_ms)] 已确认片段；空=全程烧录
        self._seg_mark_start = None  # 已标记起点、待标记终点的临时值（ms）
        cfg = self.app._saved_config
        self._vlc_sub_color = cfg.get("burn_vlc_sub_color", "#ffff00")
        self._sash_ratio = float(cfg.get("burn_sash_ratio", 0.60))
        self._ts_visible = bool(cfg.get("burn_ts_visible", True))
        for _it in cfg.get("burn_segments", []):
            try:
                _a, _b = int(_it[0]), int(_it[1])
                if _b > _a:
                    self._segments.append((_a, _b))
            except (TypeError, ValueError, IndexError):
                pass

        self._init_style_vars(cfg)

        p = self.frame
        p.columnconfigure(0, weight=1)
        p.rowconfigure(0, weight=1)

        self._paned = tk.PanedWindow(p, orient="horizontal", bg=BG,
                                     sashwidth=5, sashrelief="flat",
                                     handlepad=0, handlesize=0)
        self._paned.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        # Left pane
        self._left_pane = tk.Frame(self._paned, bg=BG)
        left = self._left_pane
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=0)
        left.rowconfigure(3, weight=1)

        self._video_frame = tk.Frame(left, bg="#000000", height=220)
        self._video_frame.grid(row=0, column=0, sticky="ew")
        self._video_frame.grid_propagate(False)

        # 预览层：父控件是 left_pane（与 _video_frame 同级）
        # 不能作为 _video_frame 的子控件——VLC 用 set_hwnd 直接操作 HWND
        # 会绘制覆盖所有 tkinter 子 widget；改为兄弟 HWND + lift() 才能显示在上方
        self._preview_label = tk.Label(
            left, bg="#000000",
            anchor="center", justify="center")

        if not HAS_VLC:
            tk.Label(self._video_frame,
                     text="需要安装 VLC 播放器和 python-vlc\npip install python-vlc",
                     bg="#000000", fg="#444444",
                     font=("Segoe UI", 10), justify="center"
                     ).place(relx=0.5, rely=0.5, anchor="center")

        ctrl = tk.Frame(left, bg="#161616")
        ctrl.grid(row=1, column=0, sticky="ew", pady=(2, 2))
        self._build_controls(ctrl)

        seg_bar = tk.Frame(left, bg=BG)
        seg_bar.grid(row=2, column=0, sticky="ew", pady=(0, 2))
        self._build_segments(seg_bar)

        tree_frame = tk.Frame(left, bg=BG)
        tree_frame.grid(row=3, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self._build_tree(tree_frame)

        self._paned.add(left, minsize=200)

        # Right pane (scrollable)
        right = tk.Frame(self._paned, bg=BG)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        self._paned.add(right, minsize=200)
        self._build_right(right)

        self._paned.bind("<Configure>",       self._on_paned_resize)
        self._paned.bind("<ButtonRelease-1>", self._on_sash_released)
        left.bind("<Configure>", self._on_left_resize)
        self._video_frame.bind("<Configure>", self._on_video_frame_configure)

        p.after(50, lambda: _set_sash(self._paned, self._sash_ratio))

        if HAS_VLC:
            self._init_vlc()

        p.after(400, self._restore_files_from_config)
        p.after(800, self._detect_encoder_async)

    # ── Style vars ────────────────────────────────────────────────────────────

    def _init_style_vars(self, cfg: dict):
        self._style_font   = tk.StringVar(value=cfg.get("burn_style_font",   "Microsoft YaHei"))
        self._style_size   = tk.IntVar(value=int(cfg.get("burn_style_size",   28)))
        self._style_bold   = tk.BooleanVar(value=bool(cfg.get("burn_style_bold",   False)))
        self._style_lsp    = tk.IntVar(value=int(cfg.get("burn_style_lsp",    8)))
        self._style_corner = tk.IntVar(value=int(cfg.get("burn_style_corner", 8)))
        self._style_padx   = tk.IntVar(value=int(cfg.get("burn_style_padx",   16)))
        self._style_pady   = tk.IntVar(value=int(cfg.get("burn_style_pady",   8)))
        self._style_alpha   = tk.IntVar(value=int(cfg.get("burn_style_alpha",   80)))
        self._style_voffset = tk.IntVar(value=int(cfg.get("burn_style_voffset", 0)))
        self._style_tcolor  = tk.StringVar(value=cfg.get("burn_style_tcolor",  "白"))
        self._style_bcolor  = tk.StringVar(value=cfg.get("burn_style_bcolor",  "黑"))

        self._banner_text   = tk.StringVar(value=cfg.get("burn_banner_text",   ""))
        self._banner_font   = tk.StringVar(value=cfg.get("burn_banner_font",   "Microsoft YaHei"))
        self._banner_size   = tk.IntVar(value=int(cfg.get("burn_banner_size",   32)))
        self._banner_bold   = tk.BooleanVar(value=bool(cfg.get("burn_banner_bold",   True)))
        self._banner_tcolor = tk.StringVar(value=cfg.get("burn_banner_tcolor", "#ffffff"))
        self._banner_bcolor = tk.StringVar(value=cfg.get("burn_banner_bcolor", "#1a1a2e"))
        self._banner_height = tk.IntVar(value=int(cfg.get("burn_banner_height", 60)))
        self._banner_pos     = tk.StringVar(value=cfg.get("burn_banner_pos",     "top"))
        self._banner_align   = tk.StringVar(value=cfg.get("burn_banner_align",   "center"))
        self._banner_voffset = tk.IntVar(value=int(cfg.get("burn_banner_voffset", 0)))
        self._with_banner      = tk.BooleanVar(value=bool(cfg.get("burn_with_banner", False)))
        self._burn_round_corners = tk.BooleanVar(value=bool(cfg.get("burn_round_corners", True)))
        self._burn_preset_var  = tk.StringVar(value=cfg.get("burn_preset", "balanced"))
        self._burn_res_var     = tk.StringVar(value=cfg.get("burn_res", "1920x1080"))
        self._burn_vbitrate_var = tk.StringVar(value=cfg.get("burn_vbitrate", "8000"))

        for v in [self._style_font, self._style_size, self._style_bold,
                  self._style_lsp, self._style_corner, self._style_padx,
                  self._style_pady, self._style_alpha, self._style_voffset,
                  self._style_tcolor,
                  self._style_bcolor, self._banner_text, self._banner_font,
                  self._banner_size, self._banner_bold, self._banner_tcolor,
                  self._banner_bcolor, self._banner_height, self._banner_pos,
                  self._banner_align, self._banner_voffset, self._with_banner]:
            v.trace_add("write", self._schedule_preview_update)


    # ── Sash / resize ─────────────────────────────────────────────────────────

    def _on_paned_resize(self, event):
        w = event.width
        if w > 1:
            self._paned.sash_place(0, int(w * self._sash_ratio), 0)

    def _on_sash_released(self, event):
        try:
            sash_x = self._paned.sash_coord(0)[0]
            total  = self._paned.winfo_width()
            if total > 1:
                self._sash_ratio = max(0.2, min(0.85, sash_x / total))
        except (tk.TclError, IndexError):
            pass

    def _on_left_resize(self, event):
        if event.widget is not self._left_pane:
            return
        w = event.width
        if w <= 10:
            return
        if self._video_aspect > 0:
            h = max(80, int(w / self._video_aspect))
        else:
            h = max(80, int(w * 9 / 16))
        self._video_frame.configure(height=h)

    def _on_video_frame_configure(self, event):
        """视频区域尺寸变化时，同步更新预览 overlay 的位置和大小。"""
        if not self._preview_label.winfo_ismapped():
            return
        vf = self._video_frame
        self._preview_label.place(x=vf.winfo_x(), y=vf.winfo_y(),
                                   width=event.width, height=event.height)
        # 尺寸变了后重新渲染一帧，保持图像与新尺寸匹配（防抖300ms）
        self._schedule_preview_update()

    def _restore_files_from_config(self):
        cfg = self.app._saved_config
        video = cfg.get("burn_video_path", "")
        srt   = cfg.get("burn_srt_path",   "")
        if video or srt:
            self.set_files(video, srt)
        if not self._ts_visible:
            self._toggle_timestamps()

    # ── VLC ───────────────────────────────────────────────────────────────────

    def _init_vlc(self):
        try:
            self._instance = vlc.Instance("--quiet")
            self._player   = self._instance.media_player_new()
            self.app.after(200, self._embed_player)
        except Exception as e:
            tk.Label(self._video_frame, text=f"VLC 初始化失败\n{e}",
                     bg="#000000", fg="#884444",
                     font=("Segoe UI", 9), justify="center"
                     ).place(relx=0.5, rely=0.5, anchor="center")

    def _embed_player(self):
        if self._player is None:
            return
        try:
            self._player.set_hwnd(self._video_frame.winfo_id())
            self._player.audio_set_volume(80)
        except Exception:
            pass

    # ── Controls bar ─────────────────────────────────────────────────────────

    def _build_controls(self, p):
        p.columnconfigure(2, weight=1)

        self._play_btn = tk.Button(
            p, text="▶", command=self._toggle_play,
            bg="#161616", fg="#cccccc", relief="flat",
            font=("Segoe UI", 12), padx=8, pady=2, width=2,
            cursor="hand2", activebackground="#2a2a2a")
        self._play_btn.grid(row=0, column=0, padx=(6, 2), pady=4)

        self._time_lbl = tk.Label(p, text="00:00:00", bg="#161616", fg="#888888",
                                  font=("Consolas", 9))
        self._time_lbl.grid(row=0, column=1, padx=(4, 2))

        self._seek_slider = ThinSlider(
            p, from_=0, to=1000,
            on_press=self._seek_press_cmd,
            command=self._seek_drag_cmd,
            on_release=self._seek_release_cmd,
        )
        self._seek_slider.grid(row=0, column=2, sticky="ew", padx=4, pady=5)

        self._dur_lbl = tk.Label(p, text="00:00:00", bg="#161616", fg="#888888",
                                 font=("Consolas", 9))
        self._dur_lbl.grid(row=0, column=3, padx=(2, 4))

        tk.Label(p, text="🔊", bg="#161616", fg="#666666",
                 font=("Segoe UI", 9)).grid(row=0, column=4, padx=(4, 0))
        self._vol_slider = ThinSlider(
            p, from_=0, to=100,
            command=self._vol_cmd,
            fill_color="#666666", handle_color="#aaaaaa",
            width=80,
        )
        self._vol_slider.set(80)
        self._vol_slider.grid(row=0, column=5, padx=(2, 6), pady=5)

        tk.Frame(p, bg="#333333", width=1).grid(
            row=0, column=6, sticky="ns", pady=4, padx=(2, 4))
        btn_lbl = "隐藏时间" if self._ts_visible else "显示时间"
        self._ts_btn = tk.Button(
            p, text=btn_lbl, command=self._toggle_timestamps,
            bg="#161616", fg="#555555", relief="flat", padx=6, pady=1,
            font=("Segoe UI", 8), cursor="hand2",
            activebackground="#2a2a2a", activeforeground="#888888")
        self._ts_btn.grid(row=0, column=7, padx=(0, 4), pady=4)

        tk.Frame(p, bg="#333333", width=1).grid(
            row=0, column=8, sticky="ns", pady=4, padx=(2, 4))
        tk.Label(p, text="字幕色", bg="#161616", fg="#555555",
                 font=("Segoe UI", 8)).grid(row=0, column=9, padx=(0, 3))
        self._vlc_color_btn = tk.Label(
            p, text="●", fg=self._vlc_sub_color, bg="#161616",
            font=("Segoe UI", 13), cursor="hand2")
        self._vlc_color_btn.bind("<Button-1>", lambda e: self._pick_vlc_sub_color())
        self._vlc_color_btn.grid(row=0, column=10, padx=(0, 8), pady=4)

    # ── Segments (time-range) ───────────────────────────────────────────────────

    def _build_segments(self, p):
        # 列 99 作为弹性占位，把状态标签推到最右
        p.columnconfigure(99, weight=1)

        tk.Label(p, text="✂ 时间片段", bg=BG, fg="#888888",
                 font=("Segoe UI", 8)).grid(row=0, column=0, padx=(2, 6), pady=(2, 2))

        def _mk(text, cmd, col, fg="#cccccc"):
            b = tk.Button(p, text=text, command=cmd,
                          bg="#2a2a2a", fg=fg, relief="flat",
                          font=("Segoe UI", 8), padx=8, pady=1, cursor="hand2",
                          activebackground="#3a3a3a", activeforeground="#ffffff")
            b.grid(row=0, column=col, padx=(0, 4), pady=(2, 2))
            return b

        self._seg_start_btn = _mk("● 标记起点", self._mark_seg_start, 1, fg="#d8a657")
        self._seg_end_btn   = _mk("■ 标记终点", self._mark_seg_end,   2, fg="#7daea3")
        self._seg_clear_btn = _mk("清除",       self._clear_segments, 3, fg="#999999")

        self._seg_status = tk.Label(p, text="", bg=BG, fg="#666666",
                                    font=("Segoe UI", 8), anchor="e")
        self._seg_status.grid(row=0, column=99, sticky="e", padx=(6, 4))

        self._seg_chips = _ItemFlowFrame(p, bg=BG)
        self._seg_chips.grid(row=1, column=0, columnspan=100,
                             sticky="ew", padx=2, pady=(0, 2))

        self._refresh_seg_chips()

    def _current_player_ms(self):
        """当前播放位置（ms）；无法获取时返回 None。"""
        if not self._player:
            return None
        try:
            t = self._player.get_time()
        except Exception:
            return None
        if t is None or t < 0:
            return None
        return int(t)

    def _flash_seg_status(self, msg: str):
        self._seg_status.configure(text=msg, fg="#cc6666")
        self.app.after(2500, self._update_seg_status)

    def _update_seg_status(self):
        if self._seg_mark_start is not None:
            self._seg_status.configure(
                text=f"起点 {_ms_to_hms(self._seg_mark_start)} → 待标记终点",
                fg="#d8a657")
        elif self._segments:
            total = sum(b - a for a, b in self._segments)
            self._seg_status.configure(
                text=f"{len(self._segments)} 段 / 保留 {_ms_to_hms(total)}",
                fg=HL_GREEN)
        else:
            self._seg_status.configure(text="", fg="#666666")

    def _refresh_seg_chips(self):
        fr = self._seg_chips
        for ch in fr.winfo_children():
            ch.destroy()
        fr._cells.clear()

        self._segments.sort()
        if not self._segments:
            lbl = tk.Label(fr, text="全程烧录（未设置片段）", bg=BG, fg="#555555",
                           font=("Segoe UI", 8))
            fr.add(lbl)
        else:
            for i, (a, b) in enumerate(self._segments, 1):
                cell = tk.Frame(fr, bg="#1e2a33")
                t = tk.Label(cell, text=f"{i}  {_ms_to_hms(a)} → {_ms_to_hms(b)}",
                             bg="#1e2a33", fg="#aaccdd", font=("Segoe UI", 8),
                             cursor="hand2", padx=6, pady=1)
                t.pack(side="left")
                t.bind("<Button-1>",
                       lambda e, s=a, en=b: self._preview_segment(s, en))
                x = tk.Label(cell, text="✕", bg="#1e2a33", fg="#cc6666",
                             font=("Segoe UI", 8), cursor="hand2", padx=4)
                x.pack(side="left")
                x.bind("<Button-1>", lambda e, s=a, en=b: self._delete_segment(s, en))
                fr.add(cell)

        fr.after_idle(fr._reflow)
        self._update_seg_status()

    def _mark_seg_start(self):
        t = self._current_player_ms()
        if t is None:
            self._flash_seg_status("无法获取当前时间（先加载并播放视频）")
            return
        self._seg_mark_start = t
        self._update_seg_status()

    def _mark_seg_end(self):
        t = self._current_player_ms()
        if t is None:
            self._flash_seg_status("无法获取当前时间")
            return
        if self._seg_mark_start is None:
            self._flash_seg_status("请先标记起点")
            return
        a, b = self._seg_mark_start, t
        if b < a:                      # 容错：终点早于起点则交换
            a, b = b, a
        if b - a < 200:
            self._flash_seg_status("片段太短（<0.2s）")
            return
        self._segments.append((a, b))
        self._seg_mark_start = None
        self._refresh_seg_chips()

    def _delete_segment(self, start_ms, end_ms):
        try:
            self._segments.remove((start_ms, end_ms))
        except ValueError:
            return
        self._stop_seg_loop()
        self._refresh_seg_chips()

    def _clear_segments(self, silent: bool = False):
        if not silent and not self._segments and self._seg_mark_start is None:
            return
        self._stop_seg_loop()
        self._segments = []
        self._seg_mark_start = None
        if hasattr(self, "_seg_chips"):
            self._refresh_seg_chips()

    def _preview_segment(self, start_ms, end_ms):
        """点击片段：在 [起点, 终点] 间循环播放，便于核对边界切得准不准。
        再次点击同一片段则停止循环。"""
        if not self._player:
            return
        s = int(start_ms)
        e = max(int(end_ms), s + 200)
        # 再次点击正在循环的同一片段 → 停止（提供一个不依赖其他操作的关闭方式）
        if self._seg_looping and self._seg_loop_start == s and self._seg_loop_end == e:
            self._stop_seg_loop()
            return
        self._stop_sub_loop()        # 与字幕编辑循环互斥
        self._seg_loop_start = s
        self._seg_loop_end   = e
        self._seg_looping    = True
        if self._seg_loop_poll_id:
            self.app.after_cancel(self._seg_loop_poll_id)
            self._seg_loop_poll_id = None
        try:
            self._player.set_time(s)
            if not self._player.is_playing():
                self._player.play()
                self._play_btn.configure(text="⏸")
        except Exception:
            pass
        self._seg_loop_poll_id = self.app.after(100, self._seg_loop_tick)

    def _seg_loop_tick(self):
        self._seg_loop_poll_id = None
        if not self._seg_looping or not self._player:
            return
        try:
            t = self._player.get_time()
        except Exception:
            t = -1
        if t < 0 or t >= self._seg_loop_end:
            try:
                self._player.set_time(self._seg_loop_start)
                if not self._player.is_playing():
                    self._player.play()
                    self._play_btn.configure(text="⏸")
            except Exception:
                pass
        self._seg_loop_poll_id = self.app.after(100, self._seg_loop_tick)

    def _stop_seg_loop(self):
        self._seg_looping = False
        if self._seg_loop_poll_id:
            self.app.after_cancel(self._seg_loop_poll_id)
            self._seg_loop_poll_id = None

    def _segments_for_burn(self):
        """合并重叠、按时间排序后的 [(start_s, end_s)]；空列表表示全程烧录。

        第三步（裁剪烧录）从此处取片段，保证传给编码流水线的区间不重叠、有序。
        """
        if not self._segments:
            return []
        segs = sorted(self._segments)
        merged = [list(segs[0])]
        for a, b in segs[1:]:
            if a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        return [(a / 1000.0, b / 1000.0) for a, b in merged]

    # ── Treeview ──────────────────────────────────────────────────────────────

    def _build_tree(self, parent):
        import tkinter.font as tkfont
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        cols = ("序号", "开始时间", "结束时间", "内容")
        self._tree = ttk.Treeview(parent, columns=cols, show="headings",
                                  selectmode="browse")

        _f    = tkfont.Font(family="Segoe UI", size=9)
        ts_w  = _f.measure("00:00:00,000") + 18
        idx_w = _f.measure("000") + 18

        col_cfg = {
            "序号":    (idx_w, idx_w, False, "center"),
            "开始时间": (ts_w,  ts_w,  False, "center"),
            "结束时间": (ts_w,  ts_w,  False, "center"),
            "内容":    (300,   50,    True,  "w"),
        }
        for c, (w, mw, stretch, anchor) in col_cfg.items():
            self._tree.heading(c, text=c)
            self._tree.column(c, width=w, minwidth=mw, stretch=stretch, anchor=anchor)

        self._ts_col_width = ts_w

        vsb = ttk.Scrollbar(parent, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self._tree.bind("<Configure>", self._on_tree_resize)

        self._tree.tag_configure("current", background="#1a3a5a", foreground="#ffffff")
        self._tree.bind("<ButtonRelease-1>", self._on_tree_click)
        self._tree.bind("<Double-1>",        self._on_tree_dbl_click)
        self._tree.bind("<Button-3>",        self._on_tree_rclick)

        self._ctx_menu = tk.Menu(self._tree, tearoff=0, bg="#2a2a2a", fg="#cccccc",
                                 activebackground="#0a5a9a", activeforeground="#ffffff",
                                 relief="flat", bd=0)
        self._ctx_menu.add_command(label="删除该条字幕",
                                   command=self._delete_selected_sub)

        self._edit_entry    = None
        self._edit_iid      = None
        self._edit_idx      = -1
        self._edit_original = ""

        _style_tree()

    # ── Right pane ────────────────────────────────────────────────────────────

    def _build_right(self, p):
        p.rowconfigure(0, weight=1)

        canvas = tk.Canvas(p, bg=BG, highlightthickness=0, bd=0)
        vsb    = ttk.Scrollbar(p, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        inner = tk.Frame(canvas, bg=BG)
        inner.columnconfigure(0, weight=1)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        def _on_canvas_resize(e):
            canvas.itemconfigure(win_id, width=e.width)
            if hasattr(self, "_log_outer_row"):
                new_h = max(e.height, inner.winfo_reqheight())
                canvas.itemconfigure(win_id, height=new_h)
        canvas.bind("<Configure>", _on_canvas_resize)

        def _wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        r = 0
        r = self._build_file_section(inner, r)
        r = self._build_style_panel(inner, r)
        r = self._build_preview_area(inner, r)
        r = self._build_banner_panel(inner, r)
        r = self._build_burn_section(inner, r)
        inner.rowconfigure(self._log_outer_row, weight=1)

    # ── File section ──────────────────────────────────────────────────────────

    def _build_file_section(self, p, r):
        tk.Label(p, text="  文 件", bg=BG, fg="#555555",
                 font=("Segoe UI", 8)).grid(row=r, column=0, sticky="w",
                                            padx=12, pady=(12, 0))
        r += 1

        self._lbl(p, "视频文件").grid(row=r, column=0, sticky="w", padx=12, pady=(6, 0))
        r += 1
        self.video_var = tk.StringVar()
        self.video_var.trace_add("write", self._check_ready)
        vf = tk.Frame(p, bg=BG)
        vf.grid(row=r, column=0, sticky="ew", padx=12, pady=(2, 0))
        vf.columnconfigure(0, weight=1)
        self._video_entry = tk.Entry(vf, textvariable=self.video_var, bg="#252525",
                                     fg="#aaaaaa", insertbackground="white",
                                     relief="flat", font=("Segoe UI", 9), bd=4)
        self._video_entry.grid(row=0, column=0, sticky="ew", ipady=4)
        self._video_entry.bind("<Return>",   lambda e: self._trigger_video_load())
        self._video_entry.bind("<FocusOut>", lambda e: self._trigger_video_load())
        tk.Button(vf, text="浏览", command=self._browse_video,
                  bg="#3a3a3a", fg="#cccccc", relief="flat", padx=10,
                  font=("Segoe UI", 9), cursor="hand2",
                  activebackground="#4a4a4a").grid(row=0, column=1, padx=(6, 0))
        if HAS_DND:
            for w in [vf, self._video_entry]:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop_video)
        r += 1

        srt_hdr = tk.Frame(p, bg=BG)
        srt_hdr.columnconfigure(1, weight=1)
        srt_hdr.grid(row=r, column=0, sticky="ew", padx=12, pady=(8, 0))
        self._lbl(srt_hdr, "SRT 字幕文件").grid(row=0, column=0, sticky="w")
        self._status_lbl = tk.Label(srt_hdr, text="", bg=BG, fg=HL_GREEN,
                                    font=("Segoe UI", 9), anchor="e")
        self._status_lbl.grid(row=0, column=1, sticky="e", padx=(8, 0))
        r += 1
        self.srt_var          = tk.StringVar()
        self.srt_var.trace_add("write", self._check_ready)
        self._srt_display_var = tk.StringVar()
        self._srt_combo_paths: list = []

        _style_srt_combo()
        sf = tk.Frame(p, bg=BG)
        sf.grid(row=r, column=0, sticky="ew", padx=12, pady=(2, 0))
        sf.columnconfigure(0, weight=1)
        self._srt_combo = ttk.Combobox(
            sf, textvariable=self._srt_display_var,
            style="Burn.TCombobox", font=("Segoe UI", 9), state="normal")
        self._srt_combo.grid(row=0, column=0, sticky="ew", ipady=4)
        self._srt_combo.bind("<<ComboboxSelected>>", self._on_srt_combo_selected)
        self._srt_combo.bind("<Return>", lambda e: self._on_srt_combo_enter())
        tk.Button(sf, text="浏览", command=self._browse_srt,
                  bg="#3a3a3a", fg="#cccccc", relief="flat", padx=10,
                  font=("Segoe UI", 9), cursor="hand2",
                  activebackground="#4a4a4a").grid(row=0, column=1, padx=(6, 0))
        if HAS_DND:
            for w in [sf, self._srt_combo]:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop_srt)
        r += 1

        return r

    # ── Style panel ───────────────────────────────────────────────────────────

    def _build_style_panel(self, p, r):
        tk.Frame(p, bg="#333333", height=1).grid(row=r, column=0, sticky="ew",
                                                  padx=12, pady=(10, 0))
        r += 1

        hdr = tk.Frame(p, bg=BG)
        hdr.grid(row=r, column=0, sticky="ew", padx=12, pady=(4, 0))
        hdr.columnconfigure(0, weight=1)
        r += 1

        tk.Label(hdr, text="  字幕样式", bg=BG, fg="#555555",
                 font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w")
        self._style_toggle_btn = tk.Button(
            hdr, text="▶ 展开", command=self._toggle_style_panel,
            bg=BG, fg="#555555", relief="flat",
            font=("Segoe UI", 8), padx=6, pady=0, cursor="hand2",
            activebackground="#2a2a2a", activeforeground="#888888")
        self._style_toggle_btn.grid(row=0, column=1, sticky="e")

        self._style_inner = tk.Frame(p, bg=BG)
        self._style_inner.columnconfigure(1, weight=1)
        self._style_inner.grid(row=r, column=0, sticky="ew", padx=12, pady=(0, 4))
        self._style_inner.grid_remove()
        r += 1

        f = self._style_inner

        # 字体 combobox（全宽单独一行）
        tk.Label(f, text="字体", bg=BG, fg="#888888",
                 font=("Segoe UI", 9), anchor="w"
                 ).grid(row=0, column=0, sticky="w", padx=(0, 6), pady=(2, 0))
        _style_srt_combo()
        ttk.Combobox(f, textvariable=self._style_font, values=_FONT_NAMES,
                     style="Burn.TCombobox", font=("Segoe UI", 9),
                     state="readonly"
                     ).grid(row=0, column=1, sticky="ew", pady=(2, 0))

        # Flow frame：自动换行，每行排满再换（动态）
        _SP = dict(bg="#252525", fg="#aaaaaa", insertbackground="white",
                   buttonbackground="#2a2a2a", relief="flat",
                   font=("Segoe UI", 9), width=6, justify="center", bd=2)

        flow = _ItemFlowFrame(f, bg=BG)
        flow.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 2))

        def _fspin(text, var, lo, hi):
            c = tk.Frame(flow, bg=BG)
            tk.Label(c, text=text, bg=BG, fg="#888888",
                     font=("Segoe UI", 9), anchor="w").pack(side="left")
            tk.Spinbox(c, from_=lo, to=hi, textvariable=var,
                       **_SP).pack(side="left", padx=(4, 0))
            flow.add(c)

        _fspin("字号",    self._style_size,   8,   200)

        bold_c = tk.Frame(flow, bg=BG)
        tk.Label(bold_c, text="加粗", bg=BG, fg="#888888",
                 font=("Segoe UI", 9), anchor="w").pack(side="left")
        self._bold_btn = tk.Button(
            bold_c, text="关", command=self._toggle_bold,
            bg="#2a2a2a", fg="#888888", relief="flat",
            font=("Segoe UI", 9), padx=10, pady=1,
            cursor="hand2", activebackground="#3a3a3a")
        self._bold_btn.pack(side="left", padx=(4, 0))
        if self._style_bold.get():
            self._bold_btn.configure(text="开", bg="#1a3a1a", fg="#5a9a5a")
        flow.add(bold_c)

        _fspin("行间距",  self._style_lsp,    0,   120)
        _fspin("圆角",    self._style_corner, 0,   120)
        _fspin("横向边距", self._style_padx,   0,   300)
        _fspin("纵向边距", self._style_pady,   0,   150)
        _fspin("背景透明", self._style_alpha,   0,   100)
        _fspin("纵向微调", self._style_voffset, -30, 30)

        # 文字颜色（flow cell：白/黑 + 调色盘）
        tc_c = tk.Frame(flow, bg=BG)
        tk.Label(tc_c, text="文字颜色", bg=BG, fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        self._text_color_btns = {}
        for name in ("白", "黑"):
            b = tk.Button(tc_c, text=name, relief="flat",
                          bg="#2a2a2a", fg="#cccccc",
                          font=("Segoe UI", 9), padx=8, pady=2,
                          cursor="hand2", activebackground="#3a3a3a",
                          command=lambda n=name: self._set_text_color(n))
            b.pack(side="left", padx=(0, 3))
            self._text_color_btns[name] = b
        self._text_custom_btn = tk.Button(
            tc_c, text="🎨", relief="flat",
            bg="#2a2a2a", fg="#cccccc",
            font=("Segoe UI", 9), padx=6, pady=2,
            cursor="hand2", activebackground="#3a3a3a",
            command=self._pick_text_color)
        self._text_custom_btn.pack(side="left")
        flow.add(tc_c)
        self._set_text_color(self._style_tcolor.get(), silent=True)

        # 背景颜色（flow cell：黑/白 + 调色盘）
        bc_c = tk.Frame(flow, bg=BG)
        tk.Label(bc_c, text="背景颜色", bg=BG, fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        self._bg_color_btns = {}
        for name in ("黑", "白"):
            b = tk.Button(bc_c, text=name, relief="flat",
                          bg="#2a2a2a", fg="#cccccc",
                          font=("Segoe UI", 9), padx=8, pady=2,
                          cursor="hand2", activebackground="#3a3a3a",
                          command=lambda n=name: self._set_bg_color(n))
            b.pack(side="left", padx=(0, 3))
            self._bg_color_btns[name] = b
        self._bg_custom_btn = tk.Button(
            bc_c, text="🎨", relief="flat",
            bg="#2a2a2a", fg="#cccccc",
            font=("Segoe UI", 9), padx=6, pady=2,
            cursor="hand2", activebackground="#3a3a3a",
            command=self._pick_bg_color)
        self._bg_custom_btn.pack(side="left")
        flow.add(bc_c)
        self._set_bg_color(self._style_bcolor.get(), silent=True)

        return r

    # ── Preview area ──────────────────────────────────────────────────────────

    def _build_preview_area(self, p, r):
        return r

    # ── Banner panel (collapsible) ────────────────────────────────────────────

    def _build_banner_panel(self, p, r):
        tk.Frame(p, bg="#333333", height=1).grid(row=r, column=0, sticky="ew",
                                                   padx=12, pady=(10, 0))
        r += 1

        hdr = tk.Frame(p, bg=BG)
        hdr.grid(row=r, column=0, sticky="ew", padx=12, pady=(4, 0))
        hdr.columnconfigure(0, weight=1)
        r += 1

        tk.Label(hdr, text="  横幅标题配置", bg=BG, fg="#555555",
                 font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w")
        self._banner_toggle_btn = tk.Button(
            hdr, text="▶ 展开", command=self._toggle_banner_panel,
            bg=BG, fg="#555555", relief="flat",
            font=("Segoe UI", 8), padx=6, pady=0, cursor="hand2",
            activebackground="#2a2a2a", activeforeground="#888888")
        self._banner_toggle_btn.grid(row=0, column=1, sticky="e")

        # 标题文字：始终可见
        text_row = tk.Frame(p, bg=BG)
        text_row.columnconfigure(1, weight=1)
        text_row.grid(row=r, column=0, sticky="ew", padx=12, pady=(4, 0))
        r += 1
        tk.Label(text_row, text="标题文字", bg=BG, fg="#888888",
                 font=("Segoe UI", 9), width=9, anchor="w"
                 ).grid(row=0, column=0, sticky="w")
        tk.Entry(text_row, textvariable=self._banner_text, bg="#252525", fg="#aaaaaa",
                 insertbackground="white", relief="flat",
                 font=("Segoe UI", 9), bd=4
                 ).grid(row=0, column=1, sticky="ew", ipady=3)

        # 参数配置：可折叠
        self._banner_inner = tk.Frame(p, bg=BG)
        self._banner_inner.columnconfigure(1, weight=1)
        self._banner_inner.grid(row=r, column=0, sticky="ew", padx=12, pady=(0, 4))
        self._banner_inner.grid_remove()
        r += 1

        bi = self._banner_inner

        # 字体（全宽行）
        tk.Label(bi, text="字体", bg=BG, fg="#888888",
                 font=("Segoe UI", 9), width=9, anchor="w"
                 ).grid(row=0, column=0, sticky="w", pady=(4, 0))
        _style_srt_combo()
        ttk.Combobox(bi, textvariable=self._banner_font,
                     values=_FONT_NAMES, style="Burn.TCombobox",
                     font=("Segoe UI", 9), state="readonly"
                     ).grid(row=0, column=1, sticky="ew", pady=(4, 0))

        # Flow frame：其余参数自动换行
        _BSP = dict(bg="#252525", fg="#aaaaaa", insertbackground="white",
                    buttonbackground="#2a2a2a", relief="flat",
                    font=("Segoe UI", 9), width=6, justify="center", bd=2)

        bflow = _ItemFlowFrame(bi, bg=BG)
        bflow.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 2))

        def _bspin(text, var, lo, hi):
            c = tk.Frame(bflow, bg=BG)
            tk.Label(c, text=text, bg=BG, fg="#888888",
                     font=("Segoe UI", 9)).pack(side="left")
            tk.Spinbox(c, from_=lo, to=hi, textvariable=var,
                       **_BSP).pack(side="left", padx=(4, 0))
            bflow.add(c)

        _bspin("字号",   self._banner_size,    8,   200)

        # 加粗
        bold_c = tk.Frame(bflow, bg=BG)
        tk.Label(bold_c, text="加粗", bg=BG, fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left")
        self._banner_bold_btn = tk.Button(
            bold_c,
            text="开" if self._banner_bold.get() else "关",
            command=self._toggle_banner_bold,
            bg="#1a3a1a" if self._banner_bold.get() else "#2a2a2a",
            fg="#5a9a5a" if self._banner_bold.get() else "#888888",
            relief="flat", font=("Segoe UI", 9), padx=10, pady=1,
            cursor="hand2", activebackground="#3a3a3a")
        self._banner_bold_btn.pack(side="left", padx=(4, 0))
        bflow.add(bold_c)

        # 文字颜色
        btc_c = tk.Frame(bflow, bg=BG)
        tk.Label(btc_c, text="文字颜色", bg=BG, fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        self._banner_tcolor_btn = tk.Button(
            btc_c, text="选择颜色", command=self._pick_banner_tcolor,
            bg="#252525", fg="#cccccc", relief="flat",
            font=("Segoe UI", 9), padx=8, pady=2,
            cursor="hand2", activebackground="#3a3a3a")
        self._banner_tcolor_btn.pack(side="left")
        self._update_color_btn(self._banner_tcolor_btn, self._banner_tcolor.get())
        bflow.add(btc_c)

        # 背景颜色
        bbc_c = tk.Frame(bflow, bg=BG)
        tk.Label(bbc_c, text="背景颜色", bg=BG, fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        self._banner_bcolor_btn = tk.Button(
            bbc_c, text="选择颜色", command=self._pick_banner_bcolor,
            bg="#252525", fg="#cccccc", relief="flat",
            font=("Segoe UI", 9), padx=8, pady=2,
            cursor="hand2", activebackground="#3a3a3a")
        self._banner_bcolor_btn.pack(side="left")
        self._update_color_btn(self._banner_bcolor_btn, self._banner_bcolor.get())
        bflow.add(bbc_c)

        _bspin("横幅高度", self._banner_height,  10, 400)
        _bspin("纵向微调", self._banner_voffset, -30,  30)

        # 位置
        pos_c = tk.Frame(bflow, bg=BG)
        tk.Label(pos_c, text="位置", bg=BG, fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        for label, val in [("顶部", "top"), ("底部", "bottom")]:
            tk.Radiobutton(pos_c, text=label, variable=self._banner_pos,
                           value=val, bg=BG, fg="#888888",
                           selectcolor=BG, activebackground=BG,
                           font=("Segoe UI", 9)
                           ).pack(side="left", padx=(0, 4))
        bflow.add(pos_c)

        # 对齐
        aln_c = tk.Frame(bflow, bg=BG)
        tk.Label(aln_c, text="对齐", bg=BG, fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        for label, val in [("左", "left"), ("居中", "center"), ("右", "right")]:
            tk.Radiobutton(aln_c, text=label, variable=self._banner_align,
                           value=val, bg=BG, fg="#888888",
                           selectcolor=BG, activebackground=BG,
                           font=("Segoe UI", 9)
                           ).pack(side="left", padx=(0, 4))
        bflow.add(aln_c)

        return r

    # ── Burn section ──────────────────────────────────────────────────────────

    def _build_burn_section(self, p, r):
        tk.Frame(p, bg="#333333", height=1).grid(row=r, column=0, sticky="ew",
                                                   padx=12, pady=(12, 0))
        r += 1

        hdr = tk.Frame(p, bg=BG)
        hdr.grid(row=r, column=0, sticky="ew", padx=12, pady=(4, 0))
        hdr.columnconfigure(0, weight=1)
        r += 1

        tk.Label(hdr, text="  烧录选项", bg=BG, fg="#555555",
                 font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w")
        self._burn_opts_toggle_btn = tk.Button(
            hdr, text="▶ 展开", command=self._toggle_burn_opts_panel,
            bg=BG, fg="#555555", relief="flat",
            font=("Segoe UI", 8), padx=6, pady=0, cursor="hand2",
            activebackground="#2a2a2a", activeforeground="#888888")
        self._burn_opts_toggle_btn.grid(row=0, column=1, sticky="e")

        self._burn_opts_inner = tk.Frame(p, bg=BG)
        self._burn_opts_inner.columnconfigure(0, weight=1)
        self._burn_opts_inner.grid(row=r, column=0, sticky="ew", padx=12, pady=(0, 4))
        self._burn_opts_inner.grid_remove()
        r += 1

        bi = self._burn_opts_inner

        cb_f = tk.Frame(bi, bg=BG)
        cb_f.grid(row=0, column=0, sticky="w", pady=(4, 0))
        tk.Checkbutton(cb_f, text="同时烧录字幕 + 横幅",
                       variable=self._with_banner,
                       bg=BG, fg="#888888", selectcolor=BG,
                       activebackground=BG, font=("Segoe UI", 9)
                       ).pack(side="left")

        rc_f = tk.Frame(bi, bg=BG)
        rc_f.grid(row=1, column=0, sticky="w", pady=(2, 0))
        tk.Checkbutton(rc_f, text="圆角字幕背景（PIL 渲染，较慢）",
                       variable=self._burn_round_corners,
                       bg=BG, fg="#888888", selectcolor=BG,
                       activebackground=BG, font=("Segoe UI", 9)
                       ).pack(side="left")
        tk.Label(rc_f, text="  关闭则用 ffmpeg 渲染（直角，速度快 5–8×）",
                 bg=BG, fg="#555555", font=("Segoe UI", 8)
                 ).pack(side="left")

        # ── Output quality presets ────────────────────────────────────────────
        tk.Label(bi, text="  输出画质", bg=BG, fg="#555555",
                 font=("Segoe UI", 8)).grid(row=2, column=0, sticky="w",
                                            pady=(10, 0))

        preset_outer = tk.Frame(bi, bg=BG)
        preset_outer.grid(row=3, column=0, sticky="ew", pady=(2, 0))
        preset_outer.columnconfigure(0, weight=1)

        _presets = [
            ("fast",     "🚀 极速",    "720p · 30fps · 2.5 Mbps · 文件最小"),
            ("balanced", "⚖️ 均衡",   "1080p · 30fps · 8 Mbps · 推荐"),
            ("quality",  "💎 最高画质", "原始分辨率 · 原始帧率 · 15 Mbps"),
            ("custom",   "🔧 自定义",  "手动设置分辨率 / 码率（帧率随源）"),
        ]
        for i, (key, label, desc) in enumerate(_presets):
            row_bg = "#252525"
            rf = tk.Frame(preset_outer, bg=row_bg, cursor="hand2")
            rf.grid(row=i, column=0, sticky="ew", pady=1)
            rf.columnconfigure(2, weight=1)
            rf.bind("<Button-1>", lambda e, k=key: self._select_burn_preset(k))

            rb = tk.Radiobutton(
                rf, variable=self._burn_preset_var, value=key,
                bg=row_bg, fg="#cccccc", activebackground=row_bg,
                selectcolor=BG, bd=0, highlightthickness=0,
                command=self._on_burn_preset_change,
            )
            rb.grid(row=0, column=0, padx=(10, 2), pady=5)
            tk.Label(rf, text=label, bg=row_bg, fg="#dddddd",
                     font=("Segoe UI", 9, "bold"), cursor="hand2",
                     ).grid(row=0, column=1, sticky="w", padx=4)
            tk.Label(rf, text=desc, bg=row_bg, fg="#666666",
                     font=("Segoe UI", 9), cursor="hand2",
                     ).grid(row=0, column=2, sticky="e", padx=(0, 12))

        # Custom settings row (hidden by default)
        self._burn_custom_frame = tk.Frame(bi, bg="#1c2535")
        self._burn_custom_frame.grid(row=4, column=0, sticky="ew", pady=0)
        self._burn_custom_frame.columnconfigure(0, weight=1)

        cust = tk.Frame(self._burn_custom_frame, bg="#1c2535")
        cust.pack(fill="x", padx=12, pady=8)

        tk.Label(cust, text="分辨率:", bg="#1c2535", fg="#aaaaaa",
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=(0, 6))
        res_cb = ttk.Combobox(cust, textvariable=self._burn_res_var, width=13,
                               values=["3840x2160", "2560x1440", "1920x1080",
                                       "1280x720", "854x480", "原始（不缩放）"])
        res_cb.grid(row=0, column=1, padx=(0, 16))

        tk.Label(cust, text="视频码率:", bg="#1c2535", fg="#aaaaaa",
                 font=("Segoe UI", 9)).grid(row=0, column=2, sticky="w", padx=(0, 6))
        tk.Entry(cust, textvariable=self._burn_vbitrate_var, width=7,
                 bg="#253050", fg="#cccccc", insertbackground="white",
                 relief="flat", font=("Segoe UI", 9)).grid(row=0, column=3)
        tk.Label(cust, text="kbps", bg="#1c2535", fg="#555555",
                 font=("Segoe UI", 9)).grid(row=0, column=4, padx=(3, 0))

        self._burn_custom_frame.grid_remove()

        # ── Output path ───────────────────────────────────────────────────────
        out_f = tk.Frame(bi, bg=BG)
        out_f.columnconfigure(1, weight=1)
        out_f.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        tk.Label(out_f, text="输出路径:", bg=BG, fg="#888888",
                 font=("Segoe UI", 9)).grid(row=0, column=0, padx=(0, 8), sticky="w")
        self._burn_out_var = tk.StringVar()
        tk.Entry(out_f, textvariable=self._burn_out_var,
                 bg="#252525", fg="#aaaaaa", insertbackground="white",
                 relief="flat", font=("Segoe UI", 9),
                 ).grid(row=0, column=1, sticky="ew")
        tk.Button(out_f, text="另存为", command=self._browse_burn_output,
                  bg="#3a3a3a", fg="#cccccc", relief="flat", padx=10, pady=2,
                  font=("Segoe UI", 9), cursor="hand2",
                  activebackground="#4a4a4a").grid(row=0, column=2, padx=(6, 0))

        self._burn_btn = tk.Button(p, text="▶  开始烧录", command=self._burn_btn_click,
                                   bg="#1e3a4a", fg="#aaccdd", relief="flat",
                                   font=("Segoe UI", 11, "bold"), padx=24, pady=8,
                                   cursor="hand2", activebackground="#2a5a6a",
                                   state="disabled")
        self._burn_btn.grid(row=r, column=0, pady=(12, 4))
        r += 1

        self._encoder_lbl = tk.Label(p, text="编码器: 检测中…",
                                     bg=BG, fg="#444444", font=("Segoe UI", 8))
        self._encoder_lbl.grid(row=r, column=0, pady=(0, 4))
        r += 1

        prog_f = tk.Frame(p, bg=BG)
        prog_f.columnconfigure(0, weight=1)
        prog_f.grid(row=r, column=0, sticky="ew", padx=12, pady=(0, 4))
        # 进度/完成明细写在进度条「上方」（row=0），进度条在下方（row=1）
        self._burn_prog_label = tk.Label(prog_f, text="", bg=BG, fg="#888888",
                                         font=("Segoe UI", 8), justify="left",
                                         anchor="w")
        self._burn_prog_label.grid(row=0, column=0, sticky="w", pady=(0, 3))
        self._progress = ttk.Progressbar(prog_f, mode="determinate")
        self._progress.grid(row=1, column=0, sticky="ew")
        r += 1

        # Log area
        self._log_outer_row = r
        log_outer = tk.Frame(p, bg=BG)
        log_outer.columnconfigure(0, weight=1)
        log_outer.rowconfigure(0, weight=1)
        log_outer.grid(row=r, column=0, sticky="nsew", padx=12, pady=(4, 0))
        r += 1

        self._burn_log_text = tk.Text(
            log_outer, height=1, bg="#111111", fg="#666666",
            font=("Consolas", 8), relief="flat", bd=0,
            state="disabled", wrap="word",
            selectbackground="#1a3a5a", selectforeground="#ffffff")
        log_vsb = ttk.Scrollbar(log_outer, orient="vertical",
                                 command=self._burn_log_text.yview)
        self._burn_log_text.configure(yscrollcommand=log_vsb.set)
        self._burn_log_text.grid(row=0, column=0, sticky="nsew")
        log_vsb.grid(row=0, column=1, sticky="ns")
        r += 1

        tk.Frame(p, bg=BG, height=12).grid(row=r, column=0)
        r += 1
        return r

    # ── Spinbox-only helper (replaces old slider+entry) ───────────────────────

    def _se_row(self, f, row, label, var, lo, hi, lbl_w=9):
        tk.Label(f, text=label, bg=BG, fg="#888888",
                 font=("Segoe UI", 9), width=lbl_w, anchor="w"
                 ).grid(row=row, column=0, sticky="w", pady=(2, 0))
        tk.Spinbox(
            f, from_=lo, to=hi, textvariable=var,
            bg="#252525", fg="#aaaaaa", insertbackground="white",
            buttonbackground="#2a2a2a", relief="flat",
            font=("Segoe UI", 9), width=7, justify="center", bd=2,
        ).grid(row=row, column=1, sticky="w", padx=(6, 0), pady=(2, 0))

    # ── Style controls ────────────────────────────────────────────────────────

    def _toggle_bold(self):
        new_val = not self._style_bold.get()
        self._style_bold.set(new_val)
        if new_val:
            self._bold_btn.configure(text="开", bg="#1a3a1a", fg="#5a9a5a")
        else:
            self._bold_btn.configure(text="关", bg="#2a2a2a", fg="#888888")

    def _toggle_banner_bold(self):
        new_val = not self._banner_bold.get()
        self._banner_bold.set(new_val)
        if new_val:
            self._banner_bold_btn.configure(text="开", bg="#1a3a1a", fg="#5a9a5a")
        else:
            self._banner_bold_btn.configure(text="关", bg="#2a2a2a", fg="#888888")

    def _set_text_color(self, name_or_hex: str, silent: bool = False):
        self._style_tcolor.set(name_or_hex)
        is_preset = name_or_hex in self._text_color_btns
        for n, b in self._text_color_btns.items():
            b.configure(bg="#1a3a5a" if n == name_or_hex else "#2a2a2a",
                        fg="#aaccff" if n == name_or_hex else "#cccccc")
        if hasattr(self, "_text_custom_btn"):
            if is_preset:
                self._text_custom_btn.configure(bg="#2a2a2a", fg="#cccccc", text="🎨")
            else:
                hex_c = _resolve_color(name_or_hex, _TEXT_COLORS)
                self._update_color_btn(self._text_custom_btn, hex_c)

    def _set_bg_color(self, name_or_hex: str, silent: bool = False):
        self._style_bcolor.set(name_or_hex)
        is_preset = name_or_hex in self._bg_color_btns
        for n, b in self._bg_color_btns.items():
            b.configure(bg="#1a3a5a" if n == name_or_hex else "#2a2a2a",
                        fg="#aaccff" if n == name_or_hex else "#cccccc")
        if hasattr(self, "_bg_custom_btn"):
            if is_preset:
                self._bg_custom_btn.configure(bg="#2a2a2a", fg="#cccccc", text="🎨")
            else:
                hex_c = _resolve_color(name_or_hex, _BG_COLORS)
                self._update_color_btn(self._bg_custom_btn, hex_c)

    def _pick_text_color(self):
        cur = _resolve_color(self._style_tcolor.get(), _TEXT_COLORS)
        _, hex_c = colorchooser.askcolor(color=cur, title="选择文字颜色")
        if hex_c:
            self._set_text_color(hex_c)

    def _pick_bg_color(self):
        cur = _resolve_color(self._style_bcolor.get(), _BG_COLORS)
        _, hex_c = colorchooser.askcolor(color=cur, title="选择背景颜色")
        if hex_c:
            self._set_bg_color(hex_c)

    def _pick_banner_tcolor(self):
        init = self._banner_tcolor.get()
        _, hex_c = colorchooser.askcolor(color=init, title="选择文字颜色")
        if hex_c:
            self._banner_tcolor.set(hex_c)
            self._update_color_btn(self._banner_tcolor_btn, hex_c)

    def _pick_banner_bcolor(self):
        init = self._banner_bcolor.get()
        _, hex_c = colorchooser.askcolor(color=init, title="选择背景颜色")
        if hex_c:
            self._banner_bcolor.set(hex_c)
            self._update_color_btn(self._banner_bcolor_btn, hex_c)

    def _update_color_btn(self, btn: tk.Button, hex_c: str):
        try:
            r, g, b = _hex_to_rgb(hex_c)
            fg = "#000000" if (r*299 + g*587 + b*114) > 128000 else "#ffffff"
            btn.configure(bg=hex_c, fg=fg, text=hex_c)
        except Exception:
            pass

    def _toggle_style_panel(self):
        self._style_expanded = not self._style_expanded
        if self._style_expanded:
            self._style_inner.grid()
            self._style_toggle_btn.configure(text="▼ 收起")
        else:
            self._style_inner.grid_remove()
            self._style_toggle_btn.configure(text="▶ 展开")

    def _toggle_banner_panel(self):
        self._banner_expanded = not self._banner_expanded
        if self._banner_expanded:
            self._banner_inner.grid()
            self._banner_toggle_btn.configure(text="▼ 收起")
        else:
            self._banner_inner.grid_remove()
            self._banner_toggle_btn.configure(text="▶ 展开")

    def _toggle_burn_opts_panel(self):
        self._burn_opts_expanded = not self._burn_opts_expanded
        if self._burn_opts_expanded:
            self._burn_opts_inner.grid()
            self._burn_opts_toggle_btn.configure(text="▼ 收起")
        else:
            self._burn_opts_inner.grid_remove()
            self._burn_opts_toggle_btn.configure(text="▶ 展开")

    # ── Preview ───────────────────────────────────────────────────────────────

    def _schedule_preview_update(self, *_):
        if self._preview_debounce_id:
            self.app.after_cancel(self._preview_debounce_id)
        self._preview_debounce_id = self.app.after(300, self._update_preview)

    def _update_preview(self, t_sec: float = None):
        self._preview_debounce_id = None
        if not HAS_PIL:
            return
        if self._player and self._player.is_playing():
            self._preview_label.place_forget()
            self._preview_photo = None
            return

        if self._loaded_video_path and HAS_AV:
            # 快速路径：PyAV 在后台线程直接读帧，无需 VLC snapshot 文件 I/O
            if t_sec is None:
                if self._preview_t_sec is not None:
                    t_sec = self._preview_t_sec   # 优先用缓存（字幕点击后 VLC seek 尚未完成）
                elif self._player:
                    t_ms = self._player.get_time()
                    t_sec = max(0.0, t_ms / 1000.0) if t_ms >= 0 else 0.0
            t_sec = t_sec or 0.0
            path  = self._loaded_video_path
            self._preview_gen += 1          # 每次新请求代次 +1
            gen   = self._preview_gen
            threading.Thread(
                target=self._grab_frame_pyav,
                args=(path, t_sec, gen),
                daemon=True,
            ).start()
        elif self._player and self._loaded_video_path:
            # 回退：VLC snapshot
            fd, tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            old_snap = self._preview_snap_tmp
            self._preview_snap_tmp = tmp
            if old_snap and old_snap != tmp:
                try:
                    os.remove(old_snap)
                except OSError:
                    pass
            try:
                self._player.video_take_snapshot(0, tmp, 0, 0)
                self.app.after(250, lambda: self._finish_preview(tmp))
            except Exception:
                self._render_preview_placeholder()
        else:
            self._render_preview_placeholder()

    def _grab_frame_pyav(self, video_path: str, t_sec: float, gen: int):
        """在后台线程用 PyAV 抓取指定时间的视频帧，结果交给主线程渲染。
        gen 为代次；若到达主线程时已被新请求取代则丢弃，避免旧帧覆盖新结果。"""
        try:
            with av.open(video_path) as container:
                container.seek(int(t_sec * 1_000_000), backward=True)
                for frame in container.decode(video=0):
                    img = frame.to_image()
                    def _show(i=img, g=gen, ts=t_sec):
                        if g == self._preview_gen:
                            self._apply_and_show_preview(i, ts)
                    self.app.after(0, _show)
                    return
        except Exception:
            pass
        def _fallback(g=gen, ts=t_sec):
            if g == self._preview_gen:
                self._render_preview_placeholder()
        self.app.after(0, _fallback)

    def _finish_preview(self, snap_path: str):
        try:
            if (not os.path.exists(snap_path)
                    or os.path.getsize(snap_path) == 0):
                self._render_preview_placeholder()
                return
            img = Image.open(snap_path).copy()
        except Exception:
            self._render_preview_placeholder()
            return
        self._apply_and_show_preview(img)

    def _render_preview_placeholder(self):
        img = Image.new("RGB", (640, 360), (20, 20, 20))
        self._apply_and_show_preview(img)

    def _apply_and_show_preview(self, img: "Image.Image", t_sec: float = None):
        text = self._get_preview_sub_text(t_sec)
        sp   = self._get_style_params()
        # ffmpeg 模式下预览也显示直角，与实际烧录一致
        if not getattr(self, "_burn_round_corners",
                       tk.BooleanVar(value=True)).get():
            sp = dict(sp, corner_radius=0)
        if text:
            img = render_subtitle_on_frame(img, text, sp)
        if self._with_banner.get():
            bp = self._get_banner_params()
            if bp.get("text", "").strip():
                img = render_banner_on_frame(img, bp)

        vf = self._video_frame
        vw = vf.winfo_width()
        vh = vf.winfo_height()
        if vw <= 1 or vh <= 1:
            vw, vh = 400, 225

        # 等比缩放，铺满视频区域（黑色背景填充余白）
        ratio = min(vw / img.width, vh / img.height)
        img   = img.resize((max(1, int(img.width * ratio)),
                             max(1, int(img.height * ratio))),
                            Image.LANCZOS)

        photo = ImageTk.PhotoImage(img)
        self._preview_photo = photo
        self._preview_label.configure(image=photo, text="", compound="none",
                                       bg="#000000")
        # 以 _left_pane 为坐标系，定位到 _video_frame 的完全相同位置
        self._preview_label.place(x=vf.winfo_x(), y=vf.winfo_y(),
                                   width=vw, height=vh)
        self._preview_label.lift()   # 提到 VLC HWND 上方

    def _get_preview_sub_text(self, t_sec: float = None) -> str:
        if self._subs:
            # 优先用调用方传入的精确时间戳（PyAV 帧时间），
            # 避免 VLC get_time() 异步未更新导致显示上一条字幕
            if t_sec is None and self._player:
                t_ms = self._player.get_time()
                if t_ms >= 0:
                    t_sec = t_ms / 1000.0
            if t_sec is not None:
                t = timedelta(seconds=t_sec)
                # 倒序遍历：当 t == N-1.end == N.start 时，正序会先命中 N-1；
                # 倒序先检查 N（start 更晚），正确返回 N
                for sub in reversed(self._subs):
                    if sub.start <= t <= sub.end:
                        return sub.content
            return self._subs[0].content
        return "示例字幕文字\nSample Subtitle"

    def _get_style_params(self) -> dict:
        return {
            "font_family":   self._style_font.get(),
            "font_size":     self._style_size.get(),
            "bold":          self._style_bold.get(),
            "line_spacing":  self._style_lsp.get(),
            "corner_radius": self._style_corner.get(),
            "pad_x":         self._style_padx.get(),
            "pad_y":         self._style_pady.get(),
            "bg_alpha":      self._style_alpha.get(),
            "voffset":       self._style_voffset.get(),
            "text_color":    self._style_tcolor.get(),
            "bg_color":      self._style_bcolor.get(),
        }

    def _get_banner_params(self) -> dict:
        return {
            "text":        self._banner_text.get(),
            "font_family": self._banner_font.get(),
            "font_size":   self._banner_size.get(),
            "bold":        self._banner_bold.get(),
            "text_color":  self._banner_tcolor.get(),
            "bg_color":    self._banner_bcolor.get(),
            "height":      self._banner_height.get(),
            "position":    self._banner_pos.get(),
            "align":       self._banner_align.get(),
            "voffset":     self._banner_voffset.get(),
        }

    # ── Encoder detection ─────────────────────────────────────────────────────

    def _detect_encoder_async(self):
        def _do():
            from backend.compress import detect_hw_encoder
            enc, desc = detect_hw_encoder()
            self._burn_encoder = enc
            # GPU 硬件编码用亮绿色突出（CPU 软编保持灰色，便于一眼区分）
            _fg = HL_GREEN if enc != "libx264" else "#888888"
            self.app.after(0, lambda d=desc, c=_fg: self._encoder_lbl.configure(
                text=f"编码器: {d}", fg=c))
        threading.Thread(target=_do, daemon=True).start()

    # ── Player: play / pause / seek / volume ──────────────────────────────────

    def _toggle_play(self):
        if not self._player:
            return
        self._stop_seg_loop()   # 用户手动播放/暂停 → 退出片段循环，不再抢控制
        if self._player.is_playing():
            t_ms = self._player.get_time()       # 暂停前采集——暂停后 VLC 内部时钟会漂回一段
            self._player.pause()
            self._play_btn.configure(text="▶")
            self._preview_t_sec = (t_ms / 1000.0) if t_ms >= 0 else None
            self._preview_gen  += 1              # 使任何在途线程失效
            self._schedule_preview_update()
        else:
            self._preview_t_sec = None           # 开始播放，清除点击缓存
            self._preview_label.place_forget()   # 播放时移开预览层，让 VLC 透出
            # 播放到结尾后 VLC 处于 Ended 状态，此时直接 play() 无效，
            # 必须先 stop() 复位，再 play() 才能从头重新播放。
            try:
                if self._player.get_state() == vlc.State.Ended:
                    self._player.stop()
            except Exception:
                pass
            self._player.play()
            self._play_btn.configure(text="⏸")
            self._start_poll()

    def _seek_press_cmd(self):
        self._dragging_seek = True
        self._stop_seg_loop()   # 用户拖动进度条 → 退出片段循环

    def _seek_drag_cmd(self, value):
        if self._player and self._duration_ms > 0:
            ms = int(value / 1000 * self._duration_ms)
            self._player.set_time(ms)
            self._time_lbl.configure(text=_ms_to_hms(ms))

    def _seek_release_cmd(self, value):
        self._dragging_seek = False
        self._preview_t_sec = None               # 进度条拖动，清除点击缓存
        self._preview_gen  += 1                  # 立即使在途线程失效
        if self._player and self._duration_ms > 0:
            ms = int(value / 1000 * self._duration_ms)
            self._player.set_time(ms)
        self._schedule_preview_update()

    def _vol_cmd(self, value):
        if self._player:
            self._player.audio_set_volume(int(value))

    # ── 500ms polling ─────────────────────────────────────────────────────────

    def _start_poll(self):
        if self._poll_id:
            self.app.after_cancel(self._poll_id)
            self._poll_id = None
        self._poll()

    def _poll(self):
        if self._player:
            self._update_controls()
            self._highlight_current_sub()
            if not self._player.is_playing():
                self._play_btn.configure(text="▶")
        self._poll_id = self.app.after(500, self._poll)

    def _update_controls(self):
        t = self._player.get_time()
        d = self._player.get_length()
        if d > 0:
            self._duration_ms = d
            self._dur_lbl.configure(text=_ms_to_hms(d))
        if t >= 0 and not self._dragging_seek:
            self._time_lbl.configure(text=_ms_to_hms(t))
            if self._duration_ms > 0:
                self._seek_slider.set(t / self._duration_ms * 1000)

    def _highlight_current_sub(self):
        if not self._subs or getattr(self, "_reloading_subs", False):
            return
        t_ms = self._player.get_time()
        if t_ms < 0:
            return
        t        = timedelta(milliseconds=t_ms)
        children = self._tree.get_children()
        new_iid  = None
        for iid, sub in zip(children, self._subs):
            if sub.start <= t <= sub.end:
                new_iid = iid
                break

        if new_iid == self._current_iid:
            return

        if self._current_iid and self._current_iid in children:
            tags = list(self._tree.item(self._current_iid, "tags"))
            if "current" in tags:
                tags.remove("current")
            self._tree.item(self._current_iid, tags=tags)

        if new_iid:
            tags = list(self._tree.item(new_iid, "tags"))
            if "current" not in tags:
                tags.append("current")
            self._tree.item(new_iid, tags=tags)
            self._tree.see(new_iid)

        self._current_iid = new_iid

    # ── VLC subtitle color ────────────────────────────────────────────────────

    def _pick_vlc_sub_color(self):
        _, hex_c = colorchooser.askcolor(color=self._vlc_sub_color, title="选择 VLC 字幕颜色")
        if hex_c:
            self._vlc_sub_color = hex_c
            self._vlc_color_btn.configure(fg=hex_c)
            self._reload_vlc_subs()

    # ── Timestamp column toggle ───────────────────────────────────────────────

    def _toggle_timestamps(self):
        self._ts_visible = not self._ts_visible
        if self._ts_visible:
            self._tree.configure(displaycolumns=("序号", "开始时间", "结束时间", "内容"))
            self._tree.column("开始时间", width=self._ts_col_width,
                              minwidth=self._ts_col_width)
            self._tree.column("结束时间", width=self._ts_col_width,
                              minwidth=self._ts_col_width)
            self._ts_btn.configure(text="隐藏时间", fg="#555555")
        else:
            self._tree.configure(displaycolumns=("序号", "内容"))
            self._ts_btn.configure(text="显示时间", fg="#888888")
        self.app.after(1, self._fit_content_col)

    def _fit_content_col(self):
        if self._ts_visible:
            return
        w = self._tree.winfo_width()
        if w <= 1:
            return
        idx_w = self._tree.column("序号", option="width")
        self._tree.column("内容", width=max(50, w - idx_w - 4))

    def _on_tree_resize(self, event):
        if not self._ts_visible:
            self._fit_content_col()

    # ── Treeview events ───────────────────────────────────────────────────────

    def _on_tree_click(self, event):
        iid = self._tree.identify_row(event.y)
        if not iid or not self._player:
            return
        idx = self._tree.get_children().index(iid)
        if 0 <= idx < len(self._subs):
            t_sec = self._subs[idx].start.total_seconds()
            self._player.set_time(int(t_sec * 1000))
            self._preview_t_sec = t_sec      # 缓存精确时间，_on_video_frame_configure 二次触发时复用
            self._preview_gen  += 1          # 立即使所有在途线程失效，不依赖 after(0) 顺序
            if self._preview_debounce_id:
                self.app.after_cancel(self._preview_debounce_id)
            self._preview_debounce_id = self.app.after(
                0, lambda ts=t_sec: self._update_preview(ts))

    def _on_tree_dbl_click(self, event):
        col_id = self._tree.identify_column(event.x)
        iid    = self._tree.identify_row(event.y)
        if not iid or not col_id:
            return
        try:
            heading = self._tree.heading(col_id, "text")
        except Exception:
            heading = ""
        if heading != "内容":
            return
        children = self._tree.get_children()
        idx = children.index(iid)
        if idx >= len(self._subs):
            return

        self._cancel_edit()

        bbox = self._tree.bbox(iid, "内容")
        if not bbox:
            return
        x, y, w, h = bbox

        self._edit_iid = iid
        self._edit_idx = idx
        self._edit_was_playing = self._player.is_playing() if self._player else False
        start_ms = int(self._subs[idx].start.total_seconds() * 1000)
        end_ms   = int(self._subs[idx].end.total_seconds()   * 1000)
        if self._player:
            self._start_sub_loop(start_ms, end_ms)

        #   = 不间断空格：显示与普通空格相同，但保存时可精确还原为 \n
        cur_text = self._subs[idx].content.replace("\n", " ")
        self._edit_original = cur_text

        self._edit_entry = tk.Text(
            self._tree, bg="#1a3050", fg="#ffffff",
            insertbackground="white", relief="flat",
            font=("Segoe UI", 9), bd=1,
            highlightthickness=1,
            highlightbackground="#0a5a9a",
            highlightcolor="#0a9aff",
            height=1, wrap="none", undo=True)
        self._edit_entry.place(x=x, y=y, width=w, height=h)
        self._edit_entry.insert("1.0", cur_text)
        self._edit_entry.mark_set("insert", "end")  # 光标置末尾，不全选
        self._edit_entry.focus_set()
        def _return_save(e):
            self._save_edit()
            return "break"
        self._edit_entry.bind("<Return>",   _return_save)
        self._edit_entry.bind("<Escape>",   lambda e: self._cancel_edit())
        self._edit_entry.bind("<FocusOut>",
                              lambda e, _iid=iid: self.app.after(
                                  200, lambda: self._smart_close(_iid)))

    def _save_edit(self):
        if self._edit_iid is None or self._edit_entry is None:
            return
        iid      = self._edit_iid
        idx      = self._edit_idx
        # strip() 会同时去掉 NBSP，改用 strip(' ') 只去普通空格
        new_text = self._edit_entry.get("1.0", "end-1c").strip(' ').replace(" ", "\n")
        self._cancel_edit()
        if 0 <= idx < len(self._subs):
            self._subs[idx].content = new_text
            self._tree.item(iid, values=(
                self._subs[idx].index,
                _td_to_str(self._subs[idx].start),
                _td_to_str(self._subs[idx].end),
                new_text.replace("\n", " "),   # 列表只显示空格
            ))
            self._reload_vlc_subs()
            self._persist_srt()

    def _cancel_edit(self):
        was_editing = self._edit_iid is not None
        self._stop_sub_loop()
        if was_editing and self._player:
            if self._edit_was_playing:
                self._player.play()
                self._play_btn.configure(text="⏸")
            else:
                self._player.pause()
                self._play_btn.configure(text="▶")
        if self._edit_entry:
            self._edit_entry.destroy()
            self._edit_entry = None
        self._edit_iid      = None
        self._edit_idx      = -1
        self._edit_original = ""

    def _smart_close(self, iid):
        if self._edit_iid != iid or self._edit_entry is None:
            return
        if self._edit_entry.get("1.0", "end-1c").strip() != self._edit_original:
            self._save_edit()
        else:
            self._cancel_edit()

    # ── Subtitle loop during inline edit ─────────────────────────────────────

    def _start_sub_loop(self, start_ms: int, end_ms: int):
        self._stop_seg_loop()   # 与片段预览循环互斥
        self._loop_start_ms = start_ms
        self._loop_end_ms   = max(end_ms, start_ms + 200)
        self._sub_looping   = True
        if self._loop_poll_id:
            self.app.after_cancel(self._loop_poll_id)
            self._loop_poll_id = None
        self._player.set_time(start_ms)
        if not self._player.is_playing():
            self._player.play()
            self._play_btn.configure(text="⏸")
        self._loop_poll_id = self.app.after(100, self._sub_loop_tick)

    def _sub_loop_tick(self):
        self._loop_poll_id = None
        if not self._sub_looping or self._edit_iid is None or not self._player:
            return
        t = self._player.get_time()
        if t < 0 or t >= self._loop_end_ms:
            self._player.set_time(self._loop_start_ms)
            if not self._player.is_playing():
                self._player.play()
                self._play_btn.configure(text="⏸")
        self._loop_poll_id = self.app.after(100, self._sub_loop_tick)

    def _stop_sub_loop(self):
        self._sub_looping = False          # flag first — blocks any in-flight tick
        if self._loop_poll_id:
            self.app.after_cancel(self._loop_poll_id)
            self._loop_poll_id = None

    def _on_tree_rclick(self, event):
        iid = self._tree.identify_row(event.y)
        if iid:
            self._tree.selection_set(iid)
            try:
                self._ctx_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self._ctx_menu.grab_release()

    def _delete_selected_sub(self):
        sel = self._tree.selection()
        if not sel:
            return
        iid      = sel[0]
        children = self._tree.get_children()
        idx      = children.index(iid)
        if 0 <= idx < len(self._subs):
            self._subs.pop(idx)
        self._tree.delete(iid)
        for i, child in enumerate(self._tree.get_children()):
            vals    = list(self._tree.item(child, "values"))
            vals[0] = i + 1
            self._tree.item(child, values=vals)
            self._subs[i].index = i + 1
        self._reload_vlc_subs()
        self._persist_srt()

    # ── SRT persistence ───────────────────────────────────────────────────────

    def _persist_srt(self):
        """将内存中的 self._subs 写回原始 SRT 文件。"""
        path = self.srt_var.get().strip()
        if not path or not self._subs:
            return
        try:
            import srt as srt_mod
            with open(path, "w", encoding="utf-8") as f:
                f.write(srt_mod.compose(self._subs))
        except Exception as e:
            self._status_lbl.configure(text=f"字幕保存失败: {e}", fg="#cc4444")

    # ── VLC subtitle reload ───────────────────────────────────────────────────

    def _reload_vlc_subs(self):
        if not self._player or not self._subs:
            return
        self._write_tmp_srt()
        t           = self._player.get_time()
        was_playing = self._player.is_playing()
        video       = self.video_var.get().strip()
        if not video or not os.path.exists(video):
            return
        self._reloading_subs = True
        media = self._instance.media_new(video)
        media.add_option(f":sub-file={self._tmp_srt.replace(os.sep, '/')}")
        self._player.set_media(media)
        self._player.play()

        def _restore():
            if self._player:
                # 显式选中外挂字幕轨，防止 VLC 回落到视频内嵌的英文字幕
                if self._tmp_srt and os.path.exists(self._tmp_srt):
                    self._player.video_set_subtitle_file(self._tmp_srt)
                self._player.set_time(max(0, t))
                if not was_playing:
                    self._player.set_pause(1)
                    self._play_btn.configure(text="▶")
                else:
                    self._play_btn.configure(text="⏸")
            self._reloading_subs = False

        self.app.after(400, _restore)

    def _write_tmp_srt(self):
        """将当前字幕写成临时 ASS 文件（含颜色），供 VLC 播放器加载。"""
        old = self._tmp_srt
        try:
            h = self._vlc_sub_color.lstrip("#")
            if len(h) == 3:
                h = h[0]*2 + h[1]*2 + h[2]*2
            ass_color = f"&H00{h[4:6]}{h[2:4]}{h[0:2]}"
            header = (
                "[Script Info]\nScriptType: v4.00+\n"
                "PlayResX: 640\nPlayResY: 360\nWrapStyle: 0\n\n"
                "[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                "Alignment, MarginL, MarginR, MarginV, Encoding\n"
                f"Style: Default,Arial,26,{ass_color},&H00FFFFFF,"
                "&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1\n\n"
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            )
            events = []
            for sub in self._subs:
                s = _td_to_ass_time(sub.start)
                e = _td_to_ass_time(sub.end)
                t = sub.content.strip().replace("\n", "\\N")
                events.append(f"Dialogue: 0,{s},{e},Default,,0,0,0,,{t}\n")
            fd, self._tmp_srt = tempfile.mkstemp(suffix=".ass")
            with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
                f.write(header)
                f.writelines(events)
        except Exception:
            self._tmp_srt = old
            return
        if old and old != self._tmp_srt:
            try:
                os.remove(old)
            except OSError:
                pass

    # ── Load video into player ────────────────────────────────────────────────

    def _load_video(self, path: str):
        if not self._player or not path or not os.path.exists(path):
            return

        srt_src = (self._tmp_srt if (self._tmp_srt and os.path.exists(self._tmp_srt))
                   else self.srt_var.get().strip())

        same_video = (os.path.normcase(path) ==
                      os.path.normcase(self._loaded_video_path))
        if same_video and self._duration_ms > 0:
            if srt_src and os.path.exists(srt_src):
                self._player.video_set_subtitle_file(srt_src)
            return

        # 换成了另一个视频：旧片段基于上一个视频的时间轴，已失效。
        # 注意 self._loaded_video_path 此刻仍是上一个路径——首次加载（含启动
        # 恢复）时它为空，条件不成立，从而保留从配置恢复的片段。
        if self._loaded_video_path and self._segments:
            self._clear_segments(silent=True)

        self._loaded_video_path = path
        media = self._instance.media_new(path)
        if srt_src and os.path.exists(srt_src):
            media.add_option(f":sub-file={srt_src.replace(os.sep, '/')}")

        try:
            media.parse()
            d = media.get_duration()
            if d > 0:
                self._duration_ms = d
                self._dur_lbl.configure(text=_ms_to_hms(d))
        except Exception:
            pass

        self._player.set_media(media)
        self._seek_slider.set(0)
        self._time_lbl.configure(text="00:00:00")
        if self._duration_ms <= 0:
            self._dur_lbl.configure(text="00:00:00")
        self._play_btn.configure(text="▶")
        user_vol = int(self._vol_slider.get())
        self._player.audio_set_volume(0)

        em = self._player.event_manager()

        def _on_playing(event):
            try:
                if self._player:
                    self._player.set_pause(1)
            except Exception:
                pass
            self.app.after(0, lambda: _do_first_pause(em))

        def _do_first_pause(em):
            if self._player:
                if self._player.is_playing():
                    self._player.pause()
                self._player.audio_set_volume(user_vol)
                if srt_src and os.path.exists(srt_src):
                    self._player.video_set_subtitle_file(srt_src)
                if self._duration_ms <= 0:
                    d = self._player.get_length()
                    if d > 0:
                        self._duration_ms = d
                        self._dur_lbl.configure(text=_ms_to_hms(d))
                try:
                    vw, vh = self._player.video_get_size()
                    if vw and vh:
                        self._video_aspect = vw / vh
                        lw = self._left_pane.winfo_width()
                        if lw > 10:
                            self._video_frame.configure(
                                height=max(80, int(lw / self._video_aspect)))
                except Exception:
                    pass
            self._start_poll()
            self._schedule_preview_update()
            try:
                em.event_detach(vlc.EventType.MediaPlayerPlaying)
            except Exception:
                pass

        em.event_attach(vlc.EventType.MediaPlayerPlaying, _on_playing)
        self._player.play()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_files(self, video_path: str, srt_path: str):
        # 仅在确有视频时覆盖；转写完成 handoff 若没带视频（例如用现有 SRT
        # 翻译、未选视频），不要清空烧录页已经加载的视频地址。
        if video_path:
            self.video_var.set(video_path)
        # 先加载视频并扫描其目录（_scan_srt_options 可能改/清空字幕选择），
        # 之后再设置显式传入的字幕，确保它最终胜出、不被目录扫描冲掉。
        if video_path and os.path.exists(video_path):
            self._scan_srt_options(video_path)
            self._load_video(video_path)
        if srt_path and os.path.exists(srt_path):
            self._add_to_srt_combo(srt_path)

    def load_srt(self, srt_path: str):
        self._tree.delete(*self._tree.get_children())
        self._subs        = []
        self._current_iid = None
        if not srt_path or not os.path.exists(srt_path):
            return
        try:
            import srt as srt_mod
            with open(srt_path, encoding="utf-8") as f:
                self._subs = list(srt_mod.parse(f.read()))
            for sub in self._subs:
                self._tree.insert("", "end", values=(
                    sub.index,
                    _td_to_str(sub.start),
                    _td_to_str(sub.end),
                    sub.content.replace("\n", " "),
                ))
            self._write_tmp_srt()
        except Exception:
            pass
        self._schedule_preview_update()

    # ── Load triggers ─────────────────────────────────────────────────────────

    def _trigger_video_load(self):
        path = self.video_var.get().strip()
        if path and os.path.exists(path):
            self._scan_srt_options(path)
            self._load_video(path)

    def _check_ready(self, *_):
        v = self.video_var.get().strip()
        s = self.srt_var.get().strip()
        if v and s and os.path.exists(v) and os.path.exists(s):
            self._status_lbl.configure(text="✅ 已就绪", fg=HL_GREEN)
            self._burn_btn.configure(state="normal")
            if hasattr(self, "_burn_out_var") and not self._burn_out_var.get().strip():
                self._burn_out_var.set(self._suggest_output(v))
        else:
            self._status_lbl.configure(text="")
            self._burn_btn.configure(state="disabled")

    def _suggest_output(self, video_path: str) -> str:
        vp = Path(video_path)
        preset = getattr(self, "_burn_preset_var", None)
        suffix_map = {
            "fast": "_burned_fast", "balanced": "_burned",
            "quality": "_burned_hq", "custom": "_burned_custom",
        }
        suffix = suffix_map.get(preset.get() if preset else "balanced", "_burned")
        return str(vp.parent / (vp.stem + suffix + vp.suffix))

    def _select_burn_preset(self, key: str):
        self._burn_preset_var.set(key)
        self._on_burn_preset_change()

    def _on_burn_preset_change(self):
        if self._burn_preset_var.get() == "custom":
            self._burn_custom_frame.grid()
        else:
            self._burn_custom_frame.grid_remove()
        v = self.video_var.get().strip()
        if v and hasattr(self, "_burn_out_var"):
            self._burn_out_var.set(self._suggest_output(v))

    def _browse_burn_output(self):
        path = filedialog.asksaveasfilename(
            title="烧录输出文件保存为",
            defaultextension=".mp4",
            filetypes=[("MP4 视频", "*.mp4"), ("所有文件", "*.*")],
        )
        if path:
            self._burn_out_var.set(path)

    # ── DnD / browse ─────────────────────────────────────────────────────────

    def _on_drop_video(self, event):
        path = event.data.strip().strip("{}")
        self.video_var.set(path)
        if os.path.exists(path):
            self._scan_srt_options(path)
            self._load_video(path)

    def _on_drop_srt(self, event):
        path = event.data.strip().strip("{}")
        if os.path.exists(path):
            self._add_to_srt_combo(path)

    def _browse_video(self):
        path = filedialog.askopenfilename(
            filetypes=[("视频文件", "*.mp4 *.mkv *.mov *.avi"), ("所有文件", "*.*")])
        if path:
            self.video_var.set(path)
            self._scan_srt_options(path)
            self._load_video(path)

    def _browse_srt(self):
        path = filedialog.askopenfilename(
            filetypes=[("SRT字幕", "*.srt"), ("所有文件", "*.*")])
        if path:
            self._add_to_srt_combo(path)

    # ── SRT combobox helpers ──────────────────────────────────────────────────

    def _scan_srt_options(self, video_path: str):
        folder      = os.path.normcase(os.path.dirname(os.path.abspath(video_path)))
        raw_folder  = os.path.dirname(os.path.abspath(video_path))
        try:
            found = [os.path.join(raw_folder, f)
                     for f in os.listdir(raw_folder)
                     if f.lower().endswith(".srt")
                     and os.path.isfile(os.path.join(raw_folder, f))]
        except OSError:
            found = []
        found.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        folder_changed = (folder != self._last_srt_scan_folder)
        self._last_srt_scan_folder = folder

        if not found:
            if folder_changed:
                self._srt_combo_paths = []
                self._srt_combo["values"] = []
                self._srt_display_var.set("")
                self.srt_var.set("")
            return

        self._srt_combo_paths     = found
        self._srt_combo["values"] = [os.path.basename(p) for p in found]

        if folder_changed:
            self._srt_combo.current(0)
            self._apply_srt(found[0])
        else:
            current  = self.srt_var.get().strip()
            cur_norm = os.path.normcase(current) if current else ""
            for i, p in enumerate(found):
                if os.path.normcase(p) == cur_norm:
                    self._srt_combo.current(i)
                    self._srt_display_var.set(os.path.basename(p))
                    return
            self._srt_combo.current(0)
            self._apply_srt(found[0])

    def _apply_srt(self, full_path: str):
        self._srt_display_var.set(os.path.basename(full_path))
        self.srt_var.set(full_path)
        if os.path.exists(full_path):
            self.load_srt(full_path)
            video = self.video_var.get().strip()
            if video and os.path.exists(video) and self._player:
                self._load_video(video)

    def _add_to_srt_combo(self, full_path: str):
        norm = os.path.normcase(full_path)
        self._srt_combo_paths = [p for p in self._srt_combo_paths
                                  if os.path.normcase(p) != norm]
        self._srt_combo_paths.insert(0, full_path)
        self._srt_combo["values"] = [os.path.basename(p)
                                      for p in self._srt_combo_paths]
        self._srt_combo.current(0)
        self._apply_srt(full_path)

    def _on_srt_combo_selected(self, _event=None):
        idx = self._srt_combo.current()
        if 0 <= idx < len(self._srt_combo_paths):
            self._apply_srt(self._srt_combo_paths[idx])

    def _on_srt_combo_enter(self):
        text = self._srt_display_var.get().strip()
        if not text:
            return
        for p in self._srt_combo_paths:
            if (os.path.basename(p) == text
                    or os.path.normcase(p) == os.path.normcase(text)):
                self._apply_srt(p)
                return
        if os.path.exists(text):
            self._add_to_srt_combo(text)

    # ── Config persistence ────────────────────────────────────────────────────

    def get_config(self):
        return {
            "burn_video_path":    self.video_var.get().strip(),
            "burn_srt_path":      self.srt_var.get().strip(),
            "burn_vlc_sub_color":  self._vlc_sub_color,
            "burn_sash_ratio":    round(self._sash_ratio, 4),
            "burn_ts_visible":    self._ts_visible,
            "burn_style_font":    self._style_font.get(),
            "burn_style_size":    self._style_size.get(),
            "burn_style_bold":    self._style_bold.get(),
            "burn_style_lsp":     self._style_lsp.get(),
            "burn_style_corner":  self._style_corner.get(),
            "burn_style_padx":    self._style_padx.get(),
            "burn_style_pady":    self._style_pady.get(),
            "burn_style_alpha":   self._style_alpha.get(),
            "burn_style_voffset": self._style_voffset.get(),
            "burn_style_tcolor":  self._style_tcolor.get(),
            "burn_style_bcolor":  self._style_bcolor.get(),
            "burn_banner_text":   self._banner_text.get(),
            "burn_banner_font":   self._banner_font.get(),
            "burn_banner_size":   self._banner_size.get(),
            "burn_banner_bold":   self._banner_bold.get(),
            "burn_banner_tcolor": self._banner_tcolor.get(),
            "burn_banner_bcolor": self._banner_bcolor.get(),
            "burn_banner_height": self._banner_height.get(),
            "burn_banner_pos":    self._banner_pos.get(),
            "burn_banner_align":   self._banner_align.get(),
            "burn_banner_voffset": self._banner_voffset.get(),
            "burn_with_banner":    self._with_banner.get(),
            "burn_round_corners":  self._burn_round_corners.get(),
            "burn_preset":       self._burn_preset_var.get(),
            "burn_res":          self._burn_res_var.get(),
            "burn_vbitrate":     self._burn_vbitrate_var.get(),
            "burn_segments":     [[a, b] for a, b in self._segments],
        }

    # ── Burn ──────────────────────────────────────────────────────────────────

    def _burn_btn_click(self):
        if self._is_running:
            self._stop_burn()
        else:
            self._start_burn()

    def _stop_burn(self):
        if hasattr(self, "_burn_stop"):
            self._burn_stop.set()
        self._burn_btn.configure(state="disabled", text="正在停止…")

    def _start_burn(self):
        if not HAS_AV:
            messagebox.showerror("缺少依赖",
                                 "需要安装 PyAV：\npip install av")
            return
        if not HAS_PIL:
            messagebox.showerror("缺少依赖",
                                 "需要安装 Pillow：\npip install pillow")
            return

        video_path = self.video_var.get().strip()
        srt_path   = self.srt_var.get().strip()
        if not video_path or not os.path.exists(video_path):
            messagebox.showerror("错误", "请先选择视频文件")
            return
        if not srt_path or not os.path.exists(srt_path):
            messagebox.showerror("错误", "请先选择 SRT 字幕文件")
            return

        sp          = self._get_style_params()
        bp          = self._get_banner_params()
        with_banner = self._with_banner.get()
        encoder     = getattr(self, "_burn_encoder", None) or "libx264"

        # Resolve output quality params from preset
        preset = self._burn_preset_var.get()
        # fps_cap：输出帧率上限（None=保留源帧率）。字幕视频 30fps 足够，
        # 极速/均衡封顶 30fps，可把 60fps 源的编码量直接砍半；最高画质保留源帧率。
        _preset_map = {
            "fast":     {"scale": (1280, 720),  "vbitrate": 2500,  "fps_cap": 30},
            "balanced": {"scale": (1920, 1080), "vbitrate": 8000,  "fps_cap": 30},
            "quality":  {"scale": None,          "vbitrate": 15000, "fps_cap": None},
        }
        if preset == "custom":
            try:
                vbr = max(100, int(self._burn_vbitrate_var.get()))
            except ValueError:
                messagebox.showerror("错误", "视频码率格式不正确，请输入整数")
                return
            res = self._burn_res_var.get()
            if res.startswith("原始"):
                scale = None
            else:
                try:
                    w, h = res.split("x")
                    scale = (int(w), int(h))
                except Exception:
                    scale = None
            quality_params = {"scale": scale, "vbitrate": vbr, "fps_cap": None}
        else:
            quality_params = _preset_map.get(preset, _preset_map["balanced"])

        vp = Path(video_path)
        custom_out = self._burn_out_var.get().strip()
        out_path = Path(custom_out) if custom_out else Path(self._suggest_output(video_path))

        self._burn_btn.configure(text="⏹  停止烧录",
                                bg="#4a1e1e", fg="#ddaaaa",
                                activebackground="#6a2a2a")
        self._progress["value"] = 0
        self._burn_prog_label.configure(text="")
        self._burn_log_text.configure(state="normal")
        self._burn_log_text.delete("1.0", "end")
        self._burn_log_text.configure(state="disabled")
        self._is_running = True
        self._burn_start_time = None
        self._burn_stop = threading.Event()

        clip_ranges = self._segments_for_burn()   # [] 表示全程

        self._burn_thread = threading.Thread(
            target=self._do_burn,
            args=(vp, Path(srt_path), sp, bp, with_banner, out_path, encoder,
                  quality_params, clip_ranges),
            daemon=True)
        self._burn_thread.start()

    def _burn_log(self, msg: str):
        import time as _t
        ts   = _t.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self._burn_log_text.configure(state="normal")
        self._burn_log_text.insert("end", line)
        self._burn_log_text.see("end")
        self._burn_log_text.configure(state="disabled")

    def _do_burn(self, video_path: Path, srt_path: Path,
                 sp: dict, bp: dict, with_banner: bool,
                 out_path: Path, encoder: str, quality_params: dict = None,
                 clip_ranges: list = None):
        import time as _time
        import shutil
        import subprocess
        import threading
        from fractions import Fraction

        if quality_params is None:
            quality_params = {"scale": (1920, 1080), "vbitrate": 8000, "fps_cap": None}
        clip_ranges = clip_ranges or []   # [(start_s, end_s), ...]；空=全程

        def log(msg: str):
            self.app.after(0, lambda m=msg: self._burn_log(m))

        try:
            import srt as srt_mod
            with open(str(srt_path), encoding="utf-8") as f:
                subs = list(srt_mod.parse(f.read()))

            with av.open(str(video_path)) as inp:
                in_vid       = inp.streams.video[0]
                src_w, src_h = in_vid.width, in_vid.height
                fps_rate     = in_vid.average_rate or Fraction(25)
                fps          = float(fps_rate)
                total_frames = in_vid.frames or 0
                if not total_frames:
                    try:
                        dur = float(in_vid.duration * in_vid.time_base)
                        total_frames = int(dur * fps) if fps else 0
                    except Exception:
                        pass
                has_audio = bool(inp.streams.audio)
                audio_codec_name = ""
                if has_audio:
                    try:
                        aud = inp.streams.audio[0]
                        # codec.name 不需要打开 codec_context，更可靠
                        audio_codec_name = (
                            (getattr(aud.codec, "name", None) or
                             getattr(aud.codec_context, "name", None) or "")
                        ).lower()
                    except Exception:
                        pass

            target = quality_params.get("scale")
            if target and (src_w > target[0] or src_h > target[1]):
                ratio = min(target[0] / src_w, target[1] / src_h)
                out_w = int(src_w * ratio) & ~1
                out_h = int(src_h * ratio) & ~1
            else:
                out_w, out_h = src_w, src_h
            vbitrate = quality_params.get("vbitrate", 8000)

            # 输出帧率：源帧率封顶到 fps_cap（仅在源更高时才降，避免给低帧率源插帧）。
            # out_fps=None 表示保持源帧率，下游不插入 fps 滤镜。
            fps_cap = quality_params.get("fps_cap")
            if fps_cap and fps > fps_cap + 0.01:
                out_fps      = float(fps_cap)
                out_fps_rate = Fraction(fps_cap)
                log(f"帧率封顶：源 {fps:.2f}fps → 输出 {out_fps:.0f}fps（减少编码量）")
            else:
                out_fps      = fps
                out_fps_rate = fps_rate

            if encoder != "libx264":
                # 圆角/直角路径都由系统 ffmpeg.exe 实际编码，故优先用 CLI 校验；
                # 仅无 ffmpeg（PyAV 兜底编码）时才用 PyAV 校验。两者均按真实
                # 输出尺寸进行，避免硬件编码器(AMD AMF 等)因尺寸过小被误判回退。
                _ff = shutil.which("ffmpeg")
                if _ff:
                    _enc_ok = _verify_encoder_cli(_ff, encoder, out_w, out_h)
                else:
                    _enc_ok = _verify_av_encoder(encoder, fps_rate, out_w, out_h, vbitrate)
                if not _enc_ok:
                    encoder = "libx264"
                    self.app.after(0, lambda: self._encoder_lbl.configure(
                        text="编码器: 软件编码 (libx264，已自动回退)"))

            log(f"开始烧录: 编码器={encoder}, 分辨率={out_w}×{out_h}, "
                f"码率={vbitrate} kbps, 共 {total_frames} 帧"
                + (f", 音频={audio_codec_name}" if audio_codec_name else ""))

            # 供完成时展示：原始/输出分辨率、帧率、时长（裁剪时输出时长为保留总和）
            _in_dur  = (total_frames / fps) if fps else 0
            _out_dur = (sum(b - a for a, b in clip_ranges) if clip_ranges
                        else _in_dur)
            self._burn_metrics = {
                "encoder": encoder, "fps": fps, "out_fps": out_fps,
                "src_w": src_w, "src_h": src_h,
                "out_w": out_w, "out_h": out_h,
                "in_dur": _in_dur, "out_dur": _out_dur,
            }

            # ffmpeg 的 -progress 报告的是「输出」帧数（已按 out_fps 降帧），
            # 故进度分母也要用输出帧数，否则降帧后进度条永远到不了头。
            out_total_frames = total_frames
            if out_fps != fps and _in_dur > 0:
                out_total_frames = max(1, int(round(_in_dur * out_fps)))

            stop             = self._burn_stop
            round_corners    = getattr(self, "_burn_round_corners",
                                       tk.BooleanVar(value=True)).get()
            ffmpeg_exe       = shutil.which("ffmpeg")

            if clip_ranges:
                # 时间片段裁剪仅在「圆角字幕轨 + ffmpeg overlay」路径实现：
                # 它的字幕是与原视频逐帧对齐的独立图层，裁剪时按相同区间裁视频与
                # 字幕轨即可天然同步，无需重算 SRT 时间码。直角 ass 路径与无 ffmpeg
                # 的 PIL 兜底都不支持裁剪，故此处强制走 overlay 路径。
                if not ffmpeg_exe:
                    log("❌ 时间片段裁剪需要 ffmpeg，请安装后重试")
                    self.app.after(0, lambda: self._on_burn_done(
                        False, "时间片段裁剪需要 ffmpeg"))
                    return
                _kept = sum(b - a for a, b in clip_ranges)
                log(f"时间片段：{len(clip_ranges)} 段，保留约 {_kept:.1f}s"
                    + ("（已忽略直角设置，裁剪仅支持圆角路径）" if not round_corners else ""))

            # ── 快速路径: ffmpeg + libass（直角，约100-200 fps）─────────────────
            if ffmpeg_exe and not round_corners and not clip_ranges:
                ass_path = _write_ass(subs, sp, out_w, out_h)
                try:
                    filter_parts: list = []
                    # fps 滤镜放最前：先把帧率降到目标，scale/ass/编码都按低帧率跑
                    if out_fps != fps:
                        filter_parts.append(f"fps={out_fps:g}")
                    if out_w != src_w or out_h != src_h:
                        filter_parts.append(f"scale={out_w}:{out_h}")

                    # Windows 驱动器冒号需转义: C:/tmp/x.ass → C\:/tmp/x.ass
                    ass_fwd = _ffmpeg_path(ass_path)
                    if len(ass_fwd) >= 2 and ass_fwd[1] == ":":
                        ass_fwd = ass_fwd[0] + "\\:" + ass_fwd[2:]
                    filter_parts.append(f"ass='{ass_fwd}'")

                    # Banner：含 emoji 时 drawtext 会崩溃，改用 PIL 渲染 PNG + overlay
                    banner_png_path = ""
                    use_banner_overlay = False
                    banner_py = 0
                    if with_banner:
                        banner_has_emoji = any(_is_emoji(c) for c in bp.get("text", ""))
                        if banner_has_emoji and HAS_PIL:
                            try:
                                patch, (_, banner_py) = _make_banner_patch(bp, out_w, out_h)
                                if patch is not None:
                                    _bn_fd, banner_png_path = tempfile.mkstemp(
                                        suffix=".png", prefix="ffmpeg_banner_")
                                    os.close(_bn_fd)
                                    patch.save(banner_png_path)
                                    use_banner_overlay = True
                                    log("  banner 含 emoji，使用 PIL 渲染叠加")
                            except Exception as _be:
                                log(f"  banner PIL 渲染失败: {_be}，跳过 banner")
                        if not use_banner_overlay:
                            bf = _banner_filter(bp, out_w, out_h)
                            if bf:
                                filter_parts.append(bf)

                    enc_opts: list = []
                    if encoder == "h264_nvenc":
                        enc_opts = ["-preset", "p4", "-tune", "hq", "-rc", "vbr"]
                    elif encoder == "libx264":
                        enc_opts = ["-preset", "medium", "-threads", "0"]
                    elif encoder == "h264_qsv":
                        enc_opts = ["-preset", "medium"]

                    _MP4_INCOMPAT_AUDIO = {"opus", "vorbis", "flac", "wavpack"}
                    out_ext = Path(str(out_path)).suffix.lower().lstrip(".")
                    if not has_audio:
                        audio_opts = ["-an"]
                    elif out_ext in ("mp4", "m4v") and audio_codec_name in _MP4_INCOMPAT_AUDIO:
                        audio_opts = ["-c:a", "aac", "-q:a", "2"]
                        log(f"音频转码: {audio_codec_name} → AAC（MP4 不支持直接复制）")
                    else:
                        audio_opts = ["-c:a", "copy"]

                    if use_banner_overlay:
                        base_filter = ",".join(filter_parts)
                        filter_complex = (
                            f"[0:v]{base_filter}[vbase];"
                            f"[vbase][1:v]overlay=0:{banner_py}:shortest=1[vout]"
                        )
                        audio_map = ["-map", "0:a:0"] if has_audio else []
                        cmd = [ffmpeg_exe, "-y",
                               "-i", str(video_path),
                               "-loop", "1", "-i", banner_png_path,
                               "-filter_complex", filter_complex,
                               "-map", "[vout]",
                               *audio_map,
                               "-c:v", encoder, "-b:v", f"{vbitrate}k",
                               *enc_opts,
                               *audio_opts,
                               "-progress", "pipe:1", "-nostats",
                               str(out_path)]
                        filter_str = filter_complex  # 仅用于日志
                    else:
                        filter_str = ",".join(filter_parts)
                        cmd = [ffmpeg_exe, "-y",
                               "-i", str(video_path),
                               "-vf", filter_str,
                               "-c:v", encoder, "-b:v", f"{vbitrate}k",
                               *enc_opts,
                               *audio_opts,
                               "-progress", "pipe:1", "-nostats",
                               str(out_path)]
                    log(f"滤镜: {filter_str[:120]}{'…' if len(filter_str) > 120 else ''}")

                    # Windows 上 libass 会尝试加载 fontconfig 配置，找不到时
                    # 部分 FFmpeg 构建会返回非零退出码。提供一个最小配置文件绕过此问题。
                    _fc_tmp = ""
                    env = os.environ.copy()
                    try:
                        fc_fd, _fc_tmp = tempfile.mkstemp(suffix=".conf")
                        os.close(fc_fd)
                        with open(_fc_tmp, "w", encoding="utf-8") as _fcf:
                            _fcf.write(
                                '<?xml version="1.0"?>\n'
                                '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
                                '<fontconfig>\n'
                                '  <dir>C:/Windows/Fonts</dir>\n'
                                '</fontconfig>\n'
                            )
                        env["FONTCONFIG_FILE"] = _fc_tmp
                    except Exception:
                        _fc_tmp = ""

                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        env=env,
                    )

                    # 防止 stderr 管道堵塞；关键错误行同步写入日志
                    stderr_lines: list = []
                    def _read_stderr():
                        for l in proc.stderr:
                            stderr_lines.append(l)
                            if any(k in l for k in ("Error", "error", "Invalid",
                                                    "No such", "not found",
                                                    "not supported", "Conversion failed",
                                                    "Cannot", "Unable")):
                                self.app.after(0, lambda m=l.rstrip(): self._burn_log(f"  ⚠ {m}"))
                    _stderr_t = threading.Thread(target=_read_stderr, daemon=True)
                    _stderr_t.start()

                    frame_n = 0
                    fps_val = 0.0
                    stdout_extra: list = []   # 非进度行（可能含 ffmpeg 错误信息）
                    _PROGRESS_KEYS = {"frame", "fps", "bitrate", "total_size",
                                      "out_time_us", "out_time_ms", "out_time",
                                      "dup_frames", "drop_frames", "speed", "progress"}
                    for line in proc.stdout:
                        if stop.is_set():
                            proc.kill()
                            break
                        stripped = line.strip()
                        k, _, v = stripped.partition("=")
                        if k in _PROGRESS_KEYS:
                            if k == "frame":
                                try:
                                    frame_n = int(v)
                                except ValueError:
                                    pass
                            elif k == "fps":
                                try:
                                    fps_val = float(v)
                                except ValueError:
                                    pass
                            elif k == "progress" and frame_n > 0 and out_total_frames > 0:
                                pct   = min(98.0, frame_n / out_total_frames * 100)
                                eta_s = ((out_total_frames - frame_n) / fps_val
                                         if fps_val > 0 else 0)
                                log(f"  {frame_n}/{out_total_frames} 帧  "
                                    f"{fps_val:.1f} fps  "
                                    f"剩余 {int(eta_s // 60)}:{int(eta_s % 60):02d}")
                                self.app.after(0, lambda p=pct, e=eta_s: self._set_progress(p, e, 1, 1, "FFmpeg 编码"))
                        elif stripped:
                            # 非进度行：可能是 ffmpeg 错误输出到了 stdout
                            stdout_extra.append(line)
                            self.app.after(0, lambda m=stripped: self._burn_log(f"  [stdout] {m}"))

                    proc.wait()
                    _stderr_t.join(timeout=10.0)  # 等待 stderr 线程读完，避免竞态
                    rc = proc.returncode
                    if rc != 0 and not stop.is_set():
                        # 把完整 stderr + stdout + 命令 + 退出码写入日志文件
                        try:
                            log_fd, log_path = tempfile.mkstemp(
                                suffix=".log", prefix="ffmpeg_burn_")
                            with os.fdopen(log_fd, "w", encoding="utf-8") as lf:
                                lf.write(f"=== exit code: {rc} "
                                         f"({'crash/SEH' if rc < 0 or rc > 255 else 'error'})"
                                         f" ===\n\n")
                                lf.write("=== command ===\n")
                                lf.write(" ".join(f'"{a}"' if " " in a else a
                                                  for a in cmd) + "\n\n")
                                lf.write("=== stderr ===\n")
                                lf.writelines(stderr_lines)
                                if stdout_extra:
                                    lf.write("\n=== stdout (非进度行) ===\n")
                                    lf.writelines(stdout_extra)
                            log(f"  ⚠ ffmpeg 退出码: {rc}"
                                + (" (进程崩溃)" if rc < 0 or rc > 255 else ""))
                            log(f"  ⚠ 完整日志已保存至: {log_path}")
                        except Exception:
                            log_path = ""
                        # 显示最后 80 行（足以覆盖真正的错误行，跳过配置 banner）
                        err_disp = "".join(stderr_lines[-80:])
                        hint = f"\n\n(完整日志: {log_path})" if log_path else ""
                        raise RuntimeError(f"ffmpeg 失败 (exit {rc}):\n" + err_disp + hint)
                finally:
                    try:
                        os.remove(ass_path)
                    except OSError:
                        pass
                    if _fc_tmp:
                        try:
                            os.remove(_fc_tmp)
                        except OSError:
                            pass
                    if banner_png_path:
                        try:
                            os.remove(banner_png_path)
                        except OSError:
                            pass

            # ── 圆角路径: RGBA 字幕轨 + FFmpeg overlay（约100-150 fps）────────────
            elif ffmpeg_exe:
                import bisect
                import numpy as np

                log("圆角模式（RGBA 字幕轨 + FFmpeg overlay）")

                from concurrent.futures import ThreadPoolExecutor, as_completed

                sub_list = [(s.start.total_seconds(), s.end.total_seconds(), s.content)
                            for s in subs]
                unique_texts = list({c for _, _, c in sub_list if c.strip()})
                n_unique = len(unique_texts)
                log(f"预渲染字幕缓存（{n_unique} 条唯一文字，多线程）…")
                t_pre0 = _time.time()

                # 并行渲染：FreeType/PIL 的 C 层会释放 GIL，多核可真正并行
                patch_cache: dict = {}
                _done = [0]

                def _render_one(txt):
                    p, pos = _make_subtitle_patch(txt, sp, out_w, out_h)
                    return txt, p, pos

                _workers = min(8, (os.cpu_count() or 2))
                with ThreadPoolExecutor(max_workers=_workers) as _pool:
                    _futs = {_pool.submit(_render_one, t): t for t in unique_texts}
                    for fut in as_completed(_futs):
                        txt, patch, pos = fut.result()
                        if patch is not None:
                            patch_cache[txt] = (np.array(patch), pos)
                        _done[0] += 1
                        _pct = min(18.0, _done[0] / n_unique * 18)
                        self.app.after(0, lambda p=_pct: self._set_progress(
                            p, None, 1, 2, "预渲染字幕"))

                banner_np = None
                if with_banner and bp.get("text", "").strip():
                    bp_img, bp_pos = _make_banner_patch(bp, out_w, out_h)
                    if bp_img is not None:
                        banner_np = (np.array(bp_img), bp_pos)

                t_pre1 = _time.time()
                log(f"缓存完成（{len(patch_cache)} 条字幕"
                    + ("，含横幅" if banner_np else "")
                    + f"，耗时 {t_pre1 - t_pre0:.1f}s）")

                def _np_paste(dst: np.ndarray, src: np.ndarray, x: int, y: int):
                    sh, sw = src.shape[:2]
                    dh, dw = dst.shape[:2]
                    x1, y1 = max(x, 0), max(y, 0)
                    x2, y2 = min(x + sw, dw), min(y + sh, dh)
                    if x2 > x1 and y2 > y1:
                        dst[y1:y2, x1:x2] = src[y1-y:y2-y, x1-x:x2-x]

                # 基础透明帧（RGBA），横幅预合成进去
                base_arr = np.zeros((out_h, out_w, 4), dtype=np.uint8)
                if banner_np:
                    b_arr, (bx, by) = banner_np
                    _np_paste(base_arr, b_arr, bx, by)

                # av.VideoFrame 懒构建缓存：按需合成，避免一次性分配 100 × 8MB
                _composed_arr = np.empty((out_h, out_w, 4), dtype=np.uint8)
                state_frames: dict = {}

                def _get_state_frame(text: str) -> "av.VideoFrame":
                    if text not in state_frames:
                        if text and text in patch_cache:
                            np.copyto(_composed_arr, base_arr)
                            _np_paste(_composed_arr, patch_cache[text][0],
                                      patch_cache[text][1][0], patch_cache[text][1][1])
                            state_frames[text] = av.VideoFrame.from_ndarray(
                                _composed_arr, format="rgba")
                        else:
                            state_frames[text] = av.VideoFrame.from_ndarray(
                                base_arr, format="rgba")
                    return state_frames[text]

                # 帧总数：已知则用已知值，否则从最后字幕结束时间估算
                n_frames = total_frames
                if n_frames == 0 and fps > 0 and sub_list:
                    n_frames = int((max(e for _, e, _ in sub_list) + 2.0) * fps)
                if n_frames == 0:
                    raise ValueError("无法确定视频帧数")

                # ── 构建稀疏字幕段列表（只在字幕切换时编一帧）──────────────────
                # 关键：段边界与字幕命中判定必须用同一口径（帧号），否则
                # 当字幕真实起点落在某帧后半段时，段中点(秒)会早于该 start，
                # 命中判定漏掉这条 → 字幕整段丢失/错位。改为全程帧单位。
                # round 而非 int：避免系统性提前约 1 帧；fe>=fs+1 保证极短
                # 字幕（30fps 吸附后可能 <1 帧）至少占 1 帧不被吞掉。
                _sub_frames = []
                for _ss, _se, _c in sub_list:
                    _fs = max(0, min(int(round(_ss * fps)), n_frames))
                    _fe = max(_fs + 1, min(int(round(_se * fps)), n_frames))
                    _sub_frames.append((_fs, _fe, _c))
                _sub_frames.sort(key=lambda x: x[0])   # 防御未排序 SRT
                _fs_list = [_f[0] for _f in _sub_frames]

                def find_sub_frame(fm: int) -> str:
                    """返回覆盖帧号 fm 的字幕文字（非重叠时唯一）。"""
                    idx = bisect.bisect_right(_fs_list, fm) - 1
                    if idx >= 0:
                        _fs, _fe, _c = _sub_frames[idx]
                        if fm < _fe:
                            return _c
                    return ""

                # 收集所有切换点（单位：帧号）
                _pts_set = {0, n_frames}
                for _fs, _fe, _ in _sub_frames:
                    _pts_set.add(_fs)
                    _pts_set.add(_fe)
                _sorted_pts = sorted(p for p in _pts_set if 0 <= p <= n_frames)

                # 合并相邻且字幕相同的段。区间 [_sp0,_sp1) 内字幕恒定，
                # 取起点帧 _sp0 判定即可（边界与判定同为帧单位，绝不漏判）。
                _segments = []   # [(start_pts, end_pts, sub_text), ...]
                for _i in range(len(_sorted_pts) - 1):
                    _sp0, _sp1 = _sorted_pts[_i], _sorted_pts[_i + 1]
                    if _sp1 <= _sp0:
                        continue
                    _txt = find_sub_frame(_sp0)
                    if _segments and _segments[-1][2] == _txt:
                        _segments[-1] = (_segments[-1][0], _sp1, _txt)
                    else:
                        _segments.append((_sp0, _sp1, _txt))

                n_segs = len(_segments)
                log(f"生成 RGBA 字幕轨（{n_segs} 段，等效 {n_frames} 帧）…")

                tmp_sub_fd, tmp_sub = tempfile.mkstemp(suffix="_sub.mov")
                os.close(tmp_sub_fd)
                try:
                    t_encode_start = _time.time()

                    with av.open(tmp_sub, "w") as sub_out:
                        sub_s         = sub_out.add_stream("png", rate=fps_rate)
                        sub_s.width   = out_w
                        sub_s.height  = out_h
                        sub_s.pix_fmt = "rgba"

                        for seg_idx, (_sp0, _sp1, sub_text) in enumerate(_segments):
                            if stop.is_set():
                                break
                            dur   = _sp1 - _sp0
                            frame = _get_state_frame(sub_text)
                            frame.pts = _sp0
                            for pkt in sub_s.encode(frame):
                                pkt.duration = dur
                                sub_out.mux(pkt)
                            pct = min(45.0, (seg_idx + 1) / n_segs * 50)
                            self.app.after(0, lambda p=pct: self._set_progress(
                                p, None, 1, 2, "生成字幕轨"))

                        if not stop.is_set():
                            for pkt in sub_s.encode():
                                sub_out.mux(pkt)

                    if stop.is_set():
                        # 字幕轨阶段被停止：必须走正常收尾，否则按钮卡在
                        # "正在停止…"、_is_running 一直为 True、无法再次开始。
                        log("已停止烧录")
                        self.app.after(0, lambda: self._on_burn_done(False, "已停止烧录"))
                        return

                    _enc_s = _time.time() - t_encode_start
                    log(f"字幕轨完成（{n_segs} 段，耗时 {_enc_s:.1f}s），开始 FFmpeg overlay 合并…")
                    self.app.after(0, lambda: self._set_progress(50.0, None, 2, 2, "FFmpeg 合并"))

                    # 基础 overlay：把字幕轨叠回（必要时先缩放）原视频。
                    # 全程烧录时直接输出 [vout]；裁剪时先输出中间标签 [ov]，
                    # 再交给下方 trim/concat 处理。
                    _ov = "ov" if clip_ranges else "vout"
                    # [0:v] 先降帧率再缩放，使 overlay/编码都按目标帧率进行
                    _v_pre: list = []
                    if out_fps != fps:
                        _v_pre.append(f"fps={out_fps:g}")
                    if out_w != src_w or out_h != src_h:
                        _v_pre.append(f"scale={out_w}:{out_h}")
                    if _v_pre:
                        base_fc = (f"[0:v]{','.join(_v_pre)}[base];"
                                   f"[base][1:v]overlay=0:0[{_ov}]")
                    else:
                        base_fc = f"[0:v][1:v]overlay=0:0[{_ov}]"

                    _MP4_INCOMPAT_AUDIO = {"opus", "vorbis", "flac", "wavpack"}
                    out_ext = Path(str(out_path)).suffix.lower().lstrip(".")

                    if clip_ranges:
                        # 多片段裁剪：视频与音频按相同区间 trim 后 concat 拼接。
                        # 字幕轨已与原视频逐帧对齐，故裁剪含字幕的 [ov] 即天然同步，
                        # 无需重算 SRT。concat 作用于解码帧，音频无法 copy，
                        # 必须重编码为 AAC。
                        n = len(clip_ranges)
                        parts = [base_fc]
                        if n == 1:
                            a, b = clip_ranges[0]
                            parts.append(f"[ov]trim={a:.3f}:{b:.3f},"
                                         f"setpts=PTS-STARTPTS[vout]")
                        else:
                            parts.append("[ov]split=" + str(n)
                                         + "".join(f"[s{i}]" for i in range(n)))
                            for i, (a, b) in enumerate(clip_ranges):
                                parts.append(f"[s{i}]trim={a:.3f}:{b:.3f},"
                                             f"setpts=PTS-STARTPTS[t{i}]")
                            parts.append("".join(f"[t{i}]" for i in range(n))
                                         + f"concat=n={n}:v=1:a=0[vout]")

                        if has_audio:
                            if n == 1:
                                a, b = clip_ranges[0]
                                parts.append(f"[0:a]atrim={a:.3f}:{b:.3f},"
                                             f"asetpts=PTS-STARTPTS[aout]")
                            else:
                                parts.append("[0:a]asplit=" + str(n)
                                             + "".join(f"[as{i}]" for i in range(n)))
                                for i, (a, b) in enumerate(clip_ranges):
                                    parts.append(f"[as{i}]atrim={a:.3f}:{b:.3f},"
                                                 f"asetpts=PTS-STARTPTS[u{i}]")
                                parts.append("".join(f"[u{i}]" for i in range(n))
                                             + f"concat=n={n}:v=0:a=1[aout]")
                            audio_opts = ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
                        else:
                            audio_opts = ["-an"]

                        filter_complex = ";".join(parts)
                    else:
                        filter_complex = base_fc
                        if not has_audio:
                            audio_opts = ["-an"]
                        elif out_ext in ("mp4", "m4v") and audio_codec_name in _MP4_INCOMPAT_AUDIO:
                            audio_opts = ["-map", "0:a:0", "-c:a", "aac", "-q:a", "2"]
                            log(f"音频转码: {audio_codec_name} → AAC")
                        else:
                            audio_opts = ["-map", "0:a?", "-c:a", "copy"]

                    enc_opts_ov: list = []
                    if encoder == "h264_nvenc":
                        enc_opts_ov = ["-preset", "p4", "-tune", "hq", "-rc", "vbr"]
                    elif encoder == "libx264":
                        enc_opts_ov = ["-preset", "medium", "-threads", "0"]
                    elif encoder == "h264_qsv":
                        enc_opts_ov = ["-preset", "medium"]

                    cmd = [ffmpeg_exe, "-y",
                           "-i", str(video_path),
                           "-i", tmp_sub,
                           "-filter_complex", filter_complex,
                           "-map", "[vout]",
                           *audio_opts,
                           "-c:v", encoder, "-b:v", f"{vbitrate}k",
                           *enc_opts_ov,
                           "-progress", "pipe:1", "-nostats",
                           str(out_path)]
                    log(f"overlay: {filter_complex}")

                    stderr_lines: list = []
                    def _read_stderr2():
                        for l in proc2.stderr:
                            stderr_lines.append(l)
                            if any(k in l for k in ("Error", "error", "Invalid",
                                                    "No such", "not found",
                                                    "not supported", "Conversion failed",
                                                    "Cannot", "Unable")):
                                self.app.after(0, lambda m=l.rstrip(): self._burn_log(f"  ⚠ {m}"))

                    proc2 = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    _stderr_t2 = threading.Thread(target=_read_stderr2, daemon=True)
                    _stderr_t2.start()

                    # 裁剪后输出帧数 ≈ 保留总时长 × fps，进度分母据此调整，
                    # 否则用原视频总帧数会让进度条永远到不了 98%。
                    prog_total = out_total_frames
                    if clip_ranges and out_fps > 0:
                        prog_total = max(1, int(sum(b - a for a, b in clip_ranges) * out_fps))

                    frame_n2 = 0
                    fps_val2 = 0.0
                    for line in proc2.stdout:
                        if stop.is_set():
                            proc2.kill()
                            break
                        k, _, v = line.strip().partition("=")
                        if k == "frame":
                            try: frame_n2 = int(v)
                            except ValueError: pass
                        elif k == "fps":
                            try: fps_val2 = float(v)
                            except ValueError: pass
                        elif k == "progress" and frame_n2 > 0 and prog_total > 0:
                            pct   = min(98.0, 50.0 + frame_n2 / prog_total * 48)
                            eta_s = ((prog_total - frame_n2) / fps_val2
                                     if fps_val2 > 0 else 0)
                            log(f"  {frame_n2}/{prog_total} 帧  "
                                f"{fps_val2:.1f} fps  "
                                f"剩余 {int(eta_s // 60)}:{int(eta_s % 60):02d}")
                            self.app.after(0, lambda p=pct, e=eta_s: self._set_progress(p, e, 2, 2, "FFmpeg 合并"))

                    proc2.wait()
                    _stderr_t2.join(timeout=10.0)
                    rc = proc2.returncode
                    if rc != 0 and not stop.is_set():
                        err_disp = "".join(stderr_lines[-80:])
                        raise RuntimeError(f"ffmpeg overlay 失败 (exit {rc}):\n{err_disp}")

                finally:
                    try:
                        os.remove(tmp_sub)
                    except OSError:
                        pass

            # ── PIL 逐帧（无 ffmpeg 时兜底）────────────────────────────────────
            else:
                import bisect
                log("未找到 ffmpeg，切换至 PIL 逐帧渲染")

                sub_list = [(s.start.total_seconds(), s.end.total_seconds(), s.content)
                            for s in subs]
                unique_texts = list({c for _, _, c in sub_list if c.strip()})
                log(f"预渲染字幕缓存（{len(unique_texts)} 条唯一文字）…")
                patch_cache: dict = {}
                for text in unique_texts:
                    patch, pos = _make_subtitle_patch(text, sp, out_w, out_h)
                    if patch is not None:
                        patch_cache[text] = (patch, pos)

                banner_patch, banner_pos = None, None
                if with_banner and bp.get("text", "").strip():
                    banner_patch, banner_pos = _make_banner_patch(bp, out_w, out_h)
                log(f"缓存完成（{len(patch_cache)} 条字幕"
                    + ("，含横幅" if banner_patch is not None else "") + "）")

                sub_starts = [s for s, e, _ in sub_list]

                def find_sub_pil(pts_s: float) -> str:
                    idx = bisect.bisect_right(sub_starts, pts_s) - 1
                    if idx >= 0:
                        s, e, c = sub_list[idx]
                        if pts_s <= e:
                            return c
                    return ""

                tmp_fd, tmp_vid = tempfile.mkstemp(suffix="_burn_vid.mp4")
                os.close(tmp_fd)
                try:
                    frame_count = 0
                    fps_count   = 0
                    t_last_log  = _time.time()

                    _next_out_idx = 0
                    with av.open(tmp_vid, "w") as out_c:
                        out_s          = out_c.add_stream(encoder, rate=out_fps_rate)
                        out_s.width    = out_w
                        out_s.height   = out_h
                        out_s.pix_fmt  = "yuv420p"
                        out_s.bit_rate = vbitrate * 1000
                        _set_av_encoder_opts(out_s, encoder)

                        with av.open(str(video_path)) as inp:
                            for raw_frame in inp.decode(video=0):
                                if stop.is_set():
                                    break
                                pts_s    = (float(raw_frame.pts * raw_frame.time_base)
                                            if raw_frame.pts is not None else 0.0)
                                # 降帧：每个输出时隙只保留首帧，其余源帧直接丢弃
                                if out_fps != fps:
                                    _oi = int(pts_s * out_fps + 1e-6)
                                    if _oi < _next_out_idx:
                                        continue
                                    _next_out_idx = _oi + 1
                                sub_text = find_sub_pil(pts_s)
                                needs_pil = bool(sub_text.strip()) or banner_patch is not None
                                if needs_pil:
                                    rgb_frame = raw_frame.reformat(
                                        width=out_w, height=out_h, format="rgb24")
                                    img = rgb_frame.to_image()
                                    if sub_text.strip() and sub_text in patch_cache:
                                        patch, pos = patch_cache[sub_text]
                                        img.paste(patch, pos, patch)
                                    if banner_patch is not None:
                                        img.paste(banner_patch, banner_pos, banner_patch)
                                    vf = av.VideoFrame.from_image(img).reformat(format="yuv420p")
                                else:
                                    vf = raw_frame.reformat(
                                        width=out_w, height=out_h, format="yuv420p")
                                vf.pts = frame_count
                                for pkt in out_s.encode(vf):
                                    out_c.mux(pkt)
                                frame_count += 1
                                fps_count   += 1
                                now = _time.time()
                                if now - t_last_log >= 2.0:
                                    render_fps = fps_count / (now - t_last_log)
                                    fps_count  = 0
                                    t_last_log = now
                                    if out_total_frames > 0:
                                        pct   = min(95.0, frame_count / out_total_frames * 100)
                                        eta_s = ((out_total_frames - frame_count) / render_fps
                                                 if render_fps > 0 else 0)
                                        log(f"  {frame_count}/{out_total_frames} 帧  "
                                            f"{render_fps:.1f} fps  "
                                            f"剩余 {int(eta_s // 60)}:{int(eta_s % 60):02d}")
                                        _tp = 2 if has_audio else 1
                                        self.app.after(0, lambda p=pct, e=eta_s, t=_tp: self._set_progress(p, e, 1, t, "PIL 渲染"))

                        if not stop.is_set():
                            for pkt in out_s.encode():
                                out_c.mux(pkt)

                    if not stop.is_set():
                        log("视频帧处理完成，音频混流中…")
                        self.app.after(0, lambda: self._set_progress(97.0, None, 2, 2, "音频混流"))
                        ffmpeg_mux = shutil.which("ffmpeg")
                        if ffmpeg_mux and has_audio:
                            r = subprocess.run(
                                [ffmpeg_mux, "-y",
                                 "-i", tmp_vid, "-i", str(video_path),
                                 "-c:v", "copy", "-c:a", "copy",
                                 "-map", "0:v:0", "-map", "1:a?",
                                 str(out_path)],
                                capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW,
                            )
                            if r.returncode != 0:
                                shutil.copy2(tmp_vid, str(out_path))
                        else:
                            shutil.copy2(tmp_vid, str(out_path))
                finally:
                    try:
                        os.remove(tmp_vid)
                    except OSError:
                        pass

            if stop.is_set():
                try:
                    out_path.unlink(missing_ok=True)
                except Exception:
                    pass
                log("已停止烧录")
                self.app.after(0, lambda: self._on_burn_done(False, "已停止烧录"))
                return

            log(f"完成！输出文件: {out_path}")
            self.app.after(0, lambda: self._on_burn_done(True, str(out_path)))

        except Exception as exc:
            import traceback
            log(f"错误: {exc}")
            log(traceback.format_exc())
            self.app.after(0, lambda e=str(exc): self._on_burn_done(False, e))

    def _set_progress(self, value: float, eta_s: float = None,
                      phase: int = None, total_phases: int = None,
                      phase_name: str = None):
        import time as _time
        self._progress["value"] = value
        if self._burn_start_time is None:
            self._burn_start_time = _time.time()

        parts = []
        if phase is not None and total_phases is not None:
            parts.append(f"第{phase}/{total_phases}阶段")
        if phase_name:
            parts.append(phase_name)
        prefix = ": ".join(parts) if parts else ""

        elapsed = (_time.time() - self._burn_start_time) if self._burn_start_time else 0
        em, es = int(elapsed // 60), int(elapsed % 60)
        used_str = f"已用 {em}:{es:02d}"

        if value > 0.5 and eta_s is not None:
            m, s = int(eta_s // 60), int(eta_s % 60)
            tail = f"{used_str} · 剩余 {m}:{s:02d}"
            text = f"{prefix}  {tail}" if prefix else f"{value:.1f}%  {tail}"
        elif prefix:
            text = f"{prefix}  {used_str}"
        else:
            text = f"{value:.1f}%  {used_str}"
        self._burn_prog_label.configure(text=text, fg=HL_GREEN)

    def _on_burn_done(self, success: bool, msg: str):
        import time as _time
        elapsed = (_time.time() - self._burn_start_time) if self._burn_start_time else 0
        self._is_running = False
        self._burn_start_time = None
        self._burn_btn.configure(
            state="normal", text="▶  开始烧录",
            bg="#1e3a4a", fg="#aaccdd", activebackground="#2a5a6a")
        if success:
            self._progress["value"] = 100

            def _fmt(sec):
                sec = int(round(sec))
                h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
                return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

            em, es = int(elapsed // 60), int(elapsed % 60)
            mt = getattr(self, "_burn_metrics", None)
            if mt:
                fps = mt["fps"]
                out_fps = mt.get("out_fps", fps)
                speed = (mt["out_dur"] / elapsed) if elapsed > 0 else 0
                # 降帧时标注「源→输出」帧率，让人一眼看出帧率确实降了
                fps_txt = (f"{out_fps:.0f}fps" if abs(out_fps - fps) < 0.01
                           else f"{fps:.0f}→{out_fps:.0f}fps")
                # 完整明细直接写在进度条上方，不再弹窗
                detail = (
                    f"✅ 烧录完成！\n"
                    f"视频时长 {_fmt(mt['out_dur'])}　总耗时 {em}:{es:02d}"
                    f"（{speed:.1f}× 实时）\n"
                    f"原始 {mt['src_w']}×{mt['src_h']} → "
                    f"输出 {mt['out_w']}×{mt['out_h']} @ {fps_txt}"
                    f"　编码器 {mt['encoder']}\n"
                    f"输出文件：{msg}"
                )
            else:
                detail = (f"✅ 烧录完成！  总耗时 {em}:{es:02d}\n"
                          f"输出文件：{msg}")
            self._burn_prog_label.configure(text=detail, fg=HL_GREEN)
            # 完成后自动打开输出视频所在文件夹，方便后续操作
            self._open_output_folder(msg)
        elif msg == "已停止烧录":
            self._progress["value"] = 0
            self._burn_prog_label.configure(text="")
        else:
            self._progress["value"] = 0
            self._burn_prog_label.configure(text="")
            # 弹窗只显示首行摘要，完整错误已写入日志并显示在下方日志框
            summary = msg.split("\n")[0]
            messagebox.showerror("烧录失败", f"{summary}\n\n详细错误请查看下方日志框。")

    def _open_output_folder(self, out_path: str):
        """烧录成功后在资源管理器中打开输出文件夹并选中视频文件。"""
        try:
            path = os.path.abspath(out_path)
            if os.path.exists(path):
                # /select 让资源管理器打开文件夹并高亮选中刚烧好的视频
                import subprocess
                subprocess.Popen(["explorer", "/select,", path])
            else:
                folder = os.path.dirname(path)
                if os.path.isdir(folder):
                    os.startfile(folder)
        except Exception as e:
            self._burn_log(f"打开输出文件夹失败：{e}")


# ── Module-level helpers ──────────────────────────────────────────────────────

def _set_sash(paned: tk.PanedWindow, ratio: float):
    try:
        total = paned.winfo_width()
        if total > 1:
            paned.sash_place(0, int(total * ratio), 0)
    except tk.TclError:
        pass


def _style_srt_combo():
    s = ttk.Style()
    s.configure("Burn.TCombobox",
                fieldbackground="#252525", background="#3a3a3a",
                foreground="#aaaaaa", selectbackground="#1a3a5a",
                selectforeground="#ffffff", arrowcolor="#888888",
                insertcolor="#aaaaaa")
    s.map("Burn.TCombobox",
          fieldbackground=[("readonly", "#252525")],
          foreground=[("readonly", "#aaaaaa")],
          background=[("active", "#4a4a4a")])


def _style_tree():
    style = ttk.Style()
    style.configure("Treeview",
                    background="#1a1a1a", foreground="#cccccc",
                    fieldbackground="#1a1a1a", rowheight=22,
                    font=("Segoe UI", 9))
    style.configure("Treeview.Heading",
                    background="#2a2a2a", foreground="#888888",
                    relief="flat", font=("Segoe UI", 9))
    style.map("Treeview",
              background=[("selected", "#0a5a9a")],
              foreground=[("selected", "#ffffff")])
    style.map("Treeview.Heading",
              background=[("active", "#333333")])


# ── Thin slider widget ────────────────────────────────────────────────────────

class ThinSlider(tk.Canvas):
    _MARGIN   = 6
    _TRACK_W  = 2
    _HANDLE_H = 14
    _HANDLE_W = 3

    def __init__(self, parent, from_=0, to=100,
                 command=None, on_press=None, on_release=None,
                 track_color="#3a3a3a", fill_color="#4a9eff",
                 handle_color="#e0e0e0", bg="#161616", **kw):
        kw.setdefault("height", 20)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(parent, bg=bg, **kw)
        self._from       = float(from_)
        self._to         = float(to)
        self._value      = float(from_)
        self._command    = command
        self._on_press   = on_press
        self._on_release = on_release
        self._track_color  = track_color
        self._fill_color   = fill_color
        self._handle_color = handle_color
        self._dragging     = False

        self.bind("<Configure>",       lambda e: self._redraw())
        self.bind("<ButtonPress-1>",   self._press)
        self.bind("<B1-Motion>",       self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.after(10, self._redraw)

    def _ratio(self):
        span = self._to - self._from
        return (self._value - self._from) / span if span else 0.0

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1:
            return
        cy = h // 2
        x0 = self._MARGIN
        x1 = w - self._MARGIN
        hx = x0 + self._ratio() * (x1 - x0)
        self.create_line(x0, cy, x1, cy,
                         fill=self._track_color, width=self._TRACK_W)
        if hx > x0:
            self.create_line(x0, cy, hx, cy,
                             fill=self._fill_color, width=self._TRACK_W)
        hy0 = cy - self._HANDLE_H // 2
        hy1 = cy + self._HANDLE_H // 2
        self.create_line(hx, hy0, hx, hy1,
                         fill=self._handle_color, width=self._HANDLE_W,
                         capstyle="round")

    def _x_to_value(self, x):
        w     = self.winfo_width()
        track = w - 2 * self._MARGIN
        if track <= 0:
            return self._from
        ratio = max(0.0, min(1.0, (x - self._MARGIN) / track))
        return self._from + ratio * (self._to - self._from)

    def _press(self, event):
        self._dragging = True
        self._value    = self._x_to_value(event.x)
        self._redraw()
        if self._on_press:
            self._on_press()
        if self._command:
            self._command(self._value)

    def _drag(self, event):
        if not self._dragging:
            return
        self._value = self._x_to_value(event.x)
        self._redraw()
        if self._command:
            self._command(self._value)

    def _release(self, event):
        self._dragging = False
        self._value    = self._x_to_value(event.x)
        self._redraw()
        if self._on_release:
            self._on_release(self._value)
        elif self._command:
            self._command(self._value)

    def get(self):
        return self._value

    def set(self, value):
        self._value = max(self._from, min(self._to, float(value)))
        self._redraw()
