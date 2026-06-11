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
    # Lazy by design: this module is imported on every CLI startup; keep
    # model/service imports out of it so `--help` stays fast.
    from models.config import ConfigShowInput  # noqa: PLC0415
    from services.config_svc import config_show  # noqa: PLC0415

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
    # Lazy by design: keep model/service imports off the CLI startup path.
    from models.config import ConfigGetInput  # noqa: PLC0415
    from services.config_svc import config_get  # noqa: PLC0415

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
    # Lazy by design: keep model/service imports off the CLI startup path.
    from models.config import ConfigSetInput  # noqa: PLC0415
    from services.config_svc import config_set  # noqa: PLC0415

    result = config_set(ConfigSetInput(key=key, value=value))
    console.print(
        f"[green]Set[/green] {result.key} = {result.coerced_value!r} in .global_config.yaml"
    )
