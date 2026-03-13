"""Manage project configuration."""

from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console

from src.utils.output import render

app = typer.Typer(no_args_is_help=True)
console = Console(stderr=True)

_ROOT_DIR = Path(__file__).parent.parent


def _load_config():
    """Lazy-load global_config to avoid import-time side effects."""
    from common import global_config

    return global_config


@app.command()
def show() -> None:
    """Show the full configuration."""
    cfg = _load_config()
    render(cfg.to_dict(), title="Configuration")


@app.command()
def get(
    key: Annotated[
        str,
        typer.Argument(help="Dot-separated config key, e.g. llm_config.cache_enabled"),
    ],
) -> None:
    """Get a single configuration value by dot-separated key."""
    cfg = _load_config()
    obj = cfg
    for part in key.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError:
            if isinstance(obj, dict):
                try:
                    obj = obj[part]
                except KeyError:
                    console.print(f"[red]Key not found:[/red] {key}")
                    raise typer.Exit(code=1) from None
            else:
                console.print(f"[red]Key not found:[/red] {key}")
                raise typer.Exit(code=1) from None

    if hasattr(obj, "model_dump"):
        render(obj.model_dump(), title=key)
    elif isinstance(obj, dict):
        render(obj, title=key)
    else:
        typer.echo(obj)


@app.command("set")
def set_value(
    key: Annotated[str, typer.Argument(help="Dot-separated config key to set.")],
    value: Annotated[str, typer.Argument(help="Value to set.")],
) -> None:
    """Set a configuration override (writes to .global_config.yaml)."""
    override_path = _ROOT_DIR / ".global_config.yaml"

    # Load existing overrides
    existing: dict = {}
    if override_path.exists():
        with open(override_path) as f:
            existing = yaml.safe_load(f) or {}

    # Build nested dict from dot-separated key
    parts = key.split(".")
    current = existing
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]

    # Coerce value
    coerced = _coerce_value(value)
    current[parts[-1]] = coerced

    with open(override_path, "w") as f:
        yaml.safe_dump(existing, f, default_flow_style=False)

    console.print(f"[green]Set[/green] {key} = {coerced!r} in .global_config.yaml")


def _coerce_value(value: str):
    """Attempt to coerce a string value to bool/int/float."""
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    if value.lower() == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value
