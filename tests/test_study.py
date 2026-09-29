import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEST_DIR = Path(tempfile.mkdtemp(prefix="tangent-study-tests-"))
os.environ.setdefault("TANGENT_DB", f"sqlite:///{(_TEST_DIR / 'test.db').as_posix()}")

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import func, select

from app import ratelimit
from app.db import Base, SessionLocal, engine
from app.llm import LLMError
from app.main import app
from app.models import Generation, Lesson, LibraryLesson, StudyDocument, User
from app.study import parse_pages


PAGE_ONE = "What does photosynthesis convert into chemical energy?"
PAGE_THREE = "Mitosis separates duplicated chromosomes into two identical daughter cells."
PAGE_FOUR = "Photosynthesis converts light into chemical energy stored in glucose."
QUESTION = {
    "prompt": PAGE_ONE,
    "answer": PAGE_FOUR,
    "question_excerpts": [{"page": 1, "text": PAGE_ONE}],
    "answer_excerpts": [{"page": 4, "text": PAGE_FOUR}],
}
DEFAULT_SOURCES = {
    "theory_document_id": None,
    "theory_pages": "",
    "question_document_id": None,
    "question_pages": "",
    "same_question_pdf": True,
    "answer_document_id": None,
    "answer_pages": "",
    "answer_pdf": "questions",
}


