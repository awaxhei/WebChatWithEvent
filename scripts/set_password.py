"""改网页端的登录密码

    python scripts/set_password.py awaxhei

会提示输入两遍新密码（不回显），把 SHA-256 写进项目根的 accounts.json。
accounts.json 已在 .gitignore 里，不会被提交；config.py 启动时会优先读它。
写完重启一下服务就生效（数据库里的旧密码会被自动覆盖）。
"""
import getpass
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACCOUNTS = os.path.join(ROOT, "accounts.json")


def load():
    if not os.path.exists(ACCOUNTS):
        return {}
    try:
        with open(ACCOUNTS, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main():
    username = (sys.argv[1] if len(sys.argv) > 1 else "").strip() or input("用户名: ").strip()
    if not username:
        print("用户名不能为空")
        return 1
    pw1 = getpass.getpass("新密码（输入时不显示）: ")
    if len(pw1) < 6:
        print("太短了，至少 6 位")
        return 1
    pw2 = getpass.getpass("再输一遍: ")
    if pw1 != pw2:
        print("两次输入不一致")
        return 1

    data = load()
    existed = username in data
    data[username] = hashlib.sha256(pw1.encode()).hexdigest()
    with open(ACCOUNTS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"\n{'已更新' if existed else '已添加'}账号「{username}」，写入 {ACCOUNTS}")
    print("现在重启服务（关掉再 python app.py），用新密码登录即可。")
    print("提示：如果这个用户名也在 config.py 的 PRESET_ACCOUNTS 里，accounts.json 会优先生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
