import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

_TEST_DIR = Path(tempfile.mkdtemp(prefix="tangent-growth-tests-"))
os.environ.setdefault(
    "TANGENT_DB", f"sqlite:///{(_TEST_DIR / 'test.db').as_posix()}"
)

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import ratelimit
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import CircleMember, Lesson, User


QUESTIONS = [
    {
        "kind": "multiple_choice",
        "prompt": "Which option connects the idea correctly?",
        "options": ["Correct bridge", "Plausible distraction", "Unrelated shortcut"],
        "answer_index": 0,
        "explanation": "The bridge makes both sets of assumptions explicit.",
    },
    {
        "kind": "multiple_choice",
        "prompt": "What should happen first?",
        "options": ["Skip the evidence", "Compare the constraints", "Choose by instinct"],
        "answer_index": 1,
        "explanation": "Comparing constraints prevents a one-discipline answer.",
    },
    {
        "kind": "true_false",
        "prompt": "A useful tangent changes how you frame a work decision.",
        "options": ["True", "False"],
        "answer_index": 0,
        "explanation": "Application is the point of learning around the job.",
    },
]


class GrowthApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        ratelimit.clear()
        self.client.cookies.clear()

    def signup(self, email="explorer@example.com", name="Explorer"):
        response = self.client.post(
            "/api/auth/signup",
            json={
                "email": email,
                "password": "verysecret",
                "display_name": name,
                "role": "Product analyst working across policy and software",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def completed_lessons(self, email="explorer@example.com"):
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == email))
            rows = []
            for title, category in (
                ("Regulatory constraints", "regulatory"),
                ("Decision systems", "technical"),
            ):
                lesson = Lesson(
                    user_id=user.id,
                    topic_title=title,
                    topic_blurb=f"A lesson about {title.lower()}.",
                    category=category,
                    picked_by="user",
                    status="ready",
                    content_json=json.dumps(
                        {
                            "title": title,
                            "subtitle": "",
                            "estimated_minutes": 5,
                            "cards": [
                                {
                                    "heading": f"{title} in practice",
                                    "body": "Make the governing assumptions visible.",
                                    "intuition": "Compare before deciding.",
                                    "key_terms": [],
                                    "diagram_svg": "",
                                    "diagram_caption": "",
                                }
                            ],
                            "questions": QUESTIONS,
                        }
                    ),
                    completed=True,
                    score=3,
                    total_questions=3,
                    xp_awarded=40,
                    coins_awarded=27,
                    completed_at=datetime.now(timezone.utc),
                )
                db.add(lesson)
                rows.append(lesson)
            user.coins = 500
            db.commit()
            return [row.id for row in rows]

    @staticmethod
    def mission(payload, reward):
        return next(item for item in payload["missions"] if item["reward"] == reward)

    def test_review_queue_strips_answers_reschedules_and_unlocks_mission(self):
        self.signup()
        self.completed_lessons()

        growth = self.client.get("/api/growth")
        self.assertEqual(growth.status_code, 200, growth.text)
        data = growth.json()
        self.assertEqual(
            {node["title"] for node in data["constellation"]["nodes"]},
            {"Regulatory constraints", "Decision systems"},
        )
        queue = data["review"]["questions"]
        self.assertEqual(len(queue), 3)
        for question in queue:
            self.assertNotIn("answer_index", question)

        for position, question in enumerate(queue):
            answer = QUESTIONS[question["question_index"]]["answer_index"]
            submitted = (
                (answer + 1) % len(question["options"])
                if position == 0
                else answer
            )
            response = self.client.post(
                "/api/growth/review/answer",
                json={
                    "lesson_id": question["lesson_id"],
                    "question_index": question["question_index"],
                    "answer_index": submitted,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["correct"], position != 0)
            self.assertEqual(response.json()["correct_index"], answer)
            self.assertIn("explanation", response.json())

        repeated = self.client.post(
            "/api/growth/review/answer",
            json={
                "lesson_id": queue[0]["lesson_id"],
                "question_index": queue[0]["question_index"],
                "answer_index": QUESTIONS[queue[0]["question_index"]]["answer_index"],
            },
        )
        self.assertEqual(repeated.status_code, 409, repeated.text)

        refreshed = self.client.get("/api/growth").json()
        mission = self.mission(refreshed, 8)
        self.assertTrue(mission["complete"])
        claimed = self.client.post(
            "/api/growth/missions/claim", json={"key": mission["key"]}
        )
        self.assertEqual(claimed.status_code, 200, claimed.text)
        duplicate = self.client.post(
            "/api/growth/missions/claim", json={"key": mission["key"]}
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

    def test_daily_actions_and_workshop_are_server_owned(self):
        self.signup()
        self.completed_lessons()

        visited = self.client.post("/api/growth/constellation/visit")
        self.assertEqual(visited.status_code, 200, visited.text)
        growth = self.client.get("/api/growth").json()
        self.assertTrue(growth["constellation"]["visited_today"])

        for reward in (5, 12):
            mission = self.mission(growth, reward)
            self.assertTrue(mission["complete"])
            response = self.client.post(
                "/api/growth/missions/claim", json={"key": mission["key"]}
            )
            self.assertEqual(response.status_code, 200, response.text)

        item = next(
            product for product in growth["workshop"]["items"] if not product["owned"]
        )
        bought = self.client.post(
            "/api/growth/workshop/purchase", json={"item_key": item["key"]}
        )
        self.assertEqual(bought.status_code, 200, bought.text)
        self.assertTrue(bought.json()["owned"])

        equipped = self.client.post(
            "/api/growth/workshop/equip", json={"item_key": item["key"]}
        )
        self.assertEqual(equipped.status_code, 200, equipped.text)
        self.assertTrue(equipped.json()["equipped"])

        duplicate = self.client.post(
            "/api/growth/workshop/purchase", json={"item_key": item["key"]}
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

    def test_weekly_boss_is_first_attempt_only(self):
        self.signup()
        self.completed_lessons()
        boss = self.client.get("/api/growth").json()["boss"]
        self.assertFalse(boss["locked"])
        self.assertNotIn("answer_index", boss)

        stale = self.client.post(
            "/api/growth/boss/answer",
            json={"answer_index": 0, "scenario_key": "0" * 16},
        )
        self.assertEqual(stale.status_code, 409, stale.text)

        attempt = self.client.post(
            "/api/growth/boss/answer",
            json={"answer_index": 0, "scenario_key": boss["scenario_key"]},
        )
        self.assertEqual(attempt.status_code, 200, attempt.text)
        self.assertTrue(attempt.json()["attempted"])
        self.assertIn(attempt.json()["reward"], (8, 25))

        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == "explorer@example.com"))
            db.add(
                Lesson(
                    user_id=user.id,
                    topic_title="A newly completed tangent",
                    topic_blurb="This should not rewrite an attempted weekly boss.",
                    category="frontier",
                    picked_by="user",
                    status="ready",
                    content_json=json.dumps({"cards": [], "questions": QUESTIONS}),
                    completed=True,
                    score=3,
                    total_questions=3,
                    completed_at=datetime.now(timezone.utc),
                )
            )
            db.commit()

        frozen = self.client.get("/api/growth").json()["boss"]
        self.assertEqual(frozen["scenario_key"], boss["scenario_key"])
        self.assertEqual(frozen["prompt"], boss["prompt"])
        self.assertEqual(frozen["options"], boss["options"])

        repeated = self.client.post(
            "/api/growth/boss/answer",
            json={"answer_index": 0, "scenario_key": boss["scenario_key"]},
        )
        self.assertEqual(repeated.status_code, 409, repeated.text)

    def test_private_circle_requires_invite_and_tracks_members(self):
        self.signup(name="Ari")
        self.completed_lessons()
        blank = self.client.post("/api/growth/circles", json={"name": "  "})
        self.assertEqual(blank.status_code, 422, blank.text)
        created = self.client.post(
            "/api/growth/circles", json={"name": "  Cross-pollinators   together  "}
        )
        self.assertEqual(created.status_code, 201, created.text)
        circle = created.json()
        self.assertEqual(circle["name"], "Cross-pollinators together")
        invite_code = circle["invite_code"]
        circle_id = circle["id"]

        self.client.post("/api/auth/signout")
        self.signup("second@example.com", "Bea")
        denied = self.client.post(
            "/api/growth/circles/join", json={"invite_code": "not-a-real-code"}
        )
        self.assertEqual(denied.status_code, 404, denied.text)

        joined = self.client.post(
            "/api/growth/circles/join", json={"invite_code": invite_code}
        )
        self.assertEqual(joined.status_code, 200, joined.text)
        circles = self.client.get("/api/growth").json()["circles"]
        mine = next(item for item in circles if item["id"] == circle_id)
        self.assertEqual(mine["member_count"], 2)
        self.assertEqual(
            {member["display_name"] for member in mine["members"]}, {"Ari", "Bea"}
        )
        for member in mine["members"]:
            self.assertNotIn("rank", member)
            self.assertNotIn("user_id", member)
            self.assertNotIn("email", member)
            self.assertEqual(member["contribution"], 0)

        left = self.client.post(f"/api/growth/circles/{circle_id}/leave")
        self.assertEqual(left.status_code, 200, left.text)
        self.assertFalse(
            any(
                item["id"] == circle_id
                for item in self.client.get("/api/growth").json()["circles"]
            )
        )

    def test_circle_progress_uses_one_utc_week_for_every_member(self):
        self.signup(name="Ari")
        created = self.client.post(
            "/api/growth/circles", json={"name": "Across timezones"}
        ).json()
        self.client.post("/api/auth/signout")
        self.signup("second@example.com", "Bea")
        joined = self.client.post(
            "/api/growth/circles/join",
            json={"invite_code": created["invite_code"]},
        )
        self.assertEqual(joined.status_code, 200, joined.text)

        boundary = datetime(2026, 1, 5, 0, 30, tzinfo=timezone.utc)
        with SessionLocal() as db:
            ari = db.scalar(select(User).where(User.email == "explorer@example.com"))
            bea = db.scalar(select(User).where(User.email == "second@example.com"))
            ari.timezone = "Pacific/Kiritimati"
            bea.timezone = "Etc/GMT+12"
            for membership in db.scalars(
                select(CircleMember).where(CircleMember.circle_id == created["id"])
            ):
                membership.joined_at = datetime(
                    2025, 12, 30, 12, tzinfo=timezone.utc
                )
            db.add(
                Lesson(
                    user_id=ari.id,
                    topic_title="A UTC-boundary tangent",
                    topic_blurb="",
                    category="frontier",
                    picked_by="user",
                    status="ready",
                    content_json=json.dumps({"cards": [], "questions": []}),
                    completed=True,
                    completed_at=datetime(
                        2026, 1, 5, 0, 15, tzinfo=timezone.utc
                    ),
                )
            )
            db.commit()

        with patch("app.routers.growth.utcnow", return_value=boundary):
            bea_view = next(
                item
                for item in self.client.get("/api/growth").json()["circles"]
                if item["id"] == created["id"]
            )

        self.client.post("/api/auth/signout")
        signed_in = self.client.post(
            "/api/auth/signin",
            json={"email": "explorer@example.com", "password": "verysecret"},
        )
        self.assertEqual(signed_in.status_code, 200, signed_in.text)
        with patch("app.routers.growth.utcnow", return_value=boundary):
            ari_view = next(
                item
                for item in self.client.get("/api/growth").json()["circles"]
                if item["id"] == created["id"]
            )

        self.assertEqual(ari_view["weekly_progress"], 3)
        self.assertEqual(bea_view["weekly_progress"], 3)
        self.assertEqual(ari_view["members"], bea_view["members"])


if __name__ == "__main__":
    unittest.main()
