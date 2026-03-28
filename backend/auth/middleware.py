from functools import wraps
from flask import request, jsonify
from auth.jwt_handler import verify_token

_UNPROTECTED = {"/health", "/token"}


def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Unauthorised", "message": "Valid Bearer token required"}), 401

        token = auth_header[len("Bearer "):].strip()
        if not token:
            return jsonify({"error": "Unauthorised", "message": "Valid Bearer token required"}), 401

        payload = verify_token(token)
        if payload is None:
            return jsonify({"error": "Unauthorised", "message": "Token expired"}), 401

        return f(*args, **kwargs)
    return wrapper
