import base64
import binascii
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from .. import mail, ratelimit
from ..config import (
    BASE_URL,
    DAILY_LESSON_CAP,
    IS_PRODUCTION,
    RESET_TTL_MINUTES,
    SESSION_DAYS,
)
from ..cosmetics import equipped_cosmetics, owl_profile_picture
from ..db import get_db
from ..gamification import catalog, streak_status, wallet
from ..models import (
    Activity,
    BossAttempt,
    Circle,
    CircleMember,
    CosmeticUnlock,
    Digest,
    Generation,
    HintUse,
    Lesson,
    LibraryLesson,
    Observation,
    PasswordReset,
    ReviewAttempt,
    ReviewProgress,
    RewardClaim,
    Session,
    User,
)
from ..schemas import (
    AccountDelete,
    ForgotPassword,
    ProfileUpdate,
    ResetPassword,
    SignIn,
    SignUp,
)
from ..security import (
    COOKIE_NAME,
    current_user,
    hash_password,
    new_token,
    verify_password,
)

log = logging.getLogger("tangent.auth")

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
    db.add(
        Session(
            token=token,
            user_id=user.id,
            expires_at=datetime.utcnow() + timedelta(days=SESSION_DAYS),
        )
    )
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
        **wallet(user),
        "reward_catalog": catalog(),
        "streak_status": streak_status(user),
        "daily_lesson_cap": DAILY_LESSON_CAP,
        "avatar": user.avatar,
        "profile_picture": owl_profile_picture(user),
        "bio": user.bio or "",
        "accent": user.accent or "",
        # Default on: an empty library helps nobody, and topics are subject
        # matter rather than anything drawn from the activity log.
        "contribute_to_library": True if user.contribute_to_library is None
        else bool(user.contribute_to_library),
        "learning_goals": user.learning_goals or "",
        "aim": user.aim or "",
        "onboarded": user.onboarded_at is not None,
        "theme": user.theme or "system",
        "timezone": user.timezone or "",
        "default_level": user.default_level or 5,
        "equipped_cosmetics": equipped_cosmetics(user),
    }
    if db is not None:
        from .learn import used_today  # local import avoids a circular import

        profile["lessons_left_today"] = max(
            0, DAILY_LESSON_CAP - used_today(db, user.id, "lesson")
        )
    return profile


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(
    body: SignUp,
    request: Request,
    response: Response,
    db: OrmSession = Depends(get_db),
):
    ratelimit.check(
        f"signup:{ratelimit.client_ip(request)}", 5, 3600,
        "Too many accounts created from here. Try again in an hour.",
    )
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
def signin(
    body: SignIn,
    request: Request,
    response: Response,
    db: OrmSession = Depends(get_db),
):
    email = body.email.strip().lower()
    # Two buckets: by IP stops one machine spraying many accounts, by email
    # stops a botnet converging on one account. Either alone leaves a hole.
    ratelimit.check(
        f"signin-ip:{ratelimit.client_ip(request)}", 20, 900,
        "Too many sign-in attempts. Wait a few minutes and try again.",
    )
    ratelimit.check(
        f"signin-email:{email}", 8, 900,
        "Too many attempts for this account. Wait a few minutes and try again.",
    )

    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    _start_session(response, db, user)
    return _profile(user, db)


@router.post("/forgot")
def forgot_password(
    body: ForgotPassword,
    request: Request,
    db: OrmSession = Depends(get_db),
):
    """Always reports success.

    Saying "no such account" would turn this endpoint into a way to test which
    email addresses are registered.
    """
    email = body.email.strip().lower()
    ratelimit.check(
        f"forgot-ip:{ratelimit.client_ip(request)}", 5, 900,
        "Too many reset requests. Wait a few minutes.",
    )
    ratelimit.check(f"forgot-email:{email}", 3, 900, "A reset link was already sent.")

    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        token = new_token()
        db.add(
            PasswordReset(
                token=token,
                user_id=user.id,
                expires_at=datetime.utcnow() + timedelta(minutes=RESET_TTL_MINUTES),
            )
        )
        db.commit()
        link = f"{BASE_URL}/reset/{token}"
        mail.send(
            user.email,
            "Reset your Tangent password",
            "Someone asked to reset the password for your Tangent account.\n\n"
            f"{link}\n\n"
            f"The link works once and expires in {RESET_TTL_MINUTES} minutes.\n"
            "If this wasn't you, ignore this email — nothing has changed.\n",
        )

    return {"ok": True, "delivery": "email" if mail.configured() else "log"}


