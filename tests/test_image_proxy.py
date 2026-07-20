"""Tests for the SSRF-guarded remote image fetcher (services/image_proxy.py).

All network I/O is mocked: DNS via ``socket.getaddrinfo`` and HTTP via a fake
``httpx.Client``. The suite pins the security contract - scheme allowlist,
public-address-only DNS, per-hop redirect validation, image-only content
type, and the streamed size cap - plus the happy path's base64 output.
"""

from __future__ import annotations

import base64
import contextlib
import socket
from typing import Any
from unittest import mock

import pytest

import services.image_proxy as image_proxy
from services.image_proxy import ImageFetchError, fetch_remote_image
from tests.test_template import TestTemplate

_PUBLIC_ADDRINFO = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
_PRIVATE_ADDRINFO = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 443))]


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
        is_redirect: bool = False,
    ):
        self.status_code = status_code
        self.headers = headers or {"content-type": "image/png"}
        self._chunks = chunks if chunks is not None else [b"png-bytes"]
        self.is_redirect = is_redirect

    def iter_bytes(self):
        yield from self._chunks


class _FakeClient:
    """Stands in for httpx.Client; serves queued responses per request URL."""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = responses
        self.requested_urls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args: Any):
        return False

    @contextlib.contextmanager
    def stream(self, _method: str, url: str):
        self.requested_urls.append(url)
        yield self._responses.pop(0)


def _patched_fetch(
    url: str,
    responses: list[_FakeResponse],
    addrinfo: list[Any] = _PUBLIC_ADDRINFO,
) -> tuple[Any, _FakeClient]:
    client = _FakeClient(responses)
    with (
        mock.patch.object(image_proxy.socket, "getaddrinfo", return_value=addrinfo),
        mock.patch.object(image_proxy.httpx, "Client", return_value=client),
    ):
        result = fetch_remote_image(url)
    return result, client


class TestImageProxy(TestTemplate):
    def test_rejects_non_http_schemes(self):
        for url in ("ftp://example.com/a.png", "file:///etc/passwd", "javascript:1"):
            with pytest.raises(ImageFetchError, match="scheme"):
                fetch_remote_image(url)

    def test_rejects_url_without_host(self):
        with pytest.raises(ImageFetchError, match="no host"):
            fetch_remote_image("https:///a.png")

    def test_rejects_host_resolving_to_private_address(self):
        with (
            mock.patch.object(
                image_proxy.socket, "getaddrinfo", return_value=_PRIVATE_ADDRINFO
            ),
            pytest.raises(ImageFetchError, match="non-public"),
        ):
            fetch_remote_image("https://internal.example.com/a.png")

    def test_rejects_loopback_literal(self):
        # No mocking needed: 127.0.0.1 resolves to itself.
        with pytest.raises(ImageFetchError, match="non-public"):
            fetch_remote_image("http://127.0.0.1:8080/admin.png")

    def test_rejects_non_image_content_type(self):
        with pytest.raises(ImageFetchError, match="content type"):
            _patched_fetch(
                "https://example.com/a.png",
                [_FakeResponse(headers={"content-type": "text/html"})],
            )

    def test_rejects_oversized_body_while_streaming(self):
        big = [b"x" * (1024 * 1024)] * 3  # 3 MB > 2 MB cap
        with pytest.raises(ImageFetchError, match="size limit"):
            _patched_fetch("https://example.com/a.png", [_FakeResponse(chunks=big)])

    def test_rejects_declared_oversize_content_length(self):
        headers = {"content-type": "image/png", "content-length": str(10 * 1024 * 1024)}
        with pytest.raises(ImageFetchError, match="size limit"):
            _patched_fetch(
                "https://example.com/a.png", [_FakeResponse(headers=headers)]
            )

    def test_rejects_non_200_status(self):
        with pytest.raises(ImageFetchError, match="HTTP 404"):
            _patched_fetch(
                "https://example.com/a.png", [_FakeResponse(status_code=404)]
            )

    def test_success_returns_base64_and_mime(self):
        result, client = _patched_fetch(
            "https://example.com/a.png",
            [_FakeResponse(chunks=[b"abc", b"def"])],
        )
        assert result.mime_type == "image/png"
        assert base64.b64decode(result.data_base64) == b"abcdef"
        assert result.url == "https://example.com/a.png"
        assert client.requested_urls == ["https://example.com/a.png"]

    def test_follows_redirect_and_validates_each_hop(self):
        redirect = _FakeResponse(
            status_code=302,
            headers={"location": "https://cdn.example.com/real.png"},
            is_redirect=True,
        )
        result, client = _patched_fetch(
            "https://example.com/a.png", [redirect, _FakeResponse()]
        )
        assert client.requested_urls == [
            "https://example.com/a.png",
            "https://cdn.example.com/real.png",
        ]
        assert result.mime_type == "image/png"

    def test_redirect_to_private_host_is_rejected(self):
        redirect = _FakeResponse(
            status_code=302,
            headers={"location": "http://169.254.169.254/latest/meta-data"},
            is_redirect=True,
        )
        client = _FakeClient([redirect])
        # First hop resolves public, redirect target resolves private - the
        # per-hop re-validation must catch the second one.
        with (
            mock.patch.object(
                image_proxy.socket,
                "getaddrinfo",
                side_effect=[_PUBLIC_ADDRINFO, _PRIVATE_ADDRINFO],
            ),
            mock.patch.object(image_proxy.httpx, "Client", return_value=client),
            pytest.raises(ImageFetchError, match="non-public"),
        ):
            fetch_remote_image("https://example.com/a.png")

    def test_too_many_redirects(self):
        hops = [
            _FakeResponse(
                status_code=302,
                headers={"location": f"https://example.com/{i}.png"},
                is_redirect=True,
            )
            for i in range(4)
        ]
        with pytest.raises(ImageFetchError, match="redirect"):
            _patched_fetch("https://example.com/a.png", hops)
