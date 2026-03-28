"""x402 payment protocol implementation wrapping the Coinbase x402 SDK."""

import os
import threading
from typing import Any

from loguru import logger as log

from common.config_models import X402ProtocolConfig
from src.payments.base import PaymentProtocol
from src.payments.types import (
    PaymentPayload,
    PaymentProtocolName,
    PaymentRequirement,
    PaymentResult,
    PaymentStatus,
)


class X402Protocol(PaymentProtocol):
    """x402 stablecoin payment protocol via Coinbase SDK.

    Follows the lazy-init pattern from api_server/billing/stripe_config.py:
    positive init results are cached, negative results are not (allows
    env var injection without restart).
    """

    def __init__(self, config: X402ProtocolConfig) -> None:
        self._config = config
        self._server: Any = None
        self._wallet_address: str = ""
        self._initialized = False
        self._lock = threading.Lock()

    @property
    def name(self) -> PaymentProtocolName:
        return PaymentProtocolName.X402

    @property
    def is_available(self) -> bool:
        return self._initialized

    async def initialize(self) -> bool:
        """Initialize the x402 resource server with SDK.

        Returns True on success. Negative results are never cached
        so missing env vars can be provided later.
        """
        if self._initialized:
            return True

        with self._lock:
            if self._initialized:
                return True

            try:
                from x402 import x402ResourceServer

                # Pre-flight: wallet address is used as the payment
                # recipient in build_payment_requirement(). The private
                # key is validated here to ensure the operator has
                # configured credentials, but is not passed to the
                # resource server - the SDK's facilitator handles
                # on-chain settlement independently.
                wallet = os.getenv(self._config.wallet_address_env)
                private_key = os.getenv(self._config.private_key_env)

                if not wallet:
                    log.debug(
                        "x402: missing env var {}",
                        self._config.wallet_address_env,
                    )
                    return False

                if not private_key:
                    log.debug(
                        "x402: missing env var {}",
                        self._config.private_key_env,
                    )
                    return False

                self._server = x402ResourceServer()
                self._wallet_address = wallet
                self._private_key = private_key
                self._initialized = True
                log.info(
                    "x402 protocol initialized (network={}, testnet={})",
                    self._config.network,
                    self._config.testnet,
                )
                return True

            except ImportError:
                log.warning("x402 SDK not installed; protocol unavailable")
                return False
            except Exception as exc:
                log.warning("x402 init failed; will retry next call: {}", exc)
                return False

    async def build_payment_requirement(
        self,
        *,
        amount: str,
        asset: str,
        recipient: str,
        description: str | None = None,
    ) -> PaymentRequirement:
        """Build a PaymentRequirement for a 402 response."""
        if not await self.initialize():
            raise RuntimeError("x402 protocol not initialized")

        return PaymentRequirement(
            protocol=PaymentProtocolName.X402,
            network=self._config.network,
            asset=asset or self._config.default_asset,
            amount=amount or self._config.default_amount,
            recipient=recipient or self._wallet_address,
            facilitator_url=self._config.facilitator_url,
            description=description,
            extra={
                "testnet": self._config.testnet,
            },
        )

    async def verify_payment(
        self,
        payload: PaymentPayload,
        requirement: PaymentRequirement,
    ) -> PaymentResult:
        """Verify an incoming payment using the x402 facilitator.

        Delegates to the SDK's FacilitatorClient for on-chain verification.
        """
        if not await self.initialize():
            return PaymentResult(
                status=PaymentStatus.FAILED,
                protocol=PaymentProtocolName.X402,
                error="x402 protocol not initialized",
            )

        try:
            from x402.http import HTTPFacilitatorClient

            facilitator_url = (
                requirement.facilitator_url or self._config.facilitator_url
            )
            facilitator = HTTPFacilitatorClient({"url": facilitator_url})

            x402_payload = self._to_sdk_payload(payload)
            x402_requirements = self._to_sdk_requirements(requirement)

            response = await facilitator.verify(x402_payload, x402_requirements)

            if response.is_valid:
                return PaymentResult(
                    status=PaymentStatus.COMPLETED,
                    protocol=PaymentProtocolName.X402,
                    transaction_id=response.payer,
                    raw_response=response.model_dump(),
                )
            else:
                return PaymentResult(
                    status=PaymentStatus.REJECTED,
                    protocol=PaymentProtocolName.X402,
                    error=response.invalid_reason or response.invalid_message,
                    raw_response=response.model_dump(),
                )

        except Exception as exc:
            log.error("x402 payment verification failed: {}", exc)
            return PaymentResult(
                status=PaymentStatus.FAILED,
                protocol=PaymentProtocolName.X402,
                error=str(exc),
            )

    def _to_sdk_payload(self, payload: PaymentPayload) -> Any:
        """Convert our PaymentPayload to x402 SDK's PaymentPayload."""
        from x402 import parse_payment_payload

        return parse_payment_payload(payload.raw)

    def _to_sdk_requirements(self, req: PaymentRequirement) -> Any:
        """Convert our PaymentRequirement to x402 SDK's PaymentRequirements."""
        from x402 import ResourceConfig

        config = ResourceConfig(
            scheme="exact-evm",
            pay_to=req.recipient,
            price=req.amount,
            network=req.network,
        )
        requirements = self._server.build_payment_requirements(config)
        if requirements:
            return requirements[0]
        raise ValueError("Failed to build x402 payment requirements from config")

    def shutdown(self) -> None:
        """Reset protocol state."""
        with self._lock:
            self._initialized = False
            self._server = None
