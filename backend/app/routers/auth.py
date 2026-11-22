from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth_cookies import (
    REFRESH_COOKIE_NAME,
    clear_auth_cookies,
    set_auth_cookies,
)
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
from app.services.auth_sessions import (
    issue_refresh_token,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
    rotate_refresh_token,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


async def _grant_session(
    response: Response,
    request: Request,
    user: User,
    db: AsyncSession,
    *,
    refresh_token: str | None = None,
) -> str:
    """Issue (or re-issue) the browser session cookies and return the access token."""
    access_token = create_access_token({"sub": user.id, "ver": user.token_version})
    raw_refresh = refresh_token or await issue_refresh_token(db, user, request)
    set_auth_cookies(
        response,
        access_token=access_token,
        refresh_token=raw_refresh,
        csrf_token=_new_csrf_token(),
    )
    return access_token


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
    response: Response,
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
    # The access token is still returned for script/API-client compatibility;
    # the browser stores the HttpOnly cookies and ignores this field.
    access_token = await _grant_session(response, request, user, db)
    return TokenResponse(access_token=access_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_session(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Rotate the refresh cookie and mint a fresh short-lived access cookie."""
    raw_refresh = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw_refresh:
        raise HTTPException(status_code=401, detail="No session to refresh")

    user, new_refresh = await rotate_refresh_token(db, raw_refresh, request)
    access_token = create_access_token({"sub": user.id, "ver": user.token_version})
    set_auth_cookies(
        response,
        access_token=access_token,
        refresh_token=new_refresh,
        csrf_token=_new_csrf_token(),
    )
    return TokenResponse(access_token=access_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Revoke the presented refresh token and clear the cookies."""
    raw_refresh = request.cookies.get(REFRESH_COOKIE_NAME)
    if raw_refresh:
        await revoke_refresh_token(db, raw_refresh)
    clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return None


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserResponse)
async def update_me(
    body: UserUpdate,
    request: Request,
    response: Response,
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
        # Revoke every other device and invalidate outstanding access tokens.
        current_user.token_version = (current_user.token_version or 0) + 1
        await revoke_all_refresh_tokens(db, current_user.id)
        # Keep THIS session alive: issue a new refresh token + access cookie
        # carrying the new version.
        await _grant_session(response, request, current_user, db)

    await db.flush()
    await db.refresh(current_user)
    return current_user
