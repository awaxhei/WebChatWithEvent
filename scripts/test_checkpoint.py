"""故事检查点 + token 预算窗口 的探针（桩模型，零额度）

参考 DeepSeek Harness 的 compaction：窗口按 token 预算保留最近一段，
更早的一段并进检查点，检查点作为既定背景放在历史最前面。

覆盖：
1. token 估价：中文按字计价，别用「4 字符 1 token」那把中文算少
2. 窗口按预算取，且**不切断一轮**（一句话被拆成的多个气泡、动作行都跟着）
3. 已被检查点覆盖的消息不再进窗口
4. 检查点以 user 消息 + <compacted-summary> 注入最前面
5. 折叠是合并式的：旧检查点拼上掉出去的一段，只记到实际读过的位置
6. 摘要失败时检查点不动、窗口照旧（不丢内容）
"""
import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database     # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "ckpt_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app, db as app_db                 # noqa: E402
import core.tokens as tokens                      # noqa: E402
import routes.chat_routes as chat_mod             # noqa: E402
import services.checkpoint as checkpoint          # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


class SummaryLLM:
    """只回答摘要请求（走 chat_sync）"""

    def __init__(self, text="## 此刻的关系\n- 她在意青梅。", fail=False):
        self.text = text
        self.fail = fail
        self.calls = 0
        self.system_prompt = ""
        self.user_text = ""

    def chat_sync(self, messages, system_prompt="", **kw):
        self.calls += 1
        self.system_prompt = system_prompt
        self.user_text = messages[-1]["content"] if messages else ""
        if self.fail:
            return "[生成失败: 测试桩]"
        return self.text

    def chat_stream(self, messages, system_prompt="", **kw):
        yield '{"对白": "嗯。"}'


