"""Dashboard API key pages: deep-link prefill, create once, revoke, auth."""

from __future__ import annotations

import re
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api_server.auth.api_key_auth import validate_api_key
from api_server.dashboard.api_keys import _parse_expiration, create_link
from api_server.server import app
from common import global_config
from db.models.api_keys import APIKey
from tests.test_mcp_e2e import _patch_db
from tests.test_template import TestTemplate

_CREATE = "/dashboard/settings/api-keys/create"


@contextmanager
def _client():
    with (
        _patch_db() as factory,
        patch.object(global_config, "WORKOS_CLIENT_ID", None),
        patch.object(global_config, "WORKOS_API_KEY", None),
        TestClient(app) as client,
    ):
        yield client, factory


def _login(client, user="human", next_path="/dashboard/approvals"):
    resp = client.post(
        "/dashboard/login",
        data={"user_id": user, "next": next_path},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return client.get("/api/v1/dashboard/session").json()["csrf_token"], resp


def _find(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    assert match is not None, pattern
    return match.group(1)


def _csrf(page: str) -> str:
    return _find(r"name='csrf' value='([^']+)'", page)


class TestDeepLink(TestTemplate):
    def test_prefills_and_creates_nothing(self):
        with _client() as (client, factory):
            _login(client)
            page = client.get(f"{_CREATE}?name=ci-bot&expiration=30d&scope=read_only")
            assert page.status_code == 200
            assert "value='ci-bot'" in page.text
            assert "<option value='30d' selected>" in page.text
            assert "<option value='read_only' selected>" in page.text
            with factory() as s:
                assert s.query(APIKey).count() == 0  # GET never mints a key

    def test_signed_out_link_survives_sign_in(self):
        with _client() as (client, _):
            link = f"{_CREATE}?name=ci-bot&expiration=7d"
            first = client.get(link, follow_redirects=False)
            assert first.status_code == 303
            assert "/dashboard/login" in first.headers["location"]
            _, resp = _login(client, next_path=link)
            assert resp.headers["location"] == link

    def test_bad_values_are_ignored_with_a_note(self):
        with _client() as (client, _):
            _login(client)
            page = client.get(f"{_CREATE}?expiration=forever&scope=root")
            assert "Ignored expiration" in page.text and "Ignored scope" in page.text
            assert "<option value='standard' selected>" in page.text

    @pytest.mark.parametrize(
        ("raw", "days"), [("none", None), ("30d", 30), ("365d", 365), (None, None)]
    )
    def test_expiration_values(self, raw, days):
        assert _parse_expiration(raw)[1] == days

    def test_out_of_range_expiration(self):
        assert _parse_expiration("400d")[2] is not None

    def test_create_link(self):
        link = create_link(name="ci bot", expiration="30d", scope="read_only")
        assert link.endswith(
            "/dashboard/settings/api-keys/create?name=ci+bot&expiration=30d&scope=read_only"
        )


class TestCreateAndRevoke(TestTemplate):
    def test_create_shows_key_once_and_it_works(self):
        with _client() as (client, factory):
            _login(client)
            page = client.get(f"{_CREATE}?name=ci-bot&expiration=30d&scope=read_only")
            made = client.post(
                _CREATE,
                data={
                    "csrf": _csrf(page.text),
                    "name": "ci-bot",
                    "expiration": "30d",
                    "scope": "read_only",
                },
            )
            assert made.status_code == 200
            raw = _find(r"(sk_[0-9a-f]+)", made.text)
            assert made.headers["cache-control"] == "no-store"
            with factory() as s:
                row = validate_api_key(s, raw)
                assert row is not None and row.user_id == "human"
                assert row.name == "ci-bot"
                assert "services:execute" not in (row.scopes or [])
            listing = client.get("/dashboard/settings/api-keys")
            assert "ci-bot" in listing.text and raw not in listing.text

    def test_create_needs_csrf(self):
        with _client() as (client, factory):
            _login(client)
            resp = client.post(_CREATE, data={"name": "x"})
            assert resp.status_code == 403
            with factory() as s:
                assert s.query(APIKey).count() == 0

    def test_revoke_only_own_keys(self):
        with _client() as (client, factory):
            _login(client, user="eve")
            page = client.get(_CREATE)
            client.post(_CREATE, data={"csrf": _csrf(page.text), "name": "mine"})
            with factory() as s:
                key_id = s.query(APIKey).one().id
            mallory_csrf, _ = _login(client, user="mallory")
            listing = client.get("/dashboard/settings/api-keys")
            assert "mine" not in listing.text
            resp = client.post(
                f"/dashboard/settings/api-keys/{key_id}/revoke",
                data={"csrf": mallory_csrf},
                follow_redirects=False,
            )
            assert resp.status_code == 303  # a valid request, just not her key
            with factory() as s:
                assert s.query(APIKey).one().revoked is False

    def test_api_key_is_not_a_session(self):
        with _client() as (client, _):
            resp = client.get(
                "/dashboard/settings/api-keys",
                headers={"X-API-KEY": "sk_x"},
                follow_redirects=False,
            )
            assert resp.status_code == 303
