"""
指代修正：青梅是女生，一律用「她」。

模型在没有明确约束时，经常把"另一个人"写成「他」——
一旦写进情境叙述或旁白，就会被后续提示词反复回灌，越错越固化。
这里做一次确定性的兜底替换。

只替换"当人称代词用"的「他」：
- 前面是 其/吉/利/排 的（其他、吉他、利他、排他）不动
- 后面是 们/乡/日/国/处/人/杀 的（他们、他乡、他日、他国、他处、他人、他杀）不动
"""
import re

_KEEP_PREFIX = "吉其利排"
_KEEP_SUFFIX = "们乡日国处人杀"
_MALE_PRONOUN = re.compile(rf"(?<![{_KEEP_PREFIX}])他(?![{_KEEP_SUFFIX}])")


def fix(text):
    """把指代青梅的「他」改成「她」"""
    if not text:
        return text
    return _MALE_PRONOUN.sub("她", text)
