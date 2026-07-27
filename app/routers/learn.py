import json
import logging
import random
import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from ..config import (
    DAILY_CAPTURE_CAP,
    LIBRARY_REUSE,
    STALE_GENERATION_MINUTES,
    DAILY_DIGEST_CAP,
    DAILY_LESSON_CAP,
    GLOBAL_DAILY_CAP,
)
from ..db import SessionLocal, get_db
from ..llm import (
    LLMError,
    generate_lesson,
    placement_questions,
    shuffle_options,
    suggest_topics,
)
from ..models import Activity, Digest, Generation, Lesson, User, utcnow
from .library import find_match, publish
from ..schemas import ActivityIn, AnswerSet, ChoosePick, PlacementAsk
from ..security import current_user

log = logging.getLogger("tangent.learn")

router = APIRouter(prefix="/api", tags=["learn"])


# --------------------------------------------------------------------------- #
# Spend guards
# --------------------------------------------------------------------------- #


def used_today(db: OrmSession, user_id: int, kind: str) -> int:
    return db.scalar(
        select(func.count(Generation.id)).where(
            Generation.user_id == user_id,
            Generation.day == date.today(),
            Generation.kind == kind,
        )
    ) or 0


def claim_quota(db: OrmSession, user_id: int, kind: str) -> None:
    """Reserve one generation for this user, or raise 429.

    Recorded *before* the Claude call, not after — a crash mid-generation should
    still cost quota, or a failing prompt becomes an unbounded retry loop.
    """
    per_user_cap = {
        "lesson": DAILY_LESSON_CAP,
        "digest": DAILY_DIGEST_CAP,
        "capture": DAILY_CAPTURE_CAP,
        # Two small questions on the cheap model; bounded generously.
        "placement": DAILY_LESSON_CAP * 2,
    }[kind]
    if used_today(db, user_id, kind) >= per_user_cap:
        noun = {
            "lesson": "lessons",
            "digest": "topic refreshes",
            "capture": "recording time",
            "placement": "topic check-ins",
        }[kind]
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You've used today's {noun} ({per_user_cap}). Come back tomorrow — "
            "that's rather the point of a streak.",
        )

    global_today = db.scalar(
        select(func.count(Generation.id)).where(Generation.day == date.today())
    ) or 0
    if global_today >= GLOBAL_DAILY_CAP:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Tangent has hit its daily limit across all users. Try again tomorrow.",
        )

    db.add(Generation(user_id=user_id, day=date.today(), kind=kind))
    db.commit()


# --------------------------------------------------------------------------- #
# Activity log
# --------------------------------------------------------------------------- #


