"""Minimal single-operator authentication for the private production workspace."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response as StarletteResponse

from app.config import settings

SESSION_COOKIE = "market_monitor_session"
CSRF_HEADER = "X-Market-Monitor-CSRF"
PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000
PUBLIC_PATHS = frozenset({"/health", "/api/auth/status", "/api/auth/login"})


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class AuthStatus(BaseModel):
    enabled: bool
    authenticated: bool


@dataclass(frozen=True)
class PasswordHash:
    iterations: int
    salt: bytes
    digest: bytes


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Return the deployment-safe password hash accepted by the login route."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}${_b64encode(salt)}${_b64encode(digest)}"


def _parse_password_hash(encoded: str | None) -> PasswordHash | None:
    if not encoded:
        return None
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        iterations = int(iterations_text)
        salt = _b64decode(salt_text)
        digest = _b64decode(digest_text)
    except (TypeError, ValueError):
        return None
    if (
        algorithm != PBKDF2_ALGORITHM
        or iterations < 100_000
        or iterations > 2_000_000
        or len(salt) < 16
        or len(digest) != 32
    ):
        return None
    return PasswordHash(iterations=iterations, salt=salt, digest=digest)


def verify_password(password: str, encoded: str | None) -> bool:
    parsed = _parse_password_hash(encoded)
    if parsed is None:
        hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), b"\0" * 16, PBKDF2_ITERATIONS
        )
        return False
    supplied = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), parsed.salt, parsed.iterations
    )
    return hmac.compare_digest(supplied, parsed.digest)


def _session_key() -> bytes | None:
    secret = settings.APP_AUTH_SESSION_SECRET
    password_hash = settings.APP_AUTH_PASSWORD_HASH
    if not secret or len(secret) < 32 or _parse_password_hash(password_hash) is None:
        return None
    # Rotating either deployment secret or the password invalidates old sessions.
    return hmac.new(
        secret.encode("utf-8"), password_hash.encode("utf-8"), hashlib.sha256
    ).digest()


def create_session_token(now: int | None = None) -> str:
    key = _session_key()
    if key is None:
        raise RuntimeError("Application authentication is not configured")
    issued_at = int(time.time() if now is None else now)
    expires_at = issued_at + settings.APP_AUTH_SESSION_TTL_SECONDS
    payload = f"v1.{expires_at}.{secrets.token_urlsafe(18)}"
    signature = hmac.new(key, payload.encode("ascii"), hashlib.sha256).digest()
    return f"{payload}.{_b64encode(signature)}"


def valid_session_token(token: str | None, now: int | None = None) -> bool:
    key = _session_key()
    if key is None or not token:
        return False
    try:
        version, expires_text, nonce, signature_text = token.split(".", 3)
        expires_at = int(expires_text)
        signature = _b64decode(signature_text)
    except (TypeError, ValueError):
        return False
    current = int(time.time() if now is None else now)
    if version != "v1" or not nonce or expires_at <= current:
        return False
    payload = f"{version}.{expires_at}.{nonce}"
    expected = hmac.new(key, payload.encode("ascii"), hashlib.sha256).digest()
    return hmac.compare_digest(signature, expected)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    expected = f"{request.url.scheme}://{request.url.netloc}"
    return hmac.compare_digest(origin.rstrip("/"), expected)


def _is_authenticated(request: Request) -> bool:
    return valid_session_token(request.cookies.get(SESSION_COOKIE))


class SingleUserAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> StarletteResponse:
        if not settings.APP_AUTH_ENABLED:
            return await call_next(request)

        path = request.url.path
        if path in PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        # Cron uses its separate server-to-server bearer credential. Its route
        # guard fails closed when that credential is unset.
        if path.startswith("/api/cron/"):
            return await call_next(request)

        if not _is_authenticated(request):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Authentication required"},
                headers={"Cache-Control": "no-store"},
            )

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get(CSRF_HEADER) != "1" or not _same_origin(request):
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Invalid request origin"},
                    headers={"Cache-Control": "no-store"},
                )
        return await call_next(request)


router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.get("/status", response_model=AuthStatus)
async def auth_status(request: Request) -> AuthStatus:
    enabled = settings.APP_AUTH_ENABLED
    return AuthStatus(
        enabled=enabled,
        authenticated=not enabled or _is_authenticated(request),
    )


@router.post("/login", response_model=AuthStatus)
async def login(payload: LoginRequest, request: Request, response: Response) -> AuthStatus:
    if not settings.APP_AUTH_ENABLED:
        return AuthStatus(enabled=False, authenticated=True)
    if _session_key() is None:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    if not _same_origin(request) or not verify_password(
        payload.password, settings.APP_AUTH_PASSWORD_HASH
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(),
        max_age=settings.APP_AUTH_SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return AuthStatus(enabled=True, authenticated=True)


@router.post("/logout", response_model=AuthStatus)
async def logout(response: Response) -> AuthStatus:
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return AuthStatus(enabled=settings.APP_AUTH_ENABLED, authenticated=False)