def main():
    app.config["TESTING"] = True
    user = app_db.get_user("awaxhei")["id"]

    # ── 1) token 估价
    zh = tokens.estimate_text("唔姆。今天有点冷，手先放进被子里。")
    en = tokens.estimate_text("hmm it is a bit cold today")
    check("中文按字计价（不按 4 字符 1 token）", zh > len("唔姆。今天有点冷，手先放进被子里。") * 0.6,
          str(zh))
    check("同一长度的中文比英文更贵", zh > en, f"{zh} vs {en}")
    check("空文本是 0", tokens.estimate_text("") == 0)

    # ── 2) 窗口按预算取，且不切断一轮
    conv = app_db.create_conversation(user)
    ids = []
    for i in range(6):
        ids.append(app_db.add_message(conv, "user", f"第{i}个问题，" + "很长的铺垫" * 6,
                                      channel="chat"))
        # 一句话被拆成三个气泡 —— 必须一起进出窗口
        for part in ("第一句。", "第二句。", "第三句。"):
            ids.append(app_db.add_message(
                conv, "assistant", json.dumps({"对白": part}, ensure_ascii=False),
                channel="chat"))
        ids.append(app_db.add_message(conv, "assistant", "把椅子往后挪了挪", channel="action"))

    full = chat_mod._context_rows(conv, budget=10 ** 6)
    check("预算够大时全都在窗口里", len(full[0]) == len(ids) and not full[1],
          f"{len(full[0])} / {len(ids)}")

    kept, dropped = chat_mod._context_rows(conv, budget=400)
    check("预算变小后确实挤掉了旧消息", bool(dropped), str(len(dropped)))
    check("窗口从玩家的一轮开始（不切半轮）",
          kept[0]["role"] == "user", str(kept[0]))
    check("这一轮的三句气泡都跟着", sum(1 for r in kept if r["role"] == "assistant"
                                        and "句。" in r["content"]) % 3 == 0,
          str([r["content"] for r in kept[:6]]))
    check("动作行不会孤零零留在开头",
          not (kept and kept[0]["channel"] == "action"), str(kept[0]))

    # 掉出去的那段正好接在保留段前面
    check("被挤掉的是更早的那些", dropped[-1]["id"] < kept[0]["id"],
          f"{dropped[-1]['id']} vs {kept[0]['id']}")

    # ── 3) 检查点以 user 消息注入最前面
    app_db.set_checkpoint(conv, "## 此刻的关系\n- 她答应过要一起去买菜。", dropped[-1]["id"])
    msgs = chat_mod._context_window(conv)
    check("检查点覆盖的消息不再进窗口",
          all("第0个问题" not in m["content"] for m in msgs), str(msgs)[:200])

    injected = checkpoint.inject(msgs, app_db.get_checkpoint(conv)[0])
    check("检查点在最前面", injected[0]["role"] == "user"
          and "<compacted-summary>" in injected[0]["content"], str(injected[0])[:120])
    check("前导写明当作既定背景、不要复述",
          "既定背景" in injected[0]["content"] and "不要复述" in injected[0]["content"])
    check("注入不会改动原消息", len(injected) == len(msgs) + 1)
    check("没有检查点时不注入", checkpoint.inject([], "") == [])

    # ── 4) 折叠是合并式的
    conv2 = app_db.create_conversation(user)
    for i in range(4):
        app_db.add_message(conv2, "user", f"闲聊{i}", channel="chat")
        app_db.add_message(conv2, "assistant", json.dumps({"对白": "嗯。"}, ensure_ascii=False),
                           channel="chat")
    kept2, dropped2 = chat_mod._context_rows(conv2, budget=30)
    check("第二组也有被挤出去的", bool(dropped2), str(len(dropped2)))

    llm = SummaryLLM()
    text = checkpoint.compose(app_db, conv2, dropped2, llm)
    check("摘要真的调了一次模型", llm.calls == 1, str(llm.calls))
    check("第一次折叠没有上一版检查点", "还没有" in llm.user_text, llm.user_text[:120])
    check("折叠结果落库",
          bool(text) and app_db.get_checkpoint(conv2)[0] == text, str(text[:60]))
    check("只记到折叠过的最后一条",
          app_db.get_checkpoint(conv2)[1] == dropped2[-1]["id"],
          f"{app_db.get_checkpoint(conv2)[1]} vs {dropped2[-1]['id']}")
    check("摘要提示词要求合并更新", "合并" in llm.system_prompt, llm.system_prompt[:80])

    # 第二次折叠要带上上一版
    app_db.add_message(conv2, "user", "又聊了两句", channel="chat")
    app_db.add_message(conv2, "assistant", json.dumps({"对白": "唔姆。"}, ensure_ascii=False),
                       channel="chat")
    kept3, dropped3 = chat_mod._context_rows(conv2, budget=30)
    llm2 = SummaryLLM("## 此刻的关系\n- 更新后的档案。")
    text2 = checkpoint.compose(app_db, conv2, dropped3, llm2)
    check("第二次折叠带上上一版检查点", "她答应过" not in llm2.user_text and text in llm2.user_text,
          llm2.user_text[:160])
    check("新检查点覆盖更远的位置",
          app_db.get_checkpoint(conv2)[1] == dropped3[-1]["id"], str(app_db.get_checkpoint(conv2)[1]))
    check("检查点确实被换掉了", app_db.get_checkpoint(conv2)[0] == text2)

    # 已经覆盖过的行不会重复折叠
    again = checkpoint.compose(app_db, conv2, dropped3, llm2)
    check("重复折叠同一段直接跳过", again is None and llm2.calls == 1, str(llm2.calls))

    # ── 5) 摘要失败：检查点不动，窗口照旧（宁可多花钱也不丢内容）
    conv3 = app_db.create_conversation(user)
    for i in range(4):
        app_db.add_message(conv3, "user", f"第{i}句", channel="chat")
        app_db.add_message(conv3, "assistant", json.dumps({"对白": "嗯。"}, ensure_ascii=False),
                           channel="chat")
    app_db.set_checkpoint(conv3, "旧档案", 0)
    kept4, dropped4 = chat_mod._context_rows(conv3, budget=20)
    bad = SummaryLLM(fail=True)
    got = checkpoint.compose(app_db, conv3, dropped4, bad)
    check("摘要失败返回空", got is None)
    check("失败时检查点保持原样", app_db.get_checkpoint(conv3) == ("旧档案", 0),
          str(app_db.get_checkpoint(conv3)))
    # 折叠没成功，窗口里一条都不该少（预算够就全在）
    all_conv3 = [m for m in app_db.get_messages(conv3)]
    check("失败时窗口还是完整的",
          len(chat_mod._context_window(conv3, budget=10 ** 6)) == len(all_conv3),
          f"{len(chat_mod._context_window(conv3, budget=10 ** 6))} / {len(all_conv3)}")

    # ── 6) 真走一次聊天接口：检查点确实进了发给模型的消息
    conv4 = app_db.create_conversation(user)
    old_ids = []
    for i in range(3):
        old_ids.append(app_db.add_message(conv4, "user", f"旧话{i}", channel="chat"))
        old_ids.append(app_db.add_message(
            conv4, "assistant", json.dumps({"对白": "嗯。"}, ensure_ascii=False), channel="chat"))
    app_db.add_message(conv4, "user", "现在这句", channel="chat")
    app_db.set_checkpoint(conv4, "## 此刻的关系\n- 她昨天说过怕冷。", old_ids[-1])

    class ChatStub(SummaryLLM):
        def __init__(self):
            super().__init__()
            self.messages = []

        def chat_stream(self, messages, system_prompt="", **kw):
            self.messages = list(messages)
            yield '{"对白": "唔姆。"}'

    chat_stub = ChatStub()
    real = chat_mod.deepseek
    chat_mod.deepseek = chat_stub
    try:
        c = app.test_client()
        from core.auth import generate_token
        c.set_cookie("auth_token", generate_token(user))
        r = c.post("/api/chat", json={"conversation_id": conv4, "message": "在吗"})
        r.data.decode("utf-8")            # 必须消费流，生成器才会跑起来
        sent = json.dumps(chat_stub.messages, ensure_ascii=False)
        check("聊天请求里带着检查点", "她昨天说过怕冷" in sent, sent[:200])
        check("检查点放在消息最前面", "<compacted-summary>" in str(chat_stub.messages[0]),
              str(chat_stub.messages[0])[:160])
        check("被覆盖的旧消息不再发给模型", "旧话0" not in sent, sent[:300])
    finally:
        chat_mod.deepseek = real

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
