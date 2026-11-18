"""
检测 DeepSeek API Key 是否有效，并列出该 Key 可用的模型。
用法：python scripts/check_api_key.py [sk-xxx]
不传参数时使用 config.py 中的 DEEPSEEK_API_KEY。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

BASE = "https://api.deepseek.com/v1"


def mask(k):
    if not k:
        return "(未配置)"
    return f"{k[:6]}...{k[-4:]} (长度 {len(k)})"


def main():
    key = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    model = ""
    if not key:
        from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
        key, model = DEEPSEEK_API_KEY, DEEPSEEK_MODEL

    print("Key:", mask(key))
    if model:
        print("config.py 中的模型:", model)
    if not key:
        print("结论: 未配置 Key")
        return 1

    ok = False
    try:
        r = requests.get(f"{BASE}/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        if r.status_code == 200:
            ok = True
            models = [m.get("id") for m in r.json().get("data", [])]
            print("鉴权: 有效")
            print("可用模型:", ", ".join(models) or "(接口未返回列表)")
            if model and models and model not in models:
                print(f"注意: config.py 里的模型 {model} 不在可用列表中")
        else:
            print(f"鉴权: 失败 (HTTP {r.status_code})")
            print("服务端信息:", r.text[:300])
    except Exception as e:
        print("请求失败:", e)
        return 1

    if ok:
        # 再做一次最小对话验证模型是否可用
        try:
            r = requests.post(
                f"{BASE}/chat/completions",
                json={"model": model or "deepseek-chat",
                      "messages": [{"role": "user", "content": "回复一个字：好"}],
                      "max_tokens": 8},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                timeout=60,
            )
            print("对话测试:", "通过" if r.status_code == 200 else f"失败 (HTTP {r.status_code}) {r.text[:200]}")
        except Exception as e:
            print("对话测试异常:", e)

    print("\n结论:", "Key 有效" if ok else "Key 无效，请更换")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
