#!/usr/bin/env bash
# One-time heavy provisioning of the Goose desktop app for e2e testing.
# Idempotent: every step is skipped if its output already exists.
# This step is REPO-AGNOSTIC - it only builds Goose + Electron, so the build under
# $E2E_HOME/goose_src is shared with any other goose-gui-e2e harness on the machine.
#
# Encodes the sandbox-specific fixes so a future session hits ZERO of the errors
# we hit discovering them:
#   - goose is BUILT from the official source (`git clone` of block/goose)
#   - Electron binary comes from the official GitHub release, checksum-verified
#     (override ELECTRON_BASE_URL, e.g. to an npmmirror URL, if GitHub release assets are blocked)
#   - pnpm install overrides @electron/node-gyp to a registry build (its git tarball 403s on codeload)
#   - the verified Electron binary is placed into node_modules so electron-forge/Playwright use it
#   - the dev main-process bundle is produced so Playwright can launch the app
#
# PRECONDITION: github.com + release-assets.githubusercontent.com reachable (or set
# ELECTRON_BASE_URL to a mirror that is).
set -euo pipefail
source "$(dirname "$0")/lib.sh"

GOOSE_REPO_URL="${GOOSE_REPO_URL:-https://github.com/block/goose.git}"
GOOSE_REF="${GOOSE_REF:-v1.60.2}"  # last release verified with this harness
ELECTRON_BASE_URL="${ELECTRON_BASE_URL:-https://github.com/electron/electron/releases/download}"
export ELECTRON_MIRROR="${ELECTRON_BASE_URL%/}/"

log "E2E_HOME=$E2E_HOME  GOOSE_SRC=$GOOSE_SRC"

# ---- 0. pin goose to a known-good release (GOOSE_REF: a tag, branch or SHA) ----
# Goose main moves fast (engines bumps, bundle renames, MCP-App bridge changes);
# building an unpinned HEAD made this harness break without any change here.
# The checkout itself is the source of truth (goose_src may be shared with other
# harnesses), so compare HEAD to the ref and re-check-out on any mismatch.
goose_ref_sha() { git -C "$GOOSE_SRC" rev-parse -q --verify "$GOOSE_REF^{commit}" 2>/dev/null; }
if [ -d "$GOOSE_SRC/.git" ]; then
  want="$(goose_ref_sha || { git -C "$GOOSE_SRC" fetch -q --depth 1 origin "$GOOSE_REF" && git -C "$GOOSE_SRC" rev-parse FETCH_HEAD; })" \
    || { echo "FATAL: cannot resolve GOOSE_REF=$GOOSE_REF in $GOOSE_REPO_URL"; exit 1; }
  if [ "$(git -C "$GOOSE_SRC" rev-parse HEAD)" != "$want" ]; then
    log "goose checkout != $GOOSE_REF; checking out $want and invalidating build outputs"
    git -C "$GOOSE_SRC" checkout -q -f "$want"
    git -C "$GOOSE_SRC" clean -q -fdx -e target/   # drop old-ref build products, keep cargo cache
    rm -f "$GOOSE_BINARY"
  fi
fi

# ---- 1. clone + build the goose CLI (the backend Goose desktop spawns as 'goose serve') ----
if [ ! -x "$GOOSE_BINARY" ]; then
  if [ ! -d "$GOOSE_SRC/.git" ]; then
    log "cloning goose $GOOSE_REF"
    git init -q "$GOOSE_SRC" && git -C "$GOOSE_SRC" remote add origin "$GOOSE_REPO_URL"
    git -C "$GOOSE_SRC" fetch -q --depth 1 origin "$GOOSE_REF" && git -C "$GOOSE_SRC" checkout -q FETCH_HEAD
  fi
  log "building goose CLI (portable-default: no local-inference/keyring) - ~5 min"
  ( cd "$GOOSE_SRC" && cargo build -p goose-cli --bin goose --no-default-features --features portable-default )
  log "goose CLI built: $($GOOSE_BINARY --version)"