@router.get("/activities")
def list_activities(
    days: int = 1,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    since = date.today() - timedelta(days=max(days, 1) - 1)
    rows = db.scalars(
        select(Activity)
        .where(Activity.user_id == user.id, Activity.day >= since)
        .order_by(Activity.created_at.desc())
    ).all()
    return [
        {"id": a.id, "text": a.text, "day": a.day.isoformat(), "source": a.source}
        for a in rows
    ]


@router.post("/activities", status_code=status.HTTP_201_CREATED)
def add_activity(
    body: ActivityIn,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    activity = Activity(
        user_id=user.id, day=date.today(), text=body.text.strip(), source=body.source
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return {"id": activity.id, "text": activity.text, "day": activity.day.isoformat()}


@router.delete("/activities/{activity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_activity(
    activity_id: int,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    activity = db.get(Activity, activity_id)
    if activity is None or activity.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such activity")
    db.delete(activity)
    db.commit()


# --------------------------------------------------------------------------- #
# Daily digest
# --------------------------------------------------------------------------- #


def _digest_payload(digest: Digest, db: OrmSession) -> dict:
    lessons = db.scalars(
        select(Lesson).where(Lesson.digest_id == digest.id).order_by(Lesson.id)
    ).all()
    return {
        "id": digest.id,
        "day": digest.day.isoformat(),
        "summary": digest.summary,
        "topics": json.loads(digest.topics_json),
        "chosen_index": digest.chosen_index,
        "wildcard_index": digest.wildcard_index,
        "lessons": [
            {
                "id": lesson.id,
                "title": lesson.topic_title,
                "picked_by": lesson.picked_by,
                "completed": lesson.completed,
                "score": lesson.score,
                "total_questions": lesson.total_questions,
                "ready": lesson.status == "ready",
                "status": lesson.status,
            }
            for lesson in lessons
        ],
    }


@router.get("/digest/today")
def get_today_digest(user: User = Depends(current_user), db: OrmSession = Depends(get_db)):
    digest = db.scalar(
        select(Digest).where(Digest.user_id == user.id, Digest.day == date.today())
    )
    if digest is None:
        return {"exists": False}
    return {"exists": True, **_digest_payload(digest, db)}


@router.post("/digest/today")
def build_today_digest(
    refresh: bool = False,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    today = date.today()
    existing = db.scalar(
        select(Digest).where(Digest.user_id == user.id, Digest.day == today)
    )
    if existing is not None and not refresh:
        return {"exists": True, **_digest_payload(existing, db)}

    activities = db.scalars(
        select(Activity)
        .where(Activity.user_id == user.id, Activity.day == today)
        .order_by(Activity.created_at)
    ).all()
    if not activities:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Log at least one thing you worked on today first.",
        )

    claim_quota(db, user.id, "digest")
    try:
        result = suggest_topics(
            user.role,
            [a.text for a in activities],
            user.learning_goals or "",
            user.aim or "",
        )
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    if existing is not None:
        # Refresh replaces the suggestion set; any lessons already generated
        # from the old set stay in history but detach from this digest.
        for lesson in db.scalars(select(Lesson).where(Lesson.digest_id == existing.id)):
            lesson.digest_id = None
        existing.summary = result.get("summary", "")
        existing.topics_json = json.dumps(result["topics"])
        existing.chosen_index = None
        existing.wildcard_index = None
        digest = existing
    else:
        digest = Digest(
            user_id=user.id,
            day=today,
            summary=result.get("summary", ""),
            topics_json=json.dumps(result["topics"]),
        )
        db.add(digest)

    db.commit()
    db.refresh(digest)
    return {"exists": True, **_digest_payload(digest, db)}


def _pick_wildcard(topics: list[dict], chosen: int) -> int:
    """The app's own pick, made after the user's.

    Prefer a different category *and* a different difficulty — the point is to
    widen the day, not to hand over a near-duplicate of what they chose.
    """
    picked = topics[chosen]
    candidates = [i for i in range(len(topics)) if i != chosen]
    if not candidates:
        return chosen

    def rank(i: int) -> tuple[int, int]:
        topic = topics[i]
        return (
            topic.get("category") != picked.get("category"),
            topic.get("difficulty") != picked.get("difficulty"),
        )

    best = max(rank(i) for i in candidates)
    tied = [i for i in candidates if rank(i) == best]
    return random.choice(tied)


def effective_level(self_rated: int, placement: list[dict]) -> int:
    """Reconcile what someone says they know with what they demonstrated.

    People are routinely modest or overconfident about their own level, and two
    questions is a weak but real signal. Getting both right nudges up, both
    wrong nudges down harder — an overestimate produces a lesson pitched over
    someone's head, which is the more damaging failure.
    """
    if not placement:
        return self_rated
    correct = sum(1 for item in placement if item.get("correct"))
    if correct == len(placement):
        return min(10, self_rated + 2)
    if correct == 0:
        return max(1, self_rated - 3)
    return self_rated


@router.post("/digest/today/placement")
def get_placement(
    body: PlacementAsk,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    """Two diagnostic questions for a topic, before committing to a lesson.

    Answers are graded in the browser and reported back. That's deliberate:
    there's nothing to win by cheating a self-assessment, and keeping the
    grading client-side avoids storing per-topic state for a throwaway quiz.
    """
    digest = db.scalar(
        select(Digest).where(Digest.user_id == user.id, Digest.day == date.today())
    )
    if digest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No digest for today yet")
    topics = json.loads(digest.topics_json)
    if body.index >= len(topics):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No topic at that position")

    claim_quota(db, user.id, "placement")
    try:
        questions = placement_questions(user.role, topics[body.index])
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"questions": questions}


@router.post("/digest/today/choose")
def choose_topic(
    body: ChoosePick,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    digest = db.scalar(
        select(Digest).where(Digest.user_id == user.id, Digest.day == date.today())
    )
    if digest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No digest for today yet")
    if digest.chosen_index is not None:
        return _digest_payload(digest, db)

    topics = json.loads(digest.topics_json)
    if body.index >= len(topics):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No topic at that position")

    wildcard = _pick_wildcard(topics, body.index)
    digest.chosen_index = body.index
    digest.wildcard_index = wildcard

    placement = [p.model_dump() for p in body.placement]
    chosen_level = effective_level(body.level, placement)
    # The wildcard is chosen precisely because it sits outside their lane, so
    # their rating for the topic they picked says nothing about it. Assume less
    # knowledge there, never more.
    wildcard_level = min(chosen_level, 3)

    # Remember the rating as the default for next time.
    user.default_level = body.level

    for index, picked_by, level in (
        (body.index, "user", chosen_level),
        (wildcard, "app", wildcard_level),
    ):
        topic = topics[index]
        db.add(
            Lesson(
                user_id=user.id,
                digest_id=digest.id,
                topic_title=topic["title"],
                topic_blurb=topic.get("blurb", ""),
                picked_by=picked_by,
                content_json="",
                level=level,
                placement_json=(
                    json.dumps(placement) if placement and picked_by == "user" else None
                ),
            )
        )
    db.commit()
    db.refresh(digest)
    return _digest_payload(digest, db)


# --------------------------------------------------------------------------- #
# Lessons
# --------------------------------------------------------------------------- #


def _topic_for(lesson: Lesson, db: OrmSession) -> dict:
    if lesson.digest_id is None:
        return {"title": lesson.topic_title, "blurb": lesson.topic_blurb}
    digest = db.get(Digest, lesson.digest_id)
    topics = json.loads(digest.topics_json)
    index = digest.chosen_index if lesson.picked_by == "user" else digest.wildcard_index
    if index is not None and index < len(topics):
        return topics[index]
    return {"title": lesson.topic_title, "blurb": lesson.topic_blurb}


def _generate_lesson_job(
    lesson_id: int,
    role: str,
    topic: dict,
    summary: str,
    level: int | None = None,
    placement: list[dict] | None = None,
) -> None:
    """Runs after the response is sent, on its own DB session."""
    db = SessionLocal()
    try:
        lesson = db.get(Lesson, lesson_id)
        if lesson is None:
            return
        try:
            content = generate_lesson(role, topic, summary, level, placement)
        except Exception as exc:  # noqa: BLE001 - the status field is the report
            log.exception("Lesson %s generation failed", lesson_id)
            lesson.status = "failed"
            lesson.generating_since = None
            lesson.error = str(exc) if isinstance(exc, LLMError) else (
                "Something went wrong writing this lesson."
            )
            db.commit()
            return
        lesson.content_json = json.dumps(content)
        lesson.total_questions = len(content.get("questions", []))
        lesson.status = "ready"
        lesson.generating_since = None
        lesson.error = ""

        # Contribute it so the next person asking this doesn't pay for it again.
        author = db.get(User, lesson.user_id)
        entry = publish(
            db,
            title=lesson.topic_title,
            blurb=lesson.topic_blurb,
            content_json=lesson.content_json,
            author=author,
            category=topic.get("category", "") if isinstance(topic, dict) else "",
            difficulty=topic.get("difficulty", "") if isinstance(topic, dict) else "",
            level=level,
        )
        if entry is not None:
            lesson.library_id = entry.id
        db.commit()
    finally:
        db.close()


@router.get("/lessons/{lesson_id}")
def get_lesson(
    lesson_id: int,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    """Lessons are generated on first open, not at choose-time — the digest
    screen stays instant and we only pay for lessons actually started.

    Generation runs in the background and the client polls: a lesson takes
    30-60s, which is long enough for a platform's proxy to drop a held-open
    request. The client sees `status`, not a spinning connection.
    """
    lesson = db.get(Lesson, lesson_id)
    if lesson is None or lesson.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such lesson")

    if lesson.status == "ready" and lesson.content_json:
        return {
            "id": lesson.id,
            "status": "ready",
            "picked_by": lesson.picked_by,
            "completed": lesson.completed,
            "score": lesson.score,
            "content": json.loads(lesson.content_json),
            "share_token": lesson.share_token,
            "author": lesson.author_name,
        }

    if lesson.status == "generating":
        # A sleeping or restarted instance kills the background task silently,
        # leaving the row stuck on "generating" with nothing coming. Time it out
        # so the user gets a retry instead of an eternal spinner.
        started = lesson.generating_since or lesson.created_at
        age = datetime.utcnow() - started
        if age > timedelta(minutes=STALE_GENERATION_MINUTES):
            log.warning("Lesson %s stuck generating for %s — marking failed", lesson.id, age)
            # Straight back to "pending", not "failed": the next call should
            # start a fresh generation, so the user's retry works on one click.
            lesson.status = "pending"
            lesson.generating_since = None
            db.commit()
            return {
                "id": lesson.id,
                "status": "failed",
                "error": (
                    "That took too long and stalled — the server may have restarted. "
                    "Try again."
                ),
            }
        return {
            "id": lesson.id,
            "status": "generating",
            "picked_by": lesson.picked_by,
            "elapsed_seconds": int(age.total_seconds()),
        }

    if lesson.status == "failed":
        # Let them retry — but a retry costs quota like any other generation.
        lesson.status = "pending"
        error, lesson.error = lesson.error, ""
        db.commit()
        return {"id": lesson.id, "status": "failed", "error": error}

    digest = db.get(Digest, lesson.digest_id) if lesson.digest_id else None
    topic = _topic_for(lesson, db)
    summary = digest.summary if digest else ""

    # Before paying to write this, check whether it already exists. A hit is
    # instant and free — this is what stops cost scaling with users rather than
    # with topics.
    if LIBRARY_REUSE:
        match = find_match(db, lesson.topic_title, lesson.level)
        if match is not None:
            content = shuffle_options(json.loads(match.content_json))
            lesson.content_json = json.dumps(content)
            lesson.total_questions = len(content.get("questions", []))
            lesson.status = "ready"
            lesson.library_id = match.id
            lesson.author_name = lesson.author_name or match.author_name
            match.times_used += 1
            db.commit()
            log.info("library hit for %r (used %s times)", lesson.topic_title, match.times_used)
            return {
                "id": lesson.id,
                "status": "ready",
                "picked_by": lesson.picked_by,
                "completed": lesson.completed,
                "score": lesson.score,
                "content": content,
                "from_library": True,
                "author": lesson.author_name,
            }

    claim_quota(db, user.id, "lesson")
    lesson.status = "generating"
    lesson.generating_since = datetime.utcnow()
    db.commit()

    placement = json.loads(lesson.placement_json) if lesson.placement_json else None
    background.add_task(
        _generate_lesson_job, lesson.id, user.role, topic, summary, lesson.level, placement
    )
    return {"id": lesson.id, "status": "generating", "picked_by": lesson.picked_by}


def _bump_streak(user: User) -> None:
    today = date.today()
    if user.last_active_day == today:
        return
    if user.last_active_day == today - timedelta(days=1):
        user.current_streak += 1
    else:
        user.current_streak = 1
    user.last_active_day = today
    user.longest_streak = max(user.longest_streak, user.current_streak)


@router.post("/lessons/{lesson_id}/complete")
def complete_lesson(
    lesson_id: int,
    body: AnswerSet,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    lesson = db.get(Lesson, lesson_id)
    if lesson is None or lesson.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such lesson")
    if not lesson.content_json:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Lesson hasn't been generated")

    questions = json.loads(lesson.content_json).get("questions", [])
    correct = sum(
        1
        for i, q in enumerate(questions)
        if i < len(body.answers) and body.answers[i] == q["answer_index"]
    )

    already_done = lesson.completed
    lesson.score = correct
    lesson.total_questions = len(questions)
    lesson.completed = True
    lesson.completed_at = utcnow()

    if not already_done:
        # Finishing is worth something; getting it right is worth more.
        xp = 10 + 5 * correct
        if questions and correct == len(questions):
            xp += 15  # perfect-round bonus
        lesson.xp_awarded = xp
        user.xp += xp
        _bump_streak(user)

    db.commit()
    return {
        "score": correct,
        "total": len(questions),
        "xp_awarded": lesson.xp_awarded,
        "already_completed": already_done,
        "xp": user.xp,
        "level": user.xp // 100 + 1,
        "xp_into_level": user.xp % 100,
        "current_streak": user.current_streak,
        "longest_streak": user.longest_streak,
    }


# --------------------------------------------------------------------------- #
# Sharing
# --------------------------------------------------------------------------- #


@router.post("/lessons/{lesson_id}/share")
def share_lesson(
    lesson_id: int,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    """Mint an unguessable link. Anyone holding it can read the lesson — that's
    the point — so it's opt-in per lesson and revocable."""
    lesson = db.get(Lesson, lesson_id)
    if lesson is None or lesson.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such lesson")
    if lesson.status != "ready":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Only a finished lesson can be shared."
        )
    if not lesson.share_token:
        lesson.share_token = secrets.token_urlsafe(24)
        lesson.author_name = lesson.author_name or user.display_name or "someone"
        db.commit()
    return {"token": lesson.share_token, "path": f"/s/{lesson.share_token}"}


@router.delete("/lessons/{lesson_id}/share", status_code=status.HTTP_204_NO_CONTENT)
def unshare_lesson(
    lesson_id: int,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    lesson = db.get(Lesson, lesson_id)
    if lesson is None or lesson.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such lesson")
    lesson.share_token = None
    db.commit()


@router.get("/shared/{token}")
def view_shared(
    token: str,
    db: OrmSession = Depends(get_db),
):
    """Deliberately unauthenticated: a shared link has to work for someone who
    doesn't have an account yet, or it isn't shareable."""
    lesson = db.scalar(select(Lesson).where(Lesson.share_token == token))
    if lesson is None or lesson.status != "ready":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link isn't valid any more.")
    author = db.get(User, lesson.user_id)
    return {
        "token": token,
        "title": lesson.topic_title,
        "blurb": lesson.topic_blurb,
        "author": lesson.author_name or (author.display_name if author else "someone"),
        "author_avatar": author.avatar if author else None,
        "content": json.loads(lesson.content_json),
    }


@router.post("/shared/{token}/add")
def add_shared(
    token: str,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    """Copy a shared lesson into my account so I can play it and earn XP.

    No quota is claimed: the content already exists, so this costs nothing to
    Anthropic. That's the nice property of sharing — the second reader is free.
    """
    original = db.scalar(select(Lesson).where(Lesson.share_token == token))
    if original is None or original.status != "ready":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link isn't valid any more.")
    if original.user_id == user.id:
        return {"id": original.id, "already_mine": True}

    existing = db.scalar(
        select(Lesson).where(
            Lesson.user_id == user.id, Lesson.shared_from_id == original.id
        )
    )
    if existing is not None:
        return {"id": existing.id, "already_added": True}

    author = db.get(User, original.user_id)
    copy = Lesson(
        user_id=user.id,
        digest_id=None,
        topic_title=original.topic_title,
        topic_blurb=original.topic_blurb,
        picked_by="shared",
        content_json=original.content_json,
        status="ready",
        total_questions=original.total_questions,
        shared_from_id=original.id,
        author_name=original.author_name
        or (author.display_name if author else "someone"),
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
    return {"id": copy.id, "added": True}


# Deliberately not "/lessons/saved": that would be shadowed by the
# "/lessons/{lesson_id}" route declared above it and 422 on the int parse.
@router.get("/saved-lessons")
def saved_lessons(user: User = Depends(current_user), db: OrmSession = Depends(get_db)):
    """Lessons not attached to a day's digest — shared ones people added."""
    rows = db.scalars(
        select(Lesson)
        .where(
            Lesson.user_id == user.id,
            Lesson.digest_id.is_(None),
            Lesson.status == "ready",
        )
        .order_by(Lesson.created_at.desc())
    ).all()
    return [
        {
            "id": lesson.id,
            "title": lesson.topic_title,
            "author": lesson.author_name,
            "picked_by": lesson.picked_by,
            "completed": lesson.completed,
            "score": lesson.score,
            "total_questions": lesson.total_questions,
        }
        for lesson in rows
    ]


@router.get("/progress")
def progress(user: User = Depends(current_user), db: OrmSession = Depends(get_db)):
    lessons = db.scalars(
        select(Lesson)
        .where(Lesson.user_id == user.id, Lesson.completed.is_(True))
        .order_by(Lesson.completed_at.desc())
    ).all()
    days = sorted({lesson.completed_at.date() for lesson in lessons if lesson.completed_at})
    return {
        "xp": user.xp,
        "level": user.xp // 100 + 1,
        "xp_into_level": user.xp % 100,
        "current_streak": user.current_streak,
        "longest_streak": user.longest_streak,
        "lessons_completed": len(lessons),
        "active_days": [d.isoformat() for d in days],
        "history": [
            {
                "id": lesson.id,
                "title": lesson.topic_title,
                "picked_by": lesson.picked_by,
                "author": lesson.author_name,
                "share_token": lesson.share_token,
                "score": lesson.score,
                "total": lesson.total_questions,
                "xp": lesson.xp_awarded,
                "completed_at": lesson.completed_at.isoformat() if lesson.completed_at else None,
            }
            for lesson in lessons[:50]
        ],
    }
