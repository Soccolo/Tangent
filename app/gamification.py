"""The small, deliberately legible economy behind Tangent's game layer.

XP measures long-term progress. Coins are the spendable loop: completing a
lesson earns them, and hints / streak freezes spend them. Keeping those two
currencies separate means buying help never makes somebody lose a level.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from .models import User


STARTER_COINS = 30
STARTER_HINTS = 1

HINT_PRICE = 15
STREAK_FREEZE_PRICE = 90
MAX_STREAK_FREEZES = 2

COIN_COMPLETION_BASE = 10
COIN_PER_CORRECT = 3
COIN_PERFECT_BONUS = 8


def coin_balance(user: "User") -> int:
    """Existing rows receive the starter balance when the new column is NULL."""
    return STARTER_COINS if user.coins is None else max(0, int(user.coins))


def hint_balance(user: "User") -> int:
    """Existing rows receive one sample hint; a genuine zero stays a zero."""
    return STARTER_HINTS if user.hint_tokens is None else max(0, int(user.hint_tokens))


def freeze_balance(user: "User") -> int:
    return max(0, int(user.streak_freezes or 0))


def wallet(user: "User") -> dict:
    return {
        "coins": coin_balance(user),
        "hint_tokens": hint_balance(user),
        "streak_freezes": freeze_balance(user),
    }


def catalog() -> dict:
    return {
        "hint": {
            "price": HINT_PRICE,
            "name": "Question hint",
            "description": "Remove one wrong answer from a question.",
        },
        "streak_freeze": {
            "price": STREAK_FREEZE_PRICE,
            "name": "Streak freeze",
            "description": "Automatically protect one missed day.",
            "max_owned": MAX_STREAK_FREEZES,
        },
    }


def lesson_coin_reward(correct: int, total: int) -> int:
    """Reward finishing first, accuracy second, and a clean sweep a little more."""
    reward = COIN_COMPLETION_BASE + COIN_PER_CORRECT * max(0, correct)
    if total > 0 and correct == total:
        reward += COIN_PERFECT_BONUS
    return reward


def user_today(user: "User", now: datetime | None = None) -> date:
    """The learner's calendar day, falling back safely for legacy profiles."""
    now = now or datetime.now(timezone.utc)
    try:
        zone = ZoneInfo(user.timezone) if user.timezone else None
    except (ZoneInfoNotFoundError, ValueError):
        zone = None
    return now.astimezone(zone).date() if zone else date.today()


def user_day(user: "User", moment: datetime) -> date:
    """Convert a stored UTC timestamp into the learner's local calendar day."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(user.timezone) if user.timezone else None
    except (ZoneInfoNotFoundError, ValueError):
        zone = None
    return moment.astimezone(zone).date() if zone else moment.date()


def streak_status(user: "User", today: date | None = None) -> dict:
    """Describe a streak without mutating it.

    A freeze is settled on the next completed lesson, so somebody returning
    after a missed day can see whether their stocked freeze will save them.
    """
    today = today or user_today(user)
    last = user.last_active_day
    current = max(0, int(user.current_streak or 0))
    freezes = freeze_balance(user)

    if not last or current == 0:
        missed = 0
        active_today = False
    else:
        gap = max(0, (today - last).days)
        missed = max(0, gap - 1)
        active_today = gap == 0

    protected = missed > 0 and freezes >= missed
    expired = missed > 0 and not protected and missed > MAX_STREAK_FREEZES

    return {
        "active_today": active_today,
        "missed_days": missed,
        "protected": protected,
        "at_risk": missed > 0 and not protected and not expired,
        "expired": expired,
    }


def advance_streak(user: "User", today: date | None = None) -> dict:
    """Count today's lesson and consume enough freezes to bridge any gap."""
    today = today or user_today(user)
    last = user.last_active_day
    current = max(0, int(user.current_streak or 0))
    freezes = freeze_balance(user)

    if last == today:
        return {"freezes_used": 0, "streak_saved": False}

    if not last or current == 0:
        user.current_streak = 1
        freezes_used = 0
    else:
        gap = (today - last).days
        if gap <= 0:
            return {"freezes_used": 0, "streak_saved": False}

        missed = max(0, gap - 1)
        if missed == 0:
            user.current_streak = current + 1
            freezes_used = 0
        elif freezes >= missed:
            freezes_used = missed
            user.streak_freezes = freezes - missed
            user.current_streak = current + 1
        else:
            freezes_used = 0
            user.current_streak = 1

    user.last_active_day = today
    user.longest_streak = max(
        int(user.longest_streak or 0), int(user.current_streak or 0)
    )
    return {
        "freezes_used": freezes_used,
        "streak_saved": freezes_used > 0,
    }