else
  log "goose CLI present: $($GOOSE_BINARY --version)"
fi

# ---- 1b. Node + pnpm satisfying goose's `engines` (official builds) ----
# Goose pins engines.node / engines.pnpm. Provision the official Node major it
# asks for (checksum-verified, under $E2E_NODE) plus that pnpm, instead of
# installing on an unsupported runtime. (engine-strict stays off for pnpm
# install below only so transitive deps' engines don't block it.)
ENGINES="$(python3 - "$DESK/package.json" <<'PY'
import json, re, sys
e = json.load(open(sys.argv[1])).get("engines", {})
node = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", e.get("node", ""))
pnpm = re.search(r"\d+\.\d+\.\d+", e.get("pnpm", ""))
print(".".join(g or "0" for g in node.groups()) if node else "-", pnpm.group(0) if pnpm else "-")
PY
)" || { echo "FATAL: cannot read engines from $DESK/package.json"; exit 1; }
read -r NEED_NODE NEED_PNPM <<<"$ENGINES"
HAVE_NODE="$(node -p process.versions.node 2>/dev/null || echo 0.0.0)"
if [ "$NEED_NODE" != "-" ] && ! { [ "${HAVE_NODE%%.*}" = "${NEED_NODE%%.*}" ] && version_ge "$HAVE_NODE" "$NEED_NODE"; }; then
  NV="$(curl -fsSL --max-time 20 https://nodejs.org/dist/index.json | python3 -c "import json,sys;print(next((r['version'] for r in json.load(sys.stdin) if r['version'].startswith('v${NEED_NODE%%.*}.')), ''))")"
  [ -n "$NV" ] || { echo "FATAL: no Node v${NEED_NODE%%.*}.x on nodejs.org"; exit 1; }
  log "goose wants Node $NEED_NODE (have $HAVE_NODE); installing official $NV into $E2E_NODE"
  NTAR="node-${NV}-linux-x64.tar.xz"
  fetch_verified "https://nodejs.org/dist/$NV" "$NTAR" "$E2E_HOME/node-dl"
  rm -rf "$E2E_NODE"; mkdir -p "$E2E_NODE"
  tar -xJf "$E2E_HOME/node-dl/$NTAR" -C "$E2E_NODE" --strip-components=1
  use_e2e_node
fi
HAVE_PNPM="$(pnpm -v 2>/dev/null || echo 0.0.0)"
if [ "$NEED_PNPM" != "-" ] && ! version_ge "$HAVE_PNPM" "$NEED_PNPM"; then
  log "goose wants pnpm >= $NEED_PNPM (have $HAVE_PNPM); installing pnpm@$NEED_PNPM into $E2E_NODE"
  mkdir -p "$E2E_NODE"
  npm install -g --prefix "$E2E_NODE" "pnpm@$NEED_PNPM" >/dev/null
  use_e2e_node
fi
log "toolchain: node $(node -v), pnpm $(pnpm -v)"

# ---- 2. Electron binary (official release by default), checksum-verified ----
EV="$(grep -m1 '"electron":' "$DESK/package.json" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
log "pinned Electron version: $EV"
if [ ! -x "$ELECTRON_BIN" ]; then
  DL="$E2E_HOME/electron-dl"
  ZIP="electron-v${EV}-linux-x64.zip"
  fetch_verified "${ELECTRON_BASE_URL%/}/v${EV}" "$ZIP" "$DL" || {
    echo "FATAL: Electron download failed. Allow github.com + release-assets.githubusercontent.com,"
    echo "  or set ELECTRON_BASE_URL to a reachable Electron mirror."
    exit 1
  }
  rm -rf "$DL/dist"; mkdir -p "$DL/dist"
  ( cd "$DL/dist" && (command -v unzip >/dev/null && unzip -oq "../$ZIP" || python3 -c "import zipfile;zipfile.ZipFile('../$ZIP').extractall('.')") )
  chmod +x "$DL/dist/electron"
