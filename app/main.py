import hashlib
import logging
import re

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import WEB_DIR
from .db import Base, engine, ensure_schema
from .routers import auth, capture, growth, learn, library, rewards

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Tangent", description="Learn the ring around your job.")

Base.metadata.create_all(engine)
ensure_schema()  # additive column migration for already-deployed databases

app.include_router(auth.router)
app.include_router(learn.router)
app.include_router(capture.router)
app.include_router(library.router)
app.include_router(rewards.router)
app.include_router(growth.router)

app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    source = (WEB_DIR / "sw.js").read_text(encoding="utf-8")
    source = source.replace("__ASSET_VERSION__", ASSET_VERSION)
    return Response(
        content=source,
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": "/",
        },
    )


def _asset_version() -> str:
    """A short hash of the front-end files, recomputed at boot.

    Without this, a browser holding a cached /static/app.js can pair it with
    freshly deployed HTML — the deploy succeeds and the user still sees the old
    app, which is indistinguishable from "the deploy didn't work".
    """
    digest = hashlib.sha256()
    for path in sorted(WEB_DIR.glob("*")):
        if path.is_file():
            stat = path.stat()
            digest.update(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()[:10]


ASSET_VERSION = _asset_version()
_ASSET_REF = re.compile(r'(?P<attr>href|src)="/static/(?P<file>[\w.\-]+)"')


def _render_index() -> HTMLResponse:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    html = _ASSET_REF.sub(
        lambda m: f'{m.group("attr")}="/static/{m.group("file")}?v={ASSET_VERSION}"',
        html,
    )
    # The HTML itself must never be cached, or it would keep pointing at the
    # previous build's asset versions and defeat the whole mechanism.
    return HTMLResponse(
        html,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/", include_in_schema=False)
def index():
    return _render_index()


@app.get("/reset/{token}", include_in_schema=False)
def reset_page(token: str):
    """Password-reset links arrive by email and land cold."""
    return _render_index()


@app.get("/s/{token}", include_in_schema=False)
def shared_lesson_page(token: str):
    """Shared-lesson links are real URLs people paste to each other, so they
    have to survive a cold load. The SPA reads the token off the path."""
    return _render_index()
