"""
DeepSeekClient: DeepSeek API 流式/同步调用封装

- 支持用户自定义 API Key（请求头 / 参数覆盖 config.py 中的 key）
- 默认关闭思考模式：本项目所有调用都在等严格 JSON 或短旁白，
  思考模式（默认开启、effort=high）会白白烧 token 并拖慢响应，
  且思考模式下 temperature 不生效。
"""
import json

import requests

from config import (
    DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_THINKING, DEEPSEEK_REASONING_EFFORT,
)

DEEPSEEK_BASE = "https://api.deepseek.com/v1/chat/completions"


class DeepSeekClient:
    def __init__(self, base_url=DEEPSEEK_BASE, model=DEEPSEEK_MODEL, api_key=None,
                 thinking=None):
        self.base_url = base_url
        self.model = model
        self._default_api_key = api_key or DEEPSEEK_API_KEY
        self.api_key = self._default_api_key
        self.thinking = DEEPSEEK_THINKING if thinking is None else thinking

    def _headers(self, api_key_override=None):
        key = api_key_override or self.api_key or self._default_api_key
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _build_messages(self, system_prompt, messages):
        msgs = [{"role": "system", "content": system_prompt}] if system_prompt else []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "assistant"
            msgs.append({"role": role, "content": msg["content"]})
        return msgs

    def _payload(self, messages, stream, temperature, thinking=None):
        think = self.thinking if thinking is None else bool(thinking)
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "thinking": {"type": "enabled" if think else "disabled"},
        }
        if think:
            # 思考模式下 temperature / top_p 无效（会被忽略）；top_p 只接受 0.95~1.0
            payload["reasoning_effort"] = DEEPSEEK_REASONING_EFFORT
        else:
            payload["temperature"] = temperature
            payload["top_p"] = 0.9
        return payload

    def chat_stream(self, messages, system_prompt="", api_key_override=None, temperature=0.7):
        msgs = self._build_messages(system_prompt, messages)
        payload = self._payload(msgs, True, temperature)
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

    def chat_sync(self, messages, system_prompt="", temperature=0.8, api_key_override=None,
                  max_tokens=None, thinking=None):
        msgs = self._build_messages(system_prompt, messages)
        payload = self._payload(msgs, False, temperature, thinking=thinking)
        if max_tokens:
            payload["max_tokens"] = max_tokens
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
