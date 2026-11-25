"""Pydantic schemas for the ``search_docs`` service."""

from pydantic import BaseModel, Field


class SearchDocsInput(BaseModel):
    query: str = Field(
        description="Keywords or a question, e.g. 'webhook signature header'",
        min_length=1,
        max_length=500,
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of sections to return",
    )


class DocHit(BaseModel):
    title: str = Field(description="Section heading")
    url: str = Field(description="Public docs URL of the page")
    snippet: str = Field(description="Section text, truncated")
    score: float = Field(description="BM25 relevance; higher is better")


class SearchDocsResult(BaseModel):
    results: list[DocHit] = Field(default_factory=list)
