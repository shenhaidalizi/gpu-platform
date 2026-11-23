import hmac

from fastapi import HTTPException, Request

from gpu_platform.config import digest


def authenticate(settings, token):
    if token and len(token) <= 1024:
        candidate = digest(token)
        for name, expected in settings.users.items():
            if hmac.compare_digest(candidate, expected):
                return name
    raise HTTPException(401, "登录凭据无效")


def current_user(request: Request):
    kind, _, token = request.headers.get("authorization", "").partition(" ")
    return authenticate(request.app.state.settings, token if kind.lower() == "bearer" else "")
