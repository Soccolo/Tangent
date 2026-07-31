"""Seed a fully-formed day for one user — no API key, no Claude calls.

Useful for demoing the lesson player, tuning the design, or letting someone try
the app before you hand over a key.

    python seed_demo.py you@example.com
"""

import json
import sys
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, or_, select

from app.db import Base, SessionLocal, engine, ensure_schema
from app.models import (
    BossAttempt,
    HintUse,
    Lesson,
    ReviewAttempt,
    ReviewProgress,
    RewardClaim,
    Activity,
    Digest,
    User,
)
from app.security import hash_password

TOPICS = [
    {
        "title": "Limitation periods in solicitors' negligence",
        "blurb": "When the clock starts, when it stops, and the s.14A extension that "
                 "decides whether a claim is live at all.",
        "why_now": "Your PI claim today turned on a missed limitation date.",
        "category": "regulatory",
        "difficulty": "working",
    },
    {
        "title": "How a solicitors' PI underwriter reads the same file",
        "blurb": "Minimum terms, aggregation clauses, and why the underwriter's "
                 "worry list differs from the pricing actuary's.",
        "why_now": "You priced the claim; this is the seat across the table.",
        "category": "commercial",
        "difficulty": "intro",
    },
    {
        "title": "Gradient boosting on sparse claims data",
        "blurb": "Why GBMs beat a GLM when exposure is thin and interactions matter, "
                 "and where they quietly overfit.",
        "why_now": "Your reserve sat on a judgement call a model could inform.",
        "category": "technical",
        "difficulty": "advanced",
    },
    {
        "title": "Aggregation: one claim or many?",
        "blurb": "The clause that decides whether a firm's related errors share one "
                 "limit or several — worth more than most rating factors.",
        "why_now": "A £1.2m reserve moves a lot depending on how it aggregates.",
        "category": "domain",
        "difficulty": "working",
    },
    {
        "title": "Large loss development and tail factors",
        "blurb": "Why large claims develop differently and what happens to your "
                 "reserve when you apply the wrong tail.",
        "why_now": "£1.2m is a large loss on most solicitors' books.",
        "category": "domain",
        "difficulty": "advanced",
    },
    {
        "title": "LLMs reading claim files",
        "blurb": "What current models can and can't extract from unstructured claim "
                 "notes, and how teams are validating the output.",
        "why_now": "The file you priced was mostly prose, not fields.",
        "category": "frontier",
        "difficulty": "intro",
    },
]

