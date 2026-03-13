"""Diagnose project environment health."""

import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from src.cli.state import is_quiet, is_verbose
from src.utils.output import render

_ROOT_DIR = Path(__file__).parent.parent
console = Console(stderr=True)


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    message: str
    detail: str = ""
    fixable: bool = False


def _check_python_version() -> CheckResult:
    ok = sys.version_info >= (3, 12)
    version_str = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    return CheckResult(
        name="Python version",
        status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        message=f"{version_str}" if ok else f"{version_str} (need >= 3.12)",
        detail=f"sys.version_info = {sys.version_info}",
    )


def _check_uv_installed() -> CheckResult:
    path = shutil.which("uv")
    return CheckResult(
        name="uv installed",
        status=CheckStatus.PASS if path else CheckStatus.FAIL,
        message=f"found at {path}" if path else "uv not found on PATH",
        detail=f"shutil.which('uv') = {path}",
    )


def _check_deps_synced() -> CheckResult:
    try:
        result = subprocess.run(
            ["uv", "sync", "--dry-run"],
            capture_output=True,
            text=True,
            cwd=_ROOT_DIR,
            timeout=30,
        )
        output = result.stdout + result.stderr
        synced = "Would make no changes" in output or result.returncode == 0
        # If there are packages to install/update, it's not fully synced
        if "Would install" in output or "Would update" in output:
            synced = False
        return CheckResult(
            name="Deps synced",
            status=CheckStatus.PASS if synced else CheckStatus.WARN,
            message="all dependencies synced" if synced else "dependencies out of sync",
            detail=output.strip(),
            fixable=True,
        )
    except FileNotFoundError:
        return CheckResult(
            name="Deps synced",
            status=CheckStatus.FAIL,
            message="uv not found, cannot check",
            detail="uv must be installed to check dependencies",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="Deps synced",
            status=CheckStatus.WARN,
            message="timed out checking dependencies",
            detail="uv sync --dry-run took longer than 30s",
            fixable=True,
        )


def _check_config_parseable() -> CheckResult:
    try:
        from common import global_config  # noqa: F811

        _ = global_config.to_dict()
        return CheckResult(
            name="Config parseable",
            status=CheckStatus.PASS,
            message="global_config loaded successfully",
        )
    except Exception as e:
        return CheckResult(
            name="Config parseable",
            status=CheckStatus.FAIL,
            message="failed to load global_config",
            detail=str(e),
        )


def _check_env_exists() -> CheckResult:
    env_path = _ROOT_DIR / ".env"
    if not env_path.exists():
        return CheckResult(
            name=".env exists",
            status=CheckStatus.FAIL,
            message=".env file not found",
            detail=f"expected at {env_path}",
            fixable=True,
        )
    if env_path.stat().st_size == 0:
        return CheckResult(
            name=".env exists",
            status=CheckStatus.WARN,
            message=".env file is empty",
            detail=f"file at {env_path} has 0 bytes",
            fixable=True,
        )
    return CheckResult(
        name=".env exists",
        status=CheckStatus.PASS,
        message=".env file found",
        detail=f"{env_path} ({env_path.stat().st_size} bytes)",
    )


def _check_api_keys() -> CheckResult:
    env_path = _ROOT_DIR / ".env"
    example_path = _ROOT_DIR / ".env.example"

    if not example_path.exists():
        return CheckResult(
            name="API keys",
            status=CheckStatus.WARN,
            message="no .env.example to compare against",
            detail="create .env.example to enable this check",
        )

    if not env_path.exists():
        return CheckResult(
            name="API keys",
            status=CheckStatus.FAIL,
            message=".env missing - cannot check keys",
        )

    from dotenv import dotenv_values

    env_vals = dotenv_values(env_path)
    example_vals = dotenv_values(example_path)

    missing = []
    placeholder = []
    for key, example_value in example_vals.items():
        actual = env_vals.get(key)
        if actual is None or actual == "":
            missing.append(key)
        elif example_value and actual == example_value:
            placeholder.append(key)

    if missing:
        return CheckResult(
            name="API keys",
            status=CheckStatus.FAIL,
            message=f"{len(missing)} key(s) missing: {', '.join(missing)}",
            detail=f"missing: {missing}, placeholder: {placeholder}",
        )
    if placeholder:
        return CheckResult(
            name="API keys",
            status=CheckStatus.WARN,
            message=f"{len(placeholder)} key(s) still have placeholder values",
            detail=f"placeholder: {placeholder}",
        )
    return CheckResult(
        name="API keys",
        status=CheckStatus.PASS,
        message="all keys set",
        detail=f"checked {len(example_vals)} key(s)",
    )


