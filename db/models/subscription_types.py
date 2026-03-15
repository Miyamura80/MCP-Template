"""Subscription enums and constants."""

import enum


class SubscriptionTier(enum.StrEnum):
    FREE = "free_tier"
    PLUS = "plus_tier"


class SubscriptionStatus(enum.StrEnum):
    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELING = "canceling"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"


class PaymentStatus(enum.StrEnum):
    CURRENT = "current"
    FAILED = "failed"
    PENDING = "pending"


class UsageAction(enum.StrEnum):
    API_REQUEST = "api_request"
    SERVICE_CALL = "service_call"
