# Release Process

This project ships three interfaces from a single version in `pyproject.toml`:

| Interface | Entry point | Distribution |
|-----------|------------|--------------|
| **CLI** | `mycli` | PyPI + GitHub Release |
| **API** | `mycli-api` | PyPI + GitHub Release |
| **MCP** | `mycli-mcp` | PyPI + GitHub Release + MCP Registry |

All three are packaged together and share the same version number.

## Quick Release

```bash
# Bump version, commit, and tag (default: patch)
make bump_version            # 0.1.1 -> 0.1.2
make bump_version BUMP=minor # 0.1.1 -> 0.2.0
make bump_version BUMP=major # 0.1.1 -> 1.0.0
```

Then push the tag to trigger the release:

```bash
git push origin main --follow-tags
```

## What Happens on Tag Push

When a `v*` tag is pushed, two GitHub Actions workflows run:

1. **Release** (`.github/workflows/release.yml`)
   - Runs CI checks (`make ci`) and tests (`make test`)
   - Builds the Python package (`uv build`)
   - Publishes to PyPI via trusted publishing (OIDC)
   - Creates a GitHub Release with auto-generated release notes
   - Uploads wheel and sdist artifacts

2. **MCP Registry Publish** (`.github/workflows/mcp-registry-publish.yml`)
   - Authenticates via GitHub OIDC
   - Publishes the MCP server to the MCP Registry

## Step-by-Step

1. **Ensure `main` is clean and CI passes**
   ```bash
   make ci && make test
   ```

2. **Bump the version**
   ```bash
   make bump_version BUMP=patch   # or minor/major
   ```
   This updates `pyproject.toml`, commits the change, and creates an annotated tag.

3. **Push with tags**
   ```bash
   git push origin main --follow-tags
   ```

4. **Verify the release**
   - Check [GitHub Actions](../../actions) for workflow status
   - Check [Releases](../../releases) for the new release
   - Check the [MCP Registry](https://registry.modelcontextprotocol.io) for the updated MCP server

## Version Location

The version is defined in a single place:

- `pyproject.toml` line `version = "x.y.z"`

All interfaces read this at runtime via `importlib.metadata.version()`.

## PyPI Setup (one-time)

The release workflow uses [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/) (no API tokens needed):

1. Create the package on [pypi.org](https://pypi.org/manage/projects/)
2. Add a trusted publisher under the package settings:
   - Owner: your GitHub username or org
   - Repository: your GitHub repository name
   - Workflow: `release.yml`
   - Environment: `release`
3. Create a `release` environment in GitHub repo settings (Settings > Environments)

After this, every tag push will auto-publish to PyPI.

## Hotfix Release

For urgent fixes on top of a release:

1. Create a branch from the release tag: `git checkout -b hotfix/description v0.1.1`
2. Apply the fix and run `make ci && make test`
3. Bump the patch version: `make bump_version BUMP=patch`
4. Open a PR to `main`, squash-merge, then push the tag from `main`
