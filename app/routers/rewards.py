import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ..db import get_db
from ..gamification import (
    HINT_PRICE,
    MAX_STREAK_FREEZES,
    STARTER_COINS,
    STARTER_HINTS,
    STREAK_FREEZE_PRICE,
    catalog,
    freeze_balance,
    streak_status,
    wallet,
)
from ..models import HintUse, Lesson, User
from ..schemas import HintRequest, RewardPurchase
from ..security import current_user

router = APIRouter(prefix="/api/rewards", tags=["rewards"])


def _payload(user: User) -> dict:
    return {
        **wallet(user),
        "catalog": catalog(),
        "streak_status": streak_status(user),
    }


@router.get("")
def rewards(
    user: User = Depends(current_user),
):
    return _payload(user)


@router.post("/purchase")
def purchase(
    body: RewardPurchase,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    if body.item == "hint":
        price = HINT_PRICE
        inventory_column = User.hint_tokens
        inventory_start = STARTER_HINTS
        extra_condition = True
    else:
        price = STREAK_FREEZE_PRICE
        inventory_column = User.streak_freezes
        inventory_start = 0
        if freeze_balance(user) >= MAX_STREAK_FREEZES:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"You can carry at most {MAX_STREAK_FREEZES} streak freezes.",
            )
        extra_condition = func.coalesce(User.streak_freezes, 0) < MAX_STREAK_FREEZES

    # One UPDATE both checks and spends the balance. Two devices cannot each
    # purchase against the same last handful of coins.
    result = db.execute(
        update(User)
        .where(
            User.id == user.id,
            func.coalesce(User.coins, STARTER_COINS) >= price,
            extra_condition,
        )
        .values(
            coins=func.coalesce(User.coins, STARTER_COINS) - price,
            **{
                inventory_column.key: func.coalesce(inventory_column, inventory_start) + 1
            },
        )
    )
    if result.rowcount != 1:
        db.rollback()
        db.refresh(user)
        if body.item == "streak_freeze" and freeze_balance(user) >= MAX_STREAK_FREEZES:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"You can carry at most {MAX_STREAK_FREEZES} streak freezes.",
            )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"You need {price} coins for that.",
        )

    db.commit()
    db.refresh(user)
    return {"purchased": body.item, **_payload(user)}


@router.post("/hints/use")
def use_hint(
    body: HintRequest,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    existing = db.scalar(
        select(HintUse).where(
            HintUse.user_id == user.id,
            HintUse.lesson_id == body.lesson_id,
            HintUse.question_index == body.question_index,
        )
    )
    if existing:
        return {
            "eliminated_index": existing.eliminated_index,
            "already_used": True,
            **_payload(user),
        }

    lesson = db.get(Lesson, body.lesson_id)
    if lesson is None or lesson.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such lesson")
    if lesson.status != "ready" or not lesson.content_json:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That lesson isn't ready yet.")

    questions = json.loads(lesson.content_json).get("questions", [])
    if body.question_index >= len(questions):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such question")
    question = questions[body.question_index]
    options = question.get("options") or []
    answer = question.get("answer_index")
    wrong = [i for i in range(len(options)) if i != answer]
    if not wrong:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This question doesn't have an answer I can remove.",
        )

    # Stable across retries and processes, but different across users/questions.
    digest = hashlib.blake2s(
        f"{user.id}:{lesson.id}:{body.question_index}".encode(), digest_size=2
    ).digest()
    eliminated = wrong[int.from_bytes(digest, "big") % len(wrong)]

    spent = db.execute(
        update(User)
        .where(
            User.id == user.id,
            func.coalesce(User.hint_tokens, STARTER_HINTS) > 0,
        )
        .values(hint_tokens=func.coalesce(User.hint_tokens, STARTER_HINTS) - 1)
    )
    if spent.rowcount != 1:
        db.rollback()
        existing = db.scalar(
            select(HintUse).where(
                HintUse.user_id == user.id,
                HintUse.lesson_id == body.lesson_id,
                HintUse.question_index == body.question_index,
            )
        )
        if existing:
            db.refresh(user)
            return {
                "eliminated_index": existing.eliminated_index,
                "already_used": True,
                **_payload(user),
            }
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "You don't have a hint token. Buy one with coins first.",
        )

    db.add(
        HintUse(
            user_id=user.id,
            lesson_id=lesson.id,
            question_index=body.question_index,
            eliminated_index=eliminated,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # A simultaneous retry won the unique key. This transaction (including
        # its token spend) rolled back, so return the already-paid result.
        db.rollback()
        existing = db.scalar(
            select(HintUse).where(
                HintUse.user_id == user.id,
                HintUse.lesson_id == body.lesson_id,
                HintUse.question_index == body.question_index,
            )
        )
        if existing is None:
            raise
        db.refresh(user)
        return {
            "eliminated_index": existing.eliminated_index,
            "already_used": True,
            **_payload(user),
        }

    db.refresh(user)
    return {
        "eliminated_index": eliminated,
        "already_used": False,
        **_payload(user),
    }
