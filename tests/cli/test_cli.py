"""Tests for main CLI entry point and commands."""

from typer.testing import CliRunner

from cli import _register_builtin_commands, _register_user_commands, app
from tests.test_template import TestTemplate

runner = CliRunner()

# Register commands once for test module
_register_builtin_commands()
_register_user_commands()


class TestCLI(TestTemplate):
    def test_version(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "mycli" in result.output

    def test_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "CLI Template" in result.output

    def test_greet(self):
        result = runner.invoke(app, ["greet", "Alice"])
        assert result.exit_code == 0
        assert "Hello, Alice!" in result.output

    def test_greet_shout(self):
        result = runner.invoke(app, ["greet", "Alice", "--shout"])
        assert result.exit_code == 0
        assert "HELLO, ALICE!" in result.output

    def test_greet_dry_run(self):
        result = runner.invoke(app, ["--dry-run", "greet", "Bob"])
        assert result.exit_code == 0
        assert "DRY RUN" in result.output

    def test_greet_times(self):
        result = runner.invoke(app, ["greet", "Alice", "--times", "3"])
        assert result.exit_code == 0
        assert result.output.count("Hello, Alice!") == 3

    def test_config_show(self):
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0

    def test_config_get(self):
        result = runner.invoke(app, ["config", "get", "llm_config.cache_enabled"])
        assert result.exit_code == 0

    def test_config_get_nonexistent(self):
        result = runner.invoke(app, ["config", "get", "nonexistent.key"])
        assert result.exit_code == 1

    def test_format_json(self):
        result = runner.invoke(app, ["--format", "json", "config", "show"])
        assert result.exit_code == 0

    def test_format_plain(self):
        result = runner.invoke(app, ["--format", "plain", "config", "show"])
        assert result.exit_code == 0

    def test_telemetry_status(self):
        result = runner.invoke(app, ["telemetry", "status"])
        assert result.exit_code == 0

    def test_completions_show(self):
        result = runner.invoke(app, ["completions", "show", "bash"])
        assert result.exit_code == 0
