from uuid import uuid4

import jwt
import pytest

from app.core import security
from app.core.config import Settings


async def test_register_login_me_flow(client):
    register_resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "test@example.com", "password": "supersecret123"},
    )
    assert register_resp.status_code == 201
    body = register_resp.json()
    assert body["email"] == "test@example.com"
    assert "password" not in body
    assert "password_hash" not in body

    duplicate_resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "test@example.com", "password": "supersecret123"},
    )
    assert duplicate_resp.status_code == 409

    bad_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com", "password": "wrongpassword"},
    )
    assert bad_login.status_code == 401

    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com", "password": "supersecret123"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    me_resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "test@example.com"

    unauth_resp = await client.get("/api/v1/auth/me")
    assert unauth_resp.status_code == 401


def test_custom_secret_preserves_jwt_signing_and_verification(monkeypatch):
    settings = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        DATABASE_URL="postgresql+asyncpg://test:test@localhost/test",
        DATABASE_URL_SYNC="postgresql+psycopg://test:test@localhost/test",
        JWT_SECRET_KEY="s1-test-only-custom-secret-with-sufficient-random-looking-length",
    )
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    subject = uuid4()

    token = security.create_access_token(subject=subject, role="user")
    payload = security.decode_access_token(token)

    assert payload["sub"] == str(subject)
    assert payload["role"] == "user"
    assert {"iat", "exp"}.issubset(payload)

    wrong_settings = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        DATABASE_URL="postgresql+asyncpg://test:test@localhost/test",
        DATABASE_URL_SYNC="postgresql+psycopg://test:test@localhost/test",
        JWT_SECRET_KEY="different-s1-test-only-custom-secret",
    )
    monkeypatch.setattr(security, "get_settings", lambda: wrong_settings)
    with pytest.raises(jwt.InvalidSignatureError):
        security.decode_access_token(token)
