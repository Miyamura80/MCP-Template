"""Tests for the doctor command."""

from unittest.mock import patch

from typer.testing import CliRunner

from cli import _register_builtin_commands, _register_user_commands, app
from commands.doctor import (
    CheckResult,
    CheckStatus,
    _check_env_exists,
    _check_git_repo,
    _check_python_version,
    _check_uv_installed,
)
from tests.test_template import TestTemplate

runner = CliRunner()

_register_builtin_commands()
_register_user_commands()


class TestDoctor(TestTemplate):
    def test_doctor_runs(self):
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code in (0, 1)

    def test_doctor_verbose(self):
        result = runner.invoke(app, ["--verbose", "doctor"])
        assert result.exit_code in (0, 1)
        assert "Detail" in result.output or "detail" in result.output.lower()

    def test_doctor_quiet(self):
        result = runner.invoke(app, ["--quiet", "doctor"])
        assert result.exit_code in (0, 1)
        assert "doctor:" in result.output

    def test_doctor_json(self):
        result = runner.invoke(app, ["--format", "json", "doctor"])
        assert result.exit_code in (0, 1)

    def test_doctor_fix(self):
        result = runner.invoke(app, ["doctor", "--fix"])
        assert result.exit_code in (0, 1)

    def test_check_python_version(self):
        result = _check_python_version()
        assert isinstance(result, CheckResult)
        assert result.status == CheckStatus.PASS
        assert result.name == "Python version"

    def test_check_uv_installed(self):
        result = _check_uv_installed()
        assert isinstance(result, CheckResult)
        assert result.name == "uv installed"

    def test_check_git_repo(self):
        result = _check_git_repo()
        assert isinstance(result, CheckResult)
        assert result.name == "Git repo"
        assert result.status == CheckStatus.PASS

    def test_check_env_exists_missing(self, tmp_path):
        with patch("commands.doctor._ROOT_DIR", tmp_path):
            result = _check_env_exists()
            assert isinstance(result, CheckResult)
            assert result.status == CheckStatus.FAIL
            assert result.fixable is True

    def test_check_env_exists_empty(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.touch()
        with patch("commands.doctor._ROOT_DIR", tmp_path):
            result = _check_env_exists()
            assert result.status == CheckStatus.WARN

    def test_check_result_dataclass(self):
        cr = CheckResult(
            name="test",
            status=CheckStatus.PASS,
            message="ok",
            detail="details here",
            fixable=True,
        )
        assert cr.name == "test"
        assert cr.status == CheckStatus.PASS
        assert cr.fixable is True

    def test_check_status_values(self):
        assert CheckStatus.PASS == "pass"
        assert CheckStatus.FAIL == "fail"
        assert CheckStatus.WARN == "warn"
