"""Loud error when a non-dev deployment seals with the self-signed dev cert.

Issue #206: with ``pdf_forms.signing.cert_path``/``key_path`` unset, the
server silently falls back to an auto-generated self-signed certificate whose
seals validate as untrusted. Outside dev that must be surfaced at startup and
on the first seal, once per process. A half-configured pair must raise.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

import pytest
from loguru import logger

from api_server import server
from common import global_config
from services import pdf_signing
from tests.test_template import TestTemplate


class TestUntrustedSealWarning(TestTemplate):
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        signing = global_config.pdf_forms.signing
        monkeypatch.setattr(pdf_signing, "_untrusted_seal_warned", False)
        monkeypatch.setattr(signing, "cert_path", None)
        monkeypatch.setattr(signing, "key_path", None)
        monkeypatch.setattr(signing, "dev_cert_dir", str(tmp_path))
        self.monkeypatch = monkeypatch
        self.records: list = []
        sink = logger.add(self.records.append, level="DEBUG", format="{message}")
        yield
        logger.remove(sink)

    def _set_env(self, value: str) -> None:
        self.monkeypatch.setattr(global_config, "DEV_ENV", value)

    def _seal_alerts(self) -> list:
        return [
            r.record
            for r in self.records
            if r.record["level"].no >= logging.WARNING
            and "SELF-SIGNED" in r.record["message"]
        ]

    def test_prod_without_cert_logs_error(self):
        self._set_env("prod")
        pdf_signing.warn_if_untrusted_seal()
        alerts = self._seal_alerts()
        assert len(alerts) == 1
        assert alerts[0]["level"].name == "ERROR"
        assert "UNTRUSTED" in alerts[0]["message"]
        assert "DEV_ENV=prod" in alerts[0]["message"]

    def test_logs_only_once_per_process(self):
        self._set_env("prod")
        pdf_signing.warn_if_untrusted_seal()
        pdf_signing.warn_if_untrusted_seal()
        assert len(self._seal_alerts()) == 1

    def test_dev_is_silent(self):
        for env in ("dev", "local", "DEV"):
            self._set_env(env)
            pdf_signing.warn_if_untrusted_seal()
        assert self._seal_alerts() == []

    def test_unknown_env_fails_secure(self):
        self._set_env("staging")
        pdf_signing.warn_if_untrusted_seal()
        assert len(self._seal_alerts()) == 1

    def test_configured_cert_is_silent(self):
        self._set_env("prod")
        signing = global_config.pdf_forms.signing
        self.monkeypatch.setattr(signing, "cert_path", "/etc/seal/cert.pem")
        self.monkeypatch.setattr(signing, "key_path", "/etc/seal/key.pem")
        pdf_signing.warn_if_untrusted_seal()
        assert self._seal_alerts() == []

    @pytest.mark.parametrize("which", ["cert_path", "key_path"])
    def test_half_configured_pair_raises(self, which):
        self._set_env("prod")
        signing = global_config.pdf_forms.signing
        self.monkeypatch.setattr(signing, which, "/etc/seal/one.pem")
        with pytest.raises(ValueError, match="must be set together"):
            pdf_signing.sealing_cert_paths()
        assert self._seal_alerts() == []

    def test_first_seal_fallback_logs(self):
        self._set_env("prod")
        cert_path, key_path = pdf_signing.sealing_cert_paths()
        assert cert_path.exists() and key_path.exists()
        assert len(self._seal_alerts()) == 1

    def test_startup_lifespan_logs(self):
        self._set_env("prod")

        async def _noop_lifespan(*_args):
            yield

        stub = asynccontextmanager(_noop_lifespan)
        self.monkeypatch.setattr(server, "mcp_lifespan", stub)
        self.monkeypatch.setattr(server, "runner_lifespan", stub)
        self.monkeypatch.setattr(server, "cimd_lifespan", stub)

        async def _run():
            async with server._lifespan(server.app):
                pass

        asyncio.run(_run())
        assert len(self._seal_alerts()) == 1