fi

# ---- 3. pnpm install the ui workspace (with the node-gyp override) ----
# Gate on a completion stamp, NOT on node_modules existing: a failed pnpm install
# leaves a partial node_modules behind, and gating on the dir would skip the retry.
PNPM_STAMP="$GOOSE_SRC/ui/node_modules/.mcp-template-install-done"
if [ ! -f "$PNPM_STAMP" ]; then
  WS="$GOOSE_SRC/ui/pnpm-workspace.yaml"
  # Match the override KEY (quoted, with colon), not any mention: goose's file has a
  # *comment* about @electron/node-gyp that a bare `grep @electron/node-gyp` matches,
  # which would fool us into thinking the override is already there and skip inserting it.
  if ! grep -q "'@electron/node-gyp':" "$WS"; then
    log "adding @electron/node-gyp registry override (codeload git tarball is egress-blocked)"
    # insert under the existing `overrides:` block
    python3 - "$WS" <<'PY'
import sys,re
p=sys.argv[1]; s=open(p).read()
# subn returns the match count: if the `overrides:` block was reformatted upstream
# (e.g. `overrides: {}`) the sub silently no-ops, the override goes missing, and
# pnpm install later 403s on the codeload tarball with a misleading error. Fail here.
s,n=re.subn(r"(\noverrides:\n)", r"\1  '@electron/node-gyp': 10.2.0-electron.2\n", s, count=1)
if not n:
    sys.exit("FATAL: 'overrides:' block not found in pnpm-workspace.yaml - upstream goose layout changed; update setup.sh")
open(p,"w").write(s)
PY
  fi
  log "pnpm install (Electron binary download skipped; we supply a verified one)"
  ( cd "$GOOSE_SRC/ui" && ELECTRON_SKIP_BINARY_DOWNLOAD=1 PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm_config_engine_strict=false pnpm install --no-frozen-lockfile )
  touch "$PNPM_STAMP"  # only reached if pnpm install succeeded (set -e aborts otherwise)
fi

# ---- 4. place the verified Electron binary where the package expects it ----
if [ ! -x "$ELECTRON_BIN" ]; then
  log "installing verified Electron into node_modules"
  rm -rf "$GOOSE_SRC/ui/node_modules/electron/dist"
  cp -r "$E2E_HOME/electron-dl/dist" "$GOOSE_SRC/ui/node_modules/electron/dist"
  printf 'electron' > "$GOOSE_SRC/ui/node_modules/electron/path.txt"
fi
log "Electron: $($ELECTRON_BIN --no-sandbox --version 2>/dev/null | tail -1)"

# ---- 5. build the dev main-process bundle (.vite/build/main.{js,cjs}) that Playwright launches ----
if [ -z "$(goose_main_bundle)" ]; then
  log "building desktop bundles via electron-forge start (dev), then stopping"
  ( cd "$DESK" && node scripts/i18n-compile.js >/dev/null 2>&1 || true )
  xvfb-run -a bash -c "cd '$DESK' && ELECTRON_DISABLE_SANDBOX=1 timeout 180 ../node_modules/.bin/electron-forge start -- --no-sandbox --disable-gpu >'$E2E_HOME/forge_build.log' 2>&1" &
  FPID=$!
  # wait (bounded) for the bundle to appear, then stop forge
  for _ in $(seq 1 90); do [ -n "$(goose_main_bundle)" ] && break; sleep 2; done
  pkill -9 -P "$FPID" 2>/dev/null || true; pkill -9 -f 'electron-forge' 2>/dev/null || true; pkill -9 -f 'dist/electron' 2>/dev/null || true
  [ -n "$(goose_main_bundle)" ] || { echo "FATAL: main bundle not built (see $E2E_HOME/forge_build.log)"; exit 1; }
fi
log "desktop main bundle ready: $(goose_main_bundle)"

log "SETUP COMPLETE - now: bash up.sh && bash run_test.sh settings_render"
