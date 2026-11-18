"""
JWT 鉴权工具：token 生成、验证、require_auth 装饰器、会话归属校验、用户 API Key 读取
"""
from datetime import datetime, timedelta
from functools import wraps

import jwt
from flask import request, jsonify
from config import JWT_SECRET_KEY, JWT_EXPIRE_DAYS

# 前端可通过该请求头携带自己的 DeepSeek Key（覆盖 config.py 中的默认 key）
API_KEY_HEADER = "X-DeepSeek-API-Key"


def request_api_key():
    """读取当前请求携带的用户 API Key，没有则返回 None（使用服务端默认 key）"""
    key = (request.headers.get(API_KEY_HEADER) or "").strip()
    return key or None


def ensure_conversation_owner(db, conv_id, user_id):
    """会话归属校验。

    返回 None 表示校验通过；否则返回可直接 return 的 Flask 响应。
    所有按 conversation_id 读写的接口都必须先调用它，避免越权访问他人会话。
    """
    if not conv_id or not db.conversation_belongs_to(conv_id, user_id):
        return jsonify({"error": "会话不存在或无权访问"}), 404
    return None


def generate_token(user_id, remember=False):
    expire_hours = 24 if not remember else JWT_EXPIRE_DAYS * 24
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(hours=expire_hours),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm="HS256")


def verify_token(token):
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get("auth_token") or ""
        if not token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
        if not token:
            return jsonify({"error": "未授权"}), 401
        payload = verify_token(token)
        if not payload:
            return jsonify({"error": "令牌无效或已过期"}), 401
        request.user_id = payload["user_id"]
        return f(*args, **kwargs)

    return decorated