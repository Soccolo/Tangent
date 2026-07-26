from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    # Free text: "pricing actuary, financial lines, UK market". Used to steer
    # what counts as "adjacent" rather than "already known".
    role: Mapped[str] = mapped_column(Text, default="")
    # Avatars are stored as a resized data: URL rather than a file, because the
    # free hosting tier has no persistent disk — anything written to disk is
    # gone on the next deploy, while this rides along in Postgres.
    avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    accent: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Whether lessons generated for this user join the shared library. Topics
    # are subject matter ("limitation periods"), never the activity log that
    # produced them — but it's still the user's call.
    contribute_to_library: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # "system" | "light" | "dark". Null means system.
    theme: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Default self-rated knowledge, 1-10, used to pre-set the per-lesson slider.
    default_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    xp: Mapped[int] = mapped_column(Integer, default=0)
    current_streak: Mapped[int] = mapped_column(Integer, default=0)
    longest_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_active_day: Mapped[date | None] = mapped_column(Date, nullable=True)

    activities: Mapped[list["Activity"]] = relationship(back_populates="user")
    lessons: Mapped[list["Lesson"]] = relationship(back_populates="user")


class Session(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Nullable so rows created before this column existed stay valid; they're
    # treated as expiring SESSION_DAYS after creation instead.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PasswordReset(Base):
    __tablename__ = "password_resets"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LibraryLesson(Base):
    """A generated lesson kept for reuse by anyone who picks the same topic.

    This is the economics of the product: generation is the dominant cost and
    it's per-lesson, not per-user. Two people asking about limitation periods
    should cost one generation, not two.
    """

    __tablename__ = "library_lessons"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Normalised title — the lookup key for "have we already written this?"
    topic_key: Mapped[str] = mapped_column(String(255), index=True)
    # Lessons differ by assumed prior knowledge, so the cache key does too —
    # but banded, not per-level: ten bands would cut the hit rate tenfold and
    # undo the point of having a library.
    level_band: Mapped[str | None] = mapped_column(String(16), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    blurb: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(32), default="")
    difficulty: Mapped[str] = mapped_column(String(16), default="")
    content_json: Mapped[str] = mapped_column(Text)
    author_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    times_used: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Activity(Base):
    """One thing the person worked on / searched for."""

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="activities")


class Observation(Base):
    """Something the capture loop thinks you were doing, awaiting your yes/no.

    Only the derived text lives here. Frames are never written to disk or to
    this table — they exist in browser memory, go to the model, and are dropped.
    """

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    activity: Mapped[str] = mapped_column(Text)          # the proposed log line
    evidence: Mapped[str] = mapped_column(Text, default="")  # why it thinks so
    confidence: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|confirmed|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Digest(Base):
    """The end-of-day suggestion set for one user on one day."""

    __tablename__ = "digests"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_digest_user_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    topics_json: Mapped[str] = mapped_column(Text)  # list of topic dicts
    chosen_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wildcard_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Generation(Base):
    """One billable Claude call. The spend guard counts rows in here rather than
    inferring from lessons, so a failed or abandoned generation still costs the
    user their quota — otherwise retry-spamming a failure is free."""

    __tablename__ = "generations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # digest | lesson
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    digest_id: Mapped[int | None] = mapped_column(ForeignKey("digests.id"), nullable=True)
    topic_title: Mapped[str] = mapped_column(String(255))
    topic_blurb: Mapped[str] = mapped_column(Text, default="")
    picked_by: Mapped[str] = mapped_column(String(16), default="user")  # user | app
    content_json: Mapped[str] = mapped_column(Text)
    # pending | generating | ready | failed. Generation runs in the background so
    # the HTTP request doesn't sit open for a minute behind a platform proxy.
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str] = mapped_column(Text, default="")
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[int] = mapped_column(Integer, default=0)
    total_questions: Mapped[int] = mapped_column(Integer, default=0)
    xp_awarded: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Sharing: an unguessable token makes this lesson readable at /s/<token>.
    # Null means private. Copies keep a pointer back to the original so the
    # author's name can be credited without duplicating user rows.
    share_token: Mapped[str | None] = mapped_column(String(48), unique=True, nullable=True)
    shared_from_id: Mapped[int | None] = mapped_column(ForeignKey("lessons.id"), nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    library_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_lessons.id"), nullable=True
    )
    # What the learner said they already knew, 1-10, at the moment they picked.
    level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # When generation started, so a run killed by a restart or a sleeping
    # instance can be spotted and retried instead of hanging on "generating".
    generating_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Their answers to the two diagnostic questions, if they took them.
    placement_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="lessons")
