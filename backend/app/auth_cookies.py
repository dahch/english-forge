"""Session cookies for the browser auth flow.

The API used to hand the frontend a long-lived bearer token that lived in
localStorage — readable by any XSS. Sessions now ride on:

- `ef_access`  HttpOnly, short-lived (15 min), path `/`
- `ef_refresh` HttpOnly, rotating (30 days), path `/api/auth` so it is only
  sent to the endpoints that need it
- `ef_csrf`    deliberately readable by JS; the API client echoes it in the
  `X-CSRF-Token` header for unsafe methods (double-submit defence)

Bearer auth is still accepted for scripts and API clients (see
`get_current_user`), which is why the login response keeps `access_token`.
"""

from __future__ import annotations

from fastapi import Response

from app.config import get_settings

ACCESS_COOKIE_NAME = "ef_access"
REFRESH_COOKIE_NAME = "ef_refresh"
CSRF_COOKIE_NAME = "ef_csrf"
CSRF_HEADER_NAME = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

_REFRESH_COOKIE_PATH = "/api/auth"


def _samesite() -> str:
    value = (get_settings().COOKIE_SAMESITE or "lax").lower()
    return value if value in {"lax", "strict", "none"} else "lax"


def _common() -> dict:
    settings = get_settings()
    return {
        "secure": settings.COOKIE_SECURE,
        "samesite": _samesite(),
        "domain": settings.COOKIE_DOMAIN or None,
    }


def set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    csrf_token: str,
) -> None:
    settings = get_settings()
    common = _common()
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        access_token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        path="/",
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        httponly=True,
        path=_REFRESH_COOKIE_PATH,
        **common,
    )
    # Must be readable by the API client to be echoed as a header.
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        httponly=False,
        path="/",
        **common,
    )


def clear_auth_cookies(response: Response) -> None:
    common = _common()
    response.delete_cookie(ACCESS_COOKIE_NAME, path="/", **common)
    response.delete_cookie(REFRESH_COOKIE_NAME, path=_REFRESH_COOKIE_PATH, **common)
    response.delete_cookie(CSRF_COOKIE_NAME, path="/", **common)
