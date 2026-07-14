"""Unit tests for the SEP-1036 not-connected -> URL elicitation conversion.

Wire-level coverage (the -32042 JSON-RPC error as serialized over streamable
HTTP) lives in ``tests/test_mcp_e2e.py``; this module covers the conversion
policy in ``mcp_server/url_elicitation.py`` directly.
"""

from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.shared.exceptions import UrlElicitationRequiredError
from mcp.types import (
    URL_ELICITATION_REQUIRED,
    ClientCapabilities,
    ElicitationCapability,
    FormElicitationCapability,
    UrlElicitationCapability,
)

from common import global_config
from mcp_server.url_elicitation import _client_url_support, raise_connect_elicitation
from services.gmail_svc import GmailNotConnectedError
from tests.test_template import TestTemplate


def _session_with(capabilities: ClientCapabilities | None) -> SimpleNamespace:
    """Fake ServerSession: client_params present iff capabilities given."""
    if capabilities is None:
        return SimpleNamespace(client_params=None)
    return SimpleNamespace(client_params=SimpleNamespace(capabilities=capabilities))


def _google_configured():
    return (
        patch.object(global_config, "GOOGLE_CLIENT_ID", "test-client"),
        patch.object(
            global_config,
            "GOOGLE_REDIRECT_URI",
            "http://localhost:8000/api/v1/auth/google/callback",
        ),
    )


class TestClientUrlSupport(TestTemplate):
    def test_unknown_when_no_client_params(self):
        # The production stateless-HTTP case: initialize never reached this
        # transport, so capabilities are unknowable.
        assert _client_url_support(_session_with(None)) is None

    def test_unknown_when_no_session(self):
        # Direct in-process invocation (no MCP request context at all).
        assert _client_url_support(None) is None

    def test_false_when_client_declares_no_elicitation(self):
        session = _session_with(ClientCapabilities())
        assert _client_url_support(session) is False

    def test_false_when_client_declares_form_only(self):
        # Spec back-compat: `elicitation: {}` and form-only both mean no url.
        for cap in (
            ElicitationCapability(),
            ElicitationCapability(form=FormElicitationCapability()),
        ):
            session = _session_with(ClientCapabilities(elicitation=cap))
            assert _client_url_support(session) is False

    def test_true_when_client_declares_url(self):
        cap = ElicitationCapability(url=UrlElicitationCapability())
        session = _session_with(ClientCapabilities(elicitation=cap))
        assert _client_url_support(session) is True


class TestRaiseConnectElicitation(TestTemplate):
    def test_converts_when_client_declares_url(self):
        cap = ElicitationCapability(url=UrlElicitationCapability())
        session = _session_with(ClientCapabilities(elicitation=cap))
        exc = GmailNotConnectedError("user-7")
        p1, p2 = _google_configured()
        with p1, p2, pytest.raises(UrlElicitationRequiredError) as excinfo:
            raise_connect_elicitation(session, exc)

        err = excinfo.value.error
        assert err.code == URL_ELICITATION_REQUIRED == -32042
        elicitations = excinfo.value.elicitations
        assert len(elicitations) == 1
        elic = elicitations[0]
        assert elic.mode == "url"
        assert elic.elicitationId.startswith("gmail-connect-")

        parsed = urlparse(elic.url)
        assert parsed.netloc == "accounts.google.com"
        # Identity binding per the spec: the signed state in the auth URL
        # carries the user, not any client-supplied value.
        assert parse_qs(parsed.query)["state"]

        # The top-level message must keep naive hosts (no -32042 support)
        # self-recovering: original recovery script + the raw auth URL.
        assert "gmail_connect" in err.message
        assert elic.url in err.message
        assert excinfo.value.__cause__ is exc

    def test_converts_when_capabilities_unknown(self):
        exc = GmailNotConnectedError("user-8")
        p1, p2 = _google_configured()
        with p1, p2, pytest.raises(UrlElicitationRequiredError):
            raise_connect_elicitation(_session_with(None), exc)

    def test_returns_when_client_declares_form_only(self):
        session = _session_with(ClientCapabilities(elicitation=ElicitationCapability()))
        p1, p2 = _google_configured()
        with p1, p2:
            # No raise: caller re-raises the original textual error instead.
            raise_connect_elicitation(session, GmailNotConnectedError("user-9"))

    def test_returns_when_google_oauth_unconfigured(self):
        with patch.object(global_config, "GOOGLE_CLIENT_ID", None):
            raise_connect_elicitation(
                _session_with(None), GmailNotConnectedError("user-10")
            )

    def test_elicitation_ids_are_unique(self):
        exc = GmailNotConnectedError("user-11")
        p1, p2 = _google_configured()
        ids = set()
        with p1, p2:
            for _ in range(3):
                with pytest.raises(UrlElicitationRequiredError) as excinfo:
                    raise_connect_elicitation(_session_with(None), exc)
                ids.add(excinfo.value.elicitations[0].elicitationId)
        assert len(ids) == 3
