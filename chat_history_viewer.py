r"""把 Claude Code 的会话日志(.jsonl)转成可浏览的聊天记录网页。

用法:
    py chat_history_viewer.py            # 用默认日志目录
    py chat_history_viewer.py <日志目录>  # 指定目录

输出:在日志目录下生成 chat_history 内的 index.html(会话列表,含记忆)和每个会话一页。
解析与渲染逻辑统一在 backend/chat_log.py,GUI 的「聊天记录」tab 也复用它。
"""
import sys
from backend import chat_log


def main():
    log_dir = sys.argv[1] if len(sys.argv) > 1 else None
    index = chat_log.generate_html(log_dir)
    print(f"✅ 已生成会话记录页\n📂 打开: {index}")


if __name__ == "__main__":
    main()
