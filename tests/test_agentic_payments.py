"""Tests for agentic payment protocol core abstractions."""

import asyncio
from unittest.mock import patch

from src.payments.registry import PaymentRegistry
from src.payments.types import (
    PaymentPayload,
    PaymentProtocolName,
    PaymentRequirement,
    PaymentResult,
    PaymentStatus,
)
from tests.test_template import TestTemplate


class TestPaymentTypes(TestTemplate):
    def test_protocol_name_values(self):
        assert PaymentProtocolName.X402 == "x402"
        assert PaymentProtocolName.MPP == "mpp"
        assert PaymentProtocolName.ACP == "acp"

    def test_payment_status_values(self):
        assert PaymentStatus.COMPLETED == "completed"
        assert PaymentStatus.FAILED == "failed"
        assert PaymentStatus.REJECTED == "rejected"
        assert PaymentStatus.PENDING == "pending"

    def test_payment_requirement_frozen(self):
        req = PaymentRequirement(
            protocol=PaymentProtocolName.X402,
            network="base-sepolia",
            asset="USDC",
            amount="0.001",
            recipient="0xabc",
        )
        assert req.protocol == "x402"
        assert req.network == "base-sepolia"
        assert req.facilitator_url is None
        assert req.extra == {}

    def test_payment_result_defaults(self):
        result = PaymentResult(status=PaymentStatus.COMPLETED)
        assert result.transaction_id is None
        assert result.error is None
        assert result.raw_response == {}

    def test_payment_payload_defaults(self):
        payload = PaymentPayload(protocol=PaymentProtocolName.X402)
        assert payload.raw == {}
        assert payload.header_value == ""


class TestPaymentRegistry(TestTemplate):
    def setup_method(self):
        PaymentRegistry.reset()

    def test_singleton(self):
        r1 = PaymentRegistry.get()
        r2 = PaymentRegistry.get()
        assert r1 is r2

    def test_no_protocols_when_all_disabled(self):
        registry = PaymentRegistry.get()
        # Default config has all protocols disabled
        registry.initialize()
        assert registry.list_enabled() == []
        assert registry.is_any_enabled() is False

    def test_reset_clears_singleton(self):
        r1 = PaymentRegistry.get()
        PaymentRegistry.reset()
        r2 = PaymentRegistry.get()
        assert r1 is not r2

    def test_get_protocol_returns_none_for_unknown(self):
        registry = PaymentRegistry.get()
        assert registry.get_protocol("nonexistent") is None


class TestX402Protocol(TestTemplate):
    def test_name(self):
        from common.config_models import X402ProtocolConfig
        from src.payments.x402.protocol import X402Protocol

        proto = X402Protocol(X402ProtocolConfig())
        assert proto.name == PaymentProtocolName.X402

    def test_not_available_before_init(self):
        from common.config_models import X402ProtocolConfig
        from src.payments.x402.protocol import X402Protocol

        proto = X402Protocol(X402ProtocolConfig())
        assert proto.is_available is False

    def test_init_fails_without_env_vars(self):
        from common.config_models import X402ProtocolConfig
        from src.payments.x402.protocol import X402Protocol

        proto = X402Protocol(X402ProtocolConfig())
        result = asyncio.run(proto.initialize())
        assert result is False
        assert proto.is_available is False

    def test_init_succeeds_with_env_vars(self):
        from common.config_models import X402ProtocolConfig
        from src.payments.x402.protocol import X402Protocol

        proto = X402Protocol(X402ProtocolConfig())
        with patch.dict(
            "os.environ",
            {
                "X402_WALLET_ADDRESS": "0x1234567890abcdef",
                "X402_PRIVATE_KEY": "0xdeadbeef",
            },
        ):
            result = asyncio.run(proto.initialize())
            assert result is True
            assert proto.is_available is True

    def test_build_requirement(self):
        from common.config_models import X402ProtocolConfig
        from src.payments.x402.protocol import X402Protocol

        proto = X402Protocol(X402ProtocolConfig())
        with patch.dict(
            "os.environ",
            {
                "X402_WALLET_ADDRESS": "0x1234567890abcdef",
                "X402_PRIVATE_KEY": "0xdeadbeef",
            },
        ):

            async def _run():
                await proto.initialize()
                return await proto.build_payment_requirement(
                    amount="0.01",
                    asset="USDC",
                    recipient="0xrecipient",
                    description="test payment",
                )

            req = asyncio.run(_run())
            assert req.protocol == PaymentProtocolName.X402
            assert req.amount == "0.01"
            assert req.asset == "USDC"
            assert req.recipient == "0xrecipient"
            assert req.description == "test payment"

    def test_shutdown_resets_state(self):
        from common.config_models import X402ProtocolConfig
        from src.payments.x402.protocol import X402Protocol

        proto = X402Protocol(X402ProtocolConfig())
        with patch.dict(
            "os.environ",
            {
                "X402_WALLET_ADDRESS": "0x1234567890abcdef",
                "X402_PRIVATE_KEY": "0xdeadbeef",
            },
        ):
            asyncio.run(proto.initialize())
            assert proto.is_available is True
            proto.shutdown()
            assert proto.is_available is False
