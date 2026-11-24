"""Send feedback about this tool to its developers."""

import sys
from typing import Annotated

import typer
from rich.console import Console

from src.cli.state import is_dry_run
from src.utils.cli_help import examples_epilog
from src.utils.output import render
from src.utils.stdin import resolve_value

console = Console(stderr=True)

# Mirrors models.feedback.FeedbackCategory (a test pins them equal); kept
# literal so --help stays import-free.
_CATEGORIES = ("bug", "confusing_docs", "missing_tool", "other")

EPILOG = examples_epilog(
    'mymcp feedback "gmail_send docs do not say drafts must exist first"',
    'mymcp feedback "need a tool to snooze threads" --category missing_tool',
    "cat error.log | mymcp feedback --stdin --category bug --tool gmail_send",
)


def main(
    message: Annotated[
        str | None,
        typer.Argument(help="What happened and what you expected. Use '-' for stdin."),
    ] = None,
    category: Annotated[
        str,
        typer.Option("--category", "-c", help=" | ".join(_CATEGORIES)),
    ] = "other",
    tool: Annotated[
        str | None,
        typer.Option("--tool", help="The tool the feedback is about."),
    ] = None,
    use_stdin: Annotated[
        bool, typer.Option("--stdin", help="Read the message from stdin.")
    ] = False,
) -> None:
    """Send feedback about this tool to its developers."""
    message = resolve_value(message, use_stdin=use_stdin)
    if not message:
        if sys.stdin.isatty():
            message = typer.prompt("Feedback")
        else:
            console.print("[red]Error:[/red] no feedback message given.")
            console.print(
                '  mymcp feedback "<message>"  |  echo "<message>" | mymcp feedback --stdin'
            )
            raise typer.Exit(code=1)
    if category not in _CATEGORIES:
        console.print(f"[red]Error:[/red] unknown category {category!r}.")
        console.print(f"  Use one of: {', '.join(_CATEGORIES)}")
        raise typer.Exit(code=1)

    if is_dry_run():
        console.print(f"[yellow][DRY RUN][/yellow] Would send {category} feedback")
        return

    # Lazy: keep service/DB imports off the CLI startup path.
    from models.feedback import FeedbackInput  # noqa: PLC0415
    from services.feedback_svc import feedback  # noqa: PLC0415

    result = feedback(
        FeedbackInput(message=message, category=category, tool_name=tool)  # ty: ignore[invalid-argument-type]
    )
    render(result.model_dump(mode="json"), title="Feedback")
