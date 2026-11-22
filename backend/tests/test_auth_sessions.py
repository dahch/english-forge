"""Cookie sessions, refresh rotation and revocation (ADR-022).

The browser never sees a long-lived bearer token anymore: access + refresh live
in HttpOnly cookies, unsafe cookie requests must echo the CSRF cookie, and a
reused refresh token revokes every session for the account.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from fastapi import HTTPException, Response
from starlette.requests import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth_cookies import (
    ACCESS_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    REFRESH_COOKIE_NAME,
)
from app.dependencies import get_current_user
from app.models.models import Base, RefreshToken, User
from app.routers.auth import login, logout, refresh_session, update_me
from app.schemas.auth import UserLogin, UserUpdate
from app.security import create_access_token, hash_password


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(
        email="cookies@example.com",
        hashed_password=hash_password("correct horse battery"),
        display_name="Cookies",
    )
    db.add(u)
    await db.flush()
    return u


def _request(
    method: str = "GET",
    *,
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Request:
    raw_headers: list[tuple[bytes, bytes]] = [(b"host", b"testserver")]
    if cookies:
        raw_headers.append(
            (b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode())
        )
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode(), value.encode()))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "path": "/api/auth/me",
            "headers": raw_headers,
            "client": ("203.0.113.9", 4321),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def _set_cookie(response: Response, name: str) -> str | None:
    for header in response.headers.getlist("set-cookie"):
        if header.startswith(f"{name}="):
            return header.split(";", 1)[0].split("=", 1)[1]
    return None


def _raw_set_cookie(response: Response, name: str) -> str:
    for header in response.headers.getlist("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    raise AssertionError(f"{name} cookie not set")


async def _login(db: AsyncSession, user: User) -> Response:
    response = Response()
    await login(
        body=UserLogin(email=user.email, password="correct horse battery"),
        request=_request("POST"),
        response=response,
        db=db,
    )
    return response


@pytest.mark.asyncio
async def test_login_sets_httponly_session_cookies(db: AsyncSession, user: User):
    response = await _login(db, user)

    access_header = _raw_set_cookie(response, ACCESS_COOKIE_NAME)
    refresh_header = _raw_set_cookie(response, REFRESH_COOKIE_NAME)
    csrf_header = _raw_set_cookie(response, CSRF_COOKIE_NAME)

    assert "httponly" in access_header.lower()
    assert "httponly" in refresh_header.lower()
    # The CSRF cookie must be readable by the API client.
    assert "httponly" not in csrf_header.lower()
    # Refresh cookie is scoped to the auth endpoints only.
    assert "path=/api/auth" in refresh_header.lower()
    assert _set_cookie(response, REFRESH_COOKIE_NAME)


@pytest.mark.asyncio
async def test_cookie_auth_requires_csrf_for_unsafe_methods(db: AsyncSession, user: User):
    response = await _login(db, user)
    access = _set_cookie(response, ACCESS_COOKIE_NAME)
    csrf = _set_cookie(response, CSRF_COOKIE_NAME)
    assert access and csrf

    get_user = await get_current_user(
        request=_request("GET", cookies={ACCESS_COOKIE_NAME: access}),
        credentials=None,
        db=db,
    )
    assert get_user.id == user.id

    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            request=_request("POST", cookies={ACCESS_COOKIE_NAME: access}),
            credentials=None,
            db=db,
        )
    assert exc.value.status_code == 403

    post_user = await get_current_user(
        request=_request(
            "POST",
            cookies={ACCESS_COOKIE_NAME: access, CSRF_COOKIE_NAME: csrf},
            headers={CSRF_HEADER_NAME: csrf},
        ),
        credentials=None,
        db=db,
    )
    assert post_user.id == user.id


@pytest.mark.asyncio
async def test_bearer_auth_does_not_require_csrf(db: AsyncSession, user: User):
    token = create_access_token({"sub": user.id, "ver": user.token_version})

    class Credentials:
        credentials = token

    authenticated = await get_current_user(
        request=_request("POST"), credentials=Credentials(), db=db
    )
    assert authenticated.id == user.id


@pytest.mark.asyncio
async def test_refresh_rotates_and_detects_reuse(db: AsyncSession, user: User):
    first_response = await _login(db, user)
    first_refresh = _set_cookie(first_response, REFRESH_COOKIE_NAME)
    assert first_refresh

    second_response = Response()
    await refresh_session(
        request=_request("POST", cookies={REFRESH_COOKIE_NAME: first_refresh}),
        response=second_response,
        db=db,
    )
    second_refresh = _set_cookie(second_response, REFRESH_COOKIE_NAME)
    assert second_refresh and second_refresh != first_refresh

    # The presented token is revoked after rotation.
    rows = (
        await db.execute(
            select(RefreshToken).where(RefreshToken.user_id == user.id)
        )
    ).scalars().all()
    revoked = [row for row in rows if row.revoked_at is not None]
    assert revoked

    # Reusing it is treated as theft: every session is revoked, and the
    # outstanding access tokens stop matching token_version.
    with pytest.raises(HTTPException) as exc:
        await refresh_session(
            request=_request("POST", cookies={REFRESH_COOKIE_NAME: first_refresh}),
            response=Response(),
            db=db,
        )
    assert exc.value.status_code == 401

    await db.refresh(user)
    assert user.token_version == 1

    active = (
        await db.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    assert active == []


@pytest.mark.asyncio
async def test_refresh_without_cookie_is_401(db: AsyncSession):
    with pytest.raises(HTTPException) as exc:
        await refresh_session(
            request=_request("POST"), response=Response(), db=db
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_and_clears_cookies(db: AsyncSession, user: User):
    login_response = await _login(db, user)
    refresh = _set_cookie(login_response, REFRESH_COOKIE_NAME)
    assert refresh

    response = Response()
    await logout(
        request=_request("POST", cookies={REFRESH_COOKIE_NAME: refresh}),
        response=response,
        db=db,
    )
    assert response.status_code == 204

    active = (
        await db.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    assert active == []


@pytest.mark.asyncio
async def test_password_change_bumps_version_and_keeps_current_session(
    db: AsyncSession, user: User
):
    old_access = create_access_token({"sub": user.id, "ver": user.token_version})

    response = Response()
    await update_me(
        body=UserUpdate(current_password="correct horse battery", password="a new long password"),
        request=_request("POST"),
        response=response,
        current_user=user,
        db=db,
    )

    await db.refresh(user)
    assert user.token_version == 1

    # Old access token no longer matches the user's version.
    class Credentials:
        credentials = old_access

    with pytest.raises(HTTPException) as exc:
        await get_current_user(request=_request("GET"), credentials=Credentials(), db=db)
    assert exc.value.status_code == 401

    # The device that performed the change received fresh cookies.
    new_access = _set_cookie(response, ACCESS_COOKIE_NAME)
    assert new_access and new_access != old_access


@pytest.mark.asyncio
async def test_expired_access_token_is_rejected(db: AsyncSession, user: User):
    expired = create_access_token(
        {"sub": user.id, "ver": user.token_version}, expires_delta=timedelta(seconds=-1)
    )

    class Credentials:
        credentials = expired

    with pytest.raises(HTTPException) as exc:
        await get_current_user(request=_request("GET"), credentials=Credentials(), db=db)
    assert exc.value.status_code == 401
