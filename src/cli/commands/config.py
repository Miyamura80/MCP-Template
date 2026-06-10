"""Manage project configuration."""

from typing import Annotated

import typer
from rich.console import Console

from src.utils.output import render

app = typer.Typer(no_args_is_help=True)
console = Console(stderr=True)


@app.command()
def show() -> None:
    """Show the full configuration."""
    from models.config import ConfigShowInput
    from services.config_svc import config_show

    result = config_show(ConfigShowInput())
    render(result.config, title="Configuration")


@app.command()
def get(
    key: Annotated[
        str,
        typer.Argument(help="Dot-separated config key, e.g. llm_config.cache_enabled"),
    ],
) -> None:
    """Get a single configuration value by dot-separated key."""
    from models.config import ConfigGetInput
    from services.config_svc import config_get

    try:
        result = config_get(ConfigGetInput(key=key))
    except KeyError:
        console.print(f"[red]Key not found:[/red] {key}")
        raise typer.Exit(code=1) from None

    if isinstance(result.value, dict):
        render(result.value, title=key)
    else:
        typer.echo(result.value)


@app.command("set")
def set_value(
    key: Annotated[str, typer.Argument(help="Dot-separated config key to set.")],
    value: Annotated[str, typer.Argument(help="Value to set.")],
) -> None:
    """Set a configuration override (writes to .global_config.yaml)."""
    from models.config import ConfigSetInput
    from services.config_svc import config_set

    result = config_set(ConfigSetInput(key=key, value=value))
    console.print(
        f"[green]Set[/green] {result.key} = {result.coerced_value!r} in .global_config.yaml"
    )