LESSON = {
    "title": "Limitation periods in solicitors' negligence",
    "subtitle": "When the clock starts, and when it doesn't",
    "estimated_minutes": 9,
    "cards": [
        {
            "heading": "The primary period",
            "body": "A negligence claim against a solicitor is a tort claim, so the "
                    "**primary limitation period is six years** from the date the "
                    "cause of action accrued. The critical word is *accrued* — not "
                    "when the client noticed, not when they complained, but when "
                    "damage was first suffered. In a missed-deadline case that is "
                    "usually the moment the underlying claim became unarguable.",
            "intuition": "The clock starts when the harm lands, not when anyone notices "
                         "it — like rot in a beam. The building is already weaker the "
                         "day it starts; the survey years later just tells you.",
            "diagram_caption": "Note the gap: the clock is already running before "
                               "anyone involved knows there's a problem.",
            "key_terms": [
                {"term": "Accrual", "definition": "The moment damage is first suffered — the clock's start."},
                {"term": "Primary period", "definition": "Six years from accrual for tort claims."},
            ],
            "diagram_svg": """<svg viewBox="0 0 640 320" xmlns="http://www.w3.org/2000/svg">
  <line x1="60" y1="180" x2="580" y2="180" stroke="currentColor" stroke-width="2"/>
  <circle cx="110" cy="180" r="7" fill="currentColor"/>
  <circle cx="330" cy="180" r="7" fill="currentColor"/>
  <circle cx="540" cy="180" r="7" fill="currentColor"/>
  <text x="110" y="150" text-anchor="middle" font-size="15" fill="currentColor">Retainer</text>
  <text x="330" y="150" text-anchor="middle" font-size="15" fill="currentColor">Damage accrues</text>
  <text x="540" y="150" text-anchor="middle" font-size="15" fill="currentColor">Claim time-barred</text>
  <line x1="330" y1="200" x2="540" y2="200" stroke="currentColor" stroke-width="2" stroke-dasharray="6 5"/>
  <text x="435" y="230" text-anchor="middle" font-size="16" fill="currentColor">6 years</text>
</svg>""",
        },
        {
            "heading": "The s.14A extension",
            "body": "Section 14A of the Limitation Act 1980 gives a claimant **three "
                    "years from the date of knowledge** where that falls later than "
                    "the six-year point. Knowledge means knowing the damage was "
                    "attributable to the act or omission — not knowing it was legally "
                    "negligent. This is the provision that keeps files alive years "
                    "after a reserving actuary would have closed them.",
            "intuition": "Two clocks run in parallel and the claimant gets whichever "
                         "expires later. Reserving on the six-year clock alone is "
                         "reading one of two timers and calling it the deadline.",
            "key_terms": [
                {"term": "Date of knowledge", "definition": "When the claimant knew the damage was attributable to the act."},
                {"term": "Long-stop", "definition": "s.14B caps everything at 15 years from the negligent act."},
            ],
            "diagram_caption": "Whichever bar ends further right is the real deadline — "
                               "here, the knowledge clock wins by four years.",
            "diagram_svg": """<svg viewBox="0 0 640 360" xmlns="http://www.w3.org/2000/svg">
  <text x="20" y="40" font-size="15" fill="currentColor">Two clocks, whichever ends later</text>
  <line x1="120" y1="300" x2="600" y2="300" stroke="currentColor" stroke-width="1.5"/>
  <g font-size="13" fill="currentColor">
    <text x="120" y="325" text-anchor="middle">yr 0</text>
    <text x="300" y="325" text-anchor="middle">yr 6</text>
    <text x="480" y="325" text-anchor="middle">yr 12</text>
    <text x="112" y="105" text-anchor="end">s.2</text>
    <text x="112" y="175" text-anchor="end">s.14A</text>
    <text x="112" y="245" text-anchor="end">s.14B</text>
  </g>
  <rect x="120" y="85" width="180" height="26" rx="6" fill="currentColor" opacity=".35"/>
  <text x="310" y="103" font-size="13" fill="currentColor">6 yrs from damage</text>
  <rect x="330" y="155" width="180" height="26" rx="6" fill="currentColor" opacity=".6"/>
  <text x="520" y="173" font-size="13" fill="currentColor">3 yrs from knowledge</text>
  <rect x="120" y="225" width="450" height="26" rx="6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-dasharray="5 4"/>
  <text x="345" y="243" font-size="13" text-anchor="middle" fill="currentColor">15-yr long-stop — hard backstop</text>
  <line x1="510" y1="140" x2="510" y2="300" stroke="currentColor" stroke-width="1.5" stroke-dasharray="4 4"/>
  <text x="510" y="60" font-size="14" text-anchor="middle" fill="currentColor">actual deadline</text>
  <line x1="510" y1="70" x2="510" y2="150" stroke="currentColor" stroke-width="1.5"/>
</svg>""",
        },
    ],
    "questions": [
        {
            "kind": "mcq",
            "prompt": "When does the primary limitation period start running?",
            "options": [
                "When the client first instructs the solicitor",
                "When damage is first suffered",
                "When the client discovers the error",
                "When proceedings are issued",
            ],
            "answer_index": 1,
            "explanation": "Accrual is damage, not instruction or discovery. Discovery "
                           "matters only via the separate s.14A route.",
        },
        {
            "kind": "true_false",
            "prompt": "s.14A knowledge requires the claimant to know the conduct was negligent in law.",
            "options": ["True", "False"],
            "answer_index": 1,
            "explanation": "Knowledge is factual, not legal. Knowing the damage is "
                           "attributable to the act is enough — which is why the "
                           "extension bites more often than firms expect.",
        },
        {
            "kind": "fill_blank",
            "prompt": "The s.14B long-stop bars claims _____ years after the negligent act, regardless of knowledge.",
            "options": ["6", "10", "15", "21"],
            "answer_index": 2,
            "explanation": "Fifteen years. It's the only hard backstop — everything "
                           "else can be extended by late knowledge.",
        },
    ],
}


