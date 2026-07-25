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

    user: Mapped[User] = relationship(back_populates="lessons")
