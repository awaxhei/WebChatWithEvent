"""粗略的 token 估价

只用于决定「对话窗口放多少条」这类预算，不要求精确：真正算钱的还是提供方。
参考 dsh-token-meter 的固定密度估价，但它按「4 字符 = 1 token」算——那是给英文
代码算的，中文会被严重低估（一个字往往要 0.6~1 token），窗口就会撑到爆。

所以这里分开算：ASCII 按 4 字符 1 token，CJK 按 1 字 0.8 token，
每种消息再加一点结构开销。
"""
import unicodedata

# 每条消息的角色与 JSON 外壳开销
MESSAGE_OVERHEAD = 4
# 四舍五入用的倍数（0.8 token/字 → 5 字 4 token），整数运算免得浮点误差
CJK_TOKEN_NUM, CJK_TOKEN_DEN = 4, 5
ASCII_PER_TOKEN = 4


def _is_cjk(ch):
    """中日韩字符与全角标点：按字计价"""
    code = ord(ch)
    if 0x3000 <= code <= 0x303F:      # CJK 标点（。、「」等）
        return True
    if 0xFF00 <= code <= 0xFFEF:      # 全角字符
        return True
    return unicodedata.east_asian_width(ch) in ("W", "F")


def estimate_text(text):
    """一段文本大概多少 token（不含消息开销）"""
    if not text:
        return 0
    cjk = 0
    ascii_chars = 0
    for ch in str(text):
        if ord(ch) < 128:
            ascii_chars += 1
        elif _is_cjk(ch):
            cjk += 1
        else:
            ascii_chars += 2          # 其他文字/符号按 2 个 ASCII 宽度估
    tokens = (cjk * CJK_TOKEN_NUM + CJK_TOKEN_DEN - 1) // CJK_TOKEN_DEN
    tokens += (ascii_chars + ASCII_PER_TOKEN - 1) // ASCII_PER_TOKEN
    return tokens


def estimate_message(row):
    """一条消息大概多少 token（含角色/结构开销）

    row 可以是库里的消息 dict，也可以直接是文本。
    """
    content = row.get("content") if isinstance(row, dict) else row
    return estimate_text(content) + MESSAGE_OVERHEAD
