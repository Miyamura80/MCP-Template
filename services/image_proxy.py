"""SSRF-guarded fetcher that turns remote email images into base64 payloads.

Why this exists: strict-CSP MCP hosts (claude.ai enforces
``img-src 'self' data: blob: https://assets.claude.ai`` on the app iframe)
block every image an email references from an arbitrary sender domain, while
lax hosts (Goose) load them straight from the network. The inbox reader app
therefore calls ``gmail_inbox.fetch_image`` and renders the bytes as a
``data:`` URI, which every conforming host allows.

The URL comes out of attacker-controlled email HTML, so this is a classic
SSRF surface. Guards, in order:

- scheme must be http/https and the host must not be a raw private name;
- every DNS answer for the host must be a *global* address (rejects
  loopback, RFC1918, link-local, CGNAT, multicast, reserved);
- redirects are followed manually and every hop is re-validated;
- the response must declare an ``image/*`` content type;
- the body is streamed and capped at ``_MAX_IMAGE_BYTES``.

Residual risk: a DNS-rebinding attacker could swap the record between our
resolution and httpx's. Closing that requires pinning the connection to the
validated IP; for a template this documented gap is acceptable because the
response is only ever echoed back to the iframe as image bytes, never acted
on server-side.

Not an ``@service``: this is an app-only capability of the inbox reader, so
it must not be auto-registered as an LLM-facing MCP tool / CLI command / API
route the way registry services are.
"""

from __future__ import annotations

import base64
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from models.gmail import GmailFetchImageResult

_MAX_IMAGE_BYTES = 2 * 1024 * 1024  # cap per image; emails shouldn't need more
_MAX_REDIRECTS = 3
_TIMEOUT_SECONDS = 10.0


class ImageFetchError(ValueError):
    """Raised when a remote image URL is rejected or cannot be fetched."""


def _assert_public_host(url: str) -> None:
    """Reject URLs whose host resolves to anything but global addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ImageFetchError(f"unsupported URL scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise ImageFetchError("URL has no host")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ImageFetchError(f"cannot resolve host {host!r}") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            raise ImageFetchError(f"host {host!r} resolves to non-public address")


def fetch_remote_image(url: str) -> GmailFetchImageResult:
    """Fetch one remote image with SSRF guards; return base64 bytes.

    Raises :class:`ImageFetchError` on any rejection (bad scheme, private
    address, non-image response, oversize body, network failure).
    """
    current = url
    with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            _assert_public_host(current)
            try:
                with client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            raise ImageFetchError("redirect without Location header")
                        current = str(httpx.URL(current).join(location))
                        continue
                    if resp.status_code != 200:
                        raise ImageFetchError(
                            f"image fetch returned HTTP {resp.status_code}"
                        )
                    mime = (
                        resp.headers.get("content-type", "")
                        .split(";")[0]
                        .strip()
                        .lower()
                    )
                    if not mime.startswith("image/"):
                        raise ImageFetchError(f"not an image content type: {mime!r}")
                    declared = resp.headers.get("content-length")
                    if declared and int(declared) > _MAX_IMAGE_BYTES:
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
            except httpx.HTTPError as exc:
                raise ImageFetchError(f"image fetch failed: {exc}") from exc
    raise ImageFetchError("too many redirects")
