import pytest
from pydantic import ValidationError

from app.core.config import DEVELOPMENT_JWT_SECRET, Settings

CUSTOM_JWT_SECRET = "s1-test-only-custom-secret-with-sufficient-random-looking-length"


def _settings(**overrides) -> Settings:
    values = {
        "DATABASE_URL": "postgresql+asyncpg://test:test@localhost/test",
        "DATABASE_URL_SYNC": "postgresql+psycopg://test:test@localhost/test",
        "JWT_SECRET_KEY": CUSTOM_JWT_SECRET,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_development_accepts_documented_example_jwt_secret():
    settings = _settings(ENVIRONMENT="development", JWT_SECRET_KEY=DEVELOPMENT_JWT_SECRET)

    assert settings.JWT_SECRET_KEY.get_secret_value() == DEVELOPMENT_JWT_SECRET


def test_production_rejects_missing_jwt_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            ENVIRONMENT="production",
            DATABASE_URL="postgresql+asyncpg://test:test@localhost/test",
            DATABASE_URL_SYNC="postgresql+psycopg://test:test@localhost/test",
        )

    assert "JWT_SECRET_KEY" in str(exc_info.value)


def test_production_rejects_documented_example_without_exposing_it():
    with pytest.raises(ValidationError) as exc_info:
        _settings(ENVIRONMENT="production", JWT_SECRET_KEY=DEVELOPMENT_JWT_SECRET)

    message = str(exc_info.value)
    assert "JWT secret must be explicitly configured" in message
    assert DEVELOPMENT_JWT_SECRET not in message


def test_production_accepts_custom_jwt_secret():
    settings = _settings(ENVIRONMENT="production")

    assert settings.JWT_SECRET_KEY.get_secret_value() == CUSTOM_JWT_SECRET


@pytest.mark.parametrize("environment", ["development", "production"])
def test_empty_jwt_secret_is_rejected(environment):
    with pytest.raises(ValidationError) as exc_info:
        _settings(ENVIRONMENT=environment, JWT_SECRET_KEY="   ")

    assert "JWT secret must not be empty" in str(exc_info.value)


def test_secret_is_redacted_from_unrelated_configuration_errors():
    with pytest.raises(ValidationError) as exc_info:
        _settings(
            ENVIRONMENT="production",
            STALE_REPORT_AFTER_SECONDS=900,
        )

    message = str(exc_info.value)
    assert CUSTOM_JWT_SECRET not in message
    assert CUSTOM_JWT_SECRET not in repr(exc_info.value)
    assert "input_value" not in message

    settings = _settings(ENVIRONMENT="production")
    assert CUSTOM_JWT_SECRET not in repr(settings)
    assert "**********" in repr(settings)


