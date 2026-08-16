from __future__ import annotations

import httpx
import pytest

from app.auth import (
    CSRF_HEADER,
    SESSION_COOKIE,
    create_session_token,
    hash_password,
    valid_session_token,
    verify_password,
)
from app.config import settings
from app.main import app

pytestmark = pytest.mark.asyncio


def configure_auth(monkeypatch, password: str = "correct horse battery staple") -> None:
    monkeypatch.setattr(settings, "APP_AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "APP_AUTH_PASSWORD_HASH", hash_password(password))
    monkeypatch.setattr(settings, "APP_AUTH_SESSION_SECRET", "s" * 48)
    monkeypatch.setattr(settings, "APP_AUTH_SESSION_TTL_SECONDS", 3600)


async def test_password_hash_and_signed_session_reject_wrong_or_tampered_values(monkeypatch):
    configure_auth(monkeypatch)
    assert verify_password("correct horse battery staple", settings.APP_AUTH_PASSWORD_HASH)
    assert not verify_password("wrong password", settings.APP_AUTH_PASSWORD_HASH)

    token = create_session_token(now=1_000)
    assert valid_session_token(token, now=1_001)
    assert not valid_session_token(token, now=4_601)
    assert not valid_session_token(f"{token[:-1]}x", now=1_001)


async def test_unauthenticated_direct_api_calls_cannot_read_or_mutate(monkeypatch):
    configure_auth(monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://market.example") as client:
        for method, path in (
            ("GET", "/api/competitors"),
            ("POST", "/api/sync/all"),
            ("PUT", "/api/settings"),
            ("DELETE", "/api/competitors/1"),
        ):
            response = await client.request(method, path, json={})
            assert response.status_code == 401
            assert response.json() == {"detail": "Authentication required"}
        assert (await client.get("/health")).status_code == 200


async def test_login_uses_secure_cookie_and_mutations_require_csrf(monkeypatch):
    configure_auth(monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://market.example") as client:
        wrong = await client.post("/api/auth/login", json={"password": "wrong password"})
        assert wrong.status_code == 401

        logged_in = await client.post(
            "/api/auth/login", json={"password": "correct horse battery staple"}
        )
        assert logged_in.status_code == 200
        cookie = logged_in.headers["set-cookie"]
        assert f"{SESSION_COOKIE}=" in cookie
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=strict" in cookie
        assert "Max-Age=3600" in cookie
        assert (await client.get("/api/auth/status")).json() == {
            "enabled": True,
            "authenticated": True,
        }

        missing_csrf = await client.post("/api/auth/logout")
        assert missing_csrf.status_code == 403
        cross_origin = await client.post(
            "/api/auth/logout",
            headers={CSRF_HEADER: "1", "Origin": "https://attacker.example"},
        )
        assert cross_origin.status_code == 403
        logged_out = await client.post(
            "/api/auth/logout",
            headers={CSRF_HEADER: "1", "Origin": "https://market.example"},
        )
        assert logged_out.status_code == 200
        assert (await client.get("/api/auth/status")).json() == {
            "enabled": True,
            "authenticated": False,
        }


async def test_enabled_but_incomplete_auth_configuration_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "APP_AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "APP_AUTH_PASSWORD_HASH", None)
    monkeypatch.setattr(settings, "APP_AUTH_SESSION_SECRET", None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://market.example") as client:
        protected = await client.post("/api/sync/all")
        assert protected.status_code == 401
        login = await client.post("/api/auth/login", json={"password": "anything"})
        assert login.status_code == 503
        assert login.json() == {"detail": "Authentication is not configured"}


async def test_cron_fails_closed_without_its_server_secret(monkeypatch):
    monkeypatch.setattr(settings, "APP_AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "CRON_SECRET", None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://market.example") as client:
        assert (await client.get("/api/cron/scan-due")).status_code == 401
        assert (await client.get("/api/cron/daily")).status_code == 401
