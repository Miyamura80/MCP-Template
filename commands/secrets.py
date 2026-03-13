"""Manage secrets via OS keyring."""

import importlib.metadata
import json
from typing import Annotated

import keyring
import keyring.errors
import typer
from rich.console import Console

from src.cli.state import is_quiet
from src.utils.output import render

app = typer.Typer(no_args_is_help=True)
console = Console(stderr=True)


def _get_cli_name() -> str:
    """Derive CLI name from package console_scripts entry point."""
    eps = importlib.metadata.entry_points(group="console_scripts")
    for ep in eps:
        if ep.dist and ep.dist.name == "miyamura80-cli-template":
            return ep.name
    return "mycli"


_SERVICE_NAME = _get_cli_name()
_KEYS_META = "__secret_keys__"


def _get_tracked_keys() -> list[str]:
    raw = keyring.get_password(_SERVICE_NAME, _KEYS_META)
    if raw is None:
        return []
    try:
        keys = json.loads(raw)
        return sorted(set(keys))
    except (json.JSONDecodeError, TypeError):
        return []


def _set_tracked_keys(keys: list[str]) -> None:
    keyring.set_password(_SERVICE_NAME, _KEYS_META, json.dumps(sorted(set(keys))))


def _track_key(key: str) -> None:
    keys = _get_tracked_keys()
    if key not in keys:
        keys.append(key)
        _set_tracked_keys(keys)


def _untrack_key(key: str) -> None:
    keys = _get_tracked_keys()
    if key in keys:
        keys.remove(key)
        _set_tracked_keys(keys)


def _mask_value(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return value[:3] + "*" * (len(value) - 6) + value[-3:]


@app.command("set")
def set_secret(
    key: Annotated[str, typer.Argument(help="Secret key name.")],
    value: Annotated[
        str | None,
        typer.Argument(help="Secret value. Prompts if omitted."),
    ] = None,
) -> None:
    """Store a secret in the OS keyring."""
    if value is None:
        value = typer.prompt(f"Enter value for {key}", hide_input=True)

    keyring.set_password(_SERVICE_NAME, key, value)
    _track_key(key)

    if not is_quiet():
        console.print(f"[green]Stored[/green] {key}")


@app.command("get")
def get_secret(
    key: Annotated[str, typer.Argument(help="Secret key name.")],
    reveal: Annotated[
        bool,
        typer.Option("--reveal", "-r", help="Show the full secret value."),
    ] = False,
) -> None:
    """Retrieve a secret from the OS keyring."""
    value = keyring.get_password(_SERVICE_NAME, key)
    if value is None:
        console.print(f"[red]Not found:[/red] {key}")
        raise typer.Exit(code=1)

    display = value if reveal else _mask_value(value)
    typer.echo(f"{key}={display}")


@app.command()
def delete(
    key: Annotated[str, typer.Argument(help="Secret key name to delete.")],
) -> None:
    """Remove a secret from the OS keyring."""
    try:
        keyring.delete_password(_SERVICE_NAME, key)
    except keyring.errors.PasswordDeleteError:
        console.print(f"[red]Not found:[/red] {key}")
        raise typer.Exit(code=1) from None

    _untrack_key(key)

    if not is_quiet():
        console.print(f"[green]Deleted[/green] {key}")


@app.command("list")
def list_secrets() -> None:
    """List stored secret key names (never values)."""
    keys = _get_tracked_keys()
    if not keys:
        if not is_quiet():
            console.print("No secrets stored.")
        return

    rows = []
    for key in keys:
        value = keyring.get_password(_SERVICE_NAME, key)
        rows.append(
            {
                "Key": key,
                "Status": "set" if value else "empty",
            }
        )

    render(rows, title="Secrets")


@app.command("import")
def import_secrets(
    file: Annotated[
        str,
        typer.Option("--file", "-f", help="Path to .env file to import."),
    ] = ".env",
    interactive: Annotated[
        bool,
        typer.Option("--interactive", "-i", help="Confirm each key before importing."),
    ] = False,
) -> None:
    """Import secrets from a .env file into the OS keyring."""
    from dotenv import dotenv_values

    values = dotenv_values(file)
    if not values:
        console.print(f"[yellow]No values found in {file}[/yellow]")
        return

    imported = 0
    skipped = 0
    for key, value in values.items():
        if value is None or value == "" or value.endswith("..."):
            skipped += 1
            continue

        if interactive:
            confirm = typer.confirm(f"Import {key}?")
            if not confirm:
                skipped += 1
                continue

        keyring.set_password(_SERVICE_NAME, key, value)
        _track_key(key)
        imported += 1

    if not is_quiet():
        console.print(
            f"[green]Imported {imported} secret(s)[/green], skipped {skipped}"
        )


@app.command("export")
def export_secrets(
    reveal: Annotated[
        bool,
        typer.Option("--reveal", "-r", help="Show full secret values."),
    ] = False,
) -> None:
    """Export secrets in .env format."""
    keys = _get_tracked_keys()
    if not keys:
        if not is_quiet():
            console.print("No secrets to export.")
        return

    for key in keys:
        value = keyring.get_password(_SERVICE_NAME, key)
        if value is None:
            continue
        display = value if reveal else _mask_value(value)
        typer.echo(f"{key}={display}")
