"""The CIMD fetch deadline holds against servers that stall mid-response.

See ``tests/stall_server.py`` for the attacks.
"""

import threading
import time
from unittest.mock import patch

import pytest

from api_server.oauth import cimd
from services._ssrf_guard import PinnedTarget
from tests.oauth_server_fixture import client_document
from tests.stall_server import StallServer
from tests.test_template import TestTemplate

_DEADLINE = 0.5
_LIMIT = 1.5  # what a fetch may take, deadline plus generous slack


class TestCimdFetchDeadline(TestTemplate):
    @pytest.fixture(autouse=True)
    def _server(self):
        cimd.clear_cache()
        self.stall_server = StallServer()
        with (
            patch.object(cimd, "_FETCH_DEADLINE_SECONDS", _DEADLINE),
            patch.object(cimd, "validate_and_pin", side_effect=self._pin),
        ):
            yield
        self.stall_server.close()
        cimd.clear_cache()

    def _pin(self, client_id: str) -> PinnedTarget:
        # https://client.test.example/<path> -> the loopback server's <path>
        path = "/" + client_id.split("/", 3)[3]
        return PinnedTarget(url=self.stall_server.url(path))

    @pytest.mark.parametrize("mode", ["flood", "header", "trailer"])
    def test_stalling_server_is_cut_off_at_the_deadline(self, mode):
        start = time.monotonic()
        with pytest.raises(cimd.ClientMetadataError):
            cimd._fetch_document(f"https://client.test.example/{mode}.json")
        assert time.monotonic() - start < _LIMIT

    def test_stalled_fetches_free_their_slots_at_the_deadline(self):
        errors: list[Exception] = []

        def hold(n: int) -> None:
            try:
                cimd.resolve_client(f"https://client.test.example/header-{n}.json")
            except cimd.ClientMetadataError as exc:
                errors.append(exc)

        holders = [threading.Thread(target=hold, args=(n,)) for n in range(4)]
        for thread in holders:
            thread.start()
        time.sleep(0.2)  # let all four take a slot
        start = time.monotonic()
        with pytest.raises(cimd.ClientMetadataError, match="too many"):
            cimd.resolve_client("https://client.test.example/ok.json")
        assert time.monotonic() - start < 0.1  # refused, not queued

        for thread in holders:
            thread.join(timeout=_LIMIT)
        assert not any(thread.is_alive() for thread in holders)
        assert len(errors) == 4

        client_id = "https://client.test.example/ok.json"
        self.stall_server.documents["/ok.json"] = client_document(client_id=client_id)
        assert cimd.resolve_client(client_id).client_id == client_id
