"""FastAPI application and REST endpoints."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app import crud
from app.adapters import ProviderError, get_adapter, supported_providers
from app.config import settings
from app.database import get_db, init_db
from app.schemas import TrackCreate, TrackDetailOut, TrackOut
from app.url_parser import parse_url


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # create tables on startup (use Alembic in production)
    yield


app = FastAPI(title="TripStalker API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "providers": supported_providers()}


@app.post("/api/track", response_model=TrackOut, status_code=201)
async def create_track(payload: TrackCreate, db: Session = Depends(get_db)) -> TrackOut:
    """Register a new price-tracking request.

    Parses the URL, fetches an initial price via the matching adapter,
    and persists the track (creating the user on first use).
    """
    parsed = parse_url(str(payload.url))
    if parsed.provider == "unknown":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported travel site. Supported: {supported_providers()}",
        )

    adapter = get_adapter(parsed.provider)
    try:
        result = await adapter.fetch_current_price(parsed)
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch price: {exc}") from exc

    user = crud.get_or_create_user(db, str(payload.email))
    item = crud.create_track(
        db,
        user,
        parsed,
        initial_price=result.price,
        currency=result.currency,
        hotel_name=result.hotel_name,
    )
    return item


@app.get("/api/user/tracks", response_model=list[TrackOut])
def list_tracks(email: str, db: Session = Depends(get_db)) -> list[TrackOut]:
    """Return all tracks for a given user email."""
    return crud.get_tracks_by_email(db, email)


@app.get("/api/track/{track_id}", response_model=TrackDetailOut)
def get_track(track_id: int, db: Session = Depends(get_db)) -> TrackDetailOut:
    item = crud.get_track(db, track_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Track not found")
    return item


@app.delete("/api/track/{track_id}")
def delete_track(track_id: int, db: Session = Depends(get_db)) -> dict:
    if not crud.delete_track(db, track_id):
        raise HTTPException(status_code=404, detail="Track not found")
    return {"deleted": True, "id": track_id}
