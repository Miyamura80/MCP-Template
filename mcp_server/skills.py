"""MCP Skills extension (``io.modelcontextprotocol/skills``, SEP-2640, Beta).

Serves the hand-written skills in the repo's ``skills/<name>/`` directory to
MCP hosts, so an agent connected to this server can load how-to guidance for
its tools without anyone installing it separately:

    skills/list   -> every skill: uri, verbatim frontmatter, and a manifest of
                     its files (``skill://<name>/<path>``, sha256 digest, size)
    skills/get    -> one skill by its SKILL.md uri (-32602 if unknown)
    resources/read skill://<name>/<path> -> the file (base protocol)

Hosts verify each file they read against the manifest's digest and size, so
the manifest is computed from the exact bytes served, once, at startup. The
optional ``directoryRead`` feature (``resources/directory/read``) is not
offered: every file is already enumerated in the manifest.

Spec: https://github.com/modelcontextprotocol/ext-skills
(``specification/beta/skills.mdx``). Beta, so breaking changes are possible;
verify against the current spec before changing wire shapes.
"""

from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Sequence
from dataclasses import dataclass
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

# Source of truth, shipped in the wheel (pyproject force-include) beside the
# packages. ``mcp_server`` sits one level below the packages root.
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# Skills change only with a deploy, so hosts may cache the listing a while.
_TTL_MS = 300_000
_SKILL_FILE = "SKILL.md"
# Spec limits per skill.
_MAX_FILES = 512
_MAX_BYTES = 16 * 1024 * 1024


class GetSkillParams(RequestParams):
    uri: str


@dataclass(frozen=True)
class SkillFile:
    uri: str
    mime_type: str
    text: str
    digest: str
    size: int


@dataclass(frozen=True)
class Skill:
    uri: str  # the SKILL.md uri: a skill's identity
    frontmatter: dict[str, Any]
    files: tuple[SkillFile, ...]
    body: str

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


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``---``-fenced YAML frontmatter from a SKILL.md."""
    if not text.startswith("---\n"):
        raise SkillError("SKILL.md must begin with YAML frontmatter")
    end = text.find("\n---", 4)
    if end == -1:
        raise SkillError("SKILL.md frontmatter is not closed with ---")
    data = yaml.safe_load(text[4:end]) or {}
    if not isinstance(data, dict):
        raise SkillError("SKILL.md frontmatter must be a mapping")
    return data, text[end + 4 :]


def _mime(path: Path) -> str:
    if path.suffix == ".md":
        return "text/markdown"
    return mimetypes.guess_type(path.name)[0] or "text/plain"


def load_skill(skill_dir: Path) -> Skill:
    """Read one ``skills/<name>/`` directory into a spec-shaped skill."""
    name = skill_dir.name
    skill_md = skill_dir / _SKILL_FILE
    if not skill_md.is_file():
        raise SkillError(f"{skill_dir}: no {_SKILL_FILE}")
    frontmatter, body = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    if frontmatter.get("name") != name:
        raise SkillError(
            f"{skill_md}: frontmatter name {frontmatter.get('name')!r} must equal "
            f"the directory name {name!r}"
        )
    if not str(frontmatter.get("description") or "").strip():
        raise SkillError(f"{skill_md}: frontmatter needs a description")

    files: list[SkillFile] = []
    for path in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(skill_dir).as_posix()
        if any(part.startswith(".") for part in Path(rel).parts):
            continue  # editor and OS droppings are not part of the skill
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillError(f"{path}: skill files must be UTF-8 text") from exc
        files.append(
            SkillFile(
                uri=f"skill://{name}/{rel}",
                mime_type=_mime(path),
                text=text,
                digest=f"sha256:{hashlib.sha256(raw).hexdigest()}",
                size=len(raw),
            )
        )
    if len(files) > _MAX_FILES or sum(f.size for f in files) > _MAX_BYTES:
        raise SkillError(f"{skill_dir}: over the spec's per-skill size limits")
    return Skill(
        uri=f"skill://{name}/{_SKILL_FILE}",
        frontmatter=frontmatter,
        files=tuple(files),
        body=body,
    )


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

    @property
    def skills(self) -> tuple[Skill, ...]:
        return self._skills

    def resources(self) -> Sequence[ResourceBinding]:
        return [
            ResourceBinding(
                TextResource(
                    uri=f.uri,
                    name=f.uri.removeprefix("skill://"),
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
