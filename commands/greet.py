"""Greet someone - example single command."""

from typing import Annotated

import typer

from src.cli.state import is_dry_run, is_verbose
from src.utils.interactive import interactive_fallback
from src.utils.output import render


@interactive_fallback
def main(
    name: Annotated[
        str | None, typer.Argument(help="Name of the person to greet.")
    ] = None,
    shout: Annotated[
        bool,
        typer.Option("--shout", "-s", help="SHOUT the greeting."),
    ] = False,
    times: Annotated[
        int,
        typer.Option("--times", "-t", help="Number of times to greet."),
    ] = 1,
) -> None:
    """Greet someone by name."""
    if is_dry_run():
        typer.echo(f"[DRY RUN] Would greet {name}")
        return

    greeting = f"Hello, {name}!"
    if shout:
        greeting = greeting.upper()

    if is_verbose():
        render(
            {"name": name, "shout": shout, "times": times, "greeting": greeting},
            title="Greet Details",
        )
    else:
        for _ in range(times):
            typer.echo(greeting)