def _check_prek_hooks() -> CheckResult:
    hook_path = _ROOT_DIR / ".git" / "hooks" / "pre-commit"
    if not hook_path.exists():
        return CheckResult(
            name="Pre-commit hooks",
            status=CheckStatus.FAIL,
            message="pre-commit hook not installed",
            detail=f"expected at {hook_path}",
            fixable=True,
        )
    content = hook_path.read_text()
    if "prek" in content:
        return CheckResult(
            name="Pre-commit hooks",
            status=CheckStatus.PASS,
            message="prek hook installed",
        )
    return CheckResult(
        name="Pre-commit hooks",
        status=CheckStatus.WARN,
        message="pre-commit hook exists but does not use prek",
        detail="hook content does not contain 'prek'",
    )


def _check_git_repo() -> CheckResult:
    git_dir = _ROOT_DIR / ".git"
    return CheckResult(
        name="Git repo",
        status=CheckStatus.PASS if git_dir.is_dir() else CheckStatus.FAIL,
        message="git repository found"
        if git_dir.is_dir()
        else ".git/ directory not found",
        detail=f"checked {git_dir}",
    )


_ALL_CHECKS = [
    _check_python_version,
    _check_uv_installed,
    _check_deps_synced,
    _check_config_parseable,
    _check_env_exists,
    _check_api_keys,
    _check_prek_hooks,
    _check_git_repo,
]


def _fix_deps() -> bool:
    try:
        result = subprocess.run(
            ["uv", "sync"],
            capture_output=True,
            text=True,
            cwd=_ROOT_DIR,
            timeout=120,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _fix_env() -> bool:
    env_path = _ROOT_DIR / ".env"
    example_path = _ROOT_DIR / ".env.example"
    if example_path.exists() and not env_path.exists():
        shutil.copy2(example_path, env_path)
        return True
    if not example_path.exists() and not env_path.exists():
        env_path.touch()
        return True
    return False


def _fix_prek() -> bool:
    prek_path = shutil.which("prek")
    if not prek_path:
        return False
    try:
        result = subprocess.run(
            ["prek", "install"],
            capture_output=True,
            text=True,
            cwd=_ROOT_DIR,
            timeout=30,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


_FIXERS: dict[str, Callable[[], bool]] = {
    "Deps synced": _fix_deps,
    ".env exists": _fix_env,
    "Pre-commit hooks": _fix_prek,
}


def main(
    fix: Annotated[
        bool,
        typer.Option("--fix", help="Attempt to auto-fix fixable issues."),
    ] = False,
) -> None:
    """Run health checks on your project environment."""
    results: list[CheckResult] = []

    for check_fn in _ALL_CHECKS:
        result = check_fn()
        results.append(result)

    if fix:
        results = _attempt_fixes(results)

    has_failures = any(r.status == CheckStatus.FAIL for r in results)
    _render_results(results, has_failures)

    if has_failures:
        raise typer.Exit(code=1)


def _attempt_fixes(results: list[CheckResult]) -> list[CheckResult]:
    """Try to auto-fix fixable failures, re-run checks if anything was fixed."""
    fixed_any = False
    for result in results:
        if (
            result.status != CheckStatus.PASS
            and result.fixable
            and result.name in _FIXERS
        ):
            if not is_quiet():
                console.print(f"  Fixing: {result.name}...")
            if _FIXERS[result.name]():
                fixed_any = True

    if fixed_any:
        return [check_fn() for check_fn in _ALL_CHECKS]
    return results


def _render_results(results: list[CheckResult], has_failures: bool) -> None:
    """Render check results respecting output format and verbosity."""
    if is_quiet():
        status = "FAIL" if has_failures else "OK"
        typer.echo(f"doctor: {status}")
        if has_failures:
            for r in results:
                if r.status == CheckStatus.FAIL:
                    typer.echo(f"  {r.name}: {r.message}")
        return

    rows = []
    for r in results:
        row = {
            "Check": r.name,
            "Status": r.status.value,
            "Message": r.message,
        }
        if is_verbose():
            row["Detail"] = r.detail
            row["Fixable"] = "yes" if r.fixable else ""
        rows.append(row)

    render(rows, title="Doctor")
