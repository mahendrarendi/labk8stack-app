"""Cookie sesi bertanda tangan (tanpa password, sesuai keputusan lab)."""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

COOKIE_NAME = "lab_session"
_serializer = URLSafeTimedSerializer(settings.secret_key, salt="k8s-lab-session")


def sign_user(user_id: int) -> str:
    return _serializer.dumps({"uid": user_id})


def read_user_id(token: str | None) -> int | None:
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=settings.session_max_age)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid") if isinstance(data, dict) else None
    return int(uid) if isinstance(uid, int) else None
