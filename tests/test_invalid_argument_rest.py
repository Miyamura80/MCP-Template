"""A service's ``InvalidArgument`` over REST: 400, and the idempotency claim is released."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.auth import AuthenticatedUser, get_authenticated_user
from api_server.server import app
from db.base import Base
from tests.test_template import TestTemplate


def _override_auth():
    return AuthenticatedUser(user_id="test-user", email="t@t.com", auth_method="jwt")


class TestInvalidArgumentOverREST(TestTemplate):
    """A service's ``InvalidArgument`` is a 400 and releases the idempotency claim."""

    ROUTE = "/api/v1/services/gmail_reply_to_thread"

    def setup_method(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        session_local = sessionmaker(bind=self.engine, expire_on_commit=False)

        @contextmanager
        def _ctx():
            session = session_local()
            try:
                yield session
            finally:
                session.close()

        # A thread with no messages: gmail_reply_to_thread rejects it before
        # creating any draft.
        self.mock_svc = MagicMock()
        self.mock_svc.users().threads().get().execute.return_value = {"messages": []}
        self._thread_get = self.mock_svc.users().threads().get().execute
        self._thread_get.reset_mock()

        app.dependency_overrides[get_authenticated_user] = _override_auth
        self._patchers = [
            patch("api_server.idempotency.use_db_session", _ctx),
            patch("api_server.billing.limits.use_db_session", _ctx),
            patch("api_server.idempotency.random.random", return_value=1.0),
            patch(
                "services.gmail_drafts_svc._get_gmail_client",
                return_value=self.mock_svc,
            ),
        ]
        for p in self._patchers:
            p.start()
        self.client = TestClient(app, raise_server_exceptions=False)

    def teardown_method(self):
        for p in self._patchers:
            p.stop()
        app.dependency_overrides.clear()

    def test_invalid_argument_is_400_and_retryable(self):
        headers = {"Idempotency-Key": "k-invalid"}
        payload = {"thread_id": "t-empty", "body": "hi"}
        first = self.client.post(self.ROUTE, json=payload, headers=headers)
        assert first.status_code == 400, first.text
        assert "has no messages to reply to" in first.json()["error"]["message"]
        # The claim was released: the same key runs again instead of 409.
        second = self.client.post(self.ROUTE, json=payload, headers=headers)
        assert second.status_code == 400, second.text
        assert self._thread_get.call_count == 2
