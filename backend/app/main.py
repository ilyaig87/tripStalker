"""FastAPI application and REST endpoints."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app import auth, crud
from app.adapters import ProviderError, get_adapter, supported_providers
from app.config import settings
from app.database import get_db, init_db
from app.models import User
from app.notifications import send_test_message
from app.price_check import run_price_checks, run_price_checks_for_email
from app.schemas import (
    TokenOut,
    TrackCreate,
    TrackDetailOut,
    TrackOut,
    UserLogin,
    UserOut,
    UserRegister,
)
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


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the authenticated user from a `Bearer <jwt>` Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = auth.decode_token(authorization[len("Bearer ") :])
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "providers": supported_providers()}


# ============================ auth ============================
@app.post("/api/auth/register", response_model=TokenOut, status_code=201)
def register(payload: UserRegister, db: Session = Depends(get_db)) -> TokenOut:
    existing = crud.get_user_by_email(db, payload.email)
    if existing and existing.password_hash:
        raise HTTPException(status_code=409, detail="כתובת המייל כבר רשומה")
    if existing:  # legacy email-only user → let them claim the account
        existing.password_hash = auth.hash_password(payload.password)
        db.commit()
        db.refresh(existing)
        user = existing
    else:
        user = crud.create_user(db, payload.email, auth.hash_password(payload.password))
    token = auth.create_access_token(user.id, user.email)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@app.post("/api/auth/login", response_model=TokenOut)
def login(payload: UserLogin, db: Session = Depends(get_db)) -> TokenOut:
    user = crud.get_user_by_email(db, payload.email)
    if user is None or not user.password_hash or not auth.verify_password(
        payload.password, user.password_hash
    ):
        raise HTTPException(status_code=401, detail="מייל או סיסמה שגויים")
    token = auth.create_access_token(user.id, user.email)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@app.get("/api/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)


@app.post("/api/telegram/test")
def telegram_test() -> dict:
    """Send a sample Telegram message to verify notifications are configured."""
    return send_test_message()


@app.post("/api/track", response_model=TrackOut, status_code=201)
async def create_track(
    payload: TrackCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TrackOut:
    """Register a new price-tracking request for the authenticated user.

    Parses the URL and fetches an initial price via the matching adapter.
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

    item = crud.create_track(
        db,
        user,
        parsed,
        initial_price=result.price,
        currency=result.currency,
        hotel_name=result.hotel_name,
        hotel_url=result.hotel_url,
        destination_city=result.destination_city,
        hotel_portion=result.hotel_portion,
        flight_portion=result.flight_portion,
        flight_details=result.flight_details,
    )
    return item


@app.get("/api/user/tracks", response_model=list[TrackOut])
def list_tracks(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[TrackOut]:
    """Return all tracks for the authenticated user."""
    return crud.get_tracks_for_user(db, user.id)


@app.post("/api/user/refresh", response_model=list[TrackOut])
async def refresh_user_tracks(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[TrackOut]:
    """Re-check all of the user's tracks right now, then return the updated list."""
    await run_price_checks_for_email(db, user.email)
    return crud.get_tracks_for_user(db, user.id)


@app.post("/api/track/{track_id}/reset", response_model=TrackOut)
def reset_track_baseline(
    track_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> TrackOut:
    """Reset a track's baseline to its current price (clears a false drop/increase)."""
    item = crud.reset_baseline(db, track_id, user.id)
    if item is None:
        raise HTTPException(status_code=404, detail="Track not found")
    return item


@app.get("/api/track/{track_id}", response_model=TrackDetailOut)
def get_track(
    track_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> TrackDetailOut:
    item = crud.get_track(db, track_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="Track not found")
    return item


@app.delete("/api/track/{track_id}")
def delete_track(
    track_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    if not crud.delete_track(db, track_id, user.id):
        raise HTTPException(status_code=404, detail="Track not found")
    return {"deleted": True, "id": track_id}


@app.get("/api/cron/check-prices")
async def cron_check_prices(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Daily price check, invoked by a scheduler (Vercel Cron / GitHub Actions).

    Vercel Cron sends `Authorization: Bearer <CRON_SECRET>`. When CRON_SECRET is
    set we require it; left empty (local/dev) the endpoint is open.
    """
    if settings.cron_secret and authorization != f"Bearer {settings.cron_secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return await run_price_checks(db)