@router.post("/reset")
def reset_password(
    body: ResetPassword,
    request: Request,
    response: Response,
    db: OrmSession = Depends(get_db),
):
    ratelimit.check(
        f"reset:{ratelimit.client_ip(request)}", 10, 900,
        "Too many attempts. Wait a few minutes.",
    )
    record = db.get(PasswordReset, body.token.strip())
    if (
        record is None
        or record.used_at is not None
        or record.expires_at < datetime.utcnow()
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That reset link has expired or already been used. Request a new one.",
        )

    user = db.get(User, record.user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That account no longer exists.")

    user.password_hash = hash_password(body.password)
    record.used_at = datetime.utcnow()
    # A password change ends every existing session: if the reset was because
    # someone else had access, leaving their session alive defeats the point.
    db.execute(delete(Session).where(Session.user_id == user.id))
    db.commit()

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
    if body.display_name is not None and body.display_name.strip():
        user.display_name = body.display_name.strip()
    if body.role is not None:
        user.role = body.role.strip()
    if body.bio is not None:
        user.bio = body.bio.strip() or None
    if body.accent is not None:
        # An unrecognised or empty accent means "back to the default".
        user.accent = body.accent.strip() if body.accent.strip() in ACCENTS else None

    if body.avatar is not None:
        user.avatar = _clean_avatar(body.avatar)
    if body.contribute_to_library is not None:
        user.contribute_to_library = body.contribute_to_library
    if body.learning_goals is not None:
        user.learning_goals = body.learning_goals.strip() or None
    if body.aim is not None:
        user.aim = body.aim.strip() or None
    if body.onboarded:
        user.onboarded_at = user.onboarded_at or datetime.utcnow()
    if body.theme in {"system", "light", "dark"}:
        user.theme = body.theme
    if body.timezone is not None:
        candidate = body.timezone.strip()
        if candidate:
            try:
                ZoneInfo(candidate)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "That timezone isn't recognised."
                ) from exc
            user.timezone = candidate
    if body.default_level is not None:
        user.default_level = max(1, min(10, body.default_level))

    db.commit()
    return _profile(user, db)


