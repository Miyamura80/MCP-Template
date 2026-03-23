#!/usr/bin/env bash
# Downloads the usage skill from the MCP-Template repository into .claude/skills/
set -euo pipefail

REPO="Miyamura80/MCP-Template"
BRANCH="main"
BASE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}"

dir=".claude/skills/usage"
mkdir -p "${dir}"
echo "Downloading usage skill..."
curl -fsSL -o "${dir}/SKILL.md" "${BASE_URL}/.claude/skills/usage/SKILL.md"

echo "Installed usage skill into .claude/skills/usage/"
