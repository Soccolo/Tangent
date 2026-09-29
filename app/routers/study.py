import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session as OrmSession
from starlette.formparsers import MultiPartException

from .. import ratelimit
from ..db import get_db
from ..llm import LLMError
from ..models import StudyDocument, User
from ..security import current_user
from ..study import (
    MAX_PDF_BYTES,
    MAX_QUESTION_CHARS,
    MAX_QUESTION_PAGES,
    extract_pdf,
    parse_pages,
    random_excerpt,
)
from ..study_questions import extract_study_questions
from .learn import claim_quota

router = APIRouter(prefix="/api/study/documents", tags=["study"])
sources_router = APIRouter(prefix="/api/study/sources", tags=["study"])


class StudyUploadLimit:
    """Bound incoming multipart bytes before FastAPI spools uploaded files."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] not in {router.prefix, router.prefix + "/"}
        ):
            await self.app(scope, receive, send)
            return
        # Allow multipart metadata; the handler still enforces the exact file cap.
        limit = MAX_PDF_BYTES + 64 * 1024
        message = "The upload request is too large. PDF files must be 10 MiB or smaller."
        try:
            declared_size = int(dict(scope["headers"]).get(b"content-length", b"0"))
        except ValueError:
            declared_size = 0  # The actual stream is bounded even without a usable header.
        if declared_size > limit:
            await JSONResponse({"detail": message}, status_code=413)(scope, receive, send)
            return
        received = 0
        exceeded = False

        async def limited_receive():
            nonlocal received, exceeded
            event = await receive()
            received += len(event.get("body", b""))
            if received > limit:
                exceeded = True
                # This exception makes Starlette close partially spooled files.
                raise MultiPartException(message)
            return event

        async def limited_send(event):
            if exceeded and event["type"] == "http.response.start":
                # Starlette maps multipart errors to 400; this one is a size limit.
                event = {**event, "status": 413}
            await send(event)

        await self.app(scope, limited_receive, limited_send)


class PageSelection(BaseModel):
    pages: str = Field(min_length=1, max_length=1000)


class QuizRequest(PageSelection):
    answer_document_id: int = Field(gt=0)
    answer_pages: str = Field(min_length=1, max_length=1000)
    count: int = Field(default=3, ge=1, le=5)


class DocumentUpdate(BaseModel):
    question_pages: str | None = Field(default=None, max_length=1000)
    snippet_pages: str | None = Field(default=None, max_length=1000)


class StudySources(BaseModel):
    theory_document_id: int | None = Field(default=None, gt=0)
    theory_pages: str = Field(default="", max_length=1000)
    question_document_id: int | None = Field(default=None, gt=0)
    question_pages: str = Field(default="", max_length=1000)
    same_question_pdf: bool = True
    answer_document_id: int | None = Field(default=None, gt=0)
    answer_pages: str = Field(default="", max_length=1000)
    answer_pdf: Literal["questions", "theory", "separate"] = "questions"


def _lock_account(db: OrmSession, user: User) -> None:
    # A write locks on SQLite and Postgres; account deletion takes the same lock.
    # The timestamp distinguishes accounts if SQLite reuses a deleted user's id.
    locked = db.execute(
        update(User)
        .where(User.id == user.id, User.created_at == user.created_at)
        .values(id=User.id)
    )
    if not locked.rowcount:
        raise HTTPException(401, "Session expired")
    db.refresh(user, ["study_sources_json"])


def _owned_document(db: OrmSession, user: User, document_id: int) -> StudyDocument:
    document = db.get(StudyDocument, document_id)
    if document is None or document.user_id != user.id:
        raise HTTPException(404, "No such PDF.")
    return document


def _metadata(document: StudyDocument) -> dict:
    return {
        "id": document.id,
        "filename": document.filename,
        "page_count": document.page_count,
        "question_pages": document.question_pages,
        "snippet_pages": document.snippet_pages,
        "blank_pages": [i for i, text in enumerate(json.loads(document.pages_json), 1) if not text],
    }


def _selection(document: StudyDocument, value: str, *, quiz: bool = False) -> tuple[list[int], list[dict]]:
    try:
        numbers = parse_pages(value, document.page_count)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    texts = json.loads(document.pages_json)
    pages = [{"page": n, "text": texts[n - 1]} for n in numbers if texts[n - 1]]
    if not pages:
        raise HTTPException(400, "These pages have no readable text. Choose other pages or OCR the PDF first.")
    if quiz and (
        len(numbers) > MAX_QUESTION_PAGES
        or sum(len(page["text"]) for page in pages) > MAX_QUESTION_CHARS
    ):
        raise HTTPException(400, "Choose a smaller source selection: at most 40 pages and 60,000 text characters.")
    return numbers, pages


@sources_router.get("")
def get_sources(user: User = Depends(current_user)):
    return StudySources.model_validate_json(user.study_sources_json) if user.study_sources_json else StudySources()


@sources_router.put("")
def save_sources(
    body: StudySources,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    _lock_account(db, user)
    # Check even inactive separate-PDF selections; never persist another user's id.
    documents = {
        document_id: _owned_document(db, user, document_id)
        for document_id in (body.theory_document_id, body.question_document_id, body.answer_document_id)
        if document_id is not None
    }
    question_id = body.theory_document_id if body.same_question_pdf else body.question_document_id
    answer_id = {"questions": question_id, "theory": body.theory_document_id, "separate": body.answer_document_id}[body.answer_pdf]
    for role, document_id, value in (
        ("theory", body.theory_document_id, body.theory_pages),
        ("question", question_id, body.question_pages),
        ("answer", answer_id, body.answer_pages),
    ):
        if value.strip():
            if document_id is None:
                raise HTTPException(400, f"Choose a PDF for the {role} pages.")
            _selection(documents[document_id], value, quiz=role != "theory")
    user.study_sources_json = body.model_dump_json()
    db.commit()
    return body


@router.get("")
def list_documents(user: User = Depends(current_user), db: OrmSession = Depends(get_db)):
    return [
        _metadata(document)
        for document in db.scalars(
            select(StudyDocument).where(StudyDocument.user_id == user.id).order_by(StudyDocument.id.desc())
        )
    ]


@router.post("", status_code=201)
def upload_document(
    file: UploadFile,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    ratelimit.check(f"pdf-upload:{user.id}", 30, 3600, "Too many PDF uploads. Try again in an hour.")
    filename = (file.filename or "notes.pdf").replace("\\", "/").rsplit("/", 1)[-1]
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Choose a PDF file.")
    data = file.file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, "PDFs must be 10 MiB or smaller.")
    try:
        pages = extract_pdf(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _lock_account(db, user)
    count = db.scalar(select(func.count(StudyDocument.id)).where(StudyDocument.user_id == user.id))
    if count >= 20:
        raise HTTPException(400, "You can keep up to 20 PDFs. Delete one before adding another.")
    document = StudyDocument(
        user_id=user.id,
        filename=filename.replace("\x00", "")[:255],
        page_count=len(pages),
        pages_json=json.dumps(pages),
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return _metadata(document)


@router.patch("/{document_id}")
def update_document(
    document_id: int,
    body: DocumentUpdate,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    document = _owned_document(db, user, document_id)
    for name in ("question_pages", "snippet_pages"):
        value = getattr(body, name)
        if value is not None:
            if value.strip():
                _selection(document, value, quiz=name == "question_pages")
            setattr(document, name, value.strip())
    db.commit()
    return _metadata(document)


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: int,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    _lock_account(db, user)
    if user.study_sources_json:
        sources = StudySources.model_validate_json(user.study_sources_json)
        question_id = sources.theory_document_id if sources.same_question_pdf else sources.question_document_id
        answer_id = {"questions": question_id, "theory": sources.theory_document_id, "separate": sources.answer_document_id}[sources.answer_pdf]
        effective_ids = {"theory": sources.theory_document_id, "question": question_id, "answer": answer_id}
        for role in ("theory", "question", "answer"):
            if getattr(sources, f"{role}_document_id") == document_id:
                setattr(sources, f"{role}_document_id", None)
            if effective_ids[role] == document_id:
                setattr(sources, f"{role}_pages", "")
        user.study_sources_json = sources.model_dump_json()
    db.delete(_owned_document(db, user, document_id))
    db.commit()


@router.post("/{document_id}/questions")
def questions(
    document_id: int,
    body: QuizRequest,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    document = _owned_document(db, user, document_id)
    answer_document = _owned_document(db, user, body.answer_document_id)
    numbers, pages = _selection(document, body.pages, quiz=True)
    answer_numbers, answer_pages = _selection(answer_document, body.answer_pages, quiz=True)
    if sum(len(page["text"]) for page in pages + answer_pages) > MAX_QUESTION_CHARS:
        raise HTTPException(400, "Choose fewer question and answer pages: at most 60,000 text characters combined.")
    filename, answer_filename = document.filename, answer_document.filename
    document.question_pages = body.pages.strip()
    # Use the existing lesson/global spend guards; failed calls consume quota too.
    claim_quota(db, user.id, "lesson")
    try:
        result = extract_study_questions(pages, answer_pages, body.count)
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {
        "filename": filename,
        "pages": numbers,
        "answer_filename": answer_filename,
        "answer_pages": answer_numbers,
        "questions": result,
    }


@router.post("/{document_id}/snippet")
def snippet(
    document_id: int,
    body: PageSelection,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    document = _owned_document(db, user, document_id)
    _, pages = _selection(document, body.pages)
    result = random_excerpt(pages)
    document.snippet_pages = body.pages.strip()
    db.commit()
    return {"document_id": document.id, "filename": document.filename, **result}
