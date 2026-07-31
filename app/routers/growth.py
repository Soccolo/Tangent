"""The richer return loop: review, exploration, missions, cosmetics and circles.

Everything that awards or spends coins is graded and priced here. The browser
only chooses an answer or an item key; it never supplies a reward or a price.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ..cosmetics import (
    EQUIPPED_FIELDS,
    WORKSHOP_BY_KEY,
    WORKSHOP_CATALOG,
    equipped_cosmetics,
    owl_profile_picture,
)
from ..db import get_db
from ..gamification import STARTER_COINS, user_day, user_today, wallet
from ..models import (
    BossAttempt,
    Circle,
    CircleMember,
    CosmeticUnlock,
    Lesson,
    ReviewAttempt,
    ReviewProgress,
    RewardClaim,
    User,
    utcnow,
)
from ..schemas import (
    BossAnswer,
    CircleCreate,
    CircleJoin,
    MissionClaim,
    ReviewAnswer,
    WorkshopItemRequest,
)
from ..security import current_user

router = APIRouter(prefix="/api/growth", tags=["growth"])


CATEGORIES = ("domain", "technical", "regulatory", "commercial", "frontier")
CATEGORY_LABELS = {
    "domain": "Your domain",
    "technical": "Technical",
    "regulatory": "Regulatory",
    "commercial": "Commercial",
    "frontier": "Frontier",
}

MISSION_DEFS = {
    "first_lesson": {
        "title": "Finish one tangent",
        "detail": "Complete one new lesson today.",
        "target": 1,
        "reward": 12,
    },
    "review_three": {
        "title": "Keep three ideas alive",
        "detail": "Answer three distinct due review questions today.",
        "target": 3,
        "reward": 8,
    },
    "constellation_visit": {
        "title": "Look at the wider map",
        "detail": "Visit your Tangent Constellation today.",
        "target": 1,
        "reward": 5,
    },
}

BOSS_CORRECT_REWARD = 25
BOSS_PARTICIPATION_REWARD = 8
REVIEW_CORRECT_REWARD = 2
REVIEW_PARTICIPATION_REWARD = 1
CIRCLE_WEEKLY_GOAL = 12
CIRCLE_LESSON_POINTS = 3
CIRCLE_REVIEW_POINTS = 1
CIRCLE_MAX_MEMBERS = 12

def _week_start(today: date) -> date:
    return today - timedelta(days=today.weekday())


def _week_key(today: date) -> str:
    return _week_start(today).isoformat()


def _questions(lesson: Lesson) -> list[dict]:
    try:
        content = json.loads(lesson.content_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    questions = content.get("questions", [])
    return questions if isinstance(questions, list) else []


def _completed_lessons(db: OrmSession, user_id: int) -> list[Lesson]:
    return db.scalars(
        select(Lesson)
        .where(Lesson.user_id == user_id, Lesson.completed.is_(True))
        .order_by(Lesson.completed_at.desc(), Lesson.id.desc())
    ).all()


def _category(lesson: Lesson) -> str:
    value = (lesson.category or "").strip().lower()
    return value if value in CATEGORIES else "domain"


def _constellation_payload(
    db: OrmSession,
    user: User,
    today: date,
) -> dict:
    lessons = db.scalars(
        select(Lesson)
        .where(Lesson.user_id == user.id)
        .order_by(Lesson.completed_at.desc(), Lesson.created_at.desc())
    ).all()
    categories = []
    for category in CATEGORIES:
        related = [lesson for lesson in lessons if _category(lesson) == category]
        completed = [lesson for lesson in related if lesson.completed]
        categories.append(
            {
                "key": category,
                "label": CATEGORY_LABELS[category],
                "lesson_count": len(related),
                "completed_count": len(completed),
            }
        )
    nodes = [
        {
            "id": lesson.id,
            "key": f"lesson-{lesson.id}",
            "lesson_id": lesson.id,
            "title": lesson.topic_title,
            "detail": lesson.topic_blurb
            or "A useful direction just outside your role.",
            "category": _category(lesson),
            "complete": bool(lesson.completed),
            "unlocked": bool(lesson.completed),
            "progress": 100 if lesson.completed else 0,
        }
        for lesson in lessons[:16]
    ]
    return {
        "role": user.role or "",
        "categories": categories,
        "nodes": nodes,
        "visited_today": user.last_constellation_visit == today,
    }


def _review_queue(
    db: OrmSession,
    user: User,
    today: date,
) -> dict:
    lessons = _completed_lessons(db, user.id)
    progress_rows = db.scalars(
        select(ReviewProgress).where(ReviewProgress.user_id == user.id)
    ).all()
    progress = {
        (row.lesson_id, row.question_index): row
        for row in progress_rows
    }
    reviewed_today_rows = db.scalars(
        select(ReviewAttempt).where(
            ReviewAttempt.user_id == user.id,
            ReviewAttempt.review_day == today,
        )
    ).all()
    reviewed_today_keys = {
        (row.lesson_id, row.question_index) for row in reviewed_today_rows
    }

    due: list[tuple[date, dict]] = []
    for lesson in lessons:
        completed_day = user_day(user, lesson.completed_at or lesson.created_at)
        for question_index, question in enumerate(_questions(lesson)):
            key = (lesson.id, question_index)
            row = progress.get(key)
            # Legacy lessons predate ReviewProgress. Treat their questions as
            # due immediately; newly completed lessons receive an explicit
            # tomorrow row from the completion endpoint.
            due_day = row.due_day if row else completed_day
            if due_day > today or key in reviewed_today_keys:
                continue
            options = question.get("options") or []
            if not isinstance(options, list) or not options:
                continue
            # Deliberately omit answer_index and explanation. Review answers are
            # graded server-side in review_answer().
            due.append(
                (
                    due_day,
                    {
                        "lesson_id": lesson.id,
                        "question_index": question_index,
                        "lesson_title": lesson.topic_title,
                        "prompt": str(question.get("prompt") or ""),
                        "options": [str(option) for option in options],
                    },
                )
            )

    due.sort(key=lambda item: (item[0], item[1]["lesson_id"], item[1]["question_index"]))
    return {
        "due_count": len(due),
        "questions": [item[1] for item in due[:3]],
        "reviewed_today": len(reviewed_today_keys),
    }


def _mission_claim_key(today: date, mission_key: str) -> str:
    return f"mission:{today.isoformat()}:{mission_key}"


def _missions_payload(
    db: OrmSession,
    user: User,
    today: date,
) -> list[dict]:
    lessons_today = sum(
        1
        for lesson in _completed_lessons(db, user.id)
        if lesson.completed_at and user_day(user, lesson.completed_at) == today
    )
    reviewed_today = db.scalar(
        select(func.count(ReviewAttempt.id)).where(
            ReviewAttempt.user_id == user.id,
            ReviewAttempt.review_day == today,
        )
    ) or 0
    values = {
        "first_lesson": lessons_today,
        "review_three": reviewed_today,
        "constellation_visit": 1 if user.last_constellation_visit == today else 0,
    }
    claims = {
        row.key
        for row in db.scalars(
            select(RewardClaim).where(
                RewardClaim.user_id == user.id,
                RewardClaim.key.like(f"mission:{today.isoformat()}:%"),
            )
        )
    }

    missions = []
    for key, definition in MISSION_DEFS.items():
        progress = int(values[key])
        target = int(definition["target"])
        missions.append(
            {
                "key": key,
                "title": definition["title"],
                "detail": definition["detail"],
                "progress": progress,
                "target": target,
                "complete": progress >= target,
                "claimed": _mission_claim_key(today, key) in claims,
                "reward": definition["reward"],
            }
        )
    return missions


def _boss_scenario(
    db: OrmSession,
    user: User,
    today: date,
) -> dict | None:
    unique: list[Lesson] = []
    seen: set[str] = set()
    for lesson in _completed_lessons(db, user.id):
        title_key = lesson.topic_title.strip().casefold()
        if title_key and title_key not in seen:
            seen.add(title_key)
            unique.append(lesson)
    if len(unique) < 2:
        return None

    week_key = _week_key(today)
    seed = int.from_bytes(
        hashlib.blake2s(
            f"{user.id}:{week_key}:boss".encode(),
            digest_size=4,
        ).digest(),
        "big",
    )
    first_index = seed % len(unique)
    second_index = (first_index + 1 + (seed // max(1, len(unique))) % (len(unique) - 1)) % len(unique)
    first, second = unique[first_index], unique[second_index]

    correct_option = (
        "Map the assumptions and failure modes from both topics, then test the "
        "decision against a concrete case."
    )
    distractors = [
        f"Use {first.topic_title} alone; combining disciplines will only slow the decision.",
        f"Average the conclusions from {first.topic_title} and {second.topic_title} without checking where either applies.",
        "Wait for a specialist to decide everything before identifying the actual trade-off.",
    ]
    correct_index = seed % 4
    options = list(distractors)
    options.insert(correct_index, correct_option)
    return {
        "week_key": week_key,
        "scenario_key": hashlib.blake2s(
            f"{user.id}:{week_key}:{first.id}:{second.id}".encode(),
            digest_size=8,
        ).hexdigest(),
        "topics": [first.topic_title, second.topic_title],
        "prompt": (
            f"A colleague needs a recommendation that connects “{first.topic_title}” "
            f"with “{second.topic_title}”. What is the strongest first move?"
        ),
        "options": options,
        "correct_index": correct_index,
    }


def _boss_payload(
    db: OrmSession,
    user: User,
    today: date,
) -> dict:
    week_key = _week_key(today)
    attempt = db.scalar(
        select(BossAttempt).where(
            BossAttempt.user_id == user.id,
            BossAttempt.week_key == week_key,
        )
    )
    scenario = None
    if attempt is not None and attempt.scenario_json:
        try:
            scenario = json.loads(attempt.scenario_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            scenario = None
    scenario = scenario or _boss_scenario(db, user, today)
    if scenario is None:
        return {
            "week_key": week_key,
            "locked": True,
            "reason": "Complete two different lesson topics to unlock this week's scenario.",
        }
    return {
        "week_key": week_key,
        "scenario_key": scenario["scenario_key"],
        "locked": False,
        "topics": scenario["topics"],
        "prompt": scenario["prompt"],
        "options": scenario["options"],
        "attempted": attempt is not None,
        "correct": attempt.correct if attempt else None,
        "reward": attempt.reward if attempt else None,
    }


def _equipped(user: User) -> dict:
    return equipped_cosmetics(user)


def _workshop_payload(db: OrmSession, user: User) -> dict:
    owned = {
        key
        for key in db.scalars(
            select(CosmeticUnlock.item_key).where(CosmeticUnlock.user_id == user.id)
        )
    }
    equipped = _equipped(user)
    items = []
    for definition in WORKSHOP_CATALOG:
        item = dict(definition)
        item["owned"] = item["key"] in owned
        item["equipped"] = equipped.get(item["slot"]) == item["key"]
        items.append(item)
    return {"items": items, "equipped": equipped}


def _member_contribution(
    db: OrmSession,
    member: User,
    week_start: date,
    joined_at,
) -> dict:
    week_start_at = datetime.combine(week_start, time.min, tzinfo=timezone.utc)
    week_end_at = week_start_at + timedelta(days=7)

    def as_utc(moment):
        if moment is None:
            return None
        # SQLite drops timezone metadata while Postgres retains it. Datetimes in
        # this app are written in UTC, so restore/convert that common boundary.
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    active_from = max(week_start_at, as_utc(joined_at))

    lessons = sum(
        1
        for lesson in _completed_lessons(db, member.id)
        if (completed_at := as_utc(lesson.completed_at))
        and active_from <= completed_at < week_end_at
    )
    reviews = sum(
        1
        for attempt in db.scalars(
            select(ReviewAttempt).where(ReviewAttempt.user_id == member.id)
        )
        if (created_at := as_utc(attempt.created_at))
        and active_from <= created_at < week_end_at
    )
    contribution = lessons * CIRCLE_LESSON_POINTS + reviews * CIRCLE_REVIEW_POINTS
    return {
        "lessons": lessons,
        "reviews": reviews,
        "contribution": contribution,
    }


def _circle_payload(
    db: OrmSession,
    circle: Circle,
) -> dict:
    rows = db.execute(
        select(CircleMember, User)
        .join(User, User.id == CircleMember.user_id)
        .where(CircleMember.circle_id == circle.id)
        .order_by(CircleMember.joined_at, CircleMember.id)
    ).all()
    # A circle has one shared week regardless of which member/timezone views it.
    week_start = _week_start(utcnow().date())
    members = []
    for membership, member in rows:
        contribution = _member_contribution(
            db, member, week_start, membership.joined_at
        )
        members.append(
            {
                "display_name": member.display_name or "Learner",
                "profile_picture": owl_profile_picture(member),
                **contribution,
            }
        )
    weekly_progress = sum(member["contribution"] for member in members)
    return {
        "id": circle.id,
        "name": circle.name,
        "invite_code": circle.invite_code,
        "member_count": len(members),
        "weekly_progress": weekly_progress,
        "weekly_goal": circle.weekly_goal,
        "complete": weekly_progress >= circle.weekly_goal,
        "members": members,
    }


def _circles_payload(
    db: OrmSession,
    user: User,
) -> list[dict]:
    circles = db.scalars(
        select(Circle)
        .join(CircleMember, CircleMember.circle_id == Circle.id)
        .where(CircleMember.user_id == user.id)
        .order_by(Circle.created_at, Circle.id)
    ).all()
    return [_circle_payload(db, circle) for circle in circles]


def _growth_payload(db: OrmSession, user: User) -> dict:
    today = user_today(user)
    return {
        "wallet": wallet(user),
        "constellation": _constellation_payload(db, user, today),
        "review": _review_queue(db, user, today),
        "missions": _missions_payload(db, user, today),
        "boss": _boss_payload(db, user, today),
        "workshop": _workshop_payload(db, user),
        "circles": _circles_payload(db, user),
    }


@router.get("")
def growth(
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    """One side-effect-free payload for the richer Progress experience."""
    return _growth_payload(db, user)


@router.post("/review/answer")
def review_answer(
    body: ReviewAnswer,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    lesson = db.get(Lesson, body.lesson_id)
    if lesson is None or lesson.user_id != user.id or not lesson.completed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such completed lesson.")
    questions = _questions(lesson)
    if body.question_index >= len(questions):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such question.")
    question = questions[body.question_index]
    options = question.get("options") or []
    answer = question.get("answer_index")
    if (
        not isinstance(options, list)
        or not options
        or not isinstance(answer, int)
        or not 0 <= answer < len(options)
        or body.answer_index >= len(options)
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That question cannot be graded.")

    today = user_today(user)
    existing_attempt = db.scalar(
        select(ReviewAttempt).where(
            ReviewAttempt.user_id == user.id,
            ReviewAttempt.lesson_id == lesson.id,
            ReviewAttempt.question_index == body.question_index,
            ReviewAttempt.review_day == today,
        )
    )
    if existing_attempt is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That question has already been reviewed today.",
        )

    progress = db.scalar(
        select(ReviewProgress).where(
            ReviewProgress.user_id == user.id,
            ReviewProgress.lesson_id == lesson.id,
            ReviewProgress.question_index == body.question_index,
        )
    )
    if progress is None:
        completed_day = user_day(user, lesson.completed_at or lesson.created_at)
        due_day = completed_day
        if due_day > today:
            raise HTTPException(status.HTTP_409_CONFLICT, "That question is not due yet.")
        progress = ReviewProgress(
            user_id=user.id,
            lesson_id=lesson.id,
            question_index=body.question_index,
            due_day=due_day,
            interval_days=1,
        )
        db.add(progress)
    elif progress.due_day > today:
        raise HTTPException(status.HTTP_409_CONFLICT, "That question is not due yet.")

    correct = body.answer_index == answer
    reward = REVIEW_CORRECT_REWARD if correct else REVIEW_PARTICIPATION_REWARD
    attempt = ReviewAttempt(
        user_id=user.id,
        lesson_id=lesson.id,
        question_index=body.question_index,
        review_day=today,
        answer_index=body.answer_index,
        correct=correct,
        reward=reward,
    )
    db.add(attempt)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That question has already been reviewed today.",
        ) from exc

    if correct:
        interval = 2 if progress.repetitions == 0 else min(
            60, max(2, int(progress.interval_days or 1) * 2)
        )
        progress.repetitions = int(progress.repetitions or 0) + 1
    else:
        interval = 1
        progress.repetitions = 0
    progress.interval_days = interval
    progress.due_day = today + timedelta(days=interval)
    progress.last_reviewed_day = today
    progress.last_correct = correct
    progress.updated_at = utcnow()
    db.execute(
        update(User)
        .where(User.id == user.id)
        .values(coins=func.coalesce(User.coins, STARTER_COINS) + reward)
    )
    db.commit()
    db.refresh(user)
    return {
        "lesson_id": lesson.id,
        "question_index": body.question_index,
        "correct": correct,
        "correct_index": answer,
        "explanation": str(question.get("explanation") or ""),
        "reward": reward,
        "next_due_day": progress.due_day.isoformat(),
        "interval_days": progress.interval_days,
        "repetitions": progress.repetitions,
        "wallet": wallet(user),
    }


@router.post("/constellation/visit")
def visit_constellation(
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    today = user_today(user)
    first_visit_today = user.last_constellation_visit != today
    if first_visit_today:
        user.last_constellation_visit = today
        db.commit()
    return {
        "visited_today": True,
        "first_visit_today": first_visit_today,
        "constellation": _constellation_payload(db, user, today),
        "missions": _missions_payload(db, user, today),
    }


@router.post("/missions/claim")
def claim_mission(
    body: MissionClaim,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    if body.key not in MISSION_DEFS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such mission.")
    today = user_today(user)
    mission = next(
        row for row in _missions_payload(db, user, today) if row["key"] == body.key
    )
    if not mission["complete"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "That mission is not complete yet.")

    claim_key = _mission_claim_key(today, body.key)
    existing = db.scalar(
        select(RewardClaim).where(
            RewardClaim.user_id == user.id,
            RewardClaim.key == claim_key,
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That mission was already claimed.")

    claim = RewardClaim(
        user_id=user.id,
        key=claim_key,
        reward=int(mission["reward"]),
    )
    db.add(claim)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That mission was already claimed.",
        )
    db.execute(
        update(User)
        .where(User.id == user.id)
        .values(coins=func.coalesce(User.coins, STARTER_COINS) + claim.reward)
    )
    db.commit()
    db.refresh(user)
    return {
        "key": body.key,
        "claimed": True,
        "already_claimed": False,
        "reward": claim.reward,
        "wallet": wallet(user),
    }


@router.post("/boss/answer")
def answer_boss(
    body: BossAnswer,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    today = user_today(user)
    scenario = _boss_scenario(db, user, today)
    if scenario is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Complete two different lesson topics to unlock the weekly scenario.",
        )
    if body.answer_index >= len(scenario["options"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No such answer option.")
    if body.scenario_key != scenario["scenario_key"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This scenario changed after new learning. Refresh it before answering.",
        )
    week_key = scenario["week_key"]
    existing = db.scalar(
        select(BossAttempt).where(
            BossAttempt.user_id == user.id,
            BossAttempt.week_key == week_key,
        )
    )
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This week's scenario has already been attempted.",
        )

    correct = body.answer_index == scenario["correct_index"]
    reward = BOSS_CORRECT_REWARD if correct else BOSS_PARTICIPATION_REWARD
    attempt = BossAttempt(
        user_id=user.id,
        week_key=week_key,
        scenario_key=scenario["scenario_key"],
        scenario_json=json.dumps(
            {
                "week_key": scenario["week_key"],
                "scenario_key": scenario["scenario_key"],
                "topics": scenario["topics"],
                "prompt": scenario["prompt"],
                "options": scenario["options"],
            }
        ),
        answer_index=body.answer_index,
        correct=correct,
        reward=reward,
    )
    db.add(attempt)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This week's scenario has already been attempted.",
        )
    db.execute(
        update(User)
        .where(User.id == user.id)
        .values(coins=func.coalesce(User.coins, STARTER_COINS) + reward)
    )
    db.commit()
    db.refresh(user)
    return {
        "week_key": week_key,
        "scenario_key": scenario["scenario_key"],
        "attempted": True,
        "already_attempted": False,
        "correct": correct,
        "reward": reward,
        "wallet": wallet(user),
    }


@router.post("/workshop/purchase")
def purchase_workshop_item(
    body: WorkshopItemRequest,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    item = WORKSHOP_BY_KEY.get(body.item_key)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such workshop item.")
    existing = db.scalar(
        select(CosmeticUnlock).where(
            CosmeticUnlock.user_id == user.id,
            CosmeticUnlock.item_key == body.item_key,
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already own that item.")

    unlock = CosmeticUnlock(user_id=user.id, item_key=body.item_key)
    db.add(unlock)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "You already own that item.")
    spent = db.execute(
        update(User)
        .where(
            User.id == user.id,
            func.coalesce(User.coins, STARTER_COINS) >= item["price"],
        )
        .values(coins=func.coalesce(User.coins, STARTER_COINS) - item["price"])
    )
    if spent.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"You need {item['price']} coins for that.",
        )
    auto_equipped = item["slot"] == "owl_accessory"
    if auto_equipped:
        user.equipped_owl_accessory = body.item_key
    db.commit()
    db.refresh(user)
    return {
        "item_key": body.item_key,
        "purchased": True,
        "owned": True,
        "already_owned": False,
        "auto_equipped": auto_equipped,
        "wallet": wallet(user),
        "profile_picture": owl_profile_picture(user),
        "workshop": _workshop_payload(db, user),
    }


@router.post("/workshop/equip")
def equip_workshop_item(
    body: WorkshopItemRequest,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    item = WORKSHOP_BY_KEY.get(body.item_key)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such workshop item.")
    owned = db.scalar(
        select(CosmeticUnlock).where(
            CosmeticUnlock.user_id == user.id,
            CosmeticUnlock.item_key == body.item_key,
        )
    )
    if owned is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Buy that item before equipping it.")
    setattr(user, EQUIPPED_FIELDS[item["slot"]], body.item_key)
    db.commit()
    return {
        "item_key": body.item_key,
        "equipped": True,
        "equipped_items": _equipped(user),
        "profile_picture": owl_profile_picture(user),
        "workshop": _workshop_payload(db, user),
    }


@router.post("/workshop/classic")
def equip_classic_owl(
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    """Return to the bundled classic owl without changing item ownership."""

    user.equipped_owl_accessory = None
    db.commit()
    return {
        "classic": True,
        "equipped": True,
        "equipped_items": _equipped(user),
        "profile_picture": owl_profile_picture(user),
        "workshop": _workshop_payload(db, user),
    }


def _new_invite_code(db: OrmSession) -> str:
    for _ in range(8):
        code = secrets.token_urlsafe(7).replace("-", "").replace("_", "")[:10].upper()
        if len(code) >= 6 and db.scalar(select(Circle.id).where(Circle.invite_code == code)) is None:
            return code
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Could not make an invite code. Try again.",
    )


@router.post("/circles", status_code=status.HTTP_201_CREATED)
def create_circle(
    body: CircleCreate,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    circle = Circle(
        name=body.name,
        invite_code=_new_invite_code(db),
        created_by_user_id=user.id,
        weekly_goal=CIRCLE_WEEKLY_GOAL,
    )
    db.add(circle)
    db.flush()
    db.add(CircleMember(circle_id=circle.id, user_id=user.id))
    db.commit()
    db.refresh(circle)
    return _circle_payload(db, circle)


@router.post("/circles/join")
def join_circle(
    body: CircleJoin,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    code = body.invite_code.strip().upper()
    circle = db.scalar(
        select(Circle).where(Circle.invite_code == code).with_for_update()
    )
    if circle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That invite is not valid.")
    existing = db.scalar(
        select(CircleMember).where(
            CircleMember.circle_id == circle.id,
            CircleMember.user_id == user.id,
        )
    )
    if existing is not None:
        return {
            "joined": True,
            "already_member": True,
            "circle": _circle_payload(db, circle),
        }
    member_count = db.scalar(
        select(func.count(CircleMember.id)).where(CircleMember.circle_id == circle.id)
    ) or 0
    if member_count >= CIRCLE_MAX_MEMBERS:
        raise HTTPException(status.HTTP_409_CONFLICT, "That circle is full.")
    db.add(CircleMember(circle_id=circle.id, user_id=user.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return {
        "joined": True,
        "already_member": False,
        "circle": _circle_payload(db, circle),
    }


@router.post("/circles/{circle_id}/leave")
def leave_circle(
    circle_id: int,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    circle = db.scalar(select(Circle).where(Circle.id == circle_id).with_for_update())
    membership = db.scalar(
        select(CircleMember).where(
            CircleMember.circle_id == circle_id,
            CircleMember.user_id == user.id,
        )
    )
    if circle is None or membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such circle.")

    remaining = db.scalars(
        select(CircleMember)
        .where(
            CircleMember.circle_id == circle_id,
            CircleMember.user_id != user.id,
        )
        .order_by(CircleMember.joined_at, CircleMember.id)
    ).all()
    transferred_to = None
    if not remaining:
        db.delete(membership)
        db.delete(circle)
        deleted = True
    else:
        if circle.created_by_user_id == user.id:
            circle.created_by_user_id = remaining[0].user_id
            transferred_to = remaining[0].user_id
        db.delete(membership)
        deleted = False
    db.commit()
    return {
        "left": True,
        "circle_id": circle_id,
        "deleted": deleted,
        "transferred_to_user_id": transferred_to,
    }
