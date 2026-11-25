from collections.abc import Callable, Iterator

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common import global_config


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "order: mark test to run in a specific order")
    config.addinivalue_line(
        "markers", "e2e: mark test as end-to-end (requires live services)"
    )


@pytest.fixture(scope="session", autouse=True)
def _no_real_feedback_sinks() -> Iterator[None]:
    """Blank every feedback-sink secret for the whole run.

    Any test that calls ``feedback()`` (directly or over a transport) would
    otherwise post to the Slack/Linear of whoever's ``.env`` is loaded. Tests
    that exercise a sink patch its settings back in and mock the HTTP.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(global_config, "FEEDBACK_SLACK_WEBHOOK_URL", None)
        mp.setattr(global_config, "FEEDBACK_LINEAR_API_KEY", None)
        mp.setattr(global_config.feedback.linear, "team_id", None)
        yield


@pytest.fixture(scope="session", autouse=True)
def _no_optional_pdf_fonts(tmp_path_factory) -> Iterator[None]:
    """Default PDF font chain = committed fonts only, for every test.

    ``make pdf_fonts_cjk`` writes opt-in Chinese/Korean fonts into the
    checkout; without this, local runs would cover Hangul while CI refuses
    it. Tests that exercise the opt-in path patch ``OPTIONAL_DIR`` back.
    """
    from services import pdf_fonts  # noqa: PLC0415 - keep conftest import-light

    empty = tmp_path_factory.mktemp("no-optional-pdf-fonts")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pdf_fonts, "OPTIONAL_DIR", empty)
        yield


@pytest.fixture
def feedback_db() -> Iterator[sessionmaker]:
    """Point ``db.engine`` at a fresh in-memory SQLite; yield the factory."""
    from db import engine as db_engine  # noqa: PLC0415
    from db.base import Base  # noqa: PLC0415

    orig_engine, orig_session = db_engine._engine, db_engine._SessionLocal
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    db_engine._engine, db_engine._SessionLocal = eng, factory
    try:
        yield factory
    finally:
        db_engine._engine, db_engine._SessionLocal = orig_engine, orig_session


@pytest.fixture
def isolated_sinks() -> Iterator[None]:
    """Run with no registered feedback sinks; restore the registry after."""
    from services import feedback_svc  # noqa: PLC0415

    saved = list(feedback_svc._sinks)
    feedback_svc._sinks.clear()
    try:
        yield
    finally:
        feedback_svc._sinks[:] = saved


class SinkHttp:
    """Recorder for ``sink_http``: set ``handler``; read ``requests``."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handler: Callable[[httpx.Request], httpx.Response] = lambda _r: (
            httpx.Response(200, json={})
        )

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


@pytest.fixture
def sink_http(monkeypatch: pytest.MonkeyPatch) -> SinkHttp:
    """Route every ``SinkClient`` request through an in-memory handler."""
    from services.feedback_sinks import base  # noqa: PLC0415

    rec = SinkHttp()

    def client() -> httpx.Client:
        transport = httpx.MockTransport(rec._dispatch)
        return httpx.Client(transport=transport, follow_redirects=False)

    monkeypatch.setattr(base, "_new_client", client)
    return rec
