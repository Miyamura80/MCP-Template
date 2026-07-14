"""Convert Gmail "not connected" failures into SEP-1036 URL-mode elicitation.

When a Gmail-dependent tool is called for a user with no linked account, the
MCP layer upgrades the service's ``GmailNotConnectedError`` into the spec's
URL-elicitation-required error (JSON-RPC code -32042, spec 2025-11-25) whose
``data.elicitations`` carries the Google OAuth authorization URL. Hosts that
support URL-mode elicitation open the consent flow natively and may retry the
original call; the conversion is MCP-only, so CLI/API consumers of the same
services still see the plain exception.

Conversion policy (the spec says servers SHOULD check client capabilities
before sending mode-specific requests):

- Client declared elicitation WITHOUT url mode -> no conversion. The plain
  ``GmailNotConnectedError`` text (which contains the manual recovery script)
  crosses the wire as an ``isError`` tool result.
- Client declared ``elicitation.url`` -> convert.
- Client capabilities unknown -> convert. This is the production norm: with
  ``stateless_http=True`` every tools/call arrives on a fresh transport that
  never saw the client's ``initialize`` params, so ``client_params`` is unset
  and capabilities are unknowable. Converting stays safe for hosts that don't
  understand -32042 because the error ``message`` duplicates the full textual
  recovery script including the authorization URL itself.

Known limitation under stateless HTTP: the server cannot send
``notifications/elicitation/complete`` (there is no persistent client stream
after the request ends), so hosts retry manually once the user finishes
consent - the spec anticipates exactly this ("if a completion notification
never arrives, clients SHOULD provide a manual way for the user to continue").
"""

import secrets
from typing import Any

from loguru import logger as log
from mcp.shared.exceptions import UrlElicitationRequiredError
from mcp.types import ElicitRequestURLParams

from models.gmail import GmailConnectInput
from services.gmail_svc import GmailNotConnectedError, gmail_connect


def _client_url_support(session: Any) -> bool | None:
    """Tri-state URL-elicitation support: True/False when declared, None when unknown.

    ``None`` (unknown) is the norm in production stateless-HTTP mode, where the
    per-request session never observed the client's ``initialize`` params.
    """
    params = getattr(session, "client_params", None)
    if params is None:
        return None
    elicitation = params.capabilities.elicitation
    if elicitation is None:
        return False
    # Spec back-compat rule: a bare `elicitation: {}` capability means
    # form-mode only, so absence of the `url` member is a declared inability.
    return elicitation.url is not None


def raise_connect_elicitation(session: Any, exc: GmailNotConnectedError) -> None:
    """Raise the SEP-1036 error for ``exc``, or return to let the caller re-raise.

    Returns normally (no conversion) when the client declared it cannot do
    URL-mode elicitation, or when Google OAuth is unconfigured - in both cases
    the caller re-raises the original error so its self-recovering message
    reaches the host as an ``isError`` tool result.
    """
    if _client_url_support(session) is False:
        return
    try:
        connect = gmail_connect(GmailConnectInput(user_id=exc.user_id))
    except RuntimeError as err:
        # "Google OAuth not configured" (dev box without client credentials):
        # the textual recovery path is all we can offer.
        log.debug("URL elicitation unavailable ({}); using textual recovery", err)
        return
    raise UrlElicitationRequiredError(
        [
            ElicitRequestURLParams(
                mode="url",
                message="Authorize Gmail access in your browser to continue.",
                url=connect.auth_url,
                # Opaque + unique per server as the spec requires; the actual
                # user binding travels inside the auth URL's signed `state`.
                elicitationId=f"gmail-connect-{secrets.token_urlsafe(8)}",
            )
        ],
        message=(
            f"{exc} If URL-mode elicitation is unsupported, present this "
            f"Google authorization URL to the user yourself and retry after "
            f"they consent: {connect.auth_url}"
        ),
    ) from exc
