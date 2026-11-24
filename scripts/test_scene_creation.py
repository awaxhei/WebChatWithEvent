"""
场景创建能力探针（不耗 API）
校验：玩家创建、尤夏自主创建、新场景自动生成描写、数量上限保护
"""
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database    # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "scene_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app                          # noqa: E402
from config import DEFAULT_LOCATIONS          # noqa: E402
from core.auth import generate_token          # noqa: E402
import core.locations as locations            # noqa: E402
import core.scenes as scenes                  # noqa: E402
import services.autonomy as autonomy          # noqa: E402

db = database.ChatDB()
FAILED = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


class StubLLM:
    def __init__(self, target):
        self.target = target

    def chat_sync(self, *a, **kw):
        return '{"action": "move", "text": "", "location": "%s"}' % self.target

    def chat_stream(self, *a, **kw):
        yield "{}"


class StubScene:
    """场景生成：JSON 形态（带道具）+ 记录它收到的已有道具"""

    def __init__(self, payload=None, plain=None):
        self.payload = payload
        self.plain = plain
        self.user_msg = ""

    def chat_sync(self, messages, **kw):
        self.user_msg = messages[0]["content"] if messages else ""
        if self.plain:
            return self.plain
        if self.payload:
            return self.payload
        return ('{"环境": "站台上空无一人，铁轨延伸进暮色里。",'
                ' "道具": [{"名字": "长椅", "状态": "积着一层灰"},'
                ' {"名字": "站牌", "状态": "字迹被雨泡花了"}]}')


def main():
    app.config["TESTING"] = True
    c = app.test_client()
    try:
        c.set_cookie("auth_token", generate_token(1))
    except TypeError:
        c.set_cookie("localhost", "auth_token", generate_token(1))

    conv = db.create_conversation(1)
    locations.ensure_defaults(db, conv)
    check("默认地点已初始化", len(locations.list_names(db, conv)) == len(DEFAULT_LOCATIONS),
          f"{len(locations.list_names(db, conv))} vs {len(DEFAULT_LOCATIONS)}")

    # 1) 玩家主动前往不存在的地点
    r = c.post(f"/api/move/{conv}", json={"who": "player", "location": "旧车站月台"})
    data = r.get_json() or {}
    check("玩家可创建新场景", "旧车站月台" in locations.list_names(db, conv) and data.get("created"))
    check("玩家已移动到新场景", data.get("player_location") == "旧车站月台")

    # 2) 尤夏自主前往不存在的地点
    autonomy.AUTONOMY_ENABLED = True
    autonomy.AUTONOMY_ALLOW_NEW_LOCATION = True
    acted = autonomy.maybe_act(db, conv, StubLLM("学校天台"), 18 * 60, "情境", msg_count=1)
    check("尤夏可自主创建新场景", acted and acted.get("created"), str(acted))
    check("新场景已登记", "学校天台" in locations.list_names(db, conv))
    check("尤夏已移动到新场景", db.get_current_location(conv) == "学校天台")

    # 3) 新场景自动生成环境描写（同时登记道具）
    stub_scene = StubScene()
    bucket, text, generated = scenes.ensure_description(db, conv, "学校天台", 18 * 60, stub_scene)
    check("新场景自动生成描写", generated and text.startswith("站台上空无一人"), text[:20])
    cached = db.get_location_variants(conv, "学校天台")
    check("描写按时段缓存", "傍晚" in cached)
    scene_props = db.get_props(conv, "学校天台")
    check("环境描写里的道具真的落库",
          len(scene_props) == 2 and scene_props[0]["name"] == "长椅", str(scene_props))
    check("道具带着状态", scene_props[0]["state"] == "积着一层灰", str(scene_props))

    # 道具可以被改动，改完提示词里就是新的
    db.set_prop(conv, "学校天台", "长椅", "被擦干净了")
    import core.props as props_mod
    block = props_mod.block(db, conv, "学校天台")
    check("提示词里带出此地的实物", "长椅（被擦干净了）" in block, block)
    check("道具块说明可以拿动", "可以拿、可以动" in block, block)

    # 重新生成描写时，已有道具要喂回去，不许凭空换一批
    again_stub = StubScene()
    scenes.ensure_description(db, conv, "学校天台", 19 * 60, again_stub, force=True)
    check("重新生成时带上已有道具",
          "长椅" in again_stub.user_msg and "被擦干净了" in again_stub.user_msg,
          again_stub.user_msg[:200])
    check("并说明不要凭空换一批", "不要凭空换一批" in again_stub.user_msg,
          again_stub.user_msg[:200])

    # 老缓存/模型不听话只给纯文本时，描写照用，不报错
    plain_stub = StubScene(plain="风从天台边上灌进来，晾衣绳晃个不停。")
    _b, plain_text, _g = scenes.ensure_description(db, conv, "旧车站月台", 18 * 60, plain_stub)
    check("纯文本描写也能用", "晾衣绳" in plain_text, plain_text[:20])

    # 4) 开关关闭时不允许自主新建
    autonomy.AUTONOMY_ALLOW_NEW_LOCATION = False
    before = len(locations.list_names(db, conv))
    acted2 = autonomy.maybe_act(db, conv, StubLLM("秘密基地"), 18 * 60, "情境", msg_count=1)
    check("关闭开关后不新建", acted2 is None and len(locations.list_names(db, conv)) == before,
          str(acted2))
    autonomy.AUTONOMY_ALLOW_NEW_LOCATION = True

    # 5) 数量上限保护
    names = locations.list_names(db, conv)
    i = 0
    while len(locations.list_names(db, conv)) < locations.MAX_LOCATIONS_PER_CONV:
        db.add_location(conv, f"占位地点{i}")
        i += 1
    check("已达上限", len(locations.list_names(db, conv)) == locations.MAX_LOCATIONS_PER_CONV)
    acted3 = autonomy.maybe_act(db, conv, StubLLM("又一个新地方"), 18 * 60, "情境", msg_count=1)
    check("达上限后拒绝自主新建", acted3 is None, str(acted3))

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
