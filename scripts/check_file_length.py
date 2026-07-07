from __future__ import annotations

import pathlib
import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Extensions that count as first-party source. `.ts`/`.tsx` cover the MCP App
# frontends (mcp_server/apps/*/src) and the docs/landing-page sub-projects.
SOURCE_GLOBS = ("*.py", "*.ts", "*.tsx")

# Directory names skipped at ANY depth. Vendored deps and build output live
# *inside* first-party trees (e.g. mcp_server/apps/x/node_modules,
# apps/x/dist), so a root-only skip would miss them and the scan would drown
# in third-party `.ts`. Matching on any path segment catches every nesting.
SKIP_DIRS = {
    ".git",
    ".venv",
    ".uv_cache",
    ".uv-cache",
    ".uv_tools",
    ".uv-tools",
    ".cache",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".astro",
    ".source",
    "coverage",
    "__pycache__",
    ".pytest_cache",
}


def load_config() -> tuple[int, set[str]]:
    pyproject = REPO_ROOT / "pyproject.toml"
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    cfg = data.get("tool", {}).get("file_length", {})
    max_lines = cfg.get("max_lines", 500)
    exclude = set(cfg.get("exclude", []))
    return max_lines, exclude


def main() -> int:
    max_lines, exclude = load_config()
    violations: list[tuple[pathlib.Path, int]] = []
    seen: set[pathlib.Path] = set()

    for glob in SOURCE_GLOBS:
        for path in REPO_ROOT.rglob(glob):
            rel = path.relative_to(REPO_ROOT)
            if rel in seen:
                continue
            seen.add(rel)
            if any(part in SKIP_DIRS for part in rel.parts[:-1]):
                continue
            if rel.as_posix() in exclude:
                continue
            try:
                line_count = len(
                    path.read_text(encoding="utf-8", errors="ignore").splitlines()
                )
            except OSError as e:
                print(f"  Warning: could not read {rel}: {e}")
                continue
            if line_count > max_lines:
                violations.append((rel, line_count))

    if violations:
        print(
            f"File length check failed: {len(violations)} file(s) exceed {max_lines} lines"
        )
        for rel_path, count in sorted(violations):
            print(f"  {rel_path}: {count} lines")
        print(
            "Refactor large files into smaller modules, "
            "or add to [tool.file_length] exclude in pyproject.toml."
        )
        return 1

    print(f"File length check passed (all files <= {max_lines} lines).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
