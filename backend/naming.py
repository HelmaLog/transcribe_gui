"""统一的输出文件命名规则。

全工具（转写 / 烧录 / 压缩）的产物文件名统一为：

    时间戳_类型_短名.扩展名      例如  0530-1430_双语_Putin WARNS AI is.srt

设计目的：
- 时间戳在最前 → 按「名称」排序即等于按时间先后，Windows 里设一次排序即可；
- 类型紧随其后 → 一眼区分 英文/双语/中文/烧录/压缩，且不会被超长标题挤掉；
- 短名截断到固定长度 → 文件名不再过长，关键标记始终可见；
- 时间戳到分钟 + 冲突自动追加 _2/_3 → 多次生成不会互相覆盖。
"""

import os
import re
from datetime import datetime

# 标题保留的最大字符数（超出截断）。CJK 也按字符计。
MAX_TITLE = 24

# 本工具历史上加过的前缀/后缀标记，重新命名时先剥掉，避免层层叠加。
_OLD_TS_PREFIX = re.compile(r"^\d{4}-\d{4}_")                       # 新时间戳前缀 MMDD-HHMM
_OLD_TYPE_PREFIX = re.compile(r"^(部分)?(英文|双语|中文)_")          # 旧类型前缀
_OLD_EN_SUFFIX = re.compile(r"_英文$")                              # 旧英文后缀
_OLD_DATE_SUFFIX = re.compile(r"_\d{4}-\d{2}-\d{2}$")              # 旧日期后缀 YYYY-MM-DD
_OLD_BURN_SUFFIX = re.compile(r"_burned(_fast|_hq|_custom)?$")      # 旧烧录后缀
_OLD_CMP_SUFFIX = re.compile(r"_x_(fast|balanced|quality|custom)$") # 旧压缩后缀
_OLD_CMP_SUFFIX2 = re.compile(r"_compressed$")


def short_title(name: str) -> str:
    """从任意文件名/路径提取干净、定长的短标题。

    步骤：去目录与扩展名 → 剥离本工具历史标记 → 去特殊符号、压缩空白 → 截断。
    """
    stem = os.path.splitext(os.path.basename(name))[0]

    # 反复剥离已知的旧标记（可能同时存在前缀与后缀）
    for pat in (_OLD_TS_PREFIX, _OLD_TYPE_PREFIX):
        stem = pat.sub("", stem)
    for pat in (_OLD_EN_SUFFIX, _OLD_DATE_SUFFIX, _OLD_BURN_SUFFIX,
                _OLD_CMP_SUFFIX, _OLD_CMP_SUFFIX2):
        stem = pat.sub("", stem)

    # 特殊符号替换为空格，压缩连续空白
    cleaned = re.sub(r"[^\w\s-]", " ", stem, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    if len(cleaned) > MAX_TITLE:
        cleaned = cleaned[:MAX_TITLE].rstrip()
    return cleaned or "output"


def make_name(kind: str, source_name: str, ext: str, ts: datetime = None) -> str:
    """构造文件名（不含目录）：时间戳_类型_短名.扩展名。

    kind        类型标记，如 "英文" / "双语" / "烧录HQ" / "压缩"。
    source_name 源文件名或路径，用于提取短标题。
    ext         扩展名，带不带前导点都可。
    ts          时间戳，默认取当前时间；同一次任务应传同一 ts 以便分组。
    """
    ts = ts or datetime.now()
    stamp = ts.strftime("%m%d-%H%M")
    title = short_title(source_name)
    ext = ext if ext.startswith(".") else "." + ext
    return f"{stamp}_{kind}_{title}{ext}"


def unique_path(directory: str, filename: str) -> str:
    """返回 directory 下不冲突的完整路径；若同名已存在则追加 _2/_3…"""
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    i = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base}_{i}{ext}")
        i += 1
    return candidate


def make_path(directory: str, kind: str, source_name: str, ext: str,
              ts: datetime = None) -> str:
    """make_name + unique_path 的便捷组合：直接返回可写入的完整路径。"""
    return unique_path(directory, make_name(kind, source_name, ext, ts))
