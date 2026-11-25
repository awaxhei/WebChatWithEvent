"""版本号与更新日志的一致性检查。

背景：手机上装完 APK 之后，以前没有任何地方能看出"到底装上了没有"，
连版本号都只能靠猜。现在版本号有三处来源，必须对得上：

1. android/app/build.gradle.kts 的 versionName / versionCode（唯一权威）；
2. CHANGELOG.md 里最新一节，打包时装进 APK，供「关于与更新日志」显示；
3. scripts/build_apk.ps1 生成的 static/download/apk_info.json 与 APK 内的
   assets/build_info.json，供网页下载入口和 App 自己显示。

这个测试守住几件容易悄悄坏掉的事：
- 改了版本号却没写更新日志；
- 更新日志的标题格式变了，Kotlin 那边解析不出来（会显示空白）；
- 打了包却没带版本信息，或者发布的 APK 和当前版本号对不上。
"""

import json
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRADLE = os.path.join(ROOT, "android", "app", "build.gradle.kts")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
BUILD_SCRIPT = os.path.join(ROOT, "scripts", "build_apk.ps1")
APP_PY = os.path.join(ROOT, "app.py")
JAVA = os.path.join(ROOT, "android", "app", "src", "main", "java", "com", "yuxia", "chat")
DOWNLOAD = os.path.join(ROOT, "static", "download")
APK_INFO = os.path.join(DOWNLOAD, "apk_info.json")
APK = os.path.join(DOWNLOAD, "yuxia.apk")

HEADING = re.compile(r"^##\s+(\d+\.\d+)（(\d{4}-\d{2}-\d{2})）$")

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


def parse_changelog(text):
    """和 AppInfo.kt 的 parseChangelog 保持同一套规则：`## 7.3（日期）` + `- 条目`。"""
    out = []
    version, title, lines = None, "", []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            if version:
                out.append({"version": version, "title": title, "lines": lines})
            title = line[len("## "):].strip()
            version = title.split("（")[0].split(" ")[0].strip()
            lines = []
        elif line.startswith("- ") and version:
            lines.append(line[2:].strip())
    if version:
        out.append({"version": version, "title": title, "lines": lines})
    return out


def main():
    print("== 版本号与更新日志 ==")

    gradle = read(GRADLE)
    m_name = re.search(r'versionName\s*=\s*"([^"]+)"', gradle)
    m_code = re.search(r"versionCode\s*=\s*(\d+)", gradle)
    check(m_name is not None, "gradle 里有 versionName")
    check(m_code is not None, "gradle 里有 versionCode")
    if not (m_name and m_code):
        print("\n无法继续：版本号解析失败")
        return 1
    version, code = m_name.group(1), int(m_code.group(1))

    check(re.fullmatch(r"\d+\.\d+", version) is not None,
          f"versionName 形如 7.3（当前 {version}）")
    check(code > 52, f"versionCode 已递增（当前 {code}）")
    check("buildConfig = true" in gradle, "buildConfig 已打开（版本号要编译进 BuildConfig）")

    # ---- 更新日志
    text = read(CHANGELOG)
    sections = parse_changelog(text)
    headings = [line.strip() for line in text.splitlines() if line.strip().startswith("## ")]
    check(len(headings) == len([h for h in headings if HEADING.search(h)]),
          "更新日志标题格式统一为「## 7.3（2026-11-25）」",
          ", ".join(h for h in headings if not HEADING.search(h))[:120])
    check(bool(sections), "更新日志能被解析出至少一节")
    if sections:
        check(sections[0]["version"] == version,
              "最新一节更新日志与当前版本号一致", f"日志 {sections[0]['version']} / 版本 {version}")
        empty = [s["version"] for s in sections if not s["lines"]]
        check(not empty, "每一节更新日志都有条目", ", ".join(empty))
        versions = [s["version"] for s in sections]
        check(len(versions) == len(set(versions)), "版本号没有重复", str(versions))

    # ---- App 侧读取路径
    info_kt = read(os.path.join(JAVA, "AppInfo.kt"))
    for token, label in (
        ("build_info.json", "读取 assets/build_info.json"),
        ("changelog.md", "读取 assets/changelog.md"),
        ("lastUpdateTime", "读取系统记录的安装时间"),
        ("BuildConfig.VERSION_NAME", "回退到编译期版本号"),
    ):
        check(token in info_kt, label)

    prefs_kt = read(os.path.join(JAVA, "Prefs.kt"))
    check("lastVersion" in prefs_kt, "记住上次运行的版本（用于覆盖安装后提示更新）")

    screens_kt = read(os.path.join(JAVA, "Screens.kt"))
    check("AboutDialog" in screens_kt, "有「关于与更新日志」界面")
    check("版本 " in screens_kt, "启动页显示版本号")

    # ---- 后端与打包脚本
    app_py = read(APP_PY)
    check("@app.route(\"/api/apk_info\")" in app_py, "后端提供 /api/apk_info")
    check("app_version" in app_py, "/api/ping 带出版本号")
    check("yuxia-%s.apk" in app_py, "下载文件名带版本号")

    script = read(BUILD_SCRIPT)
    check("versionName" in script and "versionCode" in script, "打包脚本从 gradle 读版本号")
    check("build_info.json" in script, "打包脚本写入 assets/build_info.json")
    check("CHANGELOG.md" in script, "打包脚本带上更新日志")
    check("packaged version mismatch" in script, "打包脚本校验包内版本")
    check("apk_info.json" in script, "打包脚本发布 apk_info.json")

    # ---- 已发布的 APK（没有就打，跳过）
    if os.path.exists(APK_INFO) and os.path.exists(APK):
        meta = json.loads(read(APK_INFO))
        check(meta.get("version") == version,
              "已发布的 APK 与当前版本号一致", f"apk_info {meta.get('version')} / 版本 {version}")
        check(int(meta.get("version_code", 0)) == code,
              "已发布的 APK 与当前 versionCode 一致",
              f"apk_info {meta.get('version_code')} / 版本 {code}")
        named = os.path.join(DOWNLOAD, "yuxia-%s.apk" % meta.get("version"))
        check(os.path.exists(named), "下载目录里有带版本号的文件名")
        real_mb = round(os.path.getsize(APK) / (1024 * 1024), 2)
        check(abs(real_mb - float(meta.get("size_mb", 0))) < 0.05,
              "apk_info.json 的大小与文件一致", f"{real_mb} MB / {meta.get('size_mb')} MB")
        try:
            with zipfile.ZipFile(APK) as z:
                packed = json.loads(z.read("assets/build_info.json").decode("utf-8"))
                has_changelog = any(n.endswith("assets/changelog.md") for n in z.namelist())
            check(packed.get("version") == version, "APK 内的版本与 gradle 一致",
                  f"{packed.get('version')} / {version}")
            check(int(packed.get("version_code", 0)) == code, "APK 内的 versionCode 与 gradle 一致")
            check(has_changelog, "APK 内带上了更新日志")
        except (zipfile.BadZipFile, KeyError) as e:
            check(False, "APK 内能读到版本信息", str(e))
    else:
        print("  --   尚未打包 APK，跳过发布物检查")

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
