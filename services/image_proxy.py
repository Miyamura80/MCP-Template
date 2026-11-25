"""SSRF-guarded fetcher that turns remote email images into base64 payloads.

Why this exists: strict-CSP MCP hosts (claude.ai enforces
``img-src 'self' data: blob: https://assets.claude.ai`` on the app iframe)
block every image an email references from an arbitrary sender domain, while
lax hosts (Goose) load them straight from the network. The inbox reader app
therefore calls ``gmail_inbox_fetch_image`` and renders the bytes as a
``data:`` URI, which every conforming host allows.

The URL comes out of attacker-controlled email HTML, so this is a classic
SSRF surface. Guards, in order:

- every hop goes through :func:`services._ssrf_guard.validate_and_pin`:
  http/https only, malformed URLs rejected, every DNS answer must be a public
  address (including the IPv6 forms that embed or translate to a private IPv4
  address), and the connection is PINNED to a validated address with the
  original hostname kept in the Host header and TLS SNI, so a DNS-rebinding
  flip between validation and connect cannot reach a private address;
- the client comes from :func:`services._ssrf_guard.build_client`: no
  environment proxy, no automatic redirects (each hop is re-validated and
  re-pinned here), and a wall-clock deadline of ``_TOTAL_DEADLINE_SECONDS``
  enforced on every connect, read and write - so a sender that trickles its
  status line, headers or body cannot hold a slot past it;
- the response must declare an ``image/*`` content type;
- the body is streamed and capped at ``_MAX_IMAGE_BYTES``.

Not an ``@service``: this is an app-only capability of the inbox reader, so
it must not be auto-registered as an LLM-facing MCP tool / CLI command / API
route the way registry services are.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx

from models.gmail import GmailFetchImageResult
from services import ModelFacingError
from services._ssrf_guard import SsrfError, build_client, validate_and_pin

_MAX_IMAGE_BYTES = 2 * 1024 * 1024  # cap per image; emails shouldn't need more
_MAX_REDIRECTS = 3
_TIMEOUT_SECONDS = 10.0  # per network operation (httpx timeout)
# Wall-clock ceiling across all hops AND mid-stream: the frontend fires up to
# 20 of these in parallel per click, so neither a redirect chain nor a
# trickling body may hold a slot longer than this.
_TOTAL_DEADLINE_SECONDS = 20.0


class ImageFetchError(ModelFacingError, ValueError):
    """Raised when a remote image URL is rejected or cannot be fetched."""


def _read_image_response(resp: Any, url: str) -> GmailFetchImageResult:
    """Validate a non-redirect response and stream its body within limits."""
    if resp.status_code != 200:
        raise ImageFetchError(f"image fetch returned HTTP {resp.status_code}")
    mime = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if not mime.startswith("image/"):
        raise ImageFetchError(f"not an image content type: {mime!r}")
    declared = resp.headers.get("content-length")
    if declared:
        try:
            declared_len = int(declared)
        except ValueError as exc:
            # ImageFetchError subclasses ValueError, so the size check must
            # stay OUTSIDE this try block.
            raise ImageFetchError(f"invalid Content-Length: {declared!r}") from exc
        if declared_len > _MAX_IMAGE_BYTES:
            raise ImageFetchError("image exceeds size limit")
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_bytes():
        total += len(chunk)
        if total > _MAX_IMAGE_BYTES:
            raise ImageFetchError("image exceeds size limit")
        chunks.append(chunk)
    return GmailFetchImageResult(
        url=url,
        mime_type=mime,
        data_base64=base64.b64encode(b"".join(chunks)).decode("ascii"),
    )


def fetch_remote_image(url: str) -> GmailFetchImageResult:
    """Fetch one remote image with SSRF guards; return base64 bytes.

    Raises :class:`ImageFetchError` on any rejection (malformed URL, private
    address, non-image response, oversize body, deadline, network failure).
    """
    current = url
    # Started before the first DNS lookup, which cannot be interrupted but
    # spends the same budget.
    deadline = time.monotonic() + _TOTAL_DEADLINE_SECONDS
    with build_client(timeout=_TIMEOUT_SECONDS, deadline=deadline) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            if time.monotonic() > deadline:
                raise ImageFetchError("image fetch deadline exceeded")
            try:
                target = validate_and_pin(current)
            except SsrfError as exc:
                raise ImageFetchError(str(exc)) from exc
            try:
                with client.stream(
                    "GET",
                    target.url,
                    headers=target.headers,
                    extensions=target.extensions,
                ) as resp:
                    if resp.is_redirect:
                        # is_redirect implies a Location header exists. Join
                        # against the hostname form, not the pinned IP form.
                        location = resp.headers["location"]
                        current = str(httpx.URL(current).join(location))
                        continue
                    return _read_image_response(resp, url)
            except httpx.TimeoutException as exc:
                raise ImageFetchError("image fetch deadline exceeded") from exc
            except httpx.HTTPError as exc:
                # httpx's text can carry the pinned IP and internal details;
                # the cause stays on the server.
                raise ImageFetchError("image fetch failed") from exc
    raise ImageFetchError("too many redirects")
