import re

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def _clean(msg):
    return _ANSI_RE.sub('', str(msg))
