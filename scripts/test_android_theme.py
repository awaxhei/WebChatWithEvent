"""固定浅色主题的回归测试。

背景：app 的底色、气泡、卡片是直接写死的淡粉白，只有 Material 组件会跟着系统深色走，
于是深色手机上会出现一块块全黑的地方。现在的策略是「这就是浅色 app」：不跟随系统深色。

这个测试守住三件事：
1. Compose 里不再出现 isSystemInDarkTheme / darkColorScheme；
2. YuxiaTheme 只喂一套 lightColorScheme；
3. 主题 XML 仍是 Light 父类，并且状态栏、导航栏都要求浅色图标。
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAVA = os.path.join(ROOT, "android", "app", "src", "main", "java", "com", "yuxia", "chat")
RES = os.path.join(ROOT, "android", "app", "src", "main", "res")

FAILED = []


def check(ok, label, detail=""):
    if ok:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))
        FAILED.append(label)


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def main():
    print("== 固定浅色主题 ==")

    # 1. 任何 Compose 文件都不许再按系统深色切换
    offenders = []
    for name in sorted(os.listdir(JAVA)):
        if not name.endswith(".kt"):
            continue
        src = read(os.path.join(JAVA, name))
        for token in ("isSystemInDarkTheme", "darkColorScheme"):
            if token in src:
                offenders.append(f"{name}:{token}")
    check(not offenders, "没有跟随系统深色的代码", ", ".join(offenders))

    # 2. YuxiaTheme 只使用 Light
    theme = read(os.path.join(JAVA, "Theme.kt"))
    body = theme.split("fun YuxiaTheme", 1)[1] if "fun YuxiaTheme" in theme else ""
    check("colorScheme = Light" in body, "YuxiaTheme 固定使用 Light")
    check("if (" not in body, "YuxiaTheme 里没有条件切换")

    # 3. 主题 XML：浅色父类 + 栏位图标与底色
    themes = read(os.path.join(RES, "values", "themes.xml"))
    check("Theme.Material.Light.NoActionBar" in themes, "主题父类仍是 Light")
    for attr, want in (
        ("android:windowLightStatusBar", "true"),
        ("android:windowLightNavigationBar", "true"),
        ("android:forceDarkAllowed", "false"),
    ):
        m = re.search(rf'<item name="{re.escape(attr)}">([^<]*)</item>', themes)
        check(m is not None and m.group(1).strip() == want,
              f"{attr} = {want}", m.group(1) if m else "缺少该属性")

    # 4. 不要有 values-night 覆盖把界面再拉回深色
    check(not os.path.isdir(os.path.join(RES, "values-night")), "没有 values-night 深色覆盖")

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
