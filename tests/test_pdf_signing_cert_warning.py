"""Loud warning when a non-dev deployment seals with the self-signed dev cert.

Issue #206: with ``pdf_forms.signing.cert_path``/``key_path`` unset, the
server silently falls back to an auto-generated self-signed certificate whose
seals validate as untrusted. Outside dev that must be surfaced at startup and
on the first seal, once per process.
"""

import asyncio
import tempfile
from contextlib import asynccontextmanager
from unittest.mock import patch

from loguru import logger

from api_server import server
from common import global_config
from services import pdf_signing
from tests.test_template import TestTemplate


class TestUntrustedSealWarning(TestTemplate):
    def setup_method(self):
        signing = global_config.pdf_forms.signing
        self._patchers: list = [
            patch.object(pdf_signing, "_untrusted_seal_warned", False),
            patch.object(signing, "cert_path", None),
            patch.object(signing, "key_path", None),
            patch.object(
                signing, "dev_cert_dir", tempfile.mkdtemp(prefix="pdf-seal-warn-")
            ),
        ]
        for p in self._patchers:
            p.start()
        self.records: list = []
        self._sink = logger.add(
            self.records.append, level="WARNING", format="{message}"
        )

    def teardown_method(self):
        logger.remove(self._sink)
        for p in reversed(self._patchers):
            p.stop()

    def _patch(self, target, name: str, value) -> None:
        patcher = patch.object(target, name, value)
        patcher.start()
        self._patchers.append(patcher)

    def _set_env(self, value: str) -> None:
        self._patch(global_config, "DEV_ENV", value)

    def _warnings(self) -> list[str]:
        return [
            r.record["message"]
            for r in self.records
            if r.record["level"].name == "WARNING"
            and "SELF-SIGNED" in r.record["message"]
        ]

    def test_prod_without_cert_warns(self):
        self._set_env("prod")
        assert pdf_signing.warn_if_untrusted_seal() is True
        messages = self._warnings()
        assert len(messages) == 1
        assert "UNTRUSTED" in messages[0]
        assert "DEV_ENV=prod" in messages[0]

    def test_warns_only_once_per_process(self):
        self._set_env("prod")
        assert pdf_signing.warn_if_untrusted_seal() is True
        assert pdf_signing.warn_if_untrusted_seal() is True
        assert len(self._warnings()) == 1

    def test_dev_is_silent(self):
        for env in ("dev", "local"):
            self._set_env(env)
            assert pdf_signing.warn_if_untrusted_seal() is False
        assert self._warnings() == []

    def test_unknown_env_fails_secure(self):
        self._set_env("staging")
        assert pdf_signing.warn_if_untrusted_seal() is True
        assert len(self._warnings()) == 1

    def test_configured_cert_is_silent(self):
        self._set_env("prod")
        signing = global_config.pdf_forms.signing
        self._patch(signing, "cert_path", "/etc/seal/cert.pem")
        self._patch(signing, "key_path", "/etc/seal/key.pem")
        assert pdf_signing.warn_if_untrusted_seal() is False
        assert self._warnings() == []

    def test_first_seal_fallback_warns(self):
        self._set_env("prod")
        cert_path, key_path = pdf_signing.sealing_cert_paths()
        assert cert_path.exists() and key_path.exists()
        assert len(self._warnings()) == 1

    def test_startup_lifespan_warns(self):
        self._set_env("prod")

        async def _noop_lifespan(*_args):
            yield

        stub = asynccontextmanager(_noop_lifespan)
        self._patch(server, "mcp_lifespan", stub)
        self._patch(server, "runner_lifespan", stub)
        self._patch(server, "cimd_lifespan", stub)

        async def _run():
            async with server._lifespan(server.app):
                pass

        asyncio.run(_run())
        assert len(self._warnings()) == 1
