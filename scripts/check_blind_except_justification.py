"""Enforce that every `# noqa: BLE001` is paired with a justification comment.

Suppressing the blind-except lint must be a deliberate decision. This script
fails if any `# noqa: BLE001` is not accompanied by a non-empty comment within
the next 3 lines (or trailing on the same line after the noqa marker) that
explains why catching everything is the right call.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ROOT_SKIP_DIRS = {
    ".git",
    ".venv",
    ".venv-test",
    ".uv_cache",
    ".uv-cache",
    ".uv_tools",
    ".uv-tools",
    ".cache",
    "node_modules",
    ".next",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
}
RECURSIVE_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".venv", ".venv-test"}

NOQA_PATTERN = re.compile(r"#\s*noqa:\s*BLE001\b")
LOOKAHEAD_LINES = 3
MIN_JUSTIFICATION_CHARS = 20


def _strip_comment(line: str) -> str:
    """Return the comment body (text after the first `#`), or '' if none."""
    idx = line.find("#")
    return line[idx + 1 :].strip() if idx >= 0 else ""


def _is_justification(comment_body: str) -> bool:
    """A justification is a non-noqa comment with enough substance to explain why."""
    if not comment_body:
        return False
    # Strip any inline noqa markers from the comment body before measuring length.
    cleaned = re.sub(r"noqa:[^#\s]*", "", comment_body, flags=re.IGNORECASE).strip()
    cleaned = cleaned.lstrip(":#- \t")
    return len(cleaned) >= MIN_JUSTIFICATION_CHARS


def _trailing_text_after_noqa(line: str) -> str:
    """Return any text written after `# noqa: BLE001` on the same line."""
    match = NOQA_PATTERN.search(line)
    if not match:
        return ""
    tail = line[match.end() :].strip()
    # The tail may be empty, or another comment, or trailing prose.
    return tail.lstrip(":#- \t")


def _has_justification(lines: list[str], idx: int) -> bool:
    """Check the noqa line itself + the next LOOKAHEAD_LINES lines for a reason."""
    inline = _trailing_text_after_noqa(lines[idx])
    if len(inline) >= MIN_JUSTIFICATION_CHARS:
        return True

    for offset in range(1, LOOKAHEAD_LINES + 1):
        if idx + offset >= len(lines):
            break
        body = _strip_comment(lines[idx + offset])
        if _is_justification(body):
            return True
    return False


def _iter_python_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for path in REPO_ROOT.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT)
        parts = rel.parts
        if parts[0] in ROOT_SKIP_DIRS:
            continue
        if any(part in RECURSIVE_SKIP_DIRS for part in parts[:-1]):
            continue
        files.append(path)
    return files


def main() -> int:
    violations: list[tuple[pathlib.Path, int, str]] = []

    for path in _iter_python_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            print(f"  Warning: could not read {path}: {e}", file=sys.stderr)
            continue
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            if not NOQA_PATTERN.search(line):
                continue
            if not _has_justification(lines, idx):
                rel = path.relative_to(REPO_ROOT)
                violations.append((rel, idx + 1, line.strip()))

    if violations:
        print(
            f"Blind-except justification check failed: "
            f"{len(violations)} `# noqa: BLE001` site(s) lack a justification comment."
        )
        print(
            f"Each `# noqa: BLE001` must be paired with a comment "
            f"(>={MIN_JUSTIFICATION_CHARS} chars) within {LOOKAHEAD_LINES} lines "
            f"explaining why a broad catch is correct."
        )
        for rel_path, line_no, content in violations:
            print(f"  {rel_path}:{line_no}: {content}")
        return 1

    print("Blind-except justification check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
