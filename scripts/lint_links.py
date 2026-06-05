#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest", "pytest-check-links"]
# ///
"""Lint markdown links with exponential backoff for rate-limited URLs.

Wraps pytest-check-links, retrying the full check up to 3 times with
exponential backoff when failures are all 429 (rate-limited). Non-429
failures (404, 403, etc.) are not retried.

Domains that block automated requests outright (403 regardless of
backoff) are added to the ignore list.
"""

import re
import subprocess
import sys
import time

BOT_BLOCKED_DOMAINS = [
    r"https://www\.mastercard\.com/.*",
    r"https://news\.ycombinator\.com/.*",
    # README "Deploy on Railway" button placeholder - intentionally a
    # non-resolving template code until a Railway template is published.
    r"https://railway\.com/new/template/YOUR_TEMPLATE_CODE",
]

MAX_RETRIES = 3
BASE_DELAY = 5


def _build_cmd(extra_ignores: list[str]) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cov",
        "-o",
        "addopts=",
        "--check-links",
        "--check-links-ignore",
        r"http://localhost:.*",
    ]
    for pattern in extra_ignores:
        cmd.extend(["--check-links-ignore", pattern])

    md_files = (
        subprocess.run(
            [
                "find",
                ".",
                "-name",
                "*.md",
                "-not",
                "-path",
                "./.venv/*",
                "-not",
                "-path",
                "./.venv-test/*",
                "-not",
                "-path",
                "*/node_modules/*",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout.strip()
        .splitlines()
    )

    cmd.extend(md_files)
    return cmd


def _is_only_429(output: str) -> bool:
    """Check if all failures in the pytest output are 429s."""
    error_lines = re.findall(r"https?://\S+:\s+\d+:.*", output)
    if not error_lines:
        return False
    non_429 = [line for line in error_lines if "429" not in line]
    return len(non_429) == 0


def main() -> int:
    ignores = list(BOT_BLOCKED_DOMAINS)
    cmd = _build_cmd(ignores)

    result = subprocess.run(cmd, capture_output=True, text=True)
    combined = result.stdout + result.stderr
    for attempt in range(MAX_RETRIES):
        if result.returncode == 0:
            return 0

        if not _is_only_429(combined):
            print(combined, file=sys.stderr)
            return result.returncode

        if attempt < MAX_RETRIES - 1:
            delay = BASE_DELAY * (2**attempt)
            print(
                f"Rate-limited (429). Retrying in {delay}s "
                f"(attempt {attempt + 2}/{MAX_RETRIES})...",
                file=sys.stderr,
            )
            time.sleep(delay)
            result = subprocess.run(cmd, capture_output=True, text=True)
            combined = result.stdout + result.stderr

    print(combined, file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
