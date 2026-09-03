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
4. Settings → **Projects**: **New…** creates a project (name + id), **Link folder…** writes the `lightrag` MCP server bound to that id into `<folder>/.mcp.json`, `<folder>/.cursor/mcp.json` and `<folder>/.codex/config.toml` (Codex reads it only for trusted projects) and records the folder in `projects.json`; **Unlink** removes those entries. **Claude Desktop →** writes the global `claude_desktop_config.json` bound to the selected project. Restart the agent. **Copy config** puts the JSON for the selected project on the clipboard for anything else.
5. Optional: **Start at login** (creates `~/Library/LaunchAgents/com.brainai.app.plist`).

BrainAI checks GitHub Releases silently about five seconds after launch, throttled to once every six hours. If a newer version exists, a notification points to **Check for updates…** in the tray menu. Installation downloads the release DMG, verifies its published SHA-256 checksum, Developer ID team and Gatekeeper assessment, then replaces and relaunches the app with rollback on failure. User data in `~/Library/Application Support/BrainAI` is not touched.

If Ollama is already running on `:11434` (user's own install), the app reuses it instead of starting the bundled one.

## Projects (data isolation)

LightRAG 1.5 binds one workspace to the whole server process and honours the `LIGHTRAG-WORKSPACE` header only in `/health`. `brainai_server.py` wraps `lightrag_server.create_app()` without touching site-packages: it swaps the single `LightRAG` / `DocumentManager` for proxies backed by a pool, and a pure-ASGI middleware resolves the header into a per-project instance (created lazily, `workspace=<id>`, so file storages land in `rag_storage/<id>/`). The design mirrors upstream's multi-workspace PRD (`plan/multi-workspace-authz` branch); once upstream ships request-level routing the wrapper can go.

- Requests **with** the header use that project; an invalid id → 400.
- Requests **without** the header (WebUI, tray polling, Ollama-compatible API) use the *UI project* (`BRAINAI_UI_PROJECT` in `.env`, switched from the tray via `POST /brainai/ui-project`).
- The MCP server always sends the header and checks `GET /brainai/projects` once; a plain `lightrag-server` (404) is rejected.
- Concurrency limits (`MAX_ASYNC_LLM`, embeddings) are per instance, so N active projects can reach N× the configured parallelism.

## Files

| File | Purpose |
|---|---|
| `brainai.py` | Tray + process manager (Ollama, LightRAG), Settings, MCP config, project switch |
| `brainai_server.py` | LightRAG server wrapper: one LightRAG instance per project, routed by `LIGHTRAG-WORKSPACE`; `/brainai/projects`, `/brainai/ui-project` |
| `mcp_server.py` | MCP stdio → LightRAG REST, bound to one project (`--project`), fail-closed |
| `updater.py` | Verified DMG download, extraction, bundle swap and rollback |
| `update_ui.py` | Native update dialog with release notes |
| `env.default` | Template copied to `~/Library/Application Support/BrainAI/.env` |
| `launcher.sh` | `Contents/MacOS/BrainAI` — execs bundled python |
| `Info.plist` | `LSUIElement` (no Dock icon) |
| `make_icon.py` | Renders 🧠 into `.icns` |
| `build.sh` | Full build + ad-hoc codesign + DMG |

## Dev run without building

```bash
pip install 'lightrag-hku[api]' 'mcp>=2,<3' certifi httpx psutil rumps pyobjc-framework-Cocoa
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
