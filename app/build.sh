#!/usr/bin/env bash
# Build a self-contained BrainAI.app (+ DMG) for macOS.
#   ./build.sh            → dist/BrainAI.app, dist/BrainAI-<ver>.dmg
# Env overrides: VERSION, PY_VER (3.12), ARCH (aarch64|x86_64), OLLAMA_VER (latest)
set -euo pipefail
cd "$(dirname "$0")"

VERSION="${VERSION:-0.1.0}"
PY_VER="${PY_VER:-3.12}"
ARCH="${ARCH:-$(uname -m | sed 's/arm64/aarch64/')}"
OLLAMA_VER="${OLLAMA_VER:-latest}"

BUILD=build; DIST=dist
APP="$DIST/BrainAI.app"
RES="$APP/Contents/Resources"
mkdir -p "$BUILD" "$DIST"

# ── 1. Relocatable CPython (python-build-standalone) ──
if [ ! -x "$BUILD/python/bin/python3" ]; then
  echo "▶ python-build-standalone $PY_VER $ARCH"
  TAG=$(curl -sIL https://github.com/astral-sh/python-build-standalone/releases/latest -o /dev/null -w '%{url_effective}' | sed 's#.*/##')
  ASSET=$(curl -sL "https://github.com/astral-sh/python-build-standalone/releases/expanded_assets/$TAG" \
          | grep -o "cpython-${PY_VER}\.[0-9]*+${TAG}-${ARCH}-apple-darwin-install_only\.tar\.gz" | head -1)
  [ -n "$ASSET" ] || { echo "python asset not found"; exit 1; }
  curl -L --progress-bar -o "$BUILD/python.tgz" "https://github.com/astral-sh/python-build-standalone/releases/download/$TAG/$ASSET"
  tar -xzf "$BUILD/python.tgz" -C "$BUILD"          # → build/python
fi
PY="$BUILD/python/bin/python3"

echo "▶ pip deps"
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q "lightrag-hku[api]" mcp httpx psutil rumps pyobjc-framework-Cocoa

# ── 2. Ollama binary ──
if [ ! -x "$BUILD/ollama/ollama" ]; then
  echo "▶ ollama ($OLLAMA_VER)"
  if [ "$OLLAMA_VER" = latest ]; then URL=https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz
  else URL=https://github.com/ollama/ollama/releases/download/$OLLAMA_VER/ollama-darwin.tgz; fi
  mkdir -p "$BUILD/ollama"
  curl -L --progress-bar -o "$BUILD/ollama.tgz" "$URL"
  tar -xzf "$BUILD/ollama.tgz" -C "$BUILD/ollama"
  [ -x "$BUILD/ollama/ollama" ] || { echo "ollama binary missing after extract"; ls -R "$BUILD/ollama"; exit 1; }
fi

# ── 3. Assemble bundle ──
echo "▶ bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES"
sed "s/__VERSION__/$VERSION/g" Info.plist > "$APP/Contents/Info.plist"
install -m 755 launcher.sh "$APP/Contents/MacOS/BrainAI"
cp brainai.py mcp_server.py env.default "$RES/"
rsync -a --exclude '__pycache__' --exclude '*.pyc' "$BUILD/python" "$RES/"
rsync -a "$BUILD/ollama" "$RES/"
# strip pip/setuptools/tests to save space
rm -rf "$RES"/python/lib/python*/site-packages/{pip,setuptools,wheel}* \
       "$RES"/python/lib/python*/test "$RES"/python/lib/python*/idlelib 2>/dev/null || true

"$RES/python/bin/python3" make_icon.py "$RES/BrainAI.icns"

# ── 4. Sign (ad-hoc; required on Apple Silicon) ──
echo "▶ codesign"
codesign --force --deep --sign "${SIGN_ID:--}" "$APP"
xattr -cr "$APP" || true

# ── 5. DMG ──
echo "▶ dmg"
DMG="$DIST/BrainAI-$VERSION.dmg"
rm -f "$DMG"
STAGE=$(mktemp -d); cp -R "$APP" "$STAGE/"; ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname BrainAI -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

du -sh "$APP" "$DMG"
echo "✓ done"
