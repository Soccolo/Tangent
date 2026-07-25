import hashlib
import hmac
import os
import secrets

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session as OrmSession

from .db import get_db
from .models import Session, User

_ITERATIONS = 260_000
COOKIE_NAME = "tangent_session"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def new_token() -> str:
    return secrets.token_urlsafe(32)[:64]


def current_user(
    tangent_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: OrmSession = Depends(get_db),
) -> User:
    if not tangent_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    session = db.get(Session, tangent_session)
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    return user
