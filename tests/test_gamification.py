import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_TEST_DIR = Path(tempfile.mkdtemp(prefix="tangent-tests-"))
os.environ["TANGENT_DB"] = f"sqlite:///{(_TEST_DIR / 'test.db').as_posix()}"

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.main import app
from app.gamification import streak_status, user_today
from app.models import HintUse, Lesson, LibraryLesson, User
from app.security import hash_password


QUESTIONS = [
    {
        "kind": "multiple_choice",
        "prompt": "Question one?",
        "options": ["right", "wrong a", "wrong b"],
        "answer_index": 0,
        "explanation": "Because.",
    },
    {
        "kind": "multiple_choice",
        "prompt": "Question two?",
        "options": ["wrong", "right", "also wrong"],
        "answer_index": 1,
        "explanation": "Because.",
    },
    {
        "kind": "true_false",
        "prompt": "Question three?",
        "options": ["True", "False"],
        "answer_index": 1,
        "explanation": "Because.",
    },
]


class GamificationApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        self.client.cookies.clear()

    def signup(self, email="learner@example.com"):
        response = self.client.post(
            "/api/auth/signup",
            json={"email": email, "password": "verysecret", "role": "Test role"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def lesson(self, email="learner@example.com"):
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == email))
            lesson = Lesson(
                user_id=user.id,
                topic_title="A useful tangent",
                topic_blurb="",
                picked_by="user",
                status="ready",
                content_json=json.dumps(
                    {
                        "title": "A useful tangent",
                        "subtitle": "",
                        "estimated_minutes": 5,
                        "cards": [],
                        "questions": QUESTIONS,
                    }
                ),
                total_questions=len(QUESTIONS),
            )
            db.add(lesson)
            db.commit()
            db.refresh(lesson)
            return lesson.id

    def test_completion_awards_coins_once_and_preserves_first_attempt(self):
        profile = self.signup()
        self.assertEqual(profile["coins"], 30)
        self.assertEqual(profile["hint_tokens"], 1)
        lesson_id = self.lesson()

        first = self.client.post(
            f"/api/lessons/{lesson_id}/complete", json={"answers": [0, 1, 1]}
        )
        self.assertEqual(first.status_code, 200, first.text)
        result = first.json()
        self.assertEqual(result["coins_awarded"], 27)
        self.assertEqual(result["coins"], 57)
        self.assertEqual(result["xp_awarded"], 40)
        self.assertEqual(result["current_streak"], 1)

        with SessionLocal() as db:
            stored = db.get(Lesson, lesson_id)
            completed_at = stored.completed_at

        review = self.client.post(
            f"/api/lessons/{lesson_id}/complete", json={"answers": [2, 2, 0]}
        )
        self.assertEqual(review.status_code, 200, review.text)
        self.assertTrue(review.json()["already_completed"])
        self.assertEqual(review.json()["coins_awarded"], 0)
        self.assertEqual(review.json()["xp_awarded"], 0)
        self.assertEqual(review.json()["coins"], 57)

        with SessionLocal() as db:
            stored = db.get(Lesson, lesson_id)
            self.assertEqual(stored.score, 3)
            self.assertEqual(stored.completed_at, completed_at)

    def test_hint_is_idempotent_and_shop_inventory_is_server_owned(self):
        self.signup()
        lesson_id = self.lesson()
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == "learner@example.com"))
            user.coins = 150
            db.commit()

        first = self.client.post(
            "/api/rewards/hints/use",
            json={"lesson_id": lesson_id, "question_index": 0},
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertIn(first.json()["eliminated_index"], (1, 2))
        self.assertEqual(first.json()["hint_tokens"], 0)

        retry = self.client.post(
            "/api/rewards/hints/use",
            json={"lesson_id": lesson_id, "question_index": 0},
        )
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertTrue(retry.json()["already_used"])
        self.assertEqual(
            retry.json()["eliminated_index"], first.json()["eliminated_index"]
        )
        self.assertEqual(retry.json()["hint_tokens"], 0)

        hint = self.client.post("/api/rewards/purchase", json={"item": "hint"})
        self.assertEqual(hint.status_code, 200, hint.text)
        self.assertEqual(hint.json()["coins"], 135)
        self.assertEqual(hint.json()["hint_tokens"], 1)

        freeze = self.client.post(
            "/api/rewards/purchase", json={"item": "streak_freeze"}
        )
        self.assertEqual(freeze.status_code, 200, freeze.text)
        self.assertEqual(freeze.json()["coins"], 45)
        self.assertEqual(freeze.json()["streak_freezes"], 1)

    def test_freeze_auto_consumes_when_it_saves_a_missed_day(self):
        self.signup()
        lesson_id = self.lesson()
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == "learner@example.com"))
            user.current_streak = 5
            user.longest_streak = 5
            user.last_active_day = date.today() - timedelta(days=2)
            user.streak_freezes = 1
            db.commit()

        response = self.client.post(
            f"/api/lessons/{lesson_id}/complete", json={"answers": [0, 1, 1]}
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["streak_saved"])
        self.assertEqual(result["freezes_used"], 1)
        self.assertEqual(result["streak_freezes"], 0)
        self.assertEqual(result["current_streak"], 6)

    def test_streak_day_uses_the_learners_timezone(self):
        user = User(
            email="timezone@example.com",
            password_hash="not-used",
            timezone="Pacific/Kiritimati",
        )
        utc_new_year_eve = datetime(2026, 12, 31, 12, 30, tzinfo=timezone.utc)
        self.assertEqual(user_today(user, utc_new_year_eve), date(2027, 1, 1))

    def test_unrecoverable_streak_gap_is_expired_not_at_risk(self):
        user = User(
            email="expired@example.com",
            password_hash="not-used",
            current_streak=8,
            last_active_day=date(2026, 7, 27),
            streak_freezes=0,
        )
        status = streak_status(user, date(2026, 7, 31))
        self.assertEqual(status["missed_days"], 3)
        self.assertTrue(status["expired"])
        self.assertFalse(status["at_risk"])
        self.assertFalse(status["protected"])

    def test_timezone_profile_and_pwa_shell(self):
        self.signup()
        profile = self.client.patch(
            "/api/auth/me", json={"timezone": "Europe/London"}
        )
        self.assertEqual(profile.status_code, 200, profile.text)
        self.assertEqual(profile.json()["timezone"], "Europe/London")

        worker = self.client.get("/sw.js")
        self.assertEqual(worker.status_code, 200)
        self.assertEqual(worker.headers["service-worker-allowed"], "/")
        self.assertIn('startsWith("/api/")', worker.text)
        self.assertNotIn("__ASSET_VERSION__", worker.text)
        self.assertIn("const CACHE = `tangent-shell-${VERSION}`", worker.text)
        self.assertNotIn("ignoreSearch", worker.text)

        manifest = self.client.get("/static/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.json()["display"], "standalone")

    def test_deletion_removes_hints_and_anonymizes_copied_lessons(self):
        self.signup()
        original_id = self.lesson()
        used = self.client.post(
            "/api/rewards/hints/use",
            json={"lesson_id": original_id, "question_index": 0},
        )
        self.assertEqual(used.status_code, 200, used.text)

        with SessionLocal() as db:
            author = db.scalar(
                select(User).where(User.email == "learner@example.com")
            )
            author_id = author.id
            recipient = User(
                email="recipient@example.com",
                password_hash=hash_password("verysecret"),
                display_name="Recipient",
            )
            third_reader = User(
                email="third@example.com",
                password_hash=hash_password("verysecret"),
                display_name="Third reader",
            )
            db.add_all([recipient, third_reader])
            db.flush()
            library_entry = LibraryLesson(
                topic_key="an-authored-library-tangent",
                title="An authored library tangent",
                content_json=json.dumps({"cards": [], "questions": QUESTIONS}),
                author_user_id=author.id,
                author_name="Original learner",
            )
            db.add(library_entry)
            db.flush()
            copy = Lesson(
                user_id=recipient.id,
                topic_title="A useful tangent",
                topic_blurb="",
                picked_by="shared",
                status="ready",
                content_json=json.dumps({"cards": [], "questions": QUESTIONS}),
                shared_from_id=original_id,
                author_name="Original learner",
                share_token="direct-copy-token",
            )
            db.add(copy)
            db.flush()
            second_hop = Lesson(
                user_id=third_reader.id,
                topic_title="A useful tangent",
                topic_blurb="",
                picked_by="shared",
                status="ready",
                content_json=json.dumps({"cards": [], "questions": QUESTIONS}),
                shared_from_id=copy.id,
                author_name="Original learner",
            )
            library_copy = Lesson(
                user_id=recipient.id,
                topic_title=library_entry.title,
                topic_blurb="",
                picked_by="library",
                status="ready",
                content_json=library_entry.content_json,
                library_id=library_entry.id,
                author_name="Original learner",
                share_token="library-copy-token",
            )
            db.add_all([second_hop, library_copy])
            db.flush()
            library_second_hop = Lesson(
                user_id=third_reader.id,
                topic_title=library_entry.title,
                topic_blurb="",
                picked_by="shared",
                status="ready",
                content_json=library_entry.content_json,
                shared_from_id=library_copy.id,
                author_name="Original learner",
            )
            db.add(library_second_hop)
            db.commit()
            copy_id = copy.id
            second_hop_id = second_hop.id
            library_copy_id = library_copy.id
            library_second_hop_id = library_second_hop.id
            library_entry_id = library_entry.id

        deleted = self.client.post(
            "/api/auth/me/delete", json={"password": "verysecret"}
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)

        with SessionLocal() as db:
            copy = db.get(Lesson, copy_id)
            self.assertIsNotNone(copy)
            self.assertIsNone(copy.shared_from_id)
            self.assertEqual(copy.author_name, "someone")
            self.assertEqual(db.get(Lesson, second_hop_id).author_name, "someone")
            self.assertEqual(db.get(Lesson, library_copy_id).author_name, "someone")
            self.assertEqual(
                db.get(Lesson, library_second_hop_id).author_name, "someone"
            )
            library_entry = db.get(LibraryLesson, library_entry_id)
            self.assertIsNone(library_entry.author_user_id)
            self.assertIsNone(library_entry.author_name)
            self.assertIsNone(db.get(User, author_id))
            self.assertEqual(
                db.scalars(select(HintUse).where(HintUse.user_id == author_id)).all(),
                [],
            )

        shared = self.client.get("/api/shared/direct-copy-token")
        self.assertEqual(shared.status_code, 200, shared.text)
        self.assertEqual(shared.json()["author"], "someone")
        library_shared = self.client.get("/api/shared/library-copy-token")
        self.assertEqual(library_shared.status_code, 200, library_shared.text)
        self.assertEqual(library_shared.json()["author"], "someone")


if __name__ == "__main__":
    unittest.main()
