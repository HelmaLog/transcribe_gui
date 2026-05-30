r"""Claude Code 会话日志(.jsonl)与记忆(memory\*.md)的解析与渲染。

被两处复用:
- chat_history_viewer.py —— 生成可在浏览器看的富文本 HTML;
- tabs/chat_history.py —— GUI 内「聊天记录」tab 的原生展示。

会话被解析成中性结构 turns:list[(speaker, ts, segments)],
segment = (kind, primary, detail),kind ∈ {text, thinking, tool_use, tool_result}。
"""
import os
import re
import json
import html
from pathlib import Path
from datetime import datetime

# 当前项目对应的 .claude 会话目录
LOG_DIR = (Path.home() / ".claude" / "projects"
           / "D--GitHub-transcribe-gui-transcribe-gui")
MEMORY_DIR = LOG_DIR / "memory"

MAX_DETAIL = 1200   # 思考/工具内容超出则截断


def default_log_dir() -> Path:
    return LOG_DIR


def memory_dir() -> Path:
    return MEMORY_DIR


def fmt_ts(ts: str) -> str:
    try:
        dt = datetime.fromisoformat((ts or "").replace("Z", "+00:00")).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts or ""


def _segments(content):
    """content(str 或 block 列表) → (segments, only_tool_result)。"""
    segs, only_tool = [], True
    if isinstance(content, str):
        return ([("text", content, "")], False)
    for b in content or []:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text":
            only_tool = False
            segs.append(("text", b.get("text", ""), ""))
        elif t == "thinking":
            only_tool = False
            segs.append(("thinking", "思考", (b.get("thinking", "") or "")[:MAX_DETAIL]))
        elif t == "tool_use":
            only_tool = False
            inp = json.dumps(b.get("input", {}), ensure_ascii=False)
            segs.append(("tool_use", str(b.get("name", "tool")), inp[:MAX_DETAIL]))
        elif t == "tool_result":
            c = b.get("content", "")
            if isinstance(c, list):
                c = "\n".join(x.get("text", "") for x in c
                              if isinstance(x, dict) and x.get("type") == "text")
            segs.append(("tool_result", "工具输出", str(c)[:MAX_DETAIL]))
    return (segs, only_tool)


def load_session(path) -> dict:
    """完整解析单个会话。返回 dict(title, start, turns)。"""
    turns, title, start = [], "", ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("type") not in ("user", "assistant"):
                    continue
                msg = obj.get("message") or {}
                role = msg.get("role", obj.get("type"))
                ts = obj.get("timestamp", "")
                if not start and ts:
                    start = ts
                segs, only_tool = _segments(msg.get("content"))
                if not segs:
                    continue
                speaker = "tool" if (role == "user" and only_tool) else role
                if speaker == "user" and not title:
                    c = msg.get("content")
                    if isinstance(c, str) and c.strip():
                        title = c.strip().replace("\n", " ")[:60]
                turns.append((speaker, ts, segs))
    except Exception:
        pass
    return {"title": title or "(无文字消息)", "start": start, "turns": turns}


def _scan_meta(path) -> dict:
    """只读到拿到标题+起始时间即停,用于快速生成列表。"""
    title, start = "", ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("type") not in ("user", "assistant"):
                    continue
                if not start:
                    start = obj.get("timestamp", "")
                if not title:
                    msg = obj.get("message") or {}
                    c = msg.get("content")
                    if msg.get("role") == "user" and isinstance(c, str) and c.strip():
                        title = c.strip().replace("\n", " ")[:60]
                if title and start:
                    break
    except Exception:
        pass
    return {"title": title or "(无文字消息)", "start": start}


def list_sessions(log_dir=None) -> list:
    """返回 [{path, title, start}],按时间倒序。"""
    d = Path(log_dir) if log_dir else LOG_DIR
    out = []
    if d.is_dir():
        for jp in d.glob("*.jsonl"):
            meta = _scan_meta(jp)
            out.append({"path": str(jp), **meta})
    out.sort(key=lambda s: s["start"], reverse=True)
    return out


