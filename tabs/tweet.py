"""
Tweet / AI chat tab.
"""

import threading
import tkinter as tk
from tkinter import ttk

from backend import (
    DEFAULT_MODELS_SF, DEFAULT_MODELS_ARK, DEFAULT_MODELS_GEMINI, DEFAULT_CONFIG,
    chat_completion_stream,
)
from .base import Tab, BG


class TweetTab(Tab):

    def _build(self):
        cfg = self.app._saved_config
        p = self.frame

        p.columnconfigure(0, weight=1)
        p.rowconfigure(0, weight=1)

        fs = cfg.get("tweet_font_size", 11)
        self._tweet_font_size = tk.IntVar(value=fs)

        prompts = cfg.get("tweet_prompts", DEFAULT_CONFIG["tweet_prompts"])

        # PanedWindow: top = prompt config (draggable), bottom = chat
        pw = tk.PanedWindow(p, orient=tk.VERTICAL,
                            sashwidth=5, sashpad=0, sashrelief="flat",
                            sashcursor="sb_v_double_arrow",
                            bg="#252525", bd=0, relief="flat")
        pw.grid(row=0, column=0, sticky="nsew")

        # ── Top pane: Prompt config ──
        top = tk.Frame(pw, bg=BG)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(1, weight=1)

        tk.Label(top, text="  提示词配置", bg=BG, fg="#444444",
                 font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w", padx=16, pady=(10, 0))

        pnb = ttk.Notebook(top)
        pnb.grid(row=1, column=0, sticky="nsew", padx=16, pady=(4, 10))
        self._tweet_prompt_nb = pnb
        self._tweet_prompt_name_vars = []
        self._tweet_prompt_texts = []

        for i, pd in enumerate(prompts):
            tab = tk.Frame(pnb, bg="#161616")
            tab.columnconfigure(1, weight=1)
            tab.rowconfigure(1, weight=1)
            pnb.add(tab, text=f"  {pd['name']}  ")

            tk.Label(tab, text="名称", bg="#161616", fg="#555555",
                     font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w",
                                                padx=(14, 6), pady=(10, 6))
            name_var = tk.StringVar(value=pd["name"])
            self._tweet_prompt_name_vars.append(name_var)
            tk.Entry(tab, textvariable=name_var, bg="#222222", fg="#bbbbbb",
                     insertbackground="#888888", relief="flat",
                     font=("Segoe UI", 10), bd=3, highlightthickness=0).grid(
                row=0, column=1, sticky="ew", padx=(0, 14), pady=(10, 6), ipady=4)

            pt = tk.Text(tab, bg="#111111", fg="#cccccc", insertbackground="#888888",
                         relief="flat", font=("Segoe UI", 10), bd=0,
                         wrap="word", padx=12, pady=10, height=4,
                         selectbackground="#1e3a5a", highlightthickness=0)
            pt.insert("1.0", pd["text"])
            pt.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=14, pady=(0, 0))
            self._tweet_prompt_texts.append(pt)

            idx = i
            tk.Button(tab, text="保 存", command=lambda i=idx: self._save_tweet_prompt(i),
                      bg="#161616", fg="#505050", relief="flat", padx=14, pady=5,
                      font=("Segoe UI", 9), cursor="hand2",
                      activebackground="#242424", activeforeground="#aaaaaa",
                      bd=0).grid(row=2, column=1, sticky="e", padx=(0, 14), pady=(6, 10))

        pw.add(top, minsize=60, stretch="never")

        # ── Bottom pane: Chat ──
        bottom = tk.Frame(pw, bg=BG)
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(1, weight=1)

        # Chat header row
        f_hdr = tk.Frame(bottom, bg=BG)
        f_hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 0))

        # Provider radios
        self.tweet_provider_var = tk.StringVar(value=cfg.get("tweet_provider", "gemini"))
        for val, txt in [("gemini", "Gemini"), ("siliconflow", "硅基"), ("volcengine", "ARK")]:
            tk.Radiobutton(f_hdr, text=txt, variable=self.tweet_provider_var, value=val,
                           bg=BG, fg="#666666", selectcolor=BG,
                           activebackground=BG, activeforeground="#aaaaaa",
                           font=("Segoe UI", 9),
                           command=self._on_tweet_provider_change).pack(side="left", padx=(0, 2))

        self.tweet_model_var = tk.StringVar(value=cfg.get("tweet_model", DEFAULT_MODELS_GEMINI[0]))
        self.tweet_model_combo = ttk.Combobox(f_hdr, textvariable=self.tweet_model_var,
                                               font=("Segoe UI", 9), width=20)
        self.tweet_model_combo.pack(side="left", padx=(8, 0))

        # Right side: font controls + new conversation
        f_right = tk.Frame(f_hdr, bg=BG)
        f_right.pack(side="right")

        _bkw = dict(bg=BG, relief="flat", font=("Segoe UI", 9), cursor="hand2",
                    activebackground="#252525", bd=0, padx=4, pady=2)
        tk.Button(f_right, text="A−", fg="#484848",
                  command=lambda: self._update_tweet_font(-1), **_bkw).pack(side="left")
        tk.Label(f_right, textvariable=self._tweet_font_size, bg=BG, fg="#3e3e3e",
                 font=("Segoe UI", 9), width=2, anchor="center").pack(side="left")
        tk.Button(f_right, text="A+", fg="#484848",
                  command=lambda: self._update_tweet_font(1), **_bkw).pack(side="left")

        # Chat display
        f_chat = tk.Frame(bottom, bg="#0c0c0c",
                          highlightbackground=BG, highlightthickness=1)
        f_chat.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 0))
        f_chat.columnconfigure(0, weight=1)
        f_chat.rowconfigure(0, weight=1)

        self._tweet_chat = tk.Text(
            f_chat, bg="#0c0c0c", fg="#d8d8d8",
            insertbackground="white", relief="flat", font=("Segoe UI", fs), bd=0,
            wrap="word", padx=16, pady=14, spacing1=1, spacing3=1,
            selectbackground="#1e3a5a", selectforeground="#ffffff",
            cursor="arrow", highlightthickness=0)
        self._tweet_chat.grid(row=0, column=0, sticky="nsew")
        self._tweet_chat.configure(state="disabled")

        # Thin 7px dark scrollbar
        chat_sb = tk.Scrollbar(
            f_chat, orient="vertical", command=self._tweet_chat.yview,
            bg="#282828", activebackground="#383838", troughcolor="#0c0c0c",
            relief="flat", bd=0, width=7, elementborderwidth=0, highlightthickness=0)
        chat_sb.grid(row=0, column=1, sticky="ns")
        self._tweet_chat.configure(yscrollcommand=chat_sb.set)
        self._apply_tweet_tags()

        # Input box
        f_input_wrap = tk.Frame(bottom, bg="#1a1a1a",
                                highlightbackground="#2a2a2a", highlightthickness=1)
        f_input_wrap.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 0))
        f_input_wrap.columnconfigure(0, weight=1)

        self._tweet_input = tk.Text(
            f_input_wrap, bg="#141414", fg="#d0d0d0",
            insertbackground="#7a7a7a", relief="flat", font=("Segoe UI", 10),
            bd=0, height=4, wrap="word", padx=14, pady=10, highlightthickness=0)
        self._tweet_input.grid(row=0, column=0, sticky="ew")
        self._tweet_input.bind("<Return>",
                               lambda e: (self._send_tweet(), "break")[-1])
        self._tweet_input.bind("<Control-Return>",
                               lambda e: (self._tweet_input.insert("insert", "\n"), "break")[-1])

        # Send row
        f_send = tk.Frame(bottom, bg=BG)
        f_send.grid(row=3, column=0, sticky="e", padx=16, pady=(8, 14))
        tk.Button(f_send, text="新对话", command=self._new_tweet_conversation,
                  bg=BG, fg="#555555", relief="flat", padx=14, pady=7,
                  font=("Segoe UI", 9), cursor="hand2",
                  activebackground="#252525", activeforeground="#999999",
                  bd=0).pack(side="left", padx=(0, 8))
        self.tweet_send_btn = tk.Button(
            f_send, text="发 送", command=self._send_tweet,
            bg="#0a6dd4", fg="#ffffff", relief="flat", padx=28, pady=7,
            font=("Segoe UI", 10, "bold"), cursor="hand2",
            activebackground="#0060bb", bd=0)
        self.tweet_send_btn.pack(side="left")

        pw.add(bottom, minsize=220, stretch="always")

        # Init
        self._tweet_history = []
        self._on_tweet_provider_change()

    # ── Tweet tags / font ─────────────────────────────────────────────────────

    def _apply_tweet_tags(self):
        fs = self._tweet_font_size.get()
        f = "Segoe UI"
        self._tweet_chat.tag_configure(
            "user_hdr", foreground="#4a9fd4",
            font=(f, fs - 1, "bold"), spacing1=16, spacing3=3)
        self._tweet_chat.tag_configure(
            "ai_hdr", foreground="#5aaa5a",
            font=(f, fs - 1, "bold"), spacing1=16, spacing3=3)
        self._tweet_chat.tag_configure(
            "user_text", foreground="#c0d8e8",
            font=(f, fs), spacing3=4, lmargin1=4, lmargin2=4)
        self._tweet_chat.tag_configure(
            "ai_text", foreground="#dedede",
            font=(f, fs), spacing3=4, lmargin1=4, lmargin2=4)
        self._tweet_chat.tag_configure(
            "err_text", foreground="#d05050",
            font=(f, fs), spacing3=4)
        self._tweet_chat.tag_configure(
            "sep", foreground=BG,
            font=(f, 6), spacing1=4, spacing3=10)

    def _update_tweet_font(self, delta):
        cur = self._tweet_font_size.get()
        new = max(8, min(24, cur + delta))
        if new == cur:
            return
        self._tweet_font_size.set(new)
        self._tweet_chat.configure(font=("Segoe UI", new))
        self._apply_tweet_tags()

    # ── Provider change ───────────────────────────────────────────────────────

    def _on_tweet_provider_change(self):
        provider = self.tweet_provider_var.get()
        cfg = self.app._saved_config
        if provider == "gemini":
            models = DEFAULT_MODELS_GEMINI + cfg.get("gemini_custom_models", [])
        elif provider == "siliconflow":
            models = DEFAULT_MODELS_SF + cfg.get("custom_models", [])
        else:
            models = DEFAULT_MODELS_ARK + cfg.get("ark_custom_models", [])
        self.tweet_model_combo["values"] = models
        cur = self.tweet_model_var.get()
        if cur not in models:
            self.tweet_model_var.set(models[0])

    # ── Prompt management ─────────────────────────────────────────────────────

    def _save_tweet_prompt(self, idx):
        name = self._tweet_prompt_name_vars[idx].get().strip() or f"场景 {idx+1}"
        text = self._tweet_prompt_texts[idx].get("1.0", "end").strip()
        prompts = self.app._saved_config.get("tweet_prompts", DEFAULT_CONFIG["tweet_prompts"])
        prompts[idx] = {"name": name, "text": text}
        self.app._saved_config["tweet_prompts"] = prompts
        self._tweet_prompt_nb.tab(idx, text=f"  {name}  ")
        self.app._do_save_config()

    def _new_tweet_conversation(self):
        self._tweet_history = []
        self._tweet_chat.configure(state="normal")
        self._tweet_chat.delete("1.0", "end")
        self._tweet_chat.configure(state="disabled")

    # ── API key access ────────────────────────────────────────────────────────

    def _get_tweet_api_key(self):
        provider = self.tweet_provider_var.get()
        return self.app.get_api_key(provider)

    def _get_tweet_system_prompt(self):
        try:
            idx = self._tweet_prompt_nb.index("current")
            return self._tweet_prompt_texts[idx].get("1.0", "end").strip()
        except Exception:
            return ""

    # ── Chat display helpers ──────────────────────────────────────────────────

    def _append_tweet(self, text, tag):
        self._tweet_chat.configure(state="normal")
        self._tweet_chat.insert("end", text, tag)
        self._tweet_chat.see("end")
        self._tweet_chat.configure(state="disabled")

    def _stream_tweet_chunk(self, text):
        self._tweet_chat.configure(state="normal")
        self._tweet_chat.insert("end", text, "ai_text")
        self._tweet_chat.see("end")
        self._tweet_chat.configure(state="disabled")

    # ── Send tweet message ────────────────────────────────────────────────────

    def _send_tweet(self):
        if self._is_running:
            return
        msg = self._tweet_input.get("1.0", "end").strip()
        if not msg:
            return
        self._tweet_input.delete("1.0", "end")

        self._tweet_history.append({"role": "user", "content": msg})
        self._append_tweet("你\n", "user_hdr")
        self._append_tweet(msg + "\n", "user_text")

        provider = self.tweet_provider_var.get()
        model = self.tweet_model_var.get()
        api_key = self._get_tweet_api_key()
        system_prompt = self._get_tweet_system_prompt()
        history = list(self._tweet_history)

        self._is_running = True
        self.tweet_send_btn.configure(state="disabled", text="···")
        self._append_tweet("AI\n", "ai_hdr")

        def on_chunk(text):
            self.app.after(0, lambda t=text: self._stream_tweet_chunk(t))

        def task():
            full_text, err = chat_completion_stream(
                history, system_prompt, provider, api_key, model, on_chunk
            )
            if err:
                self.app.after(0, lambda: self._append_tweet(f"❌ {err}\n", "err_text"))
            else:
                if full_text:
                    self._tweet_history.append({"role": "assistant", "content": full_text})
                self.app.after(0, lambda: self._stream_tweet_chunk("\n"))
            sep = "─" * 52 + "\n"
            self.app.after(0, lambda: self._append_tweet(sep, "sep"))
            self._is_running = False
            self.app.after(0, lambda: self.tweet_send_btn.configure(state="normal", text="发 送"))

        threading.Thread(target=task, daemon=True).start()

    # ── Config save ───────────────────────────────────────────────────────────

    def get_config(self):
        tweet_prompts = []
        for i in range(3):
            name = self._tweet_prompt_name_vars[i].get().strip() or f"场景 {i+1}"
            text = self._tweet_prompt_texts[i].get("1.0", "end").strip()
            tweet_prompts.append({"name": name, "text": text})
        return {
            "tweet_provider": self.tweet_provider_var.get(),
            "tweet_model": self.tweet_model_var.get().strip(),
            "tweet_prompts": tweet_prompts,
            "tweet_font_size": self._tweet_font_size.get(),
        }
