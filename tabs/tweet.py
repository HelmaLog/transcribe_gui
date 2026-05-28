"""
Tweet / AI chat tab — multi-session, collapsible prompt editor.
"""

import mimetypes
import os
import re
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    from tkinterdnd2 import DND_FILES
    _HAS_DND = True
except ImportError:
    _HAS_DND = False

from backend import (
    DEFAULT_MODELS_SF, DEFAULT_MODELS_ARK, DEFAULT_MODELS_GEMINI, DEFAULT_MODELS_PIONEER,
    DEFAULT_CONFIG,
    fetch_sf_models, fetch_ark_models, fetch_gemini_models, fetch_pioneer_models,
    test_ark_model,
    chat_completion_stream,
)
from concurrent.futures import ThreadPoolExecutor, as_completed


def _strip_model_marker(name: str) -> str:
    """Remove availability marker added by ARK model testing."""
    for suffix in (" ✓", " ✗"):
        if name.endswith(suffix):
            return name[:-2]
    return name


def _model_sort_key(name: str):
    """Sort available (✓) models before unavailable (✗), each group alphabetically."""
    return (0 if name.endswith(" ✓") else 1, name)
from .base import Tab, BG


class TweetTab(Tab):

    def _build(self):
        cfg = self.app._saved_config
        p = self.frame
        p.columnconfigure(0, weight=1)
        p.rowconfigure(1, weight=1)

        fs = cfg.get("tweet_font_size", 11)
        self._tweet_font_size = tk.IntVar(value=fs)
        self._tweet_line_spacing = tk.IntVar(value=cfg.get("tweet_line_spacing", 4))
        self._tweet_prompts = list(cfg.get("tweet_prompts", DEFAULT_CONFIG["tweet_prompts"]))
        self._tweet_editing_prompt_idx = -1
        self._pending_attachments = []

        # ── Row 0: Prompt bar (compact, collapsible) ─────────────────────────
        f_prompt_area = tk.Frame(p, bg=BG)
        f_prompt_area.grid(row=0, column=0, sticky="ew")
        f_prompt_area.columnconfigure(0, weight=1)

        f_prompt_row = tk.Frame(f_prompt_area, bg=BG)
        f_prompt_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(7, 4))
        tk.Label(f_prompt_row, text="提示词  ", bg=BG, fg="#444444",
                 font=("Segoe UI", 8)).pack(side="left")
        self._f_prompt_btns = tk.Frame(f_prompt_row, bg=BG)
        self._f_prompt_btns.pack(side="left")

        # Shared edit panel (hidden by default)
        f_edit = tk.Frame(f_prompt_area, bg="#222222",
                          highlightbackground="#2e2e2e", highlightthickness=1)
        f_edit.columnconfigure(1, weight=1)
        f_edit.rowconfigure(1, weight=1)
        self._f_prompt_edit = f_edit

        tk.Label(f_edit, text="名称", bg="#222222", fg="#606060",
                 font=("Segoe UI", 8)).grid(row=0, column=0, padx=(12, 6),
                                             pady=(8, 4), sticky="w")
        self._prompt_edit_name_var = tk.StringVar()
        f_name = tk.Frame(f_edit, bg="#222222")
        f_name.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(8, 4))
        f_name.columnconfigure(0, weight=1)
        tk.Entry(f_name, textvariable=self._prompt_edit_name_var, bg="#222222", fg="#cccccc",
                 insertbackground="#888888", relief="flat", font=("Segoe UI", 9),
                 bd=2, highlightthickness=0).grid(row=0, column=0, sticky="ew", ipady=2)
        tk.Button(f_name, text="保存", command=self._save_tweet_prompt,
                  bg="#2a2a2a", fg="#8a8a8a", relief="flat", padx=10, pady=1,
                  font=("Segoe UI", 8), cursor="hand2",
                  activebackground="#3a3a3a", activeforeground="#cccccc",
                  bd=0).grid(row=0, column=1, padx=(6, 0))
        tk.Button(f_name, text="取消", command=self._hide_prompt_edit,
                  bg="#2a2a2a", fg="#606060", relief="flat", padx=6, pady=1,
                  font=("Segoe UI", 8), cursor="hand2",
                  activebackground="#3a3a3a", activeforeground="#aaaaaa",
                  bd=0).grid(row=0, column=2, padx=(4, 0))

        self._prompt_edit_text = tk.Text(
            f_edit, bg="#1a1a1a", fg="#cccccc", insertbackground="#888888",
            relief="flat", font=("Segoe UI", 9), bd=0,
            wrap="word", padx=10, pady=7, height=4,
            selectbackground="#1e3a5a", highlightthickness=0)
        self._prompt_edit_text.grid(row=1, column=0, columnspan=2, sticky="nsew",
                                     padx=12, pady=(0, 8))

        self._build_prompt_bar()

        # ── Row 1: Sessions area (expands) ────────────────────────────────────
        f_outer = tk.Frame(p, bg=BG)
        f_outer.grid(row=1, column=0, sticky="nsew")
        f_outer.columnconfigure(0, weight=1)
        f_outer.rowconfigure(1, weight=1)

        f_tabbar = tk.Frame(f_outer, bg="#191919")
        f_tabbar.grid(row=0, column=0, sticky="ew")
        self._tweet_tabbar = f_tabbar

        f_sessions = tk.Frame(f_outer, bg=BG)
        f_sessions.grid(row=1, column=0, sticky="nsew")
        f_sessions.columnconfigure(0, weight=1)
        f_sessions.rowconfigure(0, weight=1)
        self._tweet_session_area = f_sessions

        f_input_wrap = tk.Frame(f_outer, bg="#252525",
                                highlightbackground="#2e2e2e", highlightthickness=1)
        f_input_wrap.grid(row=2, column=0, sticky="ew", padx=16, pady=(6, 0))
        f_input_wrap.columnconfigure(0, weight=1)

        self._tweet_input = tk.Text(
            f_input_wrap, bg="#252525", fg="#d0d0d0",
            insertbackground="#aaaaaa", relief="flat", font=("Segoe UI", 10),
            bd=0, height=3, wrap="word", padx=12, pady=7, highlightthickness=0)
        self._tweet_input.grid(row=0, column=0, sticky="ew")
        self._tweet_input.bind("<Return>",
                               lambda e: (self._send_tweet(), "break")[-1])
        self._tweet_input.bind("<Control-Return>",
                               lambda e: (self._tweet_input.insert("insert", "\n"), "break")[-1])

        # File chip strip (hidden until files are attached)
        self._f_file_chips = tk.Frame(f_input_wrap, bg="#1c1c1c")

        if _HAS_DND:
            for w in (self._tweet_input, f_input_wrap):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_file_drop)

        f_ctrl = tk.Frame(f_outer, bg=BG)
        f_ctrl.grid(row=3, column=0, sticky="ew", padx=16, pady=(5, 9))

        _bkw = dict(bg=BG, relief="flat", font=("Segoe UI", 9), cursor="hand2",
                    activebackground="#252525", bd=0, padx=4, pady=2)
        tk.Button(f_ctrl, text="A−", fg="#555555",
                  command=lambda: self._update_tweet_font(-1), **_bkw).pack(side="left")
        tk.Label(f_ctrl, textvariable=self._tweet_font_size, bg=BG, fg="#555555",
                 font=("Segoe UI", 9), width=2, anchor="center").pack(side="left")
        tk.Button(f_ctrl, text="A+", fg="#555555",
                  command=lambda: self._update_tweet_font(1), **_bkw).pack(side="left")

        tk.Label(f_ctrl, text="  行距", bg=BG, fg="#444444",
                 font=("Segoe UI", 8)).pack(side="left", padx=(6, 0))
        tk.Button(f_ctrl, text="−", fg="#555555",
                  command=lambda: self._update_line_spacing(-1), **_bkw).pack(side="left")
        tk.Label(f_ctrl, textvariable=self._tweet_line_spacing, bg=BG, fg="#555555",
                 font=("Segoe UI", 9), width=2, anchor="center").pack(side="left")
        tk.Button(f_ctrl, text="+", fg="#555555",
                  command=lambda: self._update_line_spacing(1), **_bkw).pack(side="left")

        tk.Button(f_ctrl, text="📎", command=self._browse_attachments,
                  bg=BG, fg="#5a7a8a", relief="flat", padx=6, pady=2,
                  font=("Segoe UI", 10), cursor="hand2",
                  activebackground="#252525", activeforeground="#7aaac0",
                  bd=0).pack(side="left", padx=(10, 0))

        self._tweet_send_btn = tk.Button(
            f_ctrl, text="发 送", command=self._send_tweet,
            bg="#0a6dd4", fg="#ffffff", relief="flat", padx=20, pady=4,
            font=("Segoe UI", 10, "bold"), cursor="hand2",
            activebackground="#0060bb", bd=0)
        self._tweet_send_btn.pack(side="right")

        tk.Button(f_ctrl, text="清空", command=self._new_tweet_conversation,
                  bg=BG, fg="#555555", relief="flat", padx=10, pady=4,
                  font=("Segoe UI", 9), cursor="hand2",
                  activebackground="#252525", activeforeground="#888888",
                  bd=0).pack(side="right", padx=(0, 8))

        self._tweet_sessions = []
        self._active_session_idx = -1
        self._init_sessions()

    # ── Prompt bar ────────────────────────────────────────────────────────────

    def _build_prompt_bar(self):
        for w in self._f_prompt_btns.winfo_children():
            w.destroy()
        n = len(self._tweet_prompts)
        for i, pr in enumerate(self._tweet_prompts):
            is_ed = (i == self._tweet_editing_prompt_idx)
            bg = "#2e2e2e" if is_ed else "#242424"
            fg = "#cccccc" if is_ed else "#717171"
            tf = tk.Frame(self._f_prompt_btns, bg=bg)
            tf.pack(side="left", padx=(0, 2))
            tk.Button(tf, text=f" {pr['name']} ",
                      command=lambda i=i: self._toggle_prompt_edit(i),
                      bg=bg, fg=fg, relief="flat", padx=2, pady=2,
                      font=("Segoe UI", 8), cursor="hand2",
                      activebackground="#2e2e2e", activeforeground="#cccccc",
                      bd=0).pack(side="left")
            if n > 1:
                tk.Button(tf, text="×",
                          command=lambda i=i: self._remove_prompt(i),
                          bg=bg, fg="#444444" if is_ed else "#282828",
                          relief="flat", padx=2, pady=2,
                          font=("Segoe UI", 7), cursor="hand2",
                          activebackground="#2e2e2e", activeforeground="#cc5555",
                          bd=0).pack(side="left")
        tk.Button(self._f_prompt_btns, text="+",
                  command=self._add_prompt,
                  bg=BG, fg="#555555", relief="flat", padx=5, pady=2,
                  font=("Segoe UI", 9), cursor="hand2",
                  activebackground="#242424", activeforeground="#999999",
                  bd=0).pack(side="left", padx=(2, 0))

    def _toggle_prompt_edit(self, idx):
        if self._tweet_editing_prompt_idx == idx:
            self._hide_prompt_edit()
            return
        pr = self._tweet_prompts[idx]
        self._prompt_edit_name_var.set(pr["name"])
        self._prompt_edit_text.delete("1.0", "end")
        self._prompt_edit_text.insert("1.0", pr["text"])
        self._tweet_editing_prompt_idx = idx
        self._f_prompt_edit.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        self._build_prompt_bar()

    def _hide_prompt_edit(self):
        self._f_prompt_edit.grid_remove()
        self._tweet_editing_prompt_idx = -1
        self._build_prompt_bar()

    def _save_tweet_prompt(self):
        idx = self._tweet_editing_prompt_idx
        if idx < 0:
            return
        name = self._prompt_edit_name_var.get().strip() or f"场景 {idx + 1}"
        text = self._prompt_edit_text.get("1.0", "end").strip()
        self._tweet_prompts[idx] = {"name": name, "text": text}
        self.app._saved_config["tweet_prompts"] = self._tweet_prompts
        self.app._do_save_config()
        names = [p["name"] for p in self._tweet_prompts]
        for s in self._tweet_sessions:
            s["prompt_cb"]["values"] = names
        self._hide_prompt_edit()

    def _add_prompt(self):
        self._tweet_prompts.append({"name": f"场景 {len(self._tweet_prompts) + 1}", "text": ""})
        names = [p["name"] for p in self._tweet_prompts]
        for s in self._tweet_sessions:
            s["prompt_cb"]["values"] = names
        self._toggle_prompt_edit(len(self._tweet_prompts) - 1)

    def _remove_prompt(self, idx):
        if len(self._tweet_prompts) <= 1:
            return
        name = self._tweet_prompts[idx]["name"]
        if not messagebox.askyesno("删除提示词", f"确定删除「{name}」？", parent=self.app):
            return
        self._tweet_prompts.pop(idx)
        if self._tweet_editing_prompt_idx == idx:
            self._tweet_editing_prompt_idx = -1
            self._f_prompt_edit.grid_remove()
        elif self._tweet_editing_prompt_idx > idx:
            self._tweet_editing_prompt_idx -= 1
        names = [p["name"] for p in self._tweet_prompts]
        for s in self._tweet_sessions:
            s["prompt_cb"]["values"] = names
            if s["prompt_var"].get() not in names:
                s["prompt_var"].set(names[0])
        self.app._saved_config["tweet_prompts"] = self._tweet_prompts
        self.app._do_save_config()
        self._build_prompt_bar()

    # ── Session creation ──────────────────────────────────────────────────────

    def _make_session_content(self, provider_val, model_val, prompt_idx):
        fs = self._tweet_font_size.get()
        prompt_names = [p["name"] for p in self._tweet_prompts]

        frame = tk.Frame(self._tweet_session_area, bg=BG)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        f_hdr = tk.Frame(frame, bg=BG)
        f_hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(5, 0))

        provider_var = tk.StringVar(value=provider_val)
        for val, txt in [
            ("gemini", "Gemini"), ("siliconflow", "硅基"),
            ("volcengine", "ARK"), ("pioneer", "Pioneer"),
        ]:
            tk.Radiobutton(f_hdr, text=txt, variable=provider_var, value=val,
                           bg=BG, fg="#5a5a5a", selectcolor=BG,
                           activebackground=BG, activeforeground="#aaaaaa",
                           font=("Segoe UI", 9)).pack(side="left", padx=(0, 2))

        # Right side: prompt selector, refresh button, model combo
        safe_idx = prompt_idx if prompt_idx < len(prompt_names) else 0
        prompt_var = tk.StringVar(value=prompt_names[safe_idx] if prompt_names else "")
        prompt_cb = ttk.Combobox(f_hdr, textvariable=prompt_var,
                                  values=prompt_names, font=("Segoe UI", 9),
                                  width=9, state="readonly")
        prompt_cb.pack(side="right")
        tk.Label(f_hdr, text="提示词 ", bg=BG, fg="#444444",
                 font=("Segoe UI", 8)).pack(side="right")

        model_var = tk.StringVar(value=model_val)
        model_combo = ttk.Combobox(f_hdr, textvariable=model_var,
                                    font=("Segoe UI", 9), height=16)
        model_combo.pack(side="left", padx=(6, 0), fill="x", expand=True)

        session = {
            "frame": frame,
            "provider_var": provider_var,
            "model_var": model_var,
            "model_combo": model_combo,
            "prompt_var": prompt_var,
            "prompt_cb": prompt_cb,
            "history": [],
            "is_running": False,
            "tab_label": None,
            "tab_frame": None,
            "close_btn": None,
        }

        refresh_btn = tk.Button(
            f_hdr, text="↻",
            command=lambda s=session: self._fetch_session_models(s),
            bg=BG, fg="#5a9fd4", relief="flat", padx=4, pady=2,
            font=("Segoe UI", 9), cursor="hand2",
            activebackground="#252525", activeforeground="#7abfe8",
            bd=0)
        refresh_btn.pack(side="left", padx=(4, 4))
        session["refresh_btn"] = refresh_btn

        # Chat area
        f_chat = tk.Frame(frame, bg="#141414",
                          highlightbackground="#252525", highlightthickness=1)
        f_chat.grid(row=1, column=0, sticky="nsew", padx=16, pady=(5, 0))
        f_chat.columnconfigure(0, weight=1)
        f_chat.rowconfigure(0, weight=1)

        chat_widget = tk.Text(
            f_chat, bg="#141414", fg="#d8d8d8",
            insertbackground="white", relief="flat", font=("Segoe UI", fs), bd=0,
            wrap="word", padx=14, pady=12, spacing1=1, spacing3=1,
            selectbackground="#1e3a5a", selectforeground="#ffffff",
            cursor="arrow", highlightthickness=0)
        chat_widget.grid(row=0, column=0, sticky="nsew")
        chat_widget.configure(state="disabled")

        sb = tk.Scrollbar(f_chat, orient="vertical", command=chat_widget.yview,
                          bg="#3a3a3a", activebackground="#4a4a4a", troughcolor="#141414",
                          relief="flat", bd=0, width=6, elementborderwidth=0,
                          highlightthickness=0)
        sb.grid(row=0, column=1, sticky="ns")
        chat_widget.configure(yscrollcommand=sb.set)

        session["chat_widget"] = chat_widget

        provider_var.trace_add("write", lambda *_, s=session: self._on_session_provider_change(s))
        self._on_session_provider_change(session)
        model_var.set(model_val)
        self._apply_tweet_tags(chat_widget)
        return session

    def _init_sessions(self):
        sessions_cfg = self.app._saved_config.get("tweet_sessions", [])
        names = [p["name"] for p in self._tweet_prompts]
        if sessions_cfg:
            for sc in sessions_cfg:
                provider_val = sc.get("provider", "gemini")
                model_val = sc.get("model", "")
                pname = sc.get("prompt_name", "")
                prompt_idx = names.index(pname) if pname in names else 0
                session = self._make_session_content(provider_val, model_val, prompt_idx)
                self._tweet_sessions.append(session)
            self._rebuild_tab_bar()
            active = min(
                self.app._saved_config.get("tweet_active_session", 0),
                len(self._tweet_sessions) - 1
            )
            self._switch_session(max(0, active))
        else:
            self._add_tweet_session(first=True)

    def _add_tweet_session(self, first=False):
        if self._tweet_sessions:
            prev = self._tweet_sessions[-1]
            provider_val = prev["provider_var"].get()
            model_val = prev["model_var"].get()
            pname = prev["prompt_var"].get()
            names = [p["name"] for p in self._tweet_prompts]
            prompt_idx = names.index(pname) if pname in names else 0
        else:
            cfg = self.app._saved_config
            provider_val = cfg.get("tweet_provider", "gemini")
            model_val = cfg.get("tweet_model", "")
            prompt_idx = 0

        session = self._make_session_content(provider_val, model_val, prompt_idx)
        idx = len(self._tweet_sessions)
        self._tweet_sessions.append(session)
        self._rebuild_tab_bar()
        self._switch_session(idx)

    # ── Session tab bar ───────────────────────────────────────────────────────

    def _rebuild_tab_bar(self):
        for w in self._tweet_tabbar.winfo_children():
            w.destroy()
        for i, session in enumerate(self._tweet_sessions):
            tf = tk.Frame(self._tweet_tabbar, bg="#191919")
            tf.pack(side="left", padx=(2, 0))
            lbl = tk.Button(tf, text=f" 对话{i + 1} ",
                            command=lambda i=i: self._switch_session(i),
                            bg="#191919", fg="#686868", relief="flat", padx=2, pady=4,
                            font=("Segoe UI", 9), cursor="hand2",
                            activebackground="#2e2e2e", activeforeground="#cccccc", bd=0)
            lbl.pack(side="left")
            close_btn = tk.Button(tf, text="×",
                                  command=lambda i=i: self._close_session(i),
                                  bg="#191919", fg="#2a2a2a", relief="flat", padx=3, pady=4,
                                  font=("Segoe UI", 8), cursor="hand2",
                                  activebackground="#2e2e2e", activeforeground="#cc5555",
                                  bd=0)
            close_btn.pack(side="left", padx=(0, 1))
            session["tab_label"] = lbl
            session["tab_frame"] = tf
            session["close_btn"] = close_btn

        tk.Button(self._tweet_tabbar, text=" + ",
                  command=self._add_tweet_session,
                  bg="#191919", fg="#555555", relief="flat", padx=6, pady=4,
                  font=("Segoe UI", 9), cursor="hand2",
                  activebackground="#252525", activeforeground="#999999",
                  bd=0).pack(side="left", padx=(2, 0))

        tk.Frame(self._tweet_tabbar, bg="#2a2a2a", height=1).pack(side="bottom", fill="x")
        self._update_tab_styles()

    def _switch_session(self, idx):
        if 0 <= self._active_session_idx < len(self._tweet_sessions):
            self._tweet_sessions[self._active_session_idx]["frame"].grid_remove()
        self._active_session_idx = idx
        self._tweet_sessions[idx]["frame"].grid(row=0, column=0, sticky="nsew")
        self._update_tab_styles()
        is_running = self._tweet_sessions[idx]["is_running"]
        self._tweet_send_btn.configure(
            state="disabled" if is_running else "normal",
            text="···" if is_running else "发 送")

    def _update_tab_styles(self):
        for i, session in enumerate(self._tweet_sessions):
            lbl = session.get("tab_label")
            tf = session.get("tab_frame")
            close_btn = session.get("close_btn")
            if not lbl:
                continue
            is_active = (i == self._active_session_idx)
            bg = "#2e2e2e" if is_active else "#191919"
            status = " ⏳" if session["is_running"] else ""
            lbl.configure(text=f" 对话{i + 1}{status} ",
                           bg=bg, fg="#cccccc" if is_active else "#686868")
            tf.configure(bg=bg)
            close_btn.configure(bg=bg, fg="#888888" if is_active else "#2a2a2a")

    def _close_session(self, idx):
        if len(self._tweet_sessions) <= 1:
            return
        session = self._tweet_sessions[idx]
        note = "正在生成中，" if session["is_running"] else ""
        if not messagebox.askyesno("关闭对话",
                                    f"「对话 {idx + 1}」{note}确定关闭？",
                                    parent=self.app):
            return
        session["frame"].destroy()
        self._tweet_sessions.pop(idx)
        self._active_session_idx = -1
        self._rebuild_tab_bar()
        self._switch_session(min(idx, len(self._tweet_sessions) - 1))

    # ── Provider / model fetch ────────────────────────────────────────────────

    def _on_session_provider_change(self, session):
        provider = session["provider_var"].get()
        cfg = self.app._saved_config
        if provider == "gemini":
            fetched = cfg.get("gemini_custom_models", [])
            models = fetched if fetched else DEFAULT_MODELS_GEMINI
        elif provider == "siliconflow":
            fetched = cfg.get("custom_models", [])
            models = fetched if fetched else DEFAULT_MODELS_SF
        elif provider == "volcengine":
            fetched = cfg.get("ark_custom_models", [])
            models = fetched if fetched else DEFAULT_MODELS_ARK
        else:  # pioneer
            models = cfg.get("pioneer_custom_models", DEFAULT_MODELS_PIONEER)
        session["model_combo"]["values"] = models
        if session["model_var"].get() not in models:
            session["model_var"].set(models[0] if models else "")

    def _fetch_session_models(self, session):
        provider = session["provider_var"].get()
        api_key = self.app.get_api_key(provider)
        btn = session.get("refresh_btn")
        if btn:
            btn.configure(text="…", state="disabled")

        def _do():
            if provider == "siliconflow":
                raw = fetch_sf_models(api_key)
                cfg_key = "custom_models"
                models = sorted(raw) if raw else raw
            elif provider == "gemini":
                raw = fetch_gemini_models(api_key)
                cfg_key = "gemini_custom_models"
                models = sorted(raw) if raw else raw
            elif provider == "pioneer":
                raw = fetch_pioneer_models(api_key)
                cfg_key = "pioneer_custom_models"
                models = sorted(raw) if raw else raw
            else:  # volcengine — fetch then probe availability
                raw = fetch_ark_models(api_key)
                cfg_key = "ark_custom_models"
                if raw:
                    results = {}
                    workers = min(8, len(raw))
                    with ThreadPoolExecutor(max_workers=workers) as ex:
                        futures = {ex.submit(test_ark_model, api_key, m): m for m in raw}
                        for f in as_completed(futures):
                            results[futures[f]] = f.result()
                    avail = sorted(m for m, v in results.items() if v is not False)
                    unavail = sorted(m for m, v in results.items() if v is False)
                    models = [f"{m} ✓" for m in avail] + [f"{m} ✗" for m in unavail]
                else:
                    models = raw  # None or []

            def _update():
                if btn:
                    btn.configure(text="↻", state="normal")
                if models:
                    self.app._saved_config[cfg_key] = models
                    self.app._do_save_config()
                    for s in self._tweet_sessions:
                        if s["provider_var"].get() == provider:
                            self._on_session_provider_change(s)

            self.app.after(0, _update)

        threading.Thread(target=_do, daemon=True).start()

    # ── Tags / font / spacing ─────────────────────────────────────────────────

    def _apply_tweet_tags(self, chat_widget=None):
        fs = self._tweet_font_size.get()
        sp = self._tweet_line_spacing.get()
        sf = max(fs - 3, 7)   # small font size shared by user_hdr and ai_meta
        f = "Segoe UI"
        targets = [chat_widget] if chat_widget else [s["chat_widget"] for s in self._tweet_sessions]
        for w in targets:
            # Username line: very small, bright cyan, own paragraph
            w.tag_configure("user_hdr", foreground="#5ac8e8",
                font=(f, sf, "bold"), spacing1=max(sp * 2, 6), spacing3=1)
            # User message: normal, no indent
            w.tag_configure("user_text", foreground="#c0d8e8",
                font=(f, fs), spacing3=sp, lmargin1=0, lmargin2=0)
            # AI header line: model · prompt, same small font as username, bright green
            w.tag_configure("ai_meta", foreground="#58d68a",
                font=(f, sf, "bold"), spacing1=max(sp * 2, 6), spacing3=1,
                lmargin1=0, lmargin2=0)
            # AI response text
            w.tag_configure("ai_text", foreground="#dedede",
                font=(f, fs), spacing3=sp, lmargin1=0, lmargin2=0)
            w.tag_configure("err_text", foreground="#d06060",
                font=(f, fs), spacing3=sp)
            w.tag_configure("timing", foreground="#787878",
                font=(f, max(fs - 3, 7)), spacing3=1, lmargin1=0, lmargin2=0)
            w.tag_configure("attachment", foreground="#7a9ab0",
                font=(f, max(fs - 2, 8)), spacing3=2, lmargin1=0, lmargin2=0)
            w.tag_configure("sep", foreground=BG,
                font=(f, 4), spacing1=1, spacing3=max(sp, 2))

    def _update_tweet_font(self, delta):
        cur = self._tweet_font_size.get()
        new = max(8, min(24, cur + delta))
        if new == cur:
            return
        self._tweet_font_size.set(new)
        for s in self._tweet_sessions:
            s["chat_widget"].configure(font=("Segoe UI", new))
        self._apply_tweet_tags()

    def _update_line_spacing(self, delta):
        cur = self._tweet_line_spacing.get()
        new = max(0, min(20, cur + delta))
        if new == cur:
            return
        self._tweet_line_spacing.set(new)
        self._apply_tweet_tags()

    # ── Chat helpers ──────────────────────────────────────────────────────────

    def _append_to(self, session, text, tag):
        w = session["chat_widget"]
        w.configure(state="normal")
        w.insert("end", text, tag)
        w.see("end")
        w.configure(state="disabled")

    def _stream_chunk(self, session, text):
        w = session["chat_widget"]
        w.configure(state="normal")
        w.insert("end", text, "ai_text")
        w.see("end")
        w.configure(state="disabled")

    def _get_session_system_prompt(self, session):
        pname = session["prompt_var"].get()
        for p in self._tweet_prompts:
            if p["name"] == pname:
                return p["text"].strip()
        return ""

    def _new_tweet_conversation(self):
        idx = self._active_session_idx
        if not (0 <= idx < len(self._tweet_sessions)):
            return
        session = self._tweet_sessions[idx]
        session["history"] = []
        session["chat_widget"].configure(state="normal")
        session["chat_widget"].delete("1.0", "end")
        session["chat_widget"].configure(state="disabled")

    # ── Send ──────────────────────────────────────────────────────────────────

    def _send_tweet(self):
        idx = self._active_session_idx
        if not (0 <= idx < len(self._tweet_sessions)):
            return
        session = self._tweet_sessions[idx]
        if session["is_running"]:
            return
        msg = self._tweet_input.get("1.0", "end").strip()
        if not msg and not self._pending_attachments:
            return
        self._tweet_input.delete("1.0", "end")

        attachments = list(self._pending_attachments)
        if attachments:
            self._pending_attachments.clear()
            self._refresh_file_chips()

        session["history"].append({"role": "user", "content": msg or "(附件)"})
        username = self.app._saved_config.get("tweet_username", "qinqincr")
        self._append_to(session, username + "\n", "user_hdr")
        if msg:
            self._append_to(session, msg + "\n", "user_text")
        if attachments:
            for a in attachments:
                self._append_to(session, f"📎 {a['name']}\n", "attachment")

        provider = session["provider_var"].get()
        model = _strip_model_marker(session["model_var"].get())
        api_key = self.app.get_api_key(provider)
        system_prompt = self._get_session_system_prompt(session)
        history = list(session["history"])

        session["is_running"] = True
        self._is_running = True
        self._tweet_send_btn.configure(state="disabled", text="···")
        self._update_tab_styles()

        # AI header: model · prompt on one small line (no "AI" prefix)
        pname = session["prompt_var"].get()
        meta = model + (f"  ·  {pname}" if pname else "")
        self._append_to(session, meta + "\n", "ai_meta")

        def on_chunk(text):
            self.app.after(0, lambda t=text: self._stream_chunk(session, t))

        def task():
            t0 = time.time()
            full_text, err = chat_completion_stream(
                history, system_prompt, provider, api_key, model, on_chunk, attachments)
            elapsed = time.time() - t0
            if err:
                self.app.after(0, lambda: self._append_to(session, f"❌ {err}\n", "err_text"))
            else:
                if full_text:
                    session["history"].append({"role": "assistant", "content": full_text})
                self.app.after(0, lambda: self._stream_chunk(session, "\n"))
            self.app.after(0, lambda: self._append_to(session, f"⏱ {elapsed:.1f}s\n", "timing"))
            self.app.after(0, lambda: self._append_to(session, "─" * 48 + "\n", "sep"))
            session["is_running"] = False
            self.app.after(0, self._on_session_done)

        threading.Thread(target=task, daemon=True).start()

    def _on_session_done(self):
        self._is_running = any(s["is_running"] for s in self._tweet_sessions)
        self._update_tab_styles()
        idx = self._active_session_idx
        if 0 <= idx < len(self._tweet_sessions):
            is_running = self._tweet_sessions[idx]["is_running"]
            self._tweet_send_btn.configure(
                state="disabled" if is_running else "normal",
                text="···" if is_running else "发 送")

    # ── File attachments ─────────────────────────────────────────────────────

    def _browse_attachments(self):
        paths = filedialog.askopenfilenames(
            title="选择附件",
            filetypes=[
                ("图片", "*.jpg *.jpeg *.png *.gif *.webp *.bmp"),
                ("文本", "*.txt *.md *.srt *.csv *.json *.xml *.py *.js *.html *.log"),
                ("所有文件", "*.*"),
            ],
        )
        for p in paths:
            self._add_attachment(p)

    def _add_attachment(self, path):
        name = os.path.basename(path)
        if any(a["name"] == name for a in self._pending_attachments):
            return
        mt, _ = mimetypes.guess_type(path)
        if not mt:
            ext = os.path.splitext(path)[1].lower()
            text_exts = {".txt", ".md", ".srt", ".csv", ".json", ".xml",
                         ".html", ".py", ".js", ".log", ".ini", ".cfg"}
            mt = "text/plain" if ext in text_exts else "application/octet-stream"
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception:
            return
        self._pending_attachments.append({"name": name, "mime_type": mt, "data": data})
        self._refresh_file_chips()

    def _remove_attachment(self, idx):
        if 0 <= idx < len(self._pending_attachments):
            self._pending_attachments.pop(idx)
            self._refresh_file_chips()

    def _refresh_file_chips(self):
        for w in self._f_file_chips.winfo_children():
            w.destroy()
        if not self._pending_attachments:
            self._f_file_chips.grid_remove()
            return
        self._f_file_chips.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 4))
        for i, att in enumerate(self._pending_attachments):
            chip = tk.Frame(self._f_file_chips, bg="#2a2a2a",
                            highlightbackground="#3a3a3a", highlightthickness=1)
            chip.pack(side="left", padx=(0, 4), pady=(4, 2))
            tk.Label(chip, text=f"📎 {att['name']}", bg="#2a2a2a", fg="#7aaac0",
                     font=("Segoe UI", 8)).pack(side="left", padx=(6, 0), pady=2)
            tk.Button(chip, text="×",
                      command=lambda i=i: self._remove_attachment(i),
                      bg="#2a2a2a", fg="#555555", relief="flat", padx=4, pady=0,
                      font=("Segoe UI", 8), cursor="hand2",
                      activeforeground="#cc5555", activebackground="#2a2a2a",
                      bd=0).pack(side="left", padx=(2, 4))

    def _on_file_drop(self, event):
        paths = re.findall(r'\{[^}]+\}|[^\s]+', event.data.strip())
        for p in paths:
            self._add_attachment(p.strip("{}"))

    # ── Config ────────────────────────────────────────────────────────────────

    def get_config(self):
        # Flush unsaved prompt edits
        ei = self._tweet_editing_prompt_idx
        if ei >= 0:
            name = self._prompt_edit_name_var.get().strip() or f"场景 {ei + 1}"
            text = self._prompt_edit_text.get("1.0", "end").strip()
            self._tweet_prompts[ei] = {"name": name, "text": text}

        sessions_cfg = []
        for s in self._tweet_sessions:
            sessions_cfg.append({
                "provider": s["provider_var"].get(),
                "model": s["model_var"].get().strip(),
                "prompt_name": s["prompt_var"].get(),
            })

        active = (self._tweet_sessions[self._active_session_idx]
                  if 0 <= self._active_session_idx < len(self._tweet_sessions) else None)
        return {
            "tweet_provider": active["provider_var"].get() if active else "gemini",
            "tweet_model": _strip_model_marker(active["model_var"].get().strip()) if active else "",
            "tweet_prompts": list(self._tweet_prompts),
            "tweet_font_size": self._tweet_font_size.get(),
            "tweet_line_spacing": self._tweet_line_spacing.get(),
            "tweet_sessions": sessions_cfg,
            "tweet_active_session": max(0, self._active_session_idx),
        }
