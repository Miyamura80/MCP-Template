"""Auto-discovery of user commands in the commands/ package."""

import importlib
import pkgutil
from pathlib import Path

import typer
from loguru import logger as log


def discover_commands(app: typer.Typer) -> None:
    """Scan commands/ and register subcommands on the Typer app.

    - If a module has ``app: typer.Typer`` → added as a sub-app (subcommand group).
    - If a module has ``main()`` callable → registered as a single command.
    - Filename ``my_tool.py`` → command name ``my-tool``.
    - Modules starting with ``_`` are skipped.
    """
    package_dir = Path(__file__).parent

    for module_info in pkgutil.iter_modules([str(package_dir)]):
        if module_info.name.startswith("_"):
            continue

        module = importlib.import_module(f"commands.{module_info.name}")
        command_name = module_info.name.replace("_", "-")

        if hasattr(module, "app") and isinstance(module.app, typer.Typer):
            help_text = getattr(module, "__doc__", None) or ""
            app.add_typer(module.app, name=command_name, help=help_text.strip())
        elif hasattr(module, "main") and callable(module.main):
            app.command(name=command_name)(module.main)
        else:
            log.warning(
                f"commands/{module_info.name}.py has no 'app' (Typer) or 'main()' - skipped"
            )