def pdf_bytes(pages, password=None):
    """A real text PDF, including empty pages, without a fixture or PDF renderer."""
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        if not text:
            continue
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
        })
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 50 700 Td ({escaped}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    if password is not None:
        writer.encrypt(password)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class StudyApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        ratelimit.clear()
        self.client.cookies.clear()

    def signup(self, email="student@example.com"):
        response = self.client.post("/api/auth/signup", json={
            "email": email,
            "password": "verysecret",
            "display_name": "Student",
            "role": "Biology student",
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def upload(self, content=None, filename="lecture.pdf"):
        if content is None:
            content = pdf_bytes([PAGE_ONE, "", PAGE_THREE, PAGE_FOUR])
        return self.client.post("/api/study/documents", files={
            "file": (filename, content, "application/pdf")
        })

    def document(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def seed_document(self, pages, user_id=None, filename="large-lecture.pdf"):
        with SessionLocal() as db:
            if user_id is None:
                user_id = db.scalar(select(User.id).where(User.email == "student@example.com"))
            document = StudyDocument(
                user_id=user_id,
                filename=filename,
                page_count=len(pages),
                pages_json=json.dumps(pages),
            )
            db.add(document)
            db.commit()
            return document.id

    def generation_count(self):
        with SessionLocal() as db:
            return db.scalar(select(func.count(Generation.id)))

    def test_real_pdf_preserves_page_numbers_and_independent_selections(self):
        self.signup()
        document = self.document()
        path = f"/api/study/documents/{document['id']}"
        self.assertEqual(document["page_count"], 4)
        self.assertEqual(document["blank_pages"], [2])
        self.assertNotIn("pages_json", document)
        self.assertNotIn(PAGE_ONE, json.dumps(self.client.get("/api/study/documents").json()))

        with patch("app.routers.study.extract_study_questions", return_value=[QUESTION]) as extract:
            response = self.client.post(path + "/questions", json={
                "pages": "1", "answer_document_id": document["id"], "answer_pages": "4", "count": 1,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["pages"], [1])
        self.assertEqual(response.json()["answer_pages"], [4])
        self.assertEqual(response.json()["answer_filename"], "lecture.pdf")
        self.assertEqual(response.json()["questions"], [QUESTION])
        extract.assert_called_once_with(
            [{"page": 1, "text": PAGE_ONE}], [{"page": 4, "text": PAGE_FOUR}], 1,
        )

        for _ in range(3):
            snippet = self.client.post(path + "/snippet", json={"pages": "3"})
            self.assertEqual(snippet.status_code, 200, snippet.text)
            self.assertEqual(snippet.json()["page"], 3)
            self.assertEqual(snippet.json()["document_id"], document["id"])
            self.assertTrue(snippet.json()["text"])
            self.assertIn(snippet.json()["text"], PAGE_THREE)
            self.assertNotIn(PAGE_ONE, snippet.text)

        saved = self.client.get("/api/study/documents").json()[0]
        self.assertEqual(parse_pages(saved["question_pages"], 4), [1])
        self.assertEqual(parse_pages(saved["snippet_pages"], 4), [3])
        self.assertEqual(self.generation_count(), 1)
        with SessionLocal() as db:
            self.assertEqual(db.scalar(select(func.count(Lesson.id))), 0)
            self.assertEqual(db.scalar(select(func.count(LibraryLesson.id))), 0)

        exported = self.client.get("/api/auth/me/export").json()["study_documents"]
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["filename"], "lecture.pdf")
        self.assertIn(PAGE_ONE, json.dumps(exported))
        self.assertIn(PAGE_THREE, json.dumps(exported))
        removed = self.client.delete(path)
        self.assertEqual(removed.status_code, 204, removed.text)
        self.assertEqual(self.client.get("/api/study/documents").json(), [])
        self.assertEqual(self.client.post(path + "/snippet", json={"pages": "1"}).status_code, 404)

    def test_page_validation_rejects_ambiguous_and_blank_selections_before_generation(self):
        self.signup()
        document = self.document()
        path = f"/api/study/documents/{document['id']}"
        quiz = {"pages": "1", "answer_document_id": document["id"], "answer_pages": "4"}
        self.assertEqual(parse_pages(" 3, 1-2, 1 ", 4), [1, 2, 3])
        with patch("app.routers.study.extract_study_questions") as extract:
            for pages in ("", " ", "0", "5", "-1", "3-1", "1,,2", "1-2-3", "1.5", "all"):
                for endpoint in ("questions", "snippet"):
                    with self.subTest(pages=pages, endpoint=endpoint):
                        body = {**quiz, "pages": pages} if endpoint == "questions" else {"pages": pages}
                        response = self.client.post(path + "/" + endpoint, json=body)
                        self.assertEqual(response.status_code, 422 if pages == "" else 400, response.text)
                with self.subTest(answer_pages=pages):
                    response = self.client.post(path + "/questions", json={**quiz, "answer_pages": pages})
                    self.assertEqual(response.status_code, 422 if pages == "" else 400, response.text)
                if pages.strip():
                    response = self.client.patch(path, json={"question_pages": pages})
                    self.assertEqual(response.status_code, 400, response.text)
            for endpoint in ("questions", "snippet"):
                response = self.client.post(path + "/" + endpoint, json={**quiz, "pages": "2"})
                self.assertEqual(response.status_code, 400, response.text)
            response = self.client.post(path + "/questions", json={**quiz, "answer_pages": "2"})
            self.assertEqual(response.status_code, 400, response.text)
            for count in (0, 6):
                response = self.client.post(path + "/questions", json={**quiz, "count": count})
                self.assertEqual(response.status_code, 422, response.text)
            for missing in ("answer_document_id", "answer_pages"):
                response = self.client.post(path + "/questions", json={key: value for key, value in quiz.items() if key != missing})
                self.assertEqual(response.status_code, 422, response.text)
            extract.assert_not_called()
        self.assertEqual(self.generation_count(), 0)
        saved = self.client.patch(path, json={"question_pages": "1-3", "snippet_pages": "3"})
        self.assertEqual(saved.status_code, 200, saved.text)
        cleared = self.client.patch(path, json={"question_pages": "", "snippet_pages": ""})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()["question_pages"], "")
        self.assertEqual(cleared.json()["snippet_pages"], "")

    def test_documents_require_authentication_and_owner_on_every_endpoint(self):
        self.assertEqual(self.client.get("/api/study/documents").status_code, 401)
        self.assertEqual(self.client.get("/api/study/sources").status_code, 401)
        self.assertEqual(self.client.put("/api/study/sources", json={}).status_code, 401)
        self.assertEqual(self.upload().status_code, 401)
        owner = self.signup()
        document = self.document()
        path = f"/api/study/documents/{document['id']}"
        self.client.post("/api/auth/signout")
        self.signup("other@example.com")
        self.assertEqual(self.client.get("/api/study/documents").json(), [])
        self.assertEqual(self.client.get("/api/auth/me/export").json()["study_documents"], [])
        other_document = self.document()
        with patch("app.routers.study.extract_study_questions") as extract:
            for method, suffix, body in (
                ("patch", "", {"question_pages": "1"}),
                ("post", "/questions", {"pages": "1", "answer_document_id": other_document["id"], "answer_pages": "4"}),
                ("post", "/snippet", {"pages": "1"}),
            ):
                response = getattr(self.client, method)(path + suffix, json=body)
                self.assertEqual(response.status_code, 404, response.text)
            response = self.client.post(f"/api/study/documents/{other_document['id']}/questions", json={
                "pages": "1", "answer_document_id": document["id"], "answer_pages": "4",
            })
            self.assertEqual(response.status_code, 404, response.text)
            extract.assert_not_called()
        for selection in (
            {"theory_document_id": document["id"], "theory_pages": "3"},
            {"question_document_id": document["id"], "question_pages": "1", "same_question_pdf": False},
            {"answer_document_id": document["id"], "answer_pages": "4", "answer_pdf": "separate"},
        ):
            response = self.client.put("/api/study/sources", json=selection)
            self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.client.get("/api/study/sources").json(), DEFAULT_SOURCES)
        self.assertEqual(self.client.delete(path).status_code, 404)
        with SessionLocal() as db:
            self.assertEqual(db.get(StudyDocument, document["id"]).user_id, owner["id"])

        self.client.post("/api/auth/signout")
        self.client.post("/api/auth/signin", json={"email": "student@example.com", "password": "verysecret"})
        deleted = self.client.post("/api/auth/me/delete", json={"password": "verysecret"})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        with SessionLocal() as db:
            self.assertIsNone(db.get(StudyDocument, document["id"]))
            self.assertIsNotNone(db.get(StudyDocument, other_document["id"]))

    def test_upload_rejects_unreadable_encrypted_and_oversized_documents(self):
        self.signup()
        invalid_uploads = (
            (b"this is not a PDF", "notes.pdf"),
            (b"%PDF-1.7\ntruncated", "broken.pdf"),
            (pdf_bytes([PAGE_ONE], password="secret"), "locked.pdf"),
            (pdf_bytes(["", ""]), "scanned.pdf"),
            (pdf_bytes([PAGE_ONE] + [""] * 300), "too-many-pages.pdf"),
            (pdf_bytes(["x" * 1_000_001]), "too-much-text.pdf"),
            (b"%PDF-1.7\n" + b"x" * (10 * 1024 * 1024), "too-large.pdf"),
        )
        for content, filename in invalid_uploads:
            with self.subTest(filename=filename):
                response = self.upload(content, filename)
                self.assertIn(response.status_code, (400, 413), response.text)
        self.assertEqual(self.client.get("/api/study/documents").json(), [])
        self.assertEqual(self.generation_count(), 0)

    def test_document_and_question_limits_apply_before_paid_generation(self):
        self.signup()
        small_id = self.seed_document([PAGE_ONE])
        many_id = self.seed_document([PAGE_ONE] * 41)
        large_questions = self.seed_document(["q" * 30_001])
        large_answers = self.seed_document(["a" * 30_000])
        with patch("app.routers.study.extract_study_questions") as extract:
            for question_id, pages, answer_id, answer_pages in (
                (many_id, "1-41", small_id, "1"),
                (small_id, "1", many_id, "1-41"),
                (large_questions, "1", large_answers, "1"),
            ):
                response = self.client.post(f"/api/study/documents/{question_id}/questions", json={
                    "pages": pages, "answer_document_id": answer_id, "answer_pages": answer_pages,
                })
                self.assertEqual(response.status_code, 400, response.text)
            extract.assert_not_called()
        self.assertEqual(self.generation_count(), 0)
        for _ in range(16):
            self.seed_document([PAGE_ONE])
        response = self.upload()
        self.assertIn(response.status_code, (400, 409, 413), response.text)
        self.assertEqual(len(self.client.get("/api/study/documents").json()), 20)

    def test_failed_generation_consumes_shared_lesson_quota(self):
        self.signup()
        document = self.document()
        path = f"/api/study/documents/{document['id']}/questions"
        quiz = {"pages": "1", "answer_document_id": document["id"], "answer_pages": "4"}
        with patch("app.routers.study.extract_study_questions", side_effect=LLMError("Try later")) as extract:
            response = self.client.post(path, json=quiz)
            self.assertEqual(response.status_code, 502, response.text)
            self.assertEqual(extract.call_count, 1)
        self.assertEqual(self.generation_count(), 1)
        with SessionLocal() as db:
            self.assertEqual(db.scalar(select(Generation.kind)), "lesson")
        with patch("app.routers.learn.DAILY_LESSON_CAP", 1), patch("app.routers.study.extract_study_questions") as extract:
            response = self.client.post(path, json=quiz)
            self.assertEqual(response.status_code, 429, response.text)
            extract.assert_not_called()
        self.assertEqual(self.generation_count(), 1)

    def test_separate_question_and_answer_pdfs_keep_page_one_evidence_separate(self):
        self.signup()
        theory_id = self.seed_document([PAGE_THREE], filename="theory.pdf")
        question_id = self.seed_document([PAGE_ONE], filename="questions.pdf")
        answer_id = self.seed_document([PAGE_FOUR], filename="answers.pdf")
        sources = {
            "theory_document_id": theory_id, "theory_pages": "1",
            "question_document_id": question_id, "question_pages": "1", "same_question_pdf": False,
            "answer_document_id": answer_id, "answer_pages": "1", "answer_pdf": "separate",
        }
        saved = self.client.put("/api/study/sources", json=sources)
        self.assertEqual(saved.status_code, 200, saved.text)
        question = {**QUESTION, "answer_excerpts": [{"page": 1, "text": PAGE_FOUR}]}
        with patch("app.routers.study.extract_study_questions", return_value=[question]) as extract:
            response = self.client.post(f"/api/study/documents/{question_id}/questions", json={
                "pages": "1", "answer_document_id": answer_id, "answer_pages": "1", "count": 1,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["filename"], "questions.pdf")
        self.assertEqual(response.json()["answer_filename"], "answers.pdf")
        self.assertEqual(response.json()["questions"], [question])
        extract.assert_called_once_with(
            [{"page": 1, "text": PAGE_ONE}], [{"page": 1, "text": PAGE_FOUR}], 1,
        )
        snippet = self.client.post(f"/api/study/documents/{theory_id}/snippet", json={"pages": "1"})
        self.assertEqual(snippet.status_code, 200, snippet.text)
        self.assertEqual(snippet.json()["text"], PAGE_THREE)

    def test_source_configuration_persists_exports_and_clears_deleted_references(self):
        self.signup()
        theory = self.document()
        question_id = self.seed_document([PAGE_ONE])
        answer_id = self.seed_document([PAGE_FOUR])
        sources = {
            "theory_document_id": theory["id"], "theory_pages": "3",
            "question_document_id": question_id, "question_pages": "1", "same_question_pdf": False,
            "answer_document_id": answer_id, "answer_pages": "1", "answer_pdf": "separate",
        }
        self.assertEqual(self.client.get("/api/study/sources").json(), DEFAULT_SOURCES)
        saved = self.client.put("/api/study/sources", json=sources)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json(), sources)
        fresh_client = TestClient(app)
        fresh_client.cookies.update(self.client.cookies)
        self.assertEqual(fresh_client.get("/api/study/sources").json(), sources)
        self.assertEqual(self.client.get("/api/auth/me/export").json()["study_sources"], sources)

        self.assertEqual(self.client.delete(f"/api/study/documents/{question_id}").status_code, 204)
        after_question = self.client.get("/api/study/sources").json()
        self.assertIsNone(after_question["question_document_id"])
        self.assertEqual(after_question["question_pages"], "")
        self.assertEqual(after_question["theory_document_id"], theory["id"])
        self.assertEqual(after_question["answer_document_id"], answer_id)
        self.assertEqual(after_question["answer_pages"], "1")
        self.assertEqual(self.client.delete(f"/api/study/documents/{answer_id}").status_code, 204)
        after_answer = self.client.get("/api/study/sources").json()
        self.assertIsNone(after_answer["answer_document_id"])
        self.assertEqual(after_answer["answer_pages"], "")

        linked = self.client.put("/api/study/sources", json={
            "theory_document_id": theory["id"], "theory_pages": "3",
            "question_pages": "1", "answer_pages": "4",
        })
        self.assertEqual(linked.status_code, 200, linked.text)
        self.assertEqual(self.client.delete(f"/api/study/documents/{theory['id']}").status_code, 204)
        self.assertEqual(self.client.get("/api/study/sources").json(), DEFAULT_SOURCES)

    def test_source_drafts_replace_previous_settings_and_validate_effective_ranges(self):
        self.signup()
        document = self.document()
        response = self.client.put("/api/study/sources", json={"theory_document_id": document["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {**DEFAULT_SOURCES, "theory_document_id": document["id"]})
        for field in ("theory_pages", "question_pages", "answer_pages"):
            for pages in ("5", "3-1", "1,,2", "2"):
                with self.subTest(field=field, pages=pages):
                    response = self.client.put("/api/study/sources", json={
                        "theory_document_id": document["id"], field: pages,
                    })
                    self.assertEqual(response.status_code, 400, response.text)
        response = self.client.put("/api/study/sources", json={
            "theory_document_id": document["id"], "theory_pages": "3",
            "question_pages": "1", "answer_pages": "4", "answer_pdf": "theory",
        })
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.put("/api/study/sources", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), DEFAULT_SOURCES)
        self.assertEqual(self.client.get("/api/study/sources").json(), DEFAULT_SOURCES)
        self.assertEqual(self.generation_count(), 0)

    def test_deleting_remembered_inactive_pdf_preserves_effective_source_ranges(self):
        self.signup()
        active = self.document()
        inactive_id = self.seed_document([PAGE_ONE, PAGE_FOUR])
        sources = {
            "theory_document_id": active["id"], "theory_pages": "3",
            "question_document_id": inactive_id, "question_pages": "1", "same_question_pdf": True,
            "answer_document_id": inactive_id, "answer_pages": "4", "answer_pdf": "questions",
        }
        saved = self.client.put("/api/study/sources", json=sources)
        self.assertEqual(saved.status_code, 200, saved.text)
        deleted = self.client.delete(f"/api/study/documents/{inactive_id}")
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertEqual(self.client.get("/api/study/sources").json(), {
            **sources, "question_document_id": None, "answer_document_id": None,
        })


if __name__ == "__main__":
    unittest.main()
