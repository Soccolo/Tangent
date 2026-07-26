"""Screen-capture observation endpoints.

The contract with the user is that no video and no image ever persists. Frames
arrive in the request body, go to the model, and fall out of scope when the
request ends — they are never written to disk, never logged, and never stored
in the database. Only the derived text lines are kept, and only after the user
confirms them.
"""

import base64
import binascii
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..config import DAILY_CAPTURE_CAP
from ..db import get_db
from ..llm import LLMError, extract_activities
from ..models import Activity, Observation, User
from ..security import current_user
from .learn import claim_quota

log = logging.getLogger("tangent.capture")

router = APIRouter(prefix="/api/capture", tags=["capture"])

MAX_FRAMES = 8
_ALLOWED_MEDIA = {"image/jpeg", "image/png", "image/webp"}


class FrameBatch(BaseModel):
    # Data URLs straight from a canvas. Capped hard: this is the one endpoint
    # that accepts bulk binary, so it needs a ceiling that isn't "whatever the
    # client felt like sending".
    frames: list[str] = Field(min_length=1, max_length=MAX_FRAMES)


class ObservationEdit(BaseModel):
    activity: str = Field(min_length=2, max_length=2000)


def _decode_frame(data_url: str) -> tuple[str, str]:
    """Validate a data: URL and split it into (media_type, base64 payload)."""
    if not data_url.startswith("data:") or "," not in data_url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Frames must be data URLs.")
    header, payload = data_url.split(",", 1)
    media_type = header[5:].split(";", 1)[0].lower()
    if media_type not in _ALLOWED_MEDIA:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Unsupported frame format: {media_type}"
        )
    try:
        decoded = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Frame isn't valid image data.") from exc
    if not decoded:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Frame is empty.")
    if len(decoded) > 2_000_000:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Frame too large — downscale before sending.",
        )
    return media_type, payload


def _serialize(observation: Observation) -> dict:
    return {
        "id": observation.id,
        "activity": observation.activity,
        "evidence": observation.evidence,
        "confidence": observation.confidence,
        "status": observation.status,
    }


@router.post("/frames")
def submit_frames(
    body: FrameBatch,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    """Extract candidate activities from a batch of in-memory frames.

    Returns proposals only. Nothing reaches the activity log until the user
    says yes — that confirmation step is the entire point of the feature.
    """
    frames = [_decode_frame(f) for f in body.frames]

    claim_quota(db, user.id, "capture")
    try:
        proposals = extract_activities(user.role, frames)
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    finally:
        # Explicit, even though scope-exit would do it: the guarantee that
        # frames don't outlive the request is the feature.
        frames.clear()

    today = date.today()
    existing = {
        row.activity.strip().lower()
        for row in db.scalars(
            select(Observation).where(
                Observation.user_id == user.id, Observation.day == today
            )
        )
    }

    created = []
    for proposal in proposals:
        activity = (proposal.get("activity") or "").strip()
        if not activity or activity.lower() in existing:
            continue  # the same task across consecutive batches, not a new one
        existing.add(activity.lower())
        observation = Observation(
            user_id=user.id,
            day=today,
            activity=activity[:2000],
            evidence=(proposal.get("evidence") or "")[:2000],
            confidence=proposal.get("confidence", "medium"),
        )
        db.add(observation)
        created.append(observation)

    db.commit()
    for observation in created:
        db.refresh(observation)

    log.info("capture: %s frames in, %s new observations", len(body.frames), len(created))
    return {"observations": [_serialize(o) for o in created]}


@router.get("/pending")
def pending(user: User = Depends(current_user), db: OrmSession = Depends(get_db)):
    rows = db.scalars(
        select(Observation)
        .where(
            Observation.user_id == user.id,
            Observation.day == date.today(),
            Observation.status == "pending",
        )
        .order_by(Observation.created_at)
    ).all()
    return {
        "observations": [_serialize(o) for o in rows],
        "capture_cap": DAILY_CAPTURE_CAP,
    }


@router.post("/observations/{observation_id}/confirm")
def confirm(
    observation_id: int,
    body: ObservationEdit | None = None,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    """Accept a proposal — optionally with the user's correction — and promote
    it to a real activity."""
    observation = db.get(Observation, observation_id)
    if observation is None or observation.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such observation")
    if observation.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Already answered")

    if body is not None and body.activity.strip():
        observation.activity = body.activity.strip()

    observation.status = "confirmed"
    activity = Activity(
        user_id=user.id,
        day=observation.day,
        text=observation.activity,
        source="capture",
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return {"id": activity.id, "text": activity.text}


@router.post("/observations/{observation_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject(
    observation_id: int,
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    observation = db.get(Observation, observation_id)
    if observation is None or observation.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such observation")
    observation.status = "rejected"
    db.commit()


@router.delete("/observations", status_code=status.HTTP_204_NO_CONTENT)
def clear_pending(
    user: User = Depends(current_user),
    db: OrmSession = Depends(get_db),
):
    """Discard everything not yet answered — the 'forget what you saw' button."""
    for observation in db.scalars(
        select(Observation).where(
            Observation.user_id == user.id, Observation.status == "pending"
        )
    ):
        db.delete(observation)
    db.commit()
