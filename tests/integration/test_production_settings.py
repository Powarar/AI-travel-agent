import pytest
from fastapi.testclient import TestClient

from services.backend.app.main import create_app


def test_production_rejects_missing_signing_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="AUTH_JWT_SECRET"), TestClient(create_app()):
        pass
