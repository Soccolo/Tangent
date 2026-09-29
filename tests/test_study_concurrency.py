import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

_TEST_DIR = Path(tempfile.mkdtemp(prefix="tangent-study-concurrency-"))
os.environ.setdefault("TANGENT_DB", f"sqlite:///{(_TEST_DIR / 'test.db').as_posix()}")

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import ratelimit
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import StudyDocument


class StudyConcurrencyTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        ratelimit.clear()
        self.client = TestClient(app)

    def signup(self, email):
        response = self.client.post("/api/auth/signup", json={
            "email": email, "password": "verysecret", "role": "Student",
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def upload(self):
        return self.client.post("/api/study/documents", files={
            "file": ("notes.pdf", b"%PDF-test", "application/pdf"),
        })

    def test_deleted_account_cannot_finish_upload_into_reused_account_id(self):
        original = self.signup("original@example.com")
        entered, resume = threading.Event(), threading.Event()

        def extract(data):
            entered.set()
            self.assertTrue(resume.wait(10))
            return ["Private lecture notes"]

        with patch("app.routers.study.extract_pdf", side_effect=extract):
            with ThreadPoolExecutor(max_workers=1) as executor:
                upload = executor.submit(self.upload)
                try:
                    self.assertTrue(entered.wait(10))
                    deleted = self.client.post("/api/auth/me/delete", json={"password": "verysecret"})
                    self.assertEqual(deleted.status_code, 200, deleted.text)
                    replacement = self.signup("replacement@example.com")
                    if engine.dialect.name == "sqlite":
                        self.assertEqual(replacement["id"], original["id"])
                finally:
                    resume.set()
                response = upload.result(timeout=10)
        self.assertEqual(response.status_code, 401, response.text)
        with SessionLocal() as db:
            self.assertEqual(db.scalar(select(func.count(StudyDocument.id))), 0)
        self.assertEqual(self.client.get("/api/study/documents").json(), [])
        self.assertEqual(self.client.get("/api/auth/me/export").json()["study_documents"], [])

    def test_simultaneous_uploads_cannot_exceed_storage_limit(self):
        user = self.signup("student@example.com")
        with SessionLocal() as db:
            db.add_all([
                StudyDocument(user_id=user["id"], filename="notes.pdf", page_count=1,
                              pages_json=json.dumps(["Notes"]))
                for _ in range(19)
            ])
            db.commit()
        both_extracted = threading.Barrier(2)

        def extract(data):
            both_extracted.wait(timeout=10)
            return ["New notes"]

        with patch("app.routers.study.extract_pdf", side_effect=extract):
            with ThreadPoolExecutor(max_workers=2) as executor:
                uploads = [executor.submit(self.upload) for _ in range(2)]
                responses = [upload.result(timeout=10) for upload in uploads]
        self.assertEqual(sorted(response.status_code for response in responses), [201, 400])
        self.assertEqual(len(self.client.get("/api/study/documents").json()), 20)


if __name__ == "__main__":
    unittest.main()
