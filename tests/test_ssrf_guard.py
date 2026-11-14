"""Unit tests for the shared SSRF guard (``services._ssrf_guard``).

The guard backs the built-in OAuth server's Client ID Metadata Document fetch,
which targets a URL the client chooses. Covered: the scheme allowlist,
is_global-only DNS answers (v4 + v6), reject-if-ANY-answer is non-global,
IPv6 addresses that embed or translate to a non-public IPv4 address, IP-literal
passthrough, and the pinned-URL / Host / SNI shape. All DNS is mocked.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from unittest.mock import patch

import httpx
import pytest

import services._ssrf_guard as ssrf_guard
from services._ssrf_guard import (
    PinnedTarget,
    SsrfError,
    build_client,
    is_public_address,
    validate_and_pin,
)
from tests.test_template import TestTemplate

_PUBLIC_IP = "93.184.216.34"
_PUBLIC_V6 = "2606:4700:4700::1111"


def _addrinfo(ip: str, *, port: int = 443):
    """A single-answer getaddrinfo result for ``ip`` (v4 or v6 by shape)."""
    if ":" in ip:
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0))]
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


_PUBLIC_ADDRINFO = _addrinfo(_PUBLIC_IP)

# Internal / non-global targets an attacker would rebind to, v4 and v6.
_INTERNAL_ADDRS = [
    "127.0.0.1",  # loopback
    "169.254.169.254",  # link-local cloud metadata
    "10.0.0.5",  # RFC1918 private
    "100.64.0.1",  # CGNAT / shared address space
    "::1",  # IPv6 loopback
    "fe80::1",  # IPv6 link-local
    "fc00::1",  # IPv6 unique-local
]


# ---------------------------------------------------------------------------
# Unit: the shared resolve-then-pin primitive
# ---------------------------------------------------------------------------


class TestSsrfGuard(TestTemplate):
    def _pin(self, url: str, addrinfo, *, allow_loopback: bool = False) -> PinnedTarget:
        with patch.object(ssrf_guard.socket, "getaddrinfo", return_value=addrinfo):
            return validate_and_pin(url, allow_loopback=allow_loopback)

    def test_rejects_non_http_scheme(self):
        for url in ("ftp://example.com/x", "file:///etc/passwd", "gopher://h/"):
            with pytest.raises(SsrfError, match="scheme"):
                validate_and_pin(url)

    def test_rejects_missing_host_and_bad_port(self):
        with pytest.raises(SsrfError, match="no host"):
            validate_and_pin("https:///x")
        with pytest.raises(SsrfError, match="invalid port"):
            validate_and_pin("https://h:notaport/x")

    def test_pins_v4_to_validated_ip_with_host_and_sni(self):
        t = self._pin("https://hook.example/path", _PUBLIC_ADDRINFO)
        assert t.url == f"https://{_PUBLIC_IP}/path"
        assert t.headers["Host"] == "hook.example"
        assert t.extensions["sni_hostname"] == "hook.example"

    def test_pins_v6_in_brackets(self):
        t = self._pin("https://hook.example/path", _addrinfo(_PUBLIC_V6))
        assert t.url == f"https://[{_PUBLIC_V6}]/path"
        assert t.headers["Host"] == "hook.example"

    def test_preserves_explicit_port(self):
        t = self._pin("https://hook.example:8443/p", _addrinfo(_PUBLIC_IP, port=8443))
        assert t.url == f"https://{_PUBLIC_IP}:8443/p"
        assert t.headers["Host"] == "hook.example:8443"

    def test_http_scheme_has_no_sni_extension(self):
        t = self._pin("http://hook.example/p", _addrinfo(_PUBLIC_IP, port=80))
        assert t.extensions == {}

    def test_ip_literal_passes_through_unpinned(self):
        # A public IP literal is validated but there is nothing to pin.
        t = validate_and_pin(f"https://{_PUBLIC_IP}/p")
        assert t == PinnedTarget(url=f"https://{_PUBLIC_IP}/p")

    def test_rejects_non_global_single_answer(self):
        for ip in _INTERNAL_ADDRS:
            with pytest.raises(SsrfError, match="non-public"):
                self._pin("https://hook.example/p", _addrinfo(ip))

    def test_rejects_if_any_answer_non_global(self):
        # Split-horizon: a public answer alongside a private one must be
        # rejected wholesale, not cherry-picked down to the public one.
        mixed = _PUBLIC_ADDRINFO + _addrinfo("10.0.0.9")
        with pytest.raises(SsrfError, match="non-public"):
            self._pin("https://hook.example/p", mixed)

    def test_allow_loopback_permits_only_loopback(self):
        # Dev override: loopback is allowed, but a private/link-local host is
        # still rejected even with allow_loopback=True.
        t = self._pin(
            "http://localhost/p", _addrinfo("127.0.0.1", port=80), allow_loopback=True
        )
        assert t.url == "http://127.0.0.1/p"
        with pytest.raises(SsrfError, match="non-public"):
            self._pin(
                "https://hook.example/p",
                _addrinfo("169.254.169.254"),
                allow_loopback=True,
            )

    def test_unresolvable_host_raises(self):
        with (
            patch.object(ssrf_guard.socket, "getaddrinfo", side_effect=socket.gaierror),
            pytest.raises(SsrfError, match="cannot resolve"),
        ):
            validate_and_pin("https://nope.invalid/p")

    def test_build_client_disables_env_and_redirects(self):
        client = build_client(timeout=1.0)
        try:
            assert client.trust_env is False
            assert client.follow_redirects is False
        finally:
            client.close()

    def test_deadline_client_keeps_the_hardening(self):
        client = build_client(timeout=1.0, deadline=time.monotonic() + 1.0)
        try:
            assert client.trust_env is False
            assert client.follow_redirects is False
        finally:
            client.close()

    def test_deadline_client_fails_once_the_deadline_has_passed(self):
        client = build_client(timeout=1.0, deadline=time.monotonic() - 1.0)
        try:
            with pytest.raises(httpx.ConnectTimeout):
                client.get("http://127.0.0.1:9/")
        finally:
            client.close()


class TestEmbeddedIpv4(TestTemplate):
    @pytest.mark.parametrize(
        "addr",
        [
            "::ffff:127.0.0.1",  # IPv4-mapped loopback
            "2002:7f00:1::",  # 6to4 around 127.0.0.1
            "2002:a00:5::",  # 6to4 around 10.0.0.5
            "64:ff9b::7f00:1",  # NAT64 to 127.0.0.1
            "64:ff9b:1::a00:5",  # local-use NAT64
            "::7f00:1",  # deprecated IPv4-compatible
        ],
    )
    def test_refuses_v6_that_reaches_a_non_public_v4(self, addr):
        assert not is_public_address(ipaddress.ip_address(addr))

    def test_plain_public_v6_still_passes(self):
        assert is_public_address(ipaddress.ip_address(_PUBLIC_V6))
