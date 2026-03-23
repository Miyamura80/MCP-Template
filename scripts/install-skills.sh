#!/usr/bin/env bash
# Downloads agent skill files from the MCP-Template repository into .claude/skills/
set -euo pipefail

REPO="Miyamura80/MCP-Template"
BRANCH="main"
BASE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}"

SKILLS=(
  code-quality
  cleanup
  prd
  prek-precommit-hook
  ralph
  wait
)

for skill in "${SKILLS[@]}"; do
  dir=".claude/skills/${skill}"
  mkdir -p "${dir}"
  echo "Downloading ${skill}..."
  curl -fsSL -o "${dir}/SKILL.md" "${BASE_URL}/.claude/skills/${skill}/SKILL.md"
done

echo "Installed ${#SKILLS[@]} skills into .claude/skills/"
