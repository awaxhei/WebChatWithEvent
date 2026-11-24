"""界面上「系统自己说的话」不要口语化

这里说的不是尤夏的台词，而是 App 自己的文案与提示：
启动中、填 API Key、新建会话、删除确认、读取失败、移动失败……

规则：
- 书面语，不用「啦/哦/嘛/呀/呗/嘿/诶/喔/咯」这类语气词
- 提示不撒娇、不卖萌，把「发生了什么 + 该怎么办」说清楚
- 聊天正文里的旁白另有 core/system_text.py，这里管的是界面文案

这个测试直接扫 Android 的 Kotlin 源码，因为那些文案是写在 Compose 里的。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAVA = os.path.join(ROOT, "android", "app", "src", "main", "java", "com", "yuxia", "chat")

# 语气词：出现在界面文案里就是没写正式
CASUAL = "啦哦嘛呀呗嘿诶喔咯"

# 这些是角色台词、用户输入、以及她的口癖，不算系统文案
ALLOW_PATTERNS = (
    "尤夏", "唔", "嘛",       # 她的台词（如「你能喂尤夏嘛=W=」）
)

FAILED = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


def string_literals(path):
    """逐行取双引号字符串字面量，跳过注释行"""
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
                continue
            for lit in re.findall(r'"[^"\n]*"', line):
                out.append((i, lit))
    return out


def main():
    if not os.path.isdir(JAVA):
        print("找不到 Android 源码目录")
        return 1

    offenders = []
    for name in sorted(os.listdir(JAVA)):
        if not name.endswith(".kt"):
            continue
        for line_no, lit in string_literals(os.path.join(JAVA, name)):
            body = lit.strip('"')
            if not body:
                continue
            hit = [ch for ch in body if ch in CASUAL]
            if not hit:
                continue
            # 角色台词里本来就会带口癖，跳过
            if any(p in body for p in ALLOW_PATTERNS):
                continue
            offenders.append(f"{name}:{line_no}: {body}")

    print(f"检查了 {len(os.listdir(JAVA))} 个 Kotlin 文件")
    check("界面文案里没有口语语气词", not offenders, " / ".join(offenders[:4]))

    # 几条关键提示语抽查：正式、说清怎么办
    screens = open(os.path.join(JAVA, "Screens.kt"), encoding="utf-8").read()
    check("启动提示说清首次较慢的原因",
          "首次启动需要解压运行环境" in screens, screens[:200])
    check("API Key 页说明数据只在本机",
          "仅保存在本机应用数据中" in screens)
    check("空会话提示给出了下一步",
          "点击右下角新建会话" in screens)
    check("删除确认写成了正式警示",
          "且不可恢复" in screens)

    vm = open(os.path.join(JAVA, "ChatViewModel.kt"), encoding="utf-8").read()
    check("错误提示不再口语", "没响应" not in vm and "不能为空" in vm and "API Key" in vm)
    check("移动/删除的提示是书面说法",
          "你前往" in vm and "已删除" in vm and "会话" in vm)

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
