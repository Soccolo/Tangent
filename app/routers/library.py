"""The shared lesson library.

Generation is the dominant cost and it is per *lesson*, not per user. Two
people who pick "limitation periods in solicitors' negligence" should cost one
generation between them, not two. Everything here exists to make that true:
lessons are written once, kept, and handed to whoever asks next.
"""

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as OrmSession

from ..db import get_db
from ..models import Lesson, LibraryLesson, User
from ..security import current_user

router = APIRouter(prefix="/api/library", tags=["library"])


def topic_key(title: str) -> str:
    """Normalise a topic title into a lookup key.

    Deliberately blunt — lowercase, strip punctuation and filler words, collapse
    whitespace. It catches "Limitation periods in solicitors' negligence" vs
    "Limitation Periods In Solicitors Negligence", which is the common case. It
    will not catch genuine paraphrases; that needs embeddings, and this is the
    version worth having before there's usage to tune against.
    """
    text = (title or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    words = [w for w in text.split() if w not in {"a", "an", "the", "in", "of", "for", "on", "to"}]
    return " ".join(words)[:255]


def find_match(db: OrmSession, title: str) -> LibraryLesson | None:
    key = topic_key(title)
    if not key:
        return None
    return db.scalar(
        select(LibraryLesson)
        .where(LibraryLesson.topic_key == key)
        .order_by(LibraryLesson.times_used.desc())
        .limit(1)
    )


def publish(
    db: OrmSession,
    *,
    title: str,
    blurb: str,
    content_json: str,
    author: User | None,
    category: str = "",
    difficulty: str = "",
) -> LibraryLesson | None:
    """Add a freshly generated lesson to the library, unless the author opted out."""
    if author is not None and author.contribute_to_library is False:
        return None
    key = topic_key(title)
    if not key:
        return None
    if db.scalar(select(LibraryLesson).where(LibraryLesson.topic_key == key)):
        return None  # already written; don't accumulate near-duplicates
    entry = LibraryLesson(
        topic_key=key,
        title=title,
        blurb=blurb or "",
        category=category,
        difficulty=difficulty,
        content_json=content_json,
        author_user_id=author.id if author else None,
        author_name=(author.display_name if author else None),
    )
    db.add(entry)
    db.flush()
    return entry


def _serialize(entry: LibraryLesson, mine: bool = False) -> dict:
    content = json.loads(entry.content_json)
    return {
        "id": entry.id,
        "title": entry.title,
        "blurb": entry.blurb,
        "category": entry.category,
        "difficulty": entry.difficulty,
        "author": entry.author_name,
        "times_used": entry.times_used,
        "cards": len(content.get("cards", [])),
        "questions": len(content.get("questions", [])),
        "minutes": content.get("estimated_minutes"),
        "already_added": mine,
    }


@router.get("")
def browse(
    q: str = Query(default="", max_length=120),
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    query = select(LibraryLesson)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(LibraryLesson.title).like(like),
                func.lower(LibraryLesson.blurb).like(like),
            )
        )
    entries = db.scalars(
        query.order_by(LibraryLesson.times_used.desc(), LibraryLesson.created_at.desc()).limit(60)
    ).all()

    mine = {
        row
        for row in db.scalars(
            select(Lesson.library_id).where(
                Lesson.user_id == user.id, Lesson.library_id.is_not(None)
            )
        )
    }
    return {
        "lessons": [_serialize(e, e.id in mine) for e in entries],
        "total": db.scalar(select(func.count(LibraryLesson.id))) or 0,
    }


@router.post("/{entry_id}/add")
def add_to_my_lessons(
    entry_id: int,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    """Take a library lesson. Costs nothing — it's already written."""
    entry = db.get(LibraryLesson, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such lesson")

    existing = db.scalar(
        select(Lesson).where(Lesson.user_id == user.id, Lesson.library_id == entry.id)
    )
    if existing is not None:
        return {"id": existing.id, "already_added": True}

    content = json.loads(entry.content_json)
    lesson = Lesson(
        user_id=user.id,
        topic_title=entry.title,
        topic_blurb=entry.blurb,
        picked_by="library",
        content_json=entry.content_json,
        status="ready",
        total_questions=len(content.get("questions", [])),
        library_id=entry.id,
        author_name=entry.author_name,
    )
    entry.times_used += 1
    db.add(lesson)
    db.commit()
    db.refresh(lesson)
    return {"id": lesson.id, "added": True}
