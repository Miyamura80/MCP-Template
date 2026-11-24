"""Tests for the ``search_docs`` service, the shared retriever and the CLI."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from models.docs import SearchDocsInput
from services import docs_retriever
from services.docs_retriever import DocsRetriever, _is_locale_page, _resolve_corpus_dir
from services.docs_svc import _SNIPPET_CHARS, _snippet, search_docs
from src.cli.app import _register_builtin_commands, _register_user_commands, app
from tests.test_template import TestTemplate

runner = CliRunner()
_register_builtin_commands()
_register_user_commands()

_BASE = "https://docs.example.test"


def _corpus(tmp: Path) -> DocsRetriever:
    (tmp / "webhooks.mdx").write_text(
        "---\ntitle: Webhooks\n---\n# Webhooks\n\n## Signature\n"
        "Every delivery carries an HMAC signature header.\n"
    )
    (tmp / "index.mdx").write_text("# Home\n\nWelcome to the server.\n")
    (tmp / "index.ja.mdx").write_text("# Home\n\nsignature signature signature\n")
    return DocsRetriever(corpus_path=str(tmp), base_url=_BASE)


class TestRetriever(TestTemplate):
    def test_skips_locale_pages(self, tmp_path):
        retriever = _corpus(tmp_path)
        assert all(".ja" not in c.path for c in retriever._chunks)

    def test_locale_detection(self):
        assert _is_locale_page(Path("index.ja.mdx"))
        assert _is_locale_page(Path("guide.zh-CN.mdx"))
        assert not _is_locale_page(Path("index.mdx"))
        assert not _is_locale_page(Path("api/v2.mdx"))

    def test_resolves_from_packages_root(self, tmp_path, monkeypatch):
        (tmp_path / "docs").mkdir()
        monkeypatch.chdir(tmp_path / "docs")  # configured path not under CWD
        monkeypatch.setattr(docs_retriever, "_PACKAGES_ROOT", tmp_path)
        (tmp_path / "corpus").mkdir()
        assert _resolve_corpus_dir("corpus") == (tmp_path / "corpus").resolve()

    def test_real_docs_corpus_loads(self):
        retriever = DocsRetriever(base_url=_BASE)
        assert retriever._chunks, "docs/content/docs should be indexable"


class TestSearchDocsService(TestTemplate):
    def test_ranked_hits_with_urls(self, tmp_path):
        retriever = _corpus(tmp_path)
        with patch("services.docs_svc.get_retriever", return_value=retriever):
            result = search_docs(SearchDocsInput(query="signature header"))
        assert result.results
        top = result.results[0]
        assert top.title == "Signature"
        assert top.url == f"{_BASE}/webhooks"
        assert "HMAC" in top.snippet

    def test_no_match_returns_empty(self, tmp_path):
        retriever = _corpus(tmp_path)
        with patch("services.docs_svc.get_retriever", return_value=retriever):
            assert search_docs(SearchDocsInput(query="zzzz")).results == []

    def test_snippet_truncates(self):
        assert _snippet("a  b\n c") == "a b c"
        long = _snippet("x" * (_SNIPPET_CHARS * 2))
        assert len(long) == _SNIPPET_CHARS and long.endswith("…")

    def test_registered_read_only(self):
        from mcp_server.server import llm_tool_surface  # noqa: PLC0415

        entry = next(e for e in llm_tool_surface() if e.name == "search_docs")
        assert entry.mutating is False


class TestDocsCli(TestTemplate):
    def test_search_json(self, tmp_path):
        retriever = _corpus(tmp_path)
        with patch("services.docs_svc.get_retriever", return_value=retriever):
            res = runner.invoke(
                app, ["--format", "json", "docs", "search", "signature"]
            )
        assert res.exit_code == 0, res.output
        assert json.loads(res.stdout)[0]["title"] == "Signature"

    def test_search_stdin(self, tmp_path):
        retriever = _corpus(tmp_path)
        with patch("services.docs_svc.get_retriever", return_value=retriever):
            res = runner.invoke(app, ["docs", "search", "--stdin"], input="signature\n")
        assert res.exit_code == 0, res.output

    def test_missing_query_fails_fast(self):
        res = runner.invoke(app, ["docs", "search"], input="")
        assert res.exit_code == 1