def read_memory() -> list:
    """读取记忆:返回 [(显示名, 文本)];MEMORY.md 排最前。"""
    items = []
    if not MEMORY_DIR.is_dir():
        return items
    files = sorted(MEMORY_DIR.glob("*.md"))
    files.sort(key=lambda p: (p.name != "MEMORY.md", p.name))
    for p in files:
        try:
            items.append((p.name, p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return items


# ── HTML 渲染(供浏览器版) ──────────────────────────────────────────────────

PAGE_CSS = """
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;background:#1e1e1e;color:#ddd;font-family:'Segoe UI',sans-serif;font-size:15px}
a{color:#3dff85;text-decoration:none}
.wrap{max-width:900px;margin:0 auto;padding:20px}
.top{position:sticky;top:0;background:#1e1e1e;padding:12px 0;border-bottom:1px solid #333;margin-bottom:16px}
.msg{margin:14px 0;display:flex;flex-direction:column}
.msg .who{font-size:12px;color:#777;margin-bottom:3px}
.bubble{padding:10px 14px;border-radius:12px;max-width:88%;word-wrap:break-word}
.user .bubble{background:#2a3f2a;align-self:flex-end;border:1px solid #3a5a3a}
.user .who{align-self:flex-end}
.assistant .bubble{background:#262626;align-self:flex-start;border:1px solid #383838}
.tool .bubble{background:#1a1a1a;align-self:flex-start;border:1px dashed #444;max-width:96%}
.text{line-height:1.55}
details{margin:6px 0}summary{cursor:pointer;color:#88aa88;font-size:13px}
details.think summary{color:#7788aa}
pre{background:#141414;border:1px solid #333;border-radius:6px;padding:8px;overflow-x:auto;
    font-family:Consolas,monospace;font-size:12.5px;color:#bbb;white-space:pre-wrap}
.ts{font-size:11px;color:#555;margin-left:8px}
table{width:100%;border-collapse:collapse}
td,th{padding:8px 10px;border-bottom:1px solid #2c2c2c;text-align:left;vertical-align:top}
th{color:#888;font-size:13px}tr:hover{background:#262626}.cnt{color:#3dff85}
input#flt{width:100%;padding:8px 10px;background:#252525;border:1px solid #3a3a3a;
          border-radius:6px;color:#ddd;font-size:14px}
.mem{background:#202a20;border:1px solid #2f4f2f;border-radius:10px;padding:14px;margin-bottom:18px}
.mem pre{background:#161d16;border-color:#2a3a2a}
.md{line-height:1.6;padding:6px 4px 2px}
.md h2{font-size:18px;color:#3dff85;margin:14px 0 6px;border-bottom:1px solid #2f4f2f;padding-bottom:4px}
.md h3{font-size:16px;color:#9fe0b0;margin:12px 0 4px}
.md h4,.md h5{font-size:14px;color:#9fe0b0;margin:10px 0 4px}
.md p{margin:6px 0}
.md ul{margin:6px 0 6px 0;padding-left:22px}
.md li{margin:3px 0}
.md code{background:#161d16;border:1px solid #2a3a2a;border-radius:4px;padding:1px 5px;
         font-family:Consolas,monospace;font-size:13px;color:#7fe0a0}
.md strong{color:#fff}
.md a{color:#3dff85}
.md .wl{color:#7fa0e0}
.fm{background:#161d16;border:1px solid #2a3a2a;border-radius:6px;padding:8px 10px;
    margin:4px 0 10px;font-family:Consolas,monospace;font-size:12px;color:#888}
.fm .fmk{color:#7fe0a0;margin-right:4px}
"""


def _esc(s):
    return html.escape(s or "").replace("\n", "<br>")


def _md_inline(s: str) -> str:
    """行内 markdown:转义 → 代码/加粗/wikilink/链接。"""
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[\[([^\]]+)\]\]", r'<span class="wl">[[\1]]</span>', s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


def md_to_html(text: str) -> str:
    """把记忆 .md 渲染成排版后的 HTML(轻量:frontmatter/标题/列表/加粗/代码)。"""
    lines = text.splitlines()
    out, i, n = [], 0, len(lines)

    # YAML frontmatter → 一个紧凑的元信息小框
    if i < n and lines[i].strip() == "---":
        fm, i = [], i + 1
        while i < n and lines[i].strip() != "---":
            fm.append(lines[i])
            i += 1
        i += 1
        rows = []
        for ln in fm:
            if ln.strip():
                k, _, v = ln.partition(":")
                rows.append(f'<span class="fmk">{html.escape(k.strip())}</span>'
                            f"{html.escape(v.strip())}")
        if rows:
            out.append('<div class="fm">' + "<br>".join(rows) + "</div>")

    para, in_list = [], False

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(_md_inline(x) for x in para) + "</p>")
            para.clear()

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    while i < n:
        st = lines[i].strip()
        i += 1
        if not st:
            flush_para(); close_list(); continue
        m = re.match(r"(#{1,4})\s+(.*)", st)
        if m:
            flush_para(); close_list()
            lvl = min(len(m.group(1)) + 1, 5)
            out.append(f"<h{lvl}>{_md_inline(m.group(2))}</h{lvl}>")
            continue
        m = re.match(r"[-*]\s+(.*)", st)
        if m:
            flush_para()
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{_md_inline(m.group(1))}</li>")
            continue
        close_list()
        para.append(st)
    flush_para(); close_list()
    return "".join(out)


