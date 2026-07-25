import logging

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import WEB_DIR
from .db import Base, engine, ensure_schema
from .routers import auth, learn

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Tangent", description="Learn the ring around your job.")

Base.metadata.create_all(engine)
ensure_schema()  # additive column migration for already-deployed databases

app.include_router(auth.router)
app.include_router(learn.router)

app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/s/{token}", include_in_schema=False)
def shared_lesson_page(token: str):
    """Shared-lesson links are real URLs people paste to each other, so they
    have to survive a cold load. The SPA reads the token off the path."""
    return FileResponse(WEB_DIR / "index.html")
