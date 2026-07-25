import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..config import DAILY_LESSON_CAP, IS_PRODUCTION
from ..db import get_db
from ..models import Session, User
from ..schemas import ProfileUpdate, SignIn, SignUp
from ..security import (
    COOKIE_NAME,
    current_user,
    hash_password,
    new_token,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# A fixed palette rather than a free hex field: the theme is dark, and an
# arbitrary colour can land unreadable against it.
ACCENTS = {"violet", "ember", "teal", "rose", "lime"}

# Only raster images, and only as a self-contained data: URL. An SVG avatar
# would be markup we then render — a script-injection vector for the price of a
# profile picture.
_AVATAR_PREFIXES = (
    "data:image/png;base64,",
    "data:image/jpeg;base64,",
    "data:image/webp;base64,",
)


def _clean_avatar(raw: str) -> str | None:
    """Validate a client-supplied avatar, or reject it outright."""
    value = (raw or "").strip()
    if not value:
        return None  # explicit removal
    if not value.startswith(_AVATAR_PREFIXES):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Avatar must be a PNG, JPEG or WebP image.",
        )
    payload = value.split(",", 1)[1] if "," in value else ""
    try:
        decoded = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Avatar isn't valid image data.") from exc
    if not decoded:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Avatar image is empty.")
    if len(decoded) > 300_000:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That image is too large even after resizing — try a smaller one.",
        )
    return value


def _start_session(response: Response, db: OrmSession, user: User) -> None:
    token = new_token()
    db.add(Session(token=token, user_id=user.id))
    db.commit()
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=IS_PRODUCTION,  # localhost is plain http; anything deployed is not
        max_age=60 * 60 * 24 * 90,
    )


def _profile(user: User, db: OrmSession | None = None) -> dict:
    profile = {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "xp": user.xp,
        "level": user.xp // 100 + 1,
        "xp_into_level": user.xp % 100,
        "current_streak": user.current_streak,
        "longest_streak": user.longest_streak,
        "daily_lesson_cap": DAILY_LESSON_CAP,
        "avatar": user.avatar,
        "bio": user.bio or "",
        "accent": user.accent or "",
    }
    if db is not None:
        from .learn import used_today  # local import avoids a circular import

        profile["lessons_left_today"] = max(
            0, DAILY_LESSON_CAP - used_today(db, user.id, "lesson")
        )
    return profile


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(body: SignUp, response: Response, db: OrmSession = Depends(get_db)):
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "That email is already registered")
    user = User(
        email=email,
        password_hash=hash_password(body.password),
        display_name=body.display_name.strip() or email.split("@")[0],
        role=body.role.strip(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    _start_session(response, db, user)
    return _profile(user, db)


@router.post("/signin")
def signin(body: SignIn, response: Response, db: OrmSession = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    _start_session(response, db, user)
    return _profile(user, db)


@router.post("/signout")
def signout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user), db: OrmSession = Depends(get_db)):
    return _profile(user, db)


@router.patch("/me")
def update_me(
    body: ProfileUpdate,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    if body.display_name.strip():
        user.display_name = body.display_name.strip()
    user.role = body.role.strip()
    user.bio = body.bio.strip() or None
    user.accent = body.accent.strip() if body.accent.strip() in ACCENTS else None

    if body.avatar is not None:
        user.avatar = _clean_avatar(body.avatar)

    db.commit()
    return _profile(user, db)
