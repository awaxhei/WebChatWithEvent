"""
DeepSeekClient: DeepSeek API 流式/同步调用封装
"""
import json, requests
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, CHARACTER_NAME

DEEPSEEK_BASE = "https://api.deepseek.com/v1/chat/completions"


class DeepSeekClient:
    def __init__(self, base_url=DEEPSEEK_BASE, model=DEEPSEEK_MODEL, api_key=DEEPSEEK_API_KEY):
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _build_messages(self, system_prompt, messages):
        msgs = [{"role": "system", "content": system_prompt}] if system_prompt else []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "assistant"
            msgs.append({"role": role, "content": msg["content"]})
        return msgs

    def chat_stream(self, messages, system_prompt=""):
        msgs = self._build_messages(system_prompt, messages)
        payload = {"model": self.model, "messages": msgs, "stream": True, "temperature": 0.7, "top_p": 0.9}
        if not self.api_key:
            yield "请在 config.py 中设置 DEEPSEEK_API_KEY"
            return
        try:
            resp = requests.post(self.base_url, json=payload, headers=self._headers, stream=True, timeout=120)
            resp.raise_for_status()
            fc = False
            buf = ""
            prefixes = (f"{CHARACTER_NAME}：", f"{CHARACTER_NAME}:", "青梅：")
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data: "):
                    ds = line[6:]
                    if ds.strip() == "[DONE]":
                        break
                    try:
                        content = json.loads(ds).get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if not content:
                            continue
                        if not fc:
                            buf += content
                            s = buf
                            for p in prefixes:
                                if buf.startswith(p):
                                    s = buf[len(p):].strip()
                                    break
                            if len(buf) >= max(len(p) for p in prefixes) or s != buf:
                                fc = True
                                if s:
                                    yield s
                        else:
                            yield content
                    except json.JSONDecodeError:
                        continue
            if not fc and buf:
                for p in prefixes:
                    if buf.startswith(p):
                        buf = buf[len(p):].strip()
                        break
                if buf:
                    yield buf
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

    def chat_sync(self, messages, system_prompt="", temperature=0.8):
        msgs = self._build_messages(system_prompt, messages)
        payload = {"model": self.model, "messages": msgs, "stream": False, "temperature": temperature, "top_p": 0.9}
        if not self.api_key:
            return "[请在 config.py 中设置 DEEPSEEK_API_KEY]"
        try:
            resp = requests.post(self.base_url, json=payload, headers=self._headers, timeout=120)
            resp.raise_for_status()
            return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        except Exception as e:
            return f"[生成失败: {e}]"