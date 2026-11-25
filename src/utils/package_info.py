"""This project's distribution name and installed version, in one place.

``DIST_NAME`` must equal ``[project].name`` in ``pyproject.toml``
(``tests/test_package_info.py`` checks). Keep it a single-line literal:
``init/onboard.py``'s rename step rewrites it along with ``pyproject.toml``.
"""

from importlib.metadata import PackageNotFoundError, version

DIST_NAME = "mcp-template"


def package_version(default: str = "0.0.0") -> str:
    """The installed version of this package, or ``default`` when not installed."""
    try:
        return version(DIST_NAME)
    except PackageNotFoundError:
        return default