@router.get("/me/export")
def export_data(user: User = Depends(current_user), db: OrmSession = Depends(get_db)):
    """Everything held about this account, as one JSON file (GDPR Art. 20)."""
    lessons = db.scalars(select(Lesson).where(Lesson.user_id == user.id)).all()
    payload = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "profile": {
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
            "bio": user.bio,
            "accent": user.accent,
            "timezone": user.timezone,
            "has_avatar": bool(user.avatar),
            "profile_picture": owl_profile_picture(user),
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "xp": user.xp,
            "current_streak": user.current_streak,
            "longest_streak": user.longest_streak,
            **wallet(user),
        },
        "activities": [
            {"day": a.day.isoformat(), "text": a.text, "source": a.source}
            for a in db.scalars(select(Activity).where(Activity.user_id == user.id))
        ],
        "observations": [
            {
                "day": o.day.isoformat(),
                "activity": o.activity,
                "evidence": o.evidence,
                "confidence": o.confidence,
                "status": o.status,
            }
            for o in db.scalars(select(Observation).where(Observation.user_id == user.id))
        ],
        "digests": [
            {
                "day": d.day.isoformat(),
                "summary": d.summary,
                "topics": json.loads(d.topics_json or "[]"),
            }
            for d in db.scalars(select(Digest).where(Digest.user_id == user.id))
        ],
        "hints_used": [
            {
                "lesson_id": hint.lesson_id,
                "question_index": hint.question_index,
                "eliminated_index": hint.eliminated_index,
                "created_at": hint.created_at.isoformat() if hint.created_at else None,
            }
            for hint in db.scalars(select(HintUse).where(HintUse.user_id == user.id))
        ],
        "lessons": [
            {
                "title": lesson.topic_title,
                "category": lesson.category,
                "picked_by": lesson.picked_by,
                "completed": lesson.completed,
                "score": lesson.score,
                "total_questions": lesson.total_questions,
                "xp_awarded": lesson.xp_awarded,
                "coins_awarded": lesson.coins_awarded or 0,
                "created_at": lesson.created_at.isoformat() if lesson.created_at else None,
                "content": json.loads(lesson.content_json) if lesson.content_json else None,
            }
            for lesson in lessons
        ],
        "growth": {
            "last_constellation_visit": (
                user.last_constellation_visit.isoformat()
                if user.last_constellation_visit
                else None
            ),
            "equipped": equipped_cosmetics(user),
            "review_progress": [
                {
                    "lesson_id": row.lesson_id,
                    "question_index": row.question_index,
                    "due_day": row.due_day.isoformat(),
                    "interval_days": row.interval_days,
                    "repetitions": row.repetitions,
                    "last_reviewed_day": (
                        row.last_reviewed_day.isoformat()
                        if row.last_reviewed_day
                        else None
                    ),
                    "last_correct": row.last_correct,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
                for row in db.scalars(
                    select(ReviewProgress).where(ReviewProgress.user_id == user.id)
                )
            ],
            "review_attempts": [
                {
                    "lesson_id": row.lesson_id,
                    "question_index": row.question_index,
                    "review_day": row.review_day.isoformat(),
                    "answer_index": row.answer_index,
                    "correct": row.correct,
                    "reward": row.reward,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in db.scalars(
                    select(ReviewAttempt).where(ReviewAttempt.user_id == user.id)
                )
            ],
            "reward_claims": [
                {
                    "key": row.key,
                    "reward": row.reward,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in db.scalars(
                    select(RewardClaim).where(RewardClaim.user_id == user.id)
                )
            ],
            "boss_attempts": [
                {
                    "week_key": row.week_key,
                    "scenario_key": row.scenario_key,
                    "scenario": (
                        json.loads(row.scenario_json)
                        if row.scenario_json
                        else None
                    ),
                    "answer_index": row.answer_index,
                    "correct": row.correct,
                    "reward": row.reward,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in db.scalars(
                    select(BossAttempt).where(BossAttempt.user_id == user.id)
                )
            ],
            "cosmetics": [
                {
                    "item_key": row.item_key,
                    "purchased_at": (
                        row.purchased_at.isoformat() if row.purchased_at else None
                    ),
                }
                for row in db.scalars(
                    select(CosmeticUnlock).where(CosmeticUnlock.user_id == user.id)
                )
            ],
            "circles": [
                {
                    "circle_id": membership.circle_id,
                    "name": circle.name,
                    "invite_code": circle.invite_code,
                    "weekly_goal": circle.weekly_goal,
                    "owner": circle.created_by_user_id == user.id,
                    "joined_at": (
                        membership.joined_at.isoformat()
                        if membership.joined_at
                        else None
                    ),
                }
                for membership, circle in db.execute(
                    select(CircleMember, Circle)
                    .join(Circle, Circle.id == CircleMember.circle_id)
                    .where(CircleMember.user_id == user.id)
                )
            ],
        },
    }
    filename = f"tangent-export-{datetime.utcnow():%Y-%m-%d}.json"
    return Response(
        content=json.dumps(payload, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/me/delete")
def delete_account(
    body: AccountDelete,
    response: Response,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    """Irreversible. Removes the account and everything attached to it."""
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That password isn't right.")

    user_id = user.id

    # Library contributions stay because other people's lessons point at them.
    # Collect their ids before unlinking the author so every surviving derived
    # lesson can be anonymised too.
    authored_library_entries = db.scalars(
        select(LibraryLesson).where(LibraryLesson.author_user_id == user_id)
    ).all()
    authored_library_ids = {entry.id for entry in authored_library_entries}

    deleted_lesson_rows = db.execute(
        select(Lesson.id, Lesson.library_id, Lesson.shared_from_id).where(
            Lesson.user_id == user_id
        )
    ).all()
    deleted_lesson_ids = {row.id for row in deleted_lesson_rows}
    authored_source_ids = {
        row.id
        for row in deleted_lesson_rows
        if row.shared_from_id is None
        and (row.library_id is None or row.library_id in authored_library_ids)
    }
    library_copy_ids = (
        set(
            db.scalars(
                select(Lesson.id).where(
                    Lesson.library_id.in_(authored_library_ids),
                    Lesson.user_id != user_id,
                )
            ).all()
        )
        if authored_library_ids
        else set()
    )

    # Follow every generation of authored shared/library copies. The explicit
    # neutral name avoids both retaining personal data and falsely attributing a
    # surviving copy to its current owner when it is shared again.
    attribution_seeds = authored_source_ids | library_copy_ids
    descendant_ids: set[int] = set()
    frontier = attribution_seeds
    while frontier:
        children = set(
            db.scalars(select(Lesson.id).where(Lesson.shared_from_id.in_(frontier))).all()
        )
        frontier = children - descendant_ids - attribution_seeds
        descendant_ids.update(frontier)

    anonymized_ids = (library_copy_ids | descendant_ids) - deleted_lesson_ids
    if anonymized_ids:
        for copy in db.scalars(select(Lesson).where(Lesson.id.in_(anonymized_ids))):
            copy.author_name = "someone"

    # Every direct edge into a soon-to-be-deleted lesson must be detached,
    # including a copy that legitimately retains some other author's name.
    if deleted_lesson_ids:
        for copy in db.scalars(
            select(Lesson).where(Lesson.shared_from_id.in_(deleted_lesson_ids))
        ):
            copy.shared_from_id = None

    for entry in authored_library_entries:
        entry.author_user_id = None
        entry.author_name = None
    db.flush()

    # A private circle survives its creator when other members remain. Transfer
    # ownership to the longest-standing remaining member; otherwise remove the
    # now-empty circle.
    for circle in db.scalars(
        select(Circle)
        .where(Circle.created_by_user_id == user_id)
        .with_for_update()
    ).all():
        successor = db.scalar(
            select(CircleMember)
            .where(
                CircleMember.circle_id == circle.id,
                CircleMember.user_id != user_id,
            )
            .order_by(CircleMember.joined_at, CircleMember.id)
            .limit(1)
        )
        if successor is not None:
            circle.created_by_user_id = successor.user_id
        else:
            db.execute(delete(CircleMember).where(CircleMember.circle_id == circle.id))
            db.delete(circle)
    db.execute(delete(CircleMember).where(CircleMember.user_id == user_id))
    db.execute(delete(ReviewAttempt).where(ReviewAttempt.user_id == user_id))
    db.execute(delete(ReviewProgress).where(ReviewProgress.user_id == user_id))
    db.execute(delete(RewardClaim).where(RewardClaim.user_id == user_id))
    db.execute(delete(BossAttempt).where(BossAttempt.user_id == user_id))
    db.execute(delete(CosmeticUnlock).where(CosmeticUnlock.user_id == user_id))
    db.execute(delete(HintUse).where(HintUse.user_id == user_id))
    for model in (Observation, Generation, Activity, Lesson, Digest, PasswordReset, Session):
        db.execute(delete(model).where(model.user_id == user_id))
    db.execute(delete(User).where(User.id == user_id))
    db.commit()

    response.delete_cookie(COOKIE_NAME)
    log.info("Account %s deleted at the user's request", user_id)
    return {"deleted": True}
