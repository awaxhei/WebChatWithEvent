"""
AI B: 环境叙述相关辅助
"""
import re

_LOCATION_RE = re.compile(r'【地点】\s*(.+)')


def extract_location(atmo_text):
    """从环境叙述文本中提取地点"""
    m = _LOCATION_RE.search(atmo_text)
    if m:
        return m.group(1).strip()
    return None


def strip_location_tag(atmo_text):
    """移除环境叙述中的地点标记，仅保留描写文字"""
    return _LOCATION_RE.sub("", atmo_text).strip()