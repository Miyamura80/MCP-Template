"""Fail-fast caps on concurrent outbound fetches to client-chosen targets.

A fetch of an attacker-chosen URL holds a worker thread for as long as the far
end stalls, so the number of fetches running at once is capped, both in total
and per target. The per-target cap stops a few slow targets from taking every
slot.

A target is the network prefix of the address the fetch is pinned to (see
:func:`services._ssrf_guard.validate_and_pin`), widened to ``IPV4_PREFIX`` or
``IPV6_PREFIX`` bits. It is not the hostname: anyone can mint unlimited
subdomains for free, but addresses cost money, and one operator usually gets a
whole /24 or /64, so a host's neighbours share its cap.

There is no queue. A caller that finds no free slot is refused at once with
:class:`FetchLimitBusyError` and decides for itself what to do instead (the
CIMD resolver serves a stale record, for example).
"""

import ipaddress
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

IPV4_PREFIX = 24
IPV6_PREFIX = 64


class FetchLimitBusyError(RuntimeError):
    """Every slot for the target, or in total, is taken."""


def target_key(address: str) -> str:
    """The network a pinned ``address`` counts against, e.g. ``203.0.113.0/24``.

    An IPv4-mapped IPv6 address counts against its IPv4 network, so one host
    cannot get two caps by being reached both ways.
    """
    addr = ipaddress.ip_address(address)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    prefix = IPV4_PREFIX if addr.version == 4 else IPV6_PREFIX
    return str(ipaddress.ip_network((addr, prefix), strict=False))


@dataclass(frozen=True, slots=True)
class FetchLimitSnapshot:
    """What :class:`FetchLimiter` holds at one moment."""

    running: int
    per_target: dict[str, int]


class FetchLimiter:
    """At most ``total`` fetches at once, and ``per_target`` per target."""

    def __init__(self, *, total: int, per_target: int) -> None:
        self._total = total
        self._per_target = per_target
        self._lock = threading.Lock()
        self._running = 0
        # target -> running fetches; a target is removed when it reaches 0, so
        # this never holds more than ``total`` entries.
        self._targets: dict[str, int] = {}

    @contextmanager
    def hold(self, address: str) -> Iterator[None]:
        """Hold a slot for the target of ``address`` while the block runs.

        Raises :class:`FetchLimitBusyError` at once if none is free.
        """
        key = target_key(address)
        with self._lock:
            held = self._targets.get(key, 0)
            if self._running >= self._total or held >= self._per_target:
                raise FetchLimitBusyError
            self._running += 1
            self._targets[key] = held + 1
        try:
            yield
        finally:
            with self._lock:
                self._running -= 1
                if self._targets[key] == 1:
                    del self._targets[key]
                else:
                    self._targets[key] -= 1

    def snapshot(self) -> FetchLimitSnapshot:
        with self._lock:
            return FetchLimitSnapshot(self._running, dict(self._targets))
