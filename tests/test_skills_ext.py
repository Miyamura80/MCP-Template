"""MCP Skills extension (``mcp_server/skills.py``): loading, wire, and drift."""

from __future__ import annotations

import hashlib
import re
import tomllib
from pathlib import Path

import pytest

from mcp_server.server import llm_tool_surface
from mcp_server.skills import (
    EXTENSION_ID,
    SKILLS_DIR,
    SkillError,
    load_skill,
    load_skills,
    parse_frontmatter,
)
from tests.test_mcp_e2e import _wire_client
from tests.test_mcp_protocol_eras import _ModernMcpClient
from tests.test_template import TestTemplate


def _write(
    tmp: Path, name: str, frontmatter: str, extra: dict[str, bytes] | None = None
):
    d = tmp / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n# Body\n")
    for rel, data in (extra or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(data)
    return d


class TestLoad(TestTemplate):
    def test_manifest_digests_the_served_bytes(self, tmp_path):
        d = _write(
            tmp_path,
            "demo",
            "name: demo\ndescription: A demo.",
            {"refs/a.md": b"alpha\n", ".DS_Store": b"x"},
        )
        skill = load_skill(d)
        assert skill.uri == "skill://demo/SKILL.md"
        md = next(f for f in skill.files if f.uri == skill.uri)
        assert (md.name, md.description) == ("demo", "A demo.")
        by_uri = {f.uri: f for f in skill.files}
        assert set(by_uri) == {"skill://demo/SKILL.md", "skill://demo/refs/a.md"}
        ref = by_uri["skill://demo/refs/a.md"]
        assert ref.digest == "sha256:" + hashlib.sha256(b"alpha\n").hexdigest()
        assert ref.size == 6 and ref.mime_type == "text/markdown"
        assert skill.frontmatter == {"name": "demo", "description": "A demo."}

    def test_name_must_match_directory(self, tmp_path):
        d = _write(tmp_path, "demo", "name: other\ndescription: x")
        with pytest.raises(SkillError, match="directory name"):
            load_skill(d)

    def test_description_required(self, tmp_path):
        with pytest.raises(SkillError, match="description"):
            load_skill(_write(tmp_path, "demo", "name: demo"))

    def test_frontmatter_required(self):
        with pytest.raises(SkillError):
            parse_frontmatter("# no frontmatter\n")

    def test_fence_must_be_a_whole_line(self):
        # ``---x: 1`` is YAML content, not the closing fence.
        assert parse_frontmatter("---\nname: a\n---x: 1\n---\n") == {
            "name": "a",
            "---x": 1,
        }
        assert parse_frontmatter("---\n---\nbody\n") == {}

    @pytest.mark.parametrize("value", ["2026-01-01", "1.10", "!!set {a}"])
    def test_frontmatter_must_survive_json_exactly(self, value):
        # Hosts compare the listed frontmatter with the file field by field;
        # a date or a float that YAML rewrites would never match.
        with pytest.raises(SkillError, match="JSON-compatible"):
            parse_frontmatter(f"---\nname: a\nv: {value}\n---\n")

    def test_crlf_and_bom_are_parsed_from_the_served_text(self, tmp_path):
        d = tmp_path / "demo"
        d.mkdir()
        raw = "\ufeff---\r\nname: demo\r\ndescription: x\r\n---\r\nbody\r\n"
        (d / "SKILL.md").write_bytes(raw.encode())
        skill = load_skill(d)
        assert skill.files[0].text == raw  # served exactly as stored
        assert skill.files[0].size == len(raw.encode())

    def test_shipped_skills_list_every_file_once(self):
        skills = load_skills()
        assert skills, f"no skills under {SKILLS_DIR}"
        for skill in skills:
            uris = [f.uri for f in skill.files]
            assert len(uris) == len(set(uris))
            assert uris.count(skill.uri) == 1

    def test_wheel_ships_the_skills(self):
        pyproject = tomllib.loads(
            (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
        )
        include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"][
            "force-include"
        ]
        assert include.get("skills") == "mcp_server/_skills"


# Tool references in a skill: backticked snake_case names and the "name" of a
# tools/call example. Snake_case identifiers that are not tools go here.
_BACKTICKED = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")
_JSON_NAME = re.compile(r'"name"\s*:\s*"([a-z][a-z0-9_]*)"')
_NOT_TOOLS = frozenset({"user_id"})


class TestDrift(TestTemplate):
    def test_skills_only_name_exposed_tools(self):
        """A skill that tells the model to call a tool that doesn't exist (renamed,
        removed, misspelled) fails here, not in a user's chat."""
        exposed = {e.name for e in llm_tool_surface()}
        assert exposed, "empty tool surface: the check would pass vacuously"
        problems = [
            f"{f.uri}: {token}"
            for skill in load_skills()
            for f in skill.files
            for token in sorted(
                set(_JSON_NAME.findall(f.text)) | set(_BACKTICKED.findall(f.text))
            )
            if token not in exposed and token not in _NOT_TOOLS
        ]
        assert not problems, (
            "Skills name tools the MCP server does not expose (or add a "
            "non-tool identifier to _NOT_TOOLS):\n  " + "\n  ".join(problems)
        )


class TestWire(TestTemplate):
    def test_discover_advertises_the_extension(self):
        with _wire_client("u-skills") as (client, key):
            caps = _ModernMcpClient(client, key).request("server/discover")[
                "capabilities"
            ]
        # No optional features: every file is in the manifest.
        assert caps["extensions"][EXTENSION_ID] == {}

    def test_list_get_and_read_agree(self):
        with _wire_client("u-skills") as (client, key):
            modern = _ModernMcpClient(client, key)
            listed = modern.request("skills/list")
            assert listed["resultType"] == "complete"
            assert listed["cacheScope"] == "public" and listed["ttlMs"] > 0
            entry = next(
                s for s in listed["skills"] if s["uri"] == "skill://gmail-mcp/SKILL.md"
            )
            got = modern.request("skills/get", {"uri": entry["uri"]})["skill"]
            assert got == entry
            for item in entry["resources"]:
                read = modern.request("resources/read", {"uri": item["uri"]})
                text = read["contents"][0]["text"].encode()
                # What a host verifies before using a skill file.
                assert "sha256:" + hashlib.sha256(text).hexdigest() == item["digest"]
                assert len(text) == item["size"]
            md = modern.request("resources/read", {"uri": entry["uri"]})["contents"][0]
            assert md["mimeType"] == "text/markdown"
            assert parse_frontmatter(md["text"]) == entry["frontmatter"]

    def test_unknown_skill_is_invalid_params(self):
        with _wire_client("u-skills") as (client, key):
            error = _ModernMcpClient(client, key).request_error(
                "skills/get", {"uri": "skill://nope/SKILL.md"}
            )
        assert error["code"] == -32602
        assert "skill://nope/SKILL.md" in error["message"]
