"""Tests for the secrets command."""

from contextlib import ExitStack
from unittest.mock import patch

from typer.testing import CliRunner

from cli import _register_builtin_commands, _register_user_commands, app
from commands.secrets import _mask_value
from tests.test_template import TestTemplate

runner = CliRunner()

_register_builtin_commands()
_register_user_commands()


class FakeKeyring:
    """Dict-backed fake keyring for testing."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, key: str) -> str | None:
        return self.store.get((service, key))

    def set_password(self, service: str, key: str, value: str) -> None:
        self.store[(service, key)] = value

    def delete_password(self, service: str, key: str) -> None:
        import keyring.errors

        if (service, key) not in self.store:
            raise keyring.errors.PasswordDeleteError(f"No password for {key}")
        del self.store[(service, key)]


def _apply_patches(stack: ExitStack, fake: FakeKeyring) -> None:
    """Apply all keyring patches onto an ExitStack."""
    stack.enter_context(
        patch("commands.secrets.keyring.get_password", fake.get_password)
    )
    stack.enter_context(
        patch("commands.secrets.keyring.set_password", fake.set_password)
    )
    stack.enter_context(
        patch("commands.secrets.keyring.delete_password", fake.delete_password)
    )


class TestSecrets(TestTemplate):
    def test_set_and_get(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(app, ["secrets", "set", "MY_KEY", "my_secret_value"])
            assert result.exit_code == 0
            assert "Stored" in result.output

            result = runner.invoke(app, ["secrets", "get", "MY_KEY", "--reveal"])
            assert result.exit_code == 0
            assert "my_secret_value" in result.output

    def test_get_masked(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            runner.invoke(app, ["secrets", "set", "MY_KEY", "my_secret_value"])
            result = runner.invoke(app, ["secrets", "get", "MY_KEY"])
            assert result.exit_code == 0
            assert "my_secret_value" not in result.output
            assert "MY_KEY=" in result.output

    def test_get_not_found(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(app, ["secrets", "get", "NONEXISTENT"])
            assert result.exit_code == 1
            assert "Not found" in result.output

    def test_delete(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            runner.invoke(app, ["secrets", "set", "DEL_KEY", "value"])
            result = runner.invoke(app, ["secrets", "delete", "DEL_KEY"])
            assert result.exit_code == 0
            assert "Deleted" in result.output

    def test_delete_not_found(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(app, ["secrets", "delete", "NONEXISTENT"])
            assert result.exit_code == 1

    def test_list_empty(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(app, ["secrets", "list"])
            assert result.exit_code == 0
            assert "No secrets" in result.output

    def test_list_with_keys(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            runner.invoke(app, ["secrets", "set", "KEY_A", "val_a"])
            runner.invoke(app, ["secrets", "set", "KEY_B", "val_b"])
            result = runner.invoke(app, ["secrets", "list"])
            assert result.exit_code == 0
            assert "KEY_A" in result.output
            assert "KEY_B" in result.output

    def test_export(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            runner.invoke(app, ["secrets", "set", "EXP_KEY", "exp_value_1234"])
            result = runner.invoke(app, ["secrets", "export", "--reveal"])
            assert result.exit_code == 0
            assert "EXP_KEY=exp_value_1234" in result.output

    def test_export_masked(self):
        fake = FakeKeyring()
        with ExitStack() as stack:
            _apply_patches(stack, fake)
            runner.invoke(app, ["secrets", "set", "EXP_KEY", "exp_value_1234"])
            result = runner.invoke(app, ["secrets", "export"])
            assert result.exit_code == 0
            assert "EXP_KEY=" in result.output
            assert "exp_value_1234" not in result.output

    def test_mask_value_short(self):
        assert _mask_value("abc") == "***"
        assert _mask_value("abcdefgh") == "********"

    def test_mask_value_long(self):
        masked = _mask_value("my_long_secret_value")
        assert masked.startswith("my_")
        assert masked.endswith("lue")
        assert "*" in masked
        assert len(masked) == len("my_long_secret_value")

    def test_import_secrets(self, tmp_path):
        fake = FakeKeyring()
        env_file = tmp_path / ".env"
        env_file.write_text("IMPORT_KEY=import_value\nSKIP_KEY=placeholder...\n")

        with ExitStack() as stack:
            _apply_patches(stack, fake)
            result = runner.invoke(app, ["secrets", "import", "--file", str(env_file)])
            assert result.exit_code == 0
            assert "Imported 1" in result.output

            result = runner.invoke(app, ["secrets", "get", "IMPORT_KEY", "--reveal"])
            assert result.exit_code == 0
            assert "import_value" in result.output
