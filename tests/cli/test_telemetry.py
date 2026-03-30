"""Tests for telemetry integration: notice shown once, events recorded, opt-out respected."""

import json
from unittest.mock import patch

from typer.testing import CliRunner

from cli import (
    _detect_command,
    _register_builtin_commands,
    _register_user_commands,
    app,
)
from tests.test_template import TestTemplate

runner = CliRunner()

_register_builtin_commands()
_register_user_commands()


class TestTelemetryNotice(TestTemplate):
    """First-run telemetry notice shows once, then never again."""

    def test_notice_shown_on_first_run(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("{}")

        with (
            patch("src.cli.telemetry._CONFIG_DIR", tmp_path),
            patch("src.cli.telemetry.load_state", return_value={}),
            patch("src.cli.telemetry.save_state") as mock_save,
        ):
            from src.cli.telemetry import show_first_run_notice

            show_first_run_notice()
            mock_save.assert_called_once()
            saved = mock_save.call_args[0][0]
            assert saved["telemetry_notice_shown"] is True

    def test_notice_not_shown_twice(self, tmp_path):
        with (
            patch("src.cli.telemetry._CONFIG_DIR", tmp_path),
            patch(
                "src.cli.telemetry.load_state",
                return_value={"telemetry_notice_shown": True},
            ),
            patch("src.cli.telemetry.save_state") as mock_save,
        ):
            from src.cli.telemetry import show_first_run_notice

            show_first_run_notice()
            mock_save.assert_not_called()


class TestRecordEvent(TestTemplate):
    """Events are recorded locally when telemetry is enabled."""

    def test_event_written_to_file(self, tmp_path):
        tel_file = tmp_path / "telemetry.json"

        with (
            patch("src.cli.telemetry._CONFIG_DIR", tmp_path),
            patch("src.cli.telemetry._TELEMETRY_FILE", tel_file),
            patch("src.cli.telemetry.is_enabled", return_value=True),
            patch("src.cli.telemetry._post_event"),
        ):
            from src.cli.telemetry import record_event

            record_event(command="greet", duration=0.123, success=True)

            events = json.loads(tel_file.read_text())
            assert len(events) == 1
            assert events[0]["command"] == "greet"
            assert events[0]["duration_s"] == 0.123
            assert events[0]["success"] is True

    def test_event_not_recorded_when_disabled(self, tmp_path):
        tel_file = tmp_path / "telemetry.json"

        with (
            patch("src.cli.telemetry._CONFIG_DIR", tmp_path),
            patch("src.cli.telemetry._TELEMETRY_FILE", tel_file),
            patch("src.cli.telemetry.is_enabled", return_value=False),
        ):
            from src.cli.telemetry import record_event

            record_event(command="greet", duration=0.1, success=True)

            assert not tel_file.exists()

    def test_opt_out_via_env_var(self):
        with patch.dict("os.environ", {"CLI_TELEMETRY_DISABLED": "1"}):
            from src.cli.telemetry import is_enabled

            assert is_enabled() is False

    def test_max_events_cap(self, tmp_path):
        tel_file = tmp_path / "telemetry.json"
        # Seed with _MAX_EVENTS existing events
        from src.cli.telemetry import _MAX_EVENTS

        existing = [{"command": f"old-{i}"} for i in range(_MAX_EVENTS)]
        tel_file.write_text(json.dumps(existing))

        with (
            patch("src.cli.telemetry._CONFIG_DIR", tmp_path),
            patch("src.cli.telemetry._TELEMETRY_FILE", tel_file),
            patch("src.cli.telemetry.is_enabled", return_value=True),
            patch("src.cli.telemetry._post_event"),
        ):
            from src.cli.telemetry import record_event

            record_event(command="new", duration=0.01, success=True)

            events = json.loads(tel_file.read_text())
            assert len(events) == _MAX_EVENTS
            assert events[-1]["command"] == "new"
            assert events[0]["command"] == "old-1"


class TestPostEvent(TestTemplate):
    """Events are POSTed to the configured endpoint."""

    def test_post_called_when_endpoint_set(self, tmp_path):
        tel_file = tmp_path / "telemetry.json"

        with (
            patch("src.cli.telemetry._CONFIG_DIR", tmp_path),
            patch("src.cli.telemetry._TELEMETRY_FILE", tel_file),
            patch("src.cli.telemetry.is_enabled", return_value=True),
            patch("src.cli.telemetry._post_event") as mock_post,
        ):
            from src.cli.telemetry import record_event

            record_event(command="config", duration=0.05, success=True)

            mock_post.assert_called_once()
            event = mock_post.call_args[0][0]
            assert event["command"] == "config"

    def test_post_skipped_when_no_endpoint(self):
        with patch("src.cli.telemetry._get_endpoint", return_value=None):
            from src.cli.telemetry import _post_event

            # Should not raise
            _post_event({"command": "test"})

    def test_post_failure_does_not_raise(self):
        with (
            patch(
                "src.cli.telemetry._get_endpoint",
                return_value="http://localhost:9999/telemetry",
            ),
            patch(
                "urllib.request.urlopen",
                side_effect=ConnectionRefusedError("refused"),
            ),
        ):
            from src.cli.telemetry import _post_event

            # Best-effort: should not raise
            _post_event({"command": "test"})


class TestDetectCommand(TestTemplate):
    """CLI command detection from argv."""

    def test_simple_command(self):
        assert _detect_command(["mycli", "greet", "Alice"]) == "greet"

    def test_with_global_flags(self):
        assert _detect_command(["mycli", "--verbose", "config", "show"]) == "config"

    def test_with_format_flag(self):
        assert _detect_command(["mycli", "--format", "json", "config"]) == "config"

    def test_with_format_equals_syntax(self):
        assert _detect_command(["mycli", "--format=json", "config"]) == "config"

    def test_no_command(self):
        assert _detect_command(["mycli", "--help"]) == "<root>"

    def test_root_only(self):
        assert _detect_command(["mycli"]) == "<root>"


class TestTelemetryCommands(TestTemplate):
    """CLI telemetry subcommands work."""

    def test_telemetry_status(self):
        result = runner.invoke(app, ["telemetry", "status"])
        assert result.exit_code == 0
        assert "Telemetry is" in result.output

    def test_telemetry_enable(self):
        with (
            patch("src.cli.telemetry.load_state", return_value={}),
            patch("src.cli.telemetry.save_state"),
        ):
            result = runner.invoke(app, ["telemetry", "enable"])
            assert result.exit_code == 0

    def test_telemetry_disable(self):
        with (
            patch("src.cli.telemetry.load_state", return_value={}),
            patch("src.cli.telemetry.save_state"),
        ):
            result = runner.invoke(app, ["telemetry", "disable"])
            assert result.exit_code == 0
