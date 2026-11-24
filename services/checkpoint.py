"""故事检查点：把掉出对话窗口的旧历史压缩成一份档案

参考 DeepSeek Harness 的 compaction（dsh-compaction-basic）：
- 不按条数、按 token 预算保留最近一段（窗口在 routes 里选）
- 更早的那一段压成一条摘要，作为「既定背景」放在历史最前面，而不是丢掉
- 摘要是**合并式**的：新掉出去的一段并进旧检查点，不重写整份
- 摘要失败不影响这一轮：检查点不动，窗口照旧，宁可多花钱也不丢内容

和 dsh 的差别：这里没有工具调用，所以不需要工具配对边界、日志标记、
崩溃恢复那套；只有「窗口 + 检查点」这一层。
"""
import threading

from config import (
    CHECKPOINT_ENABLED, CHECKPOINT_PROMPT, CHECKPOINT_MAX_TOKENS,
    CHARACTER_NAME,
)

# 检查点是以 user 消息注入的，这句话负责让她别去回应它、也别复述它
PREAMBLE = ("这是自动生成的故事检查点，压缩了更早的对话。把它当作既定背景，"
            "不要复述、不要提到它，直接从后面的消息接着演。")

_OPEN = "<compacted-summary>"
_CLOSE = "</compacted-summary>"

# 同一会话同一时刻只跑一次折叠
_inflight = {}
_inflight_lock = threading.Lock()


def wrap(text):
    """把检查点正文包成注入用的内容"""
    body = (text or "").strip()
    if not body:
        return ""
    return f"{PREAMBLE}\n\n{_OPEN}\n{body}\n{_CLOSE}"


def inject(messages, text):
    """把检查点放在历史最前面（一条 user 消息）

    放在 index 0 而不是塞进 system prompt，是照 dsh 的做法：
    摘要替换的是「更早的历史」，不是规则。
    """
    content = wrap(text)
    if not content:
        return messages
    return [{"role": "user", "content": content}] + list(messages)


def _label(row):
    """对话行 → 检查点用的说话人标签"""
    channel = row.get("channel") or "chat"
    if channel == "action":
        return "（动作）"
    if channel == "npc":
        return "（旁人）"
    who = "青梅" if row.get("role") == "user" else CHARACTER_NAME
    return f"{who}（{'手机' if channel == 'phone' else '当面'}）"


def _render(rows):
    lines = []
    for row in rows:
        content = row.get("content") or ""
        lines.append(f"- {_label(row)}：{content}")
    return "\n".join(lines)


def compose(db, conv_id, dropped, deepseek, api_key=None):
    """把 dropped 这些行并进检查点；成功返回新检查点，失败返回 None"""
    if not CHECKPOINT_ENABLED or not dropped or deepseek is None:
        return None
    old_text, old_upto = db.get_checkpoint(conv_id)
    # 只折叠比已覆盖位置更新的行，避免重复并入
    fresh = [r for r in dropped if int(r.get("id") or 0) > old_upto]
    if not fresh:
        return None

    facts = (
        f"上一版检查点：\n{old_text or '（还没有，这是第一版）'}\n\n"
        f"这次要合并进来的对话：\n{_render(fresh)}"
    )
    try:
        text = deepseek.chat_sync(
            [{"role": "user", "content": facts}],
            system_prompt=CHECKPOINT_PROMPT,
            temperature=0.3,
            api_key_override=api_key,
            max_tokens=CHECKPOINT_MAX_TOKENS,
            thinking=False,
        )
    except Exception as e:
        print(f"[检查点] 压缩失败: {e}")
        return None
    if not text or str(text).startswith("[生成失败"):
        return None
    text = str(text).strip()
    if len(text) < 10:
        return None
    # 只记到折叠过的最后一条：中途失败时检查点不会假装覆盖了没读过的内容
    db.set_checkpoint(conv_id, text, max(int(r.get("id") or 0) for r in fresh))
    return text


def fold_async(db, conv_id, dropped, deepseek, api_key=None):
    """后台折叠：掉出窗口的那段进检查点，这一轮先照常用窗口"""
    if not CHECKPOINT_ENABLED or not dropped or deepseek is None:
        return
    with _inflight_lock:
        if _inflight.get(conv_id):
            return
        _inflight[conv_id] = True

    def run():
        try:
            compose(db, conv_id, dropped, deepseek, api_key)
        except Exception as e:
            print(f"[检查点] 后台压缩失败: {e}")
        finally:
            with _inflight_lock:
                _inflight.pop(conv_id, None)

    threading.Thread(target=run, daemon=True).start()