def _segs_to_html(segs):
    out = []
    for kind, primary, detail in segs:
        if kind == "text":
            out.append(f'<div class="text">{_esc(primary)}</div>')
        elif kind == "thinking":
            out.append(f'<details class="think"><summary>💭 思考</summary>'
                       f'<div class="text">{_esc(detail)}</div></details>')
        elif kind == "tool_use":
            out.append(f'<details class="tool"><summary>🔧 {html.escape(primary)}</summary>'
                       f'<pre>{html.escape(detail)}</pre></details>')
        elif kind == "tool_result":
            out.append(f'<details class="result"><summary>📤 工具输出</summary>'
                       f'<pre>{html.escape(detail)}</pre></details>')
    return "".join(out)


def _memory_html():
    items = read_memory()
    if not items:
        return ""
    body = ['<div class="mem"><h3 style="margin-top:0;color:#3dff85">🧠 我的记忆</h3>']
    for name, text in items:
        opened = " open" if name == "MEMORY.md" else ""
        body.append(f'<details{opened}><summary>📄 {html.escape(name)}</summary>'
                    f'<div class="md">{md_to_html(text)}</div></details>')
    body.append("</div>")
    return "".join(body)


def _session_page(sess):
    rows = []
    for speaker, ts, segs in sess["turns"]:
        who = {"user": "🧑 我", "assistant": "🤖 Claude",
               "tool": "⚙ 工具"}.get(speaker, speaker)
        rows.append(f'<div class="msg {speaker}"><div class="who">{who}'
                    f'<span class="ts">{fmt_ts(ts)}</span></div>'
                    f'<div class="bubble">{_segs_to_html(segs)}</div></div>')
    t = html.escape(sess["title"])
    return (f'<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{t}</title><style>{PAGE_CSS}</style></head><body><div class="wrap">'
            f'<div class="top"><a href="index.html">← 返回会话列表</a>&nbsp;&nbsp;<b>{t}</b></div>'
            f'{"".join(rows)}</div></body></html>')


def _index_page(metas):
    rows = []
    for m in metas:
        rows.append(f'<tr><td>{fmt_ts(m["start"])}</td>'
                    f'<td><a href="{m["file"]}">{html.escape(m["title"])}</a></td></tr>')
    js = ("const f=document.getElementById('flt');f.addEventListener('input',()=>{"
          "const q=f.value.toLowerCase();document.querySelectorAll('tbody tr')"
          ".forEach(tr=>{tr.style.display=tr.innerText.toLowerCase().includes(q)?'':'none';});});")
    return (f'<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>会话记录</title><style>{PAGE_CSS}</style></head><body><div class="wrap">'
            f'<div class="top"><b>📜 会话记录</b>&nbsp;共 {len(metas)} 次对话</div>'
            f'{_memory_html()}'
            f'<input id="flt" placeholder="🔍 按标题/日期筛选…"><br><br>'
            f'<table><thead><tr><th>时间</th><th>标题(首条提问)</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div><script>{js}</script></body></html>')


def generate_html(log_dir=None) -> Path:
    """生成 chat_history\\index.html 及各会话页,返回 index.html 路径。"""
    d = Path(log_dir) if log_dir else LOG_DIR
    out_dir = d / "chat_history"
    out_dir.mkdir(exist_ok=True)
    metas = []
    for jp in d.glob("*.jsonl"):
        sess = load_session(jp)
        if not sess["turns"]:
            continue
        fname = f"s_{jp.stem[:8]}.html"
        (out_dir / fname).write_text(_session_page(sess), encoding="utf-8")
        metas.append({"file": fname, "title": sess["title"], "start": sess["start"]})
    metas.sort(key=lambda m: m["start"], reverse=True)
    (out_dir / "index.html").write_text(_index_page(metas), encoding="utf-8")
    return out_dir / "index.html"
