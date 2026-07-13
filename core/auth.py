"""
JWT 鉴权工具：token 生成、验证、require_auth 装饰器
"""
from datetime import datetime, timedelta
from functools import wraps

import jwt
from flask import request, jsonify
from config import JWT_SECRET_KEY, JWT_EXPIRE_DAYS


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