"""MCP Skills extension (``io.modelcontextprotocol/skills``, SEP-2640, Beta).

Serves the hand-written skills in the repo's ``skills/<name>/`` directory to
MCP hosts, so an agent connected to this server can load how-to guidance for
its tools without anyone installing it separately:

    skills/list   -> every skill: uri, verbatim frontmatter, and a manifest of
                     its files (``skill://<name>/<path>``, sha256 digest, size)
    skills/get    -> one skill by its SKILL.md uri (-32602 if unknown)
    resources/read skill://<name>/<path> -> the file (base protocol)

Hosts verify each file they read against the manifest's digest and size and
compare SKILL.md's frontmatter with the listed one, so everything here is
derived from one read of each file's bytes: what is parsed, served and
digested is the same text. The optional ``directoryRead`` feature
(``resources/directory/read``) is not offered: every file is in the manifest.

Failure policy: a malformed skill fails at import (CI loads the shipped ones,
so a typo can't reach production). A missing ``skills/`` directory only logs,
because a project built from this template may delete it on purpose.

Spec: https://github.com/modelcontextprotocol/ext-skills
(``specification/beta/skills.mdx``). Beta, so breaking changes are possible;
verify against the current spec before changing wire shapes.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml
from loguru import logger as log
from mcp.server.context import ServerRequestContext
from mcp.server.extension import Extension, MethodBinding, ResourceBinding
from mcp.server.mcpserver.resources import TextResource
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, PaginatedRequestParams, RequestParams

EXTENSION_ID = "io.modelcontextprotocol/skills"

# Source of truth is the repo-root ``skills/`` (where skills.sh crawls). The
# wheel carries a copy under the package (pyproject force-include), so an
# installed server finds it without a generic top-level ``skills`` directory.
_SOURCE_DIR = Path(__file__).resolve().parent.parent / "skills"
_PACKAGED_DIR = Path(__file__).resolve().parent / "_skills"
SKILLS_DIR = _SOURCE_DIR if _SOURCE_DIR.is_dir() else _PACKAGED_DIR

# Skills change only with a deploy, so hosts may cache the listing a while.
_TTL_MS = 300_000
_SKILL_FILE = "SKILL.md"
# Spec limits per skill.
_MAX_FILES = 512
_MAX_BYTES = 16 * 1024 * 1024
# Frontmatter: a first line of exactly ``---``, then up to a line of exactly
# ``---``. Anything looser (``---x: 1``, a ``----`` rule) is body text.
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*\r?$", re.S | re.M)


class GetSkillParams(RequestParams):
    uri: str


@dataclass(frozen=True)
class SkillFile:
    uri: str
    name: str
    mime_type: str
    text: str
    digest: str
    size: int
    description: str | None = None


@dataclass(frozen=True)
class Skill:
    uri: str  # the SKILL.md uri: a skill's identity
    frontmatter: dict[str, Any]
    files: tuple[SkillFile, ...]

    def entry(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "frontmatter": self.frontmatter,
            "resources": [
                {"uri": f.uri, "digest": f.digest, "size": f.size} for f in self.files
            ],
        }


class SkillError(ValueError):
    """A skill on disk breaks the spec; raised at load so CI catches it."""


def parse_frontmatter(text: str) -> dict[str, Any]:
    """The YAML frontmatter of a SKILL.md, as the JSON object hosts compare.

    YAML types JSON can't carry exactly (dates, ``1.10`` read as ``1.1``,
    sets) are refused rather than rewritten, because the spec has hosts check
    the listed frontmatter against the file field by field.
    """
    match = _FRONTMATTER.match(text.removeprefix("﻿"))
    if match is None:
        raise SkillError("SKILL.md must begin with ----fenced YAML frontmatter")
    data = yaml.safe_load(match.group(1)) or {}
    if not isinstance(data, dict):
        raise SkillError("SKILL.md frontmatter must be a mapping")
    if not _exact_json(data):
        raise SkillError(
            "SKILL.md frontmatter must be plain JSON-compatible values; quote "
            "dates and version numbers as strings"
        )
    return data


def _exact_json(value: Any) -> bool:
    """Whether ``value`` reaches the wire exactly as YAML read it.

    Floats are refused too: ``1.10`` and ``1.1`` are the same float, so a
    version number would silently change.
    """
    if isinstance(value, dict):
        return all(isinstance(k, str) and _exact_json(v) for k, v in value.items())
    if isinstance(value, list):
        return all(_exact_json(v) for v in value)
    return value is None or isinstance(value, str | bool | int)


def _mime(path: Path) -> str:
    if path.suffix == ".md":
        return "text/markdown"
    return mimetypes.guess_type(path.name)[0] or "text/plain"


def _load_file(path: Path, skill_dir: Path) -> SkillFile:
    rel = path.relative_to(skill_dir).as_posix()
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillError(f"{path}: skill files must be UTF-8 text") from exc
    uri = f"skill://{skill_dir.name}/{rel}"
    return SkillFile(
        uri=uri,
        name=uri.removeprefix("skill://"),
        mime_type=_mime(path),
        text=text,
        digest=f"sha256:{hashlib.sha256(raw).hexdigest()}",
        size=len(raw),
    )


def load_skill(skill_dir: Path) -> Skill:
    """Read one ``skills/<name>/`` directory into a spec-shaped skill."""
    name = skill_dir.name
    files = [
        _load_file(path, skill_dir)
        for path in sorted(skill_dir.rglob("*"))
        if path.is_file()
        # Editor and OS droppings are not part of the skill.
        and not any(p.startswith(".") for p in path.relative_to(skill_dir).parts)
    ]
    skill_uri = f"skill://{name}/{_SKILL_FILE}"
    skill_md = next((f for f in files if f.uri == skill_uri), None)
    if skill_md is None:
        raise SkillError(f"{skill_dir}: no {_SKILL_FILE}")
    # Parsed from the very text that is served and digested.
    frontmatter = parse_frontmatter(skill_md.text)
    if frontmatter.get("name") != name:
        raise SkillError(
            f"{skill_dir / _SKILL_FILE}: frontmatter name "
            f"{frontmatter.get('name')!r} must equal the directory name {name!r}"
        )
    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SkillError(f"{skill_dir / _SKILL_FILE}: frontmatter needs a description")
    if len(files) > _MAX_FILES or sum(f.size for f in files) > _MAX_BYTES:
        raise SkillError(f"{skill_dir}: over the spec's per-skill size limits")
    # The spec asks SKILL.md's resource to carry the skill's name and description.
    files = [
        replace(f, name=name, description=description) if f is skill_md else f
        for f in files
    ]
    return Skill(uri=skill_uri, frontmatter=frontmatter, files=tuple(files))


def load_skills(root: Path = SKILLS_DIR) -> tuple[Skill, ...]:
    if not root.is_dir():
        log.warning("Skills directory {} not found; serving no skills", root)
        return ()
    return tuple(
        load_skill(d)
        for d in sorted(root.iterdir())
        if d.is_dir() and not d.name.startswith(".")
    )


def _cacheable(**fields: Any) -> dict[str, Any]:
    return {
        "resultType": "complete",
        "ttlMs": _TTL_MS,
        "cacheScope": "public",
        **fields,
    }


class Skills(Extension):
    """Advertise and serve ``skills/`` over MCP."""

    identifier = EXTENSION_ID

    def __init__(self, skills: Sequence[Skill] | None = None) -> None:
        self._skills = tuple(load_skills() if skills is None else skills)
        self._by_uri = {s.uri: s for s in self._skills}

    def resources(self) -> Sequence[ResourceBinding]:
        return [
            ResourceBinding(
                TextResource(
                    uri=f.uri,
                    name=f.name,
                    description=f.description,
                    mime_type=f.mime_type,
                    text=f.text,
                )
            )
            for skill in self._skills
            for f in skill.files
        ]

    def methods(self) -> Sequence[MethodBinding]:
        return [
            MethodBinding("skills/list", PaginatedRequestParams, self._list),
            MethodBinding("skills/get", GetSkillParams, self._get),
        ]

    async def _list(
        self, _ctx: ServerRequestContext[Any, Any], _params: PaginatedRequestParams
    ) -> dict[str, Any]:
        # A handful of skills: one page, so no cursor is ever issued.
        return _cacheable(skills=[s.entry() for s in self._skills])

    async def _get(
        self, _ctx: ServerRequestContext[Any, Any], params: GetSkillParams
    ) -> dict[str, Any]:
        skill = self._by_uri.get(params.uri)
        if skill is None:
            raise MCPError(INVALID_PARAMS, f"No skill is served at {params.uri}")
        return _cacheable(skill=skill.entry())
