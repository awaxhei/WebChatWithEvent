"""
实体登记 —— 模型提到的每样东西、每个人都要真的存在

以前「快递箱」只是一句台词：下一轮问里面是什么，没人答得上来；「有人进来了」
也只是一句旁白，门口根本没站着人。现在模型每提到一样新东西、一个新人物，
就必须在同一轮里用结构化字段声明清楚，这里把它们写进库：

    {"物件": [{"名字": "快递箱", "状态": "鞋柜边，写着青梅的名字，还没拆"}]}
    {"人物": [{"名字": "妈妈", "身份": "尤夏的母亲", "电话": true}]}
    {"说话人": "妈妈", "消息": "到楼下了，你别下来"}

物件进 props 表（按会话 + 地点），人物进 npcs 表（在场或电话联系人）。
后续提示词里会带上这份清单，她再提到时就是同一个箱子、同一个人。
"""
import core.props as props
from services import npc

MAX_ITEMS = 6
MAX_NAME = 16
MAX_STATE = 40


def _name(item):
    if not isinstance(item, dict):
        return ""
    return str(item.get("名字") or item.get("name") or "").strip()[:MAX_NAME]


def _state(item):
    if not isinstance(item, dict):
        return ""
    return str(item.get("状态") or item.get("state") or "").strip()[:MAX_STATE]


def remember_props(db, conv_id, location, items):
    """模型声明的物件：写进这个地点的实物清单"""
    if not location or not items:
        return []
    clean = []
    for item in items[:MAX_ITEMS]:
        name = _name(item)
        if not name:
            continue
        clean.append({"名字": name, "状态": _state(item)})
    if not clean:
        return []
    try:
        return props.remember(db, conv_id, location, clean)
    except Exception as e:
        print(f"[实体] 物件登记失败: {e}")
        return []


def remember_people(db, conv_id, location, items):
    """模型声明的人物：在场的人当 NPC 建/更新，电话联系人另记"""
    if not items:
        return []
    saved = []
    for item in items[:MAX_ITEMS]:
        name = _name(item)
        if not name:
            continue
        role = str(item.get("身份") or item.get("role") or "").strip()[:20]
        phone = bool(item.get("电话") or item.get("phone"))
        try:
            person = npc.ensure_person(
                db, conv_id, name, role=role,
                location="" if phone else location,
                phone=phone,
                persona=str(item.get("设定") or "").strip()[:120],
            )
            if person:
                saved.append(name)
        except Exception as e:
            print(f"[实体] 人物登记失败: {e}")
    return saved


def apply_reply(db, conv_id, obj, location, co_located=True):
    """处理一条她输出里的实体声明（物件 / 人物）

    返回 {"props": [...], "people": [...], "speaker": "..."}；speaker 是
    这条消息的说话人（NPC 用手机回话时才有）。
    """
    out = {"props": [], "people": [], "speaker": ""}
    if not isinstance(obj, dict):
        return out
    # 她说的东西在她那边；同处一地时也是同一个房间，按尤夏所在地记。
    # `物件` 是这轮新出现的东西；`道具` 是动作把它们改了状态（挪走、打开、弄湿）。
    prop_loc = location or ""
    declared = list(obj.get("物件") or []) if isinstance(obj.get("物件"), list) else []
    touched = list(obj.get("道具") or []) if isinstance(obj.get("道具"), list) else []
    out["props"] = remember_props(db, conv_id, prop_loc, declared + touched)
    out["people"] = remember_people(db, conv_id, prop_loc, obj.get("人物"))
    speaker = str(obj.get("说话人") or "").strip()[:MAX_NAME]
    if speaker:
        out["speaker"] = speaker
    return out


def ensure_speaker(db, conv_id, name, location="", phone=True):
    """一条消息的说话人必须是登记过的人；没登记过就当场建一个（电话联系人）"""
    name = (name or "").strip()[:MAX_NAME]
    if not name:
        return None
    try:
        person = npc.ensure_person(db, conv_id, name, location="" if phone else location,
                                   phone=phone)
        return person
    except Exception as e:
        print(f"[实体] 说话人登记失败: {e}")
        return None
