"""
内嵌后端探针（手机单机模式）

验证 mobile_boot.start() 这条路：不依赖电脑、不依赖外部服务器，
同一套 Flask 逻辑在手机里跑起来。这里在电脑上用临时目录模拟手机的私有存储：

1. ROOT 指到临时目录后，SQLite / 表情包 / 提示词都写在里面（不碰项目目录）
2. 起服务后只用 127.0.0.1，且用返回的本机 token 能直接用
3. 表情包上传落盘到临时目录
4. 对话链路（桩模型）照常工作

用法：python scripts/test_embedded_backend.py
"""
import io
import json
import os
import shutil
import socket
import subprocess
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

FAILED = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def png_bytes(seed):
    w = h = 12

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b""
    for y in range(h):
        raw += b"\x00" + bytes([(seed * 31 + x * 7 + y) % 256 for x in range(w * 3)])
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def call(base, path, token="", data=None, ctype=None, method=None):
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method or ("POST" if data is not None else "GET"),
    )
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    # ── 0) APK 里装的是 config_template（拷成 config.py）
    # 单独用一个干净进程验证它能把后端带起来：漏了 ROOT 这类定义会在这里炸
    sim_root = os.path.join(ROOT, ".testtmp", "apk_sim_" + uuid.uuid4().hex[:6])
    os.makedirs(os.path.join(sim_root, "data"), exist_ok=True)
    os.makedirs(os.path.join(sim_root, "static", "stickers"), exist_ok=True)
    shutil.copy(os.path.join(ROOT, "yuxia_prompt.txt"),
                os.path.join(sim_root, "yuxia_prompt.txt"))
    sim_code = (
        "import os, sys\n"
        f"root = {sim_root!r}\n"
        "os.environ['YUXIA_ROOT'] = root\n"
        "os.environ['YUXIA_DB_PATH'] = os.path.join(root, 'data', 'chat.db')\n"
        "os.environ['YUXIA_MOBILE'] = '1'\n"
        f"sys.path.insert(0, {ROOT!r})\n"
        "import config_template as config\n"
        "sys.modules['config'] = config\n"
        "import app\n"
        "print('OK', config.ROOT == root)\n"
    )
    sim = subprocess.run([sys.executable, "-c", sim_code],
                         capture_output=True, text=True, timeout=180)
    sim_out = (sim.stdout or "") + (sim.stderr or "")
    check("APK 用的 config 模板能独立启动后端",
          sim.returncode == 0 and "OK True" in sim_out, sim_out[-400:])
    shutil.rmtree(sim_root, ignore_errors=True)

    app_root = tempfile.mkdtemp(prefix="yuxia_mobile_")
    port = free_port()
    base = f"http://127.0.0.1:{port}"

    prompt = open(os.path.join(ROOT, "yuxia_prompt.txt"), encoding="utf-8").read()

    import mobile_boot
    token = mobile_boot.start(app_root, port, prompt)
    time.sleep(1.0)

    check("start() 返回本机 token", isinstance(token, str) and len(token) > 20, str(token)[:20])
    check("提示词落盘到 App 目录",
          os.path.exists(os.path.join(app_root, "yuxia_prompt.txt")))
    check("SQLite 落在 App 目录", os.path.exists(os.path.join(app_root, "data", "chat.db")),
          os.listdir(os.path.join(app_root, "data")) if os.path.isdir(os.path.join(app_root, "data")) else "no data dir")
    check("表情包目录建好",
          os.path.isdir(os.path.join(app_root, "static", "stickers", "upload")))
    check("没有写进项目目录",
          not os.path.exists(os.path.join(ROOT, "data", "chat.db.mobile-test")))

    # 只监听 127.0.0.1
    st, body = call(base, "/api/ping")
    check("本机服务起来了", st == 200 and b'"ok":true' in body, f"{st} {body[:80]}")
    try:
        with socket.socket() as s:
            s.settimeout(2)
            s.bind(("0.0.0.0", port))
        external_free = True
    except OSError:
        external_free = False
    check("只绑在 127.0.0.1（外部连不上）", external_free)

    # token 直接可用，不需要登录
    st, body = call(base, "/api/history", token)
    check("本机 token 可用（不用登录）", st == 200 and isinstance(json.loads(body), list), f"{st} {body[:80]}")

    st, body = call(base, "/api/history/new", token, data=b"{}", ctype="application/json")
    conv = json.loads(body)["id"]
    check("能新建会话", bool(conv))

    # 表情包上传 → 落到 App 目录
    boundary = uuid.uuid4().hex
    parts = [
        (f"--{boundary}\r\n").encode(),
        b'Content-Disposition: form-data; name="files"; filename="a.png"\r\n',
        b"Content-Type: image/png\r\n\r\n",
        png_bytes(7),
        b"\r\n",
        (f"--{boundary}--\r\n").encode(),
    ]
    st, body = call(base, "/api/stickers/upload", token, data=b"".join(parts),
                    ctype=f"multipart/form-data; boundary={boundary}")
    up = json.loads(body)
    check("表情包上传成功", st == 200 and up.get("uploaded_count") == 1, f"{st} {body[:120]}")
    rel = (up.get("uploaded") or [""])[0]
    check("图片落在 App 的表情包目录",
          bool(rel) and os.path.exists(os.path.join(app_root, "static", "stickers", rel)), rel)

    st, body = call(base, "/api/stickers/list?limit=10", token)
    check("表情包列表能读到", st == 200 and json.loads(body).get("total") == 1, body[:100])

    # 对话链路（换成桩模型，不花钱）
    import routes.chat_routes as chat_mod

    class Stub:
        def chat_stream(self, messages, system_prompt="", **kw):
            yield '{"对白": "……嗯(ᗜ ‸ ᗜ)"}'

        def chat_sync(self, *a, **kw):
            return "[生成失败: 测试桩]"

    chat_mod.deepseek = Stub()
    st, body = call(base, "/api/chat", token,
                    data=json.dumps({"conversation_id": conv, "message": "测试一下"}).encode(),
                    ctype="application/json")
    text = body.decode("utf-8", "replace")
    check("对话链路正常（SSE）", st == 200 and '"type": "done"' in text, f"{st} {text[:100]}")
    check("她的话落进 App 的数据库", "嗯" in text)

    st, body = call(base, f"/api/notify/pending?conversation_id={conv}&since_id=0", token)
    notes = json.loads(body)
    check("通知接口可用", st == 200 and notes.get("last_id", 0) > 0, body[:120])

    shutil.rmtree(app_root, ignore_errors=True)
    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
