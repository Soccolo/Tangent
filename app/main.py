import logging

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import WEB_DIR
from .db import Base, engine
from .routers import auth, learn

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Tangent", description="Learn the ring around your job.")

Base.metadata.create_all(engine)

app.include_router(auth.router)
app.include_router(learn.router)

app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html")
