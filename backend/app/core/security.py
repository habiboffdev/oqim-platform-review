import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

import bcrypt
import jwt
from fastapi import Response

from app.core.config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed or not hashed.startswith("$2"):
        return False
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(
    subject: Union[str, Any], expires_delta: timedelta | None = None
) -> str:
    """Create JWT access token. Subject should be the workspace ID as a string."""
    settings = get_settings()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.access_token_expire_minutes
        )

    to_encode = {"exp": expire, "sub": str(subject)}
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def verify_token(token: str) -> Optional[str]:
    """Verify JWT token and return subject (workspace ID as string)."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_auth_cookies(response: Response, jwt_token: str, csrf_token: str) -> None:
    settings = get_settings()
    cookie_kwargs = {
        "key": "oqim_session",
        "value": jwt_token,
        "max_age": settings.access_token_expire_minutes * 60,
        "path": "/",
        "httponly": True,
        "samesite": "lax",
        "secure": settings.cookie_secure,
    }
    if settings.cookie_domain:
        cookie_kwargs["domain"] = settings.cookie_domain
    response.set_cookie(**cookie_kwargs)

    csrf_kwargs = {
        "key": "oqim_csrf",
        "value": csrf_token,
        "max_age": settings.access_token_expire_minutes * 60,
        "path": "/",
        "httponly": False,
        "samesite": "lax",
        "secure": settings.cookie_secure,
    }
    if settings.cookie_domain:
        csrf_kwargs["domain"] = settings.cookie_domain
    response.set_cookie(**csrf_kwargs)


def clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    kwargs = {"path": "/"}
    if settings.cookie_domain:
        kwargs["domain"] = settings.cookie_domain
    response.delete_cookie("oqim_session", **kwargs)
    response.delete_cookie("oqim_csrf", **kwargs)
