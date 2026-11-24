"""Search this tool's documentation."""

import sys
from typing import Annotated

import typer
from rich.console import Console

from src.utils.cli_help import examples_epilog
from src.utils.output import render
from src.utils.stdin import resolve_value

app = typer.Typer(no_args_is_help=True)
console = Console(stderr=True)


@app.command(
    "search",
    epilog=examples_epilog(
        'mymcp docs search "webhook signature"',
        'mymcp --format json docs search "oauth scopes" --top-k 3',
        'echo "rate limits" | mymcp docs search --stdin',
    ),
)
def search(
    query: Annotated[
        str | None, typer.Argument(help="Keywords or a question. Use '-' for stdin.")
    ] = None,
    top_k: Annotated[
        int, typer.Option("--top-k", "-k", min=1, max=20, help="Sections to return.")
    ] = 5,
    use_stdin: Annotated[
        bool, typer.Option("--stdin", help="Read the query from stdin.")
    ] = False,
) -> None:
    """Search the docs and print the most relevant sections."""
    query = resolve_value(query, use_stdin=use_stdin)
    if not query:
        if sys.stdin.isatty():
            query = typer.prompt("Search docs for")
        else:
            console.print("[red]Error:[/red] no search query given.")
            console.print(
                '  mymcp docs search "<query>"  |  echo "<query>" | mymcp docs search --stdin'
            )
            raise typer.Exit(code=1)

    # Lazy: keep service imports (and the BM25 dependency) off CLI startup.
    from models.docs import SearchDocsInput  # noqa: PLC0415
    from services.docs_svc import search_docs  # noqa: PLC0415

    result = search_docs(SearchDocsInput(query=query, top_k=top_k))
    if not result.results:
        console.print(f"No docs sections matched {query!r}.")
        return
    render(
        [r.model_dump() for r in result.results],
        title=f"Docs: {query}",
    )
