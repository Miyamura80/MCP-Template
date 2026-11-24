"""``search_docs`` - keyword search over this server's own documentation.

Returns ranked doc sections and leaves the synthesis to the calling agent: no
LLM call, so it is deterministic, free and identical on every transport. The
api_server-only ``/ask`` route is the LLM-answer counterpart over the same
retriever.
"""

from models.docs import DocHit, SearchDocsInput, SearchDocsResult
from services import service
from services.docs_retriever import DocChunk, get_retriever, tokenize

# Long enough for a section's key paragraph, short enough that top_k=20 stays
# a modest tool result. The URL is there for the full page.
_SNIPPET_CHARS = 1200


def _snippet(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= _SNIPPET_CHARS:
        return text
    return text[: _SNIPPET_CHARS - 1].rstrip() + "…"


def _mentions_query(chunk: DocChunk, query_terms: set[str]) -> bool:
    # BM25 scores can be zero or negative for a term common across the corpus,
    # so relevance is "shares a term with the query", ordering is the score.
    return bool(query_terms & set(tokenize(chunk.text + " " + chunk.heading)))


@service(
    name="search_docs",
    description=(
        "Search this server's documentation and return the most relevant "
        "sections with their URLs. Use it when you are unsure how a tool, auth "
        "flow, webhook or setting works, before guessing or giving up."
    ),
    input_model=SearchDocsInput,
    output_model=SearchDocsResult,
)
def search_docs(input: SearchDocsInput) -> SearchDocsResult:
    terms = set(tokenize(input.query))
    chunks = get_retriever().search(input.query, input.top_k)
    return SearchDocsResult(
        results=[
            DocHit(
                title=c.heading,
                url=c.url,
                snippet=_snippet(c.text),
                score=round(c.score, 4),
            )
            for c in chunks
            if _mentions_query(c, terms)
        ]
    )
