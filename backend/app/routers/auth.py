from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User
from app.rate_limit import (
    check_rate_limit,
    clear_rate_limit,
    client_ip,
    record_rate_limit,
)
from app.schemas.auth import (
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
    UserUpdate,
)
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _normalize_email(email: str) -> str:
    return email.strip().lower()


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: UserRegister,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    ip = client_ip(request)
    # Count every attempt (successful or not) so one IP cannot mass-create
    # accounts or probe the email space.
    check_rate_limit(
        "register-ip",
        ip,
        settings.REGISTER_RATE_LIMIT_ATTEMPTS,
        settings.REGISTER_RATE_LIMIT_WINDOW_SECONDS,
    )
    record_rate_limit("register-ip", ip, settings.REGISTER_RATE_LIMIT_WINDOW_SECONDS)

    email = _normalize_email(body.email)
    existing = await db.execute(select(User).where(func.lower(User.email) == email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=email,
        hashed_password=hash_password(body.password),
        display_name=(body.display_name or email.split("@")[0]).strip()[:100],
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    body: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    email = _normalize_email(body.email)
    ip = client_ip(request)

    # Failed logins consume budget, successes do not (and reset the email
    # budget), so normal usage never throttles the account owner.
    check_rate_limit(
        "login-email",
        email,
        settings.AUTH_RATE_LIMIT_ATTEMPTS,
        settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
    )
    check_rate_limit(
        "login-ip",
        ip,
        settings.AUTH_RATE_LIMIT_ATTEMPTS * 4,
        settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
    )

    result = await db.execute(select(User).where(func.lower(User.email) == email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        record_rate_limit("login-email", email, settings.AUTH_RATE_LIMIT_WINDOW_SECONDS)
        record_rate_limit("login-ip", ip, settings.AUTH_RATE_LIMIT_WINDOW_SECONDS)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    clear_rate_limit("login-email", email)
    token = create_access_token({"sub": user.id})
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserResponse)
async def update_me(
    body: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.display_name is not None:
        current_user.display_name = body.display_name.strip()[:100]

    if body.password is not None:
        # A valid token alone is not enough to rotate the password: require the
        # current one so a stolen/loaned session cannot lock the owner out.
        if not body.current_password or not verify_password(
            body.current_password, current_user.hashed_password
        ):
            raise HTTPException(
                status_code=400,
                detail="Current password is required and must be correct to set a new one",
            )
        if verify_password(body.password, current_user.hashed_password):
            raise HTTPException(status_code=400, detail="New password must be different")
        current_user.hashed_password = hash_password(body.password)

    await db.flush()
    await db.refresh(current_user)
    return current_user
