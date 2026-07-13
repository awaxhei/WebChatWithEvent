"""
认证路由：登录、验证、登出
"""
import hashlib
from flask import Blueprint, request, jsonify
from core.auth import generate_token, verify_token
from core.database import ChatDB
from config import JWT_EXPIRE_DAYS

auth_bp = Blueprint("auth", __name__)
db = None  # 由外部注入


@auth_bp.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    remember = data.get("remember", False)
    if not username or not password:
        return jsonify({"success": False, "error": "请输入用户名和密码"}), 400
    user = db.get_user(username)
    if not user:
        return jsonify({"success": False, "error": "用户不存在"}), 401
    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    if pw_hash != user["password"]:
        return jsonify({"success": False, "error": "密码错误"}), 401
    token = generate_token(user["id"], remember=remember)
    resp = jsonify({"success": True, "username": user["username"]})
    max_age = JWT_EXPIRE_DAYS * 86400 if remember else 86400
    resp.set_cookie("auth_token", token, httponly=True, max_age=max_age, samesite="Lax")
    return resp


@auth_bp.route("/api/auth/verify", methods=["GET"])
def auth_verify():
    token = request.cookies.get("auth_token") or ""
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        return jsonify({"valid": False})
    payload = verify_token(token)
    return jsonify({"valid": bool(payload)})


@auth_bp.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    resp = jsonify({"success": True})
    resp.set_cookie("auth_token", "", max_age=0)
    return resp