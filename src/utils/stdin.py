"""Helpers for reading CLI values from stdin (the ``-`` / ``--stdin`` convention).

Agents think in pipelines, so every value an agent might want to pipe should be
acceptable on stdin as well as via a flag/argument.
"""

import sys


def read_stdin() -> str:
    """Read all of stdin and strip a single trailing newline."""
    return sys.stdin.read().rstrip("\n")


def resolve_value(value: str | None, *, use_stdin: bool = False) -> str | None:
    """Resolve a CLI value, reading from stdin when requested.

    Reads stdin when ``use_stdin`` is True or when ``value`` is the sentinel
    ``-``. Otherwise returns ``value`` unchanged.
    """
    if use_stdin or value == "-":
        return read_stdin()
    return value
