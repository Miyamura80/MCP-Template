"""The distribution name lives in one constant that onboarding can rename."""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

from src.utils import package_info
from tests.test_template import TestTemplate

ROOT = Path(__file__).resolve().parents[1]


class TestPackageInfo(TestTemplate):
    def test_dist_name_matches_pyproject(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        assert project["name"] == package_info.DIST_NAME

    def test_version_falls_back_when_not_installed(self, monkeypatch):
        monkeypatch.setattr(package_info, "DIST_NAME", "surely-not-installed-x9")
        assert package_info.package_version("9.9.9") == "9.9.9"

    def test_no_other_module_hard_codes_the_name(self):
        literal = f'"{package_info.DIST_NAME}"'
        offenders = [
            str(path.relative_to(ROOT))
            for top in ("api_server", "mcp_server", "services", "src", "common")
            for path in (ROOT / top).rglob("*.py")
            if literal in path.read_text() and path.name != "package_info.py"
        ]
        assert offenders == []

    def test_onboarding_rename_rewrites_the_constant(self):
        # init/ is a leaf (import-linter), so drive onboard.py in a subprocess.
        script = """
import json, sys
sys.path.insert(0, "init")
import onboard
t = onboard._TEMPLATE_DIST_NAME
pairs = onboard._build_rename_replacements("acme-mcp", "", "acme", "acme-mcp")
lines = [f'DIST_NAME = "{t}"', f'name = "{t}"']
print(json.dumps({
    "template": t,
    "runs": t in onboard._TEMPLATE_NAMES,
    "renamed": [onboard._apply_replacements(line, pairs) for line in lines],
}))
"""
        out = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        result = json.loads(out.stdout.strip().splitlines()[-1])
        assert result["runs"]  # the rename step is not skipped for it
        assert result["renamed"] == ['DIST_NAME = "acme-mcp"', 'name = "acme-mcp"']
