"""
DeepSeekClient: DeepSeek API 流式/同步调用封装
支持用户自定义 API Key（通过请求头或参数覆盖 config.py 中的 key）
"""
import json, requests
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, CHARACTER_NAME

DEEPSEEK_BASE = "https://api.deepseek.com/v1/chat/completions"


class DeepSeekClient:
    def __init__(self, base_url=DEEPSEEK_BASE, model=DEEPSEEK_MODEL, api_key=None):
        self.base_url = base_url
        self.model = model
        self._default_api_key = api_key or DEEPSEEK_API_KEY
        self.api_key = self._default_api_key

    def _headers(self, api_key_override=None):
        key = api_key_override or self.api_key or self._default_api_key
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _build_messages(self, system_prompt, messages):
        msgs = [{"role": "system", "content": system_prompt}] if system_prompt else []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "assistant"
            msgs.append({"role": role, "content": msg["content"]})
        return msgs

    def chat_stream(self, messages, system_prompt="", api_key_override=None):
        msgs = self._build_messages(system_prompt, messages)
        payload = {"model": self.model, "messages": msgs, "stream": True, "temperature": 0.7, "top_p": 0.9}
        key = api_key_override or self.api_key or self._default_api_key
        if not key:
            yield "请在 config.py 中设置 DEEPSEEK_API_KEY"
            return
        hdrs = self._headers(api_key_override)
        try:
            resp = requests.post(self.base_url, json=payload, headers=hdrs, stream=True, timeout=120)
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data: "):
                    ds = line[6:]
                    if ds.strip() == "[DONE]":
                        break
                    try:
                        content = json.loads(ds).get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue
        except requests.exceptions.ConnectionError:
            yield "无法连接到 DeepSeek API，请检查网络。"
        except requests.exceptions.HTTPError as e:
            try:
                msg = resp.json().get("error", {}).get("message", str(e))
            except Exception:
                msg = str(e)
            yield f"API 错误: {msg}"
        except Exception as e:
            yield f"错误: {str(e)}"

    def chat_sync(self, messages, system_prompt="", temperature=0.8, api_key_override=None):
        msgs = self._build_messages(system_prompt, messages)
        payload = {"model": self.model, "messages": msgs, "stream": False, "temperature": temperature, "top_p": 0.9}
        key = api_key_override or self.api_key or self._default_api_key
        if not key:
            return "[请在 config.py 中设置 DEEPSEEK_API_KEY]"
        hdrs = self._headers(api_key_override)
        try:
            resp = requests.post(self.base_url, json=payload, headers=hdrs, timeout=120)
            resp.raise_for_status()
            return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        except Exception as e:
            return f"[生成失败: {e}]"