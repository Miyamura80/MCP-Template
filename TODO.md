# TODO

## Telemetry Integration

`record_event()` and `show_first_run_notice()` in `src/cli/telemetry.py` are implemented but never called.

- [ ] Call `show_first_run_notice()` in the `main()` callback in `cli.py` so the opt-out notice displays on first use
- [ ] Wrap `app()` in `main_cli()` to time command execution and call `record_event()` with the command name, duration, and success/failure
- [ ] Set a telemetry endpoint in `common/global_config.yaml` (`telemetry.endpoint`) and wire `record_event()` to POST events there when the endpoint is configured
- [ ] Add tests for telemetry integration (notice shown once, events recorded, opt-out respected)

## PyPI Packaging & Publishing

- [x] Run through full PyPI packaging: verify `pyproject.toml` metadata (description, classifiers, URLs, license), build with `uv build`, and test install from the wheel
- [x] Publish to PyPI and confirm `uvx --from miyamura80-cli-template mycli --help` installs correctly with the `mycli` entry point working
