"""The first-party event types this server emits, as ``events/list`` shows them.

Each type names its subscription arguments (``inputSchema``) and its payload
(``payloadSchema``), plus a ``matches`` filter that applies a subscription's
arguments to one occurrence. Keep changes additive for the life of a name; a
breaking change gets a new name (spec: schema evolution).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from services import InvalidArgument

APPROVAL_RESOLVED = "approval.resolved"
FEEDBACK_RECEIVED = "feedback.received"
GMAIL_MESSAGE_RECEIVED = "gmail.message_received"

DELIVERY_MODES = ("poll", "webhook")


@dataclass(frozen=True)
class EventType:
    name: str
    description: str
    payload_schema: dict[str, Any]
    input_properties: dict[str, dict[str, Any]] = field(default_factory=dict)
    matches: Callable[[dict[str, Any], dict[str, Any]], bool] = lambda _args, _data: (
        True
    )

    def descriptor(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "delivery": list(DELIVERY_MODES),
            "inputSchema": {
                "type": "object",
                "properties": self.input_properties,
                "additionalProperties": False,
            },
            "payloadSchema": self.payload_schema,
        }

    def validate_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Reject unknown or mistyped arguments (they would silently never match)."""
        unknown = set(arguments) - set(self.input_properties)
        if unknown:
            raise InvalidArgument(
                f"{self.name} takes no argument(s) {', '.join(sorted(unknown))}."
            )
        for key, value in arguments.items():
            if not isinstance(value, str):
                raise InvalidArgument(f"{self.name} argument {key!r} must be a string.")
        return dict(arguments)


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _object(**properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties}


def _sender_matches(args: dict[str, Any], data: dict[str, Any]) -> bool:
    wanted = args.get("from")
    return not wanted or wanted.lower() in str(data.get("from") or "").lower()


EVENT_TYPES: dict[str, EventType] = {
    t.name: t
    for t in (
        EventType(
            name=APPROVAL_RESOLVED,
            description=(
                "A held dangerous call was approved or denied. On approved, retry "
                "the call to run it."
            ),
            payload_schema=_object(
                approvalId=_string("The approval id."),
                tool=_string("The held tool."),
                status={"type": "string", "enum": ["approved", "denied"]},
            ),
            input_properties={"tool": _string("Only this tool's approvals.")},
            matches=lambda args, data: (
                not args.get("tool") or args["tool"] == data.get("tool")
            ),
        ),
        EventType(
            name=FEEDBACK_RECEIVED,
            description="Feedback you submitted with the feedback tool was stored.",
            payload_schema=_object(
                id=_string("The feedback id."),
                category=_string("bug, confusing_docs, missing_tool or other."),
                toolName={"type": ["string", "null"]},
            ),
        ),
        EventType(
            name=GMAIL_MESSAGE_RECEIVED,
            description=(
                "A new message arrived in the connected Gmail inbox (needs "
                "gmail_watch_start)."
            ),
            payload_schema=_object(
                message_id=_string("Gmail message id."),
                thread_id=_string("Gmail thread id."),
                **{"from": _string("The From header.")},
                subject=_string("The Subject header."),
                snippet=_string("Gmail's short preview."),
                date=_string("The Date header."),
                label_ids={"type": "array", "items": {"type": "string"}},
            ),
            input_properties={
                "from": _string("Only messages whose From header contains this.")
            },
            matches=_sender_matches,
        ),
    )
}
