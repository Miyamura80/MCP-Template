"""Integration tests for API server route registration."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.auth import AuthenticatedUser, get_authenticated_user
from api_server.server import app
from db.base import Base
from db.engine import get_db_session
from tests.test_template import TestTemplate

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_engine)
_SessionLocal = sessionmaker(bind=_engine)


def _override_auth():
    return AuthenticatedUser(user_id="test-user", email="t@t.com", auth_method="jwt")


def _override_db():
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


class TestAPIServer(TestTemplate):
    def setup_method(self):
        app.dependency_overrides[get_authenticated_user] = _override_auth
        app.dependency_overrides[get_db_session] = _override_db
        self.client = TestClient(app)

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_health_endpoint(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_greet_service(self):
        resp = self.client.post(
            "/api/v1/services/greet",
            json={"name": "World"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Hello, World!"

    def test_service_requires_auth(self):
        """Without auth override, endpoints should require credentials."""
        # Remove only the auth override; keep the DB override
        app.dependency_overrides.pop(get_authenticated_user, None)
        with patch("api_server.auth.workos_auth.global_config") as mock_config:
            mock_config.WORKOS_CLIENT_ID = None
            client = TestClient(app)
            resp = client.post(
                "/api/v1/services/greet",
                json={"name": "World"},
            )
            assert resp.status_code == 401

    def test_me_endpoint(self):
        resp = self.client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "test-user"
        assert data["auth_method"] == "jwt"

    def test_service_routes_registered(self):
        """All services should have corresponding API routes."""
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/v1/services/greet" in routes
        assert "/api/v1/services/config_show" in routes
