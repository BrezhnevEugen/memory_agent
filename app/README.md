# BrainAI.app — self-contained build

One `.app` with everything inside: relocatable CPython + LightRAG server, Ollama binary (embeddings only), MCP server, menu bar UI. No Homebrew, no launchd, no Python required on the target Mac.

## Build

```bash
cd app
./build.sh                 # → dist/BrainAI.app, dist/BrainAI-0.1.0.dmg
./bump.sh minor            # 0.1.0 → 0.2.0, opens a CHANGELOG section
./release.sh               # tag + GitHub release with the DMG
ARCH=x86_64 ./build.sh     # Intel build (run on Intel or with Rosetta python)
```

Downloads at build time: python-build-standalone (~30 MB), `lightrag-hku[api]` + pyobjc (~400 MB), `ollama-darwin.tgz` (~30 MB). Result ≈ 600–700 MB app, DMG ≈ 250 MB.

`bge-m3` (~1.2 GB) is **not** bundled — downloaded on first launch into `~/Library/Application Support/BrainAI/ollama/models` with progress in the menu bar.

## First launch (end user)

1. Drag BrainAI.app to Applications. Unsigned build → right-click → Open (or `xattr -dr com.apple.quarantine /Applications/BrainAI.app`).
2. App starts bundled Ollama, pulls `bge-m3`, then opens Settings.
3. Enter DeepSeek API key (button **Get** opens the keys page) → Apply. Server starts on `http://127.0.0.1:9621`.
4. Settings → **Connect agents**: one click writes the `lightrag` MCP server into Claude Desktop (`claude_desktop_config.json`), Claude Code (`~/.claude.json`), Cursor (`~/.cursor/mcp.json`) or Codex (`~/.codex/config.toml`). Restart that app. **Copy config** puts the JSON on the clipboard for anything else.
5. Optional: **Start at login** (creates `~/Library/LaunchAgents/com.brainai.app.plist`).

If Ollama is already running on `:11434` (user's own install), the app reuses it instead of starting the bundled one.

## Files

| File | Purpose |
|---|---|
| `brainai.py` | Tray + process manager (Ollama, LightRAG), Settings, MCP config |
| `mcp_server.py` | MCP stdio → LightRAG REST |
| `env.default` | Template copied to `~/Library/Application Support/BrainAI/.env` |
| `launcher.sh` | `Contents/MacOS/BrainAI` — execs bundled python |
| `Info.plist` | `LSUIElement` (no Dock icon) |
| `make_icon.py` | Renders 🧠 into `.icns` |
| `build.sh` | Full build + ad-hoc codesign + DMG |

## Dev run without building

```bash
pip install lightrag-hku[api] mcp httpx psutil rumps pyobjc-framework-Cocoa
brew install ollama   # or any ollama on PATH
python3 brainai.py
```

## Signing / notarization

`build.sh` auto-detects a `Developer ID Application` identity in the keychain (override with `SIGN_ID`, use `SIGN_ID=-` for ad-hoc). Signs every Mach-O inside-out with hardened runtime + `entitlements.plist`, then the DMG.

Notarization runs when `NOTARY_PROFILE` is set to a profile created with `xcrun notarytool store-credentials <name>`:

```bash
NOTARY_PROFILE=brainai ./build.sh    # sign → notarize (wait) → staple app + dmg → spctl check
```

## Versioning

Single source of truth: `app/VERSION` (semver). `build.sh` stamps it into `Info.plist` and the bundle, the tray shows it in the menu header. Flow for a new release:

```bash
./bump.sh patch|minor|major   # updates VERSION, adds "## x.y.z — date" to CHANGELOG.md
# fill in CHANGELOG.md
./build.sh && ./release.sh    # release notes are taken from that CHANGELOG section
git commit -am "Release x.y.z" && git push
```

`release.sh` refuses to overwrite an existing tag or release.