def main() -> None:
    email = (sys.argv[1] if len(sys.argv) > 1 else "demo@tangent.local").lower()
    Base.metadata.create_all(engine)
    ensure_schema()
    db = SessionLocal()

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email,
            password_hash=hash_password("demodemo"),
            display_name="Demo",
            role="Pricing actuary, financial lines, UK market. PI and D&O case pricing.",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"Created {email} with password 'demodemo'")

    # Enough inventory to try every part of the reward loop during a demo.
    user.coins = 150
    user.hint_tokens = 2
    user.streak_freezes = 1
    user.timezone = user.timezone or "Europe/London"
    user.xp = max(int(user.xp or 0), 180)
    user.last_constellation_visit = None

    today = date.today()
    if not db.scalar(
        select(Activity).where(Activity.user_id == user.id, Activity.day == today)
    ):
        db.add(Activity(
            user_id=user.id, day=today,
            text="Priced a solicitors' PI claim — missed limitation date, £1.2m reserve",
        ))

    digest = db.scalar(select(Digest).where(Digest.user_id == user.id, Digest.day == today))
    if digest is None:
        digest = Digest(user_id=user.id, day=today, topics_json="")
        db.add(digest)
    digest.summary = "A day on solicitors' PI — one large claim turning on limitation."
    digest.topics_json = json.dumps(TOPICS)
    digest.chosen_index = 0
    digest.wildcard_index = 2
    db.commit()
    db.refresh(digest)

    old_lessons = db.scalars(
        select(Lesson).where(
            or_(
                Lesson.digest_id == digest.id,
                (Lesson.user_id == user.id) & (Lesson.picked_by == "demo"),
            )
        )
    ).all()
    old_ids = [lesson.id for lesson in old_lessons]
    if old_ids:
        db.execute(delete(HintUse).where(HintUse.lesson_id.in_(old_ids)))
        db.execute(delete(ReviewAttempt).where(ReviewAttempt.lesson_id.in_(old_ids)))
        db.execute(delete(ReviewProgress).where(ReviewProgress.lesson_id.in_(old_ids)))
    for lesson in old_lessons:
        db.delete(lesson)
    db.execute(
        delete(RewardClaim).where(
            RewardClaim.user_id == user.id,
            RewardClaim.key.like(f"mission:{today.isoformat()}:%"),
        )
    )
    week_key = (today - timedelta(days=today.weekday())).isoformat()
    db.execute(
        delete(BossAttempt).where(
            BossAttempt.user_id == user.id,
            BossAttempt.week_key == week_key,
        )
    )
    db.commit()

    db.add(Lesson(
        user_id=user.id, digest_id=digest.id,
        topic_title=TOPICS[0]["title"], topic_blurb=TOPICS[0]["blurb"],
        category=TOPICS[0]["category"],
        picked_by="user", content_json=json.dumps(LESSON),
        total_questions=len(LESSON["questions"]), status="ready",
    ))
    # The app's own pick stays ungenerated — opening it exercises the real
    # Claude path once a key is configured.
    db.add(Lesson(
        user_id=user.id, digest_id=digest.id,
        topic_title=TOPICS[2]["title"], topic_blurb=TOPICS[2]["blurb"],
        category=TOPICS[2]["category"],
        picked_by="app", content_json="", status="pending",
    ))

    # Two historical completions unlock the weekly scenario; three questions
    # from one are due now so the three-minute review and mission are demoable.
    now = datetime.now(timezone.utc)
    history = []
    for offset, topic_index in ((2, 1), (4, 3)):
        topic = TOPICS[topic_index]
        content = dict(LESSON)
        content["title"] = topic["title"]
        lesson = Lesson(
            user_id=user.id,
            digest_id=None,
            topic_title=topic["title"],
            topic_blurb=topic["blurb"],
            category=topic["category"],
            picked_by="demo",
            content_json=json.dumps(content),
            status="ready",
            completed=True,
            score=2,
            total_questions=len(LESSON["questions"]),
            xp_awarded=25,
            coins_awarded=16,
            created_at=now - timedelta(days=offset),
            completed_at=now - timedelta(days=offset),
        )
        db.add(lesson)
        history.append(lesson)
    db.flush()
    for question_index in range(min(3, len(LESSON["questions"]))):
        db.add(
            ReviewProgress(
                user_id=user.id,
                lesson_id=history[0].id,
                question_index=question_index,
                due_day=today,
                interval_days=1,
            )
        )
    db.commit()
    print(f"Seeded today's digest and lessons for {email}")


if __name__ == "__main__":
    main()
