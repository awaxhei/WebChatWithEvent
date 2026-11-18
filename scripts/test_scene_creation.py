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
    def chat_sync(self, *a, **kw):
        return "站台上空无一人，铁轨延伸进暮色里。"


def main():
    app.config["TESTING"] = True
    c = app.test_client()
    try:
        c.set_cookie("auth_token", generate_token(1))
    except TypeError:
        c.set_cookie("localhost", "auth_token", generate_token(1))

    conv = db.create_conversation(1)
    locations.ensure_defaults(db, conv)
    check("默认地点已初始化", len(locations.list_names(db, conv)) == 11,
          str(len(locations.list_names(db, conv))))

    # 1) 玩家主动前往不存在的地点
    r = c.post(f"/api/move/{conv}", json={"who": "player", "location": "旧车站月台"})
    data = r.get_json() or {}
    check("玩家可创建新场景", "旧车站月台" in locations.list_names(db, conv) and data.get("created"))
    check("玩家已移动到新场景", data.get("player_location") == "旧车站月台")

    # 2) 尤夏自主前往不存在的地点
    autonomy.AUTONOMY_ENABLED = True
    autonomy.AUTONOMY_CHANCE = 1.0
    autonomy.AUTONOMY_COOLDOWN_TURNS = 1
    autonomy.AUTONOMY_ALLOW_NEW_LOCATION = True
    acted = autonomy.maybe_act(db, conv, StubLLM("学校天台"), 18 * 60, "情境", msg_count=1)
    check("尤夏可自主创建新场景", acted and acted.get("created"), str(acted))
    check("新场景已登记", "学校天台" in locations.list_names(db, conv))
    check("尤夏已移动到新场景", db.get_current_location(conv) == "学校天台")

    # 3) 新场景自动生成环境描写
    bucket, text, generated = scenes.ensure_description(db, conv, "学校天台", 18 * 60, StubScene())
    check("新场景自动生成描写", generated and text.startswith("站台上空无一人"), text[:20])
    cached = db.get_location_variants(conv, "学校天台")
    check("描写按时段缓存", "傍晚" in cached)

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
