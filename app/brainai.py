#!/usr/bin/env python3
"""
BrainAI — self-contained macOS menu bar app.

Manages three things without launchd or system-wide installs:
  * bundled Ollama (embeddings only, model bge-m3)
  * bundled LightRAG server (LLM = DeepSeek API)
  * MCP server config for agents (Cowork / Cursor / Claude Code)

Layout inside BrainAI.app/Contents/Resources:
  brainai.py, mcp_server.py, env.default
  python/            relocatable CPython with lightrag-hku[api], rumps, pyobjc…
  ollama/ollama      Ollama binary

User data (never inside the bundle):
  ~/Library/Application Support/BrainAI/{.env, rag_storage, inputs, logs, ollama/models}

Runs from source too (dev mode): python3 brainai.py — uses sys.executable and `ollama` from PATH.
"""

import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser

import httpx
import psutil
import rumps
import objc
from AppKit import (
    NSColor, NSFont, NSForegroundColorAttributeName, NSFontAttributeName,
    NSWindow, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSBackingStoreBuffered, NSTextField, NSButton, NSPopUpButton, NSBox,
    NSApp, NSObject, NSSecureTextField, NSBezelStyleRounded, NSPasteboard,
    NSPasteboardTypeString, NSButtonTypeSwitch,
)
from Foundation import NSMutableAttributedString, NSDictionary, NSMakeRect

# ─────────────────────────────────────────────────────────
# Paths & constants
# ─────────────────────────────────────────────────────────

APP_NAME = "BrainAI"
RES = pathlib.Path(__file__).resolve().parent
IN_BUNDLE = RES.name == "Resources" and RES.parent.name == "Contents"
APP_BUNDLE = RES.parent.parent if IN_BUNDLE else None

PYTHON = str(RES / "python" / "bin" / "python3") if IN_BUNDLE else sys.executable
OLLAMA_BIN = str(RES / "ollama" / "ollama") if IN_BUNDLE else (shutil.which("ollama") or "ollama")
MCP_SERVER = str(RES / "mcp_server.py")
ENV_DEFAULT = RES / "env.default"
try:
    VERSION = (RES / "VERSION").read_text().strip()
except Exception:
    VERSION = "dev"

DATA = pathlib.Path.home() / "Library" / "Application Support" / APP_NAME
ENV_FILE = DATA / ".env"
LOG_DIR = DATA / "logs"
OLLAMA_MODELS = DATA / "ollama" / "models"
LAUNCH_AGENT = pathlib.Path.home() / "Library" / "LaunchAgents" / "com.brainai.app.plist"

LIGHTRAG_PORT = 9621
LIGHTRAG_URL = f"http://127.0.0.1:{LIGHTRAG_PORT}"
OLLAMA_PORT = 11434
OLLAMA_URL = f"http://127.0.0.1:{OLLAMA_PORT}"
DEEPSEEK_URL = "https://api.deepseek.com"
EMBED_MODEL = "bge-m3"

CHECK_INTERVAL = 10
DOC_POLL_INTERVAL = 10

LLM_MODELS = [
    ("deepseek-v4-flash", "Fast & cheap — indexing"),
    ("deepseek-v4-pro", "Best quality — slower, pricier"),
]

# Bars / fonts / colors
BAR_WIDTH = 20
MONO_FONT = NSFont.fontWithName_size_("Menlo", 11.0)
LABEL_FONT = MONO_FONT
COLOR_GREEN = NSColor.colorWithSRGBRed_green_blue_alpha_(0.30, 0.75, 0.35, 1.0)
COLOR_YELLOW = NSColor.colorWithSRGBRed_green_blue_alpha_(0.90, 0.75, 0.10, 1.0)
COLOR_ORANGE = NSColor.colorWithSRGBRed_green_blue_alpha_(0.95, 0.55, 0.10, 1.0)
COLOR_RED = NSColor.colorWithSRGBRed_green_blue_alpha_(0.90, 0.25, 0.20, 1.0)
COLOR_GRAY = NSColor.colorWithSRGBRed_green_blue_alpha_(0.78, 0.78, 0.78, 1.0)
COLOR_DIMGRAY = NSColor.colorWithSRGBRed_green_blue_alpha_(0.55, 0.55, 0.55, 1.0)
COLOR_LABEL = NSColor.labelColor()


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def notify(title, subtitle, message):
    msg = message.replace('"', "'")
    script = f'display notification "{msg}" with title "{title}" subtitle "{subtitle}"'
    subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def log(msg):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "brainai.log", "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def read_env():
    env = {}
    try:
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("'\"")
    except Exception:
        pass
    return env


def write_env_value(key, value):
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    out, found = [], False
    for line in lines:
        if line.strip().startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n")


def ensure_data_dirs():
    for d in (DATA, LOG_DIR, DATA / "rag_storage", DATA / "inputs", OLLAMA_MODELS):
        d.mkdir(parents=True, exist_ok=True)
    if not ENV_FILE.exists():
        shutil.copy(ENV_DEFAULT, ENV_FILE)
        os.chmod(ENV_FILE, 0o600)


RUN_DIR = DATA / "run"


def _write_pid(name, pid):
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / f"{name}.pid").write_text(str(pid))


def _kill_stale(name):
    """Kill a child left over from a previous BrainAI instance (by pidfile + cmdline check)."""
    f = RUN_DIR / f"{name}.pid"
    try:
        pid = int(f.read_text())
        p = psutil.Process(pid)
        cmd = " ".join(p.cmdline())
        if str(RES) in cmd or "lightrag" in cmd or "ollama" in cmd:
            log(f"killing stale {name} pid={pid}")
            try:
                os.killpg(pid, signal.SIGTERM)
            except Exception:
                p.terminate()
            try:
                p.wait(5)
            except Exception:
                p.kill()
    except Exception:
        pass
    try:
        f.unlink()
    except Exception:
        pass


def acquire_single_instance():
    """Exit if another BrainAI is already running; otherwise record our pid."""
    f = RUN_DIR / "brainai.pid"
    try:
        pid = int(f.read_text())
        p = psutil.Process(pid)
        if pid != os.getpid() and "brainai.py" in " ".join(p.cmdline()):
            log(f"already running pid={pid}, exiting")
            notify(APP_NAME, "Already running", "BrainAI is in the menu bar")
            sys.exit(0)
    except (FileNotFoundError, ValueError, psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    _write_pid("brainai", os.getpid())


def port_open(url, path="/", timeout=2):
    try:
        return httpx.get(url + path, timeout=timeout).status_code < 500
    except Exception:
        return False


def _color_for_ratio(r):
    return COLOR_GREEN if r < 0.5 else COLOR_YELLOW if r < 0.7 else COLOR_ORANGE if r < 0.85 else COLOR_RED


def _attr(text, color, font):
    attrs = NSDictionary.dictionaryWithObjects_forKeys_(
        [font, color], [NSFontAttributeName, NSForegroundColorAttributeName])
    return NSMutableAttributedString.alloc().initWithString_attributes_(text, attrs)


def make_bar(label, used, total):
    ratio = min(used / total, 1.0) if total > 0 else 0.0
    filled = round(ratio * BAR_WIDTH)
    s = NSMutableAttributedString.alloc().init()
    s.appendAttributedString_(_attr(label.ljust(8), COLOR_DIMGRAY, LABEL_FONT))
    if filled:
        s.appendAttributedString_(_attr("█" * filled, _color_for_ratio(ratio), MONO_FONT))
    if BAR_WIDTH - filled:
        s.appendAttributedString_(_attr("░" * (BAR_WIDTH - filled), COLOR_GRAY, MONO_FONT))
    s.appendAttributedString_(_attr(f"  {used:.1f}/{total:.0f} GB  {int(ratio * 100)}%", COLOR_LABEL, LABEL_FONT))
    return s


def make_text(text, color=None, font=None):
    return _attr(text, color or COLOR_LABEL, font or NSFont.menuFontOfSize_(13.0))


def make_label(text, frame, bold=False, size=13.0):
    lbl = NSTextField.alloc().initWithFrame_(frame)
    lbl.setStringValue_(text)
    lbl.setBezeled_(False)
    lbl.setDrawsBackground_(False)
    lbl.setEditable_(False)
    lbl.setSelectable_(False)
    lbl.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    return lbl


def make_button(title, frame, target, action):
    b = NSButton.alloc().initWithFrame_(frame)
    b.setTitle_(title)
    b.setBezelStyle_(NSBezelStyleRounded)
    b.setTarget_(target)
    b.setAction_(action)
    return b


def mcp_entry():
    return {"command": PYTHON, "args": [MCP_SERVER, "--lightrag-url", LIGHTRAG_URL]}


def mcp_config():
    return json.dumps({"mcpServers": {"lightrag": mcp_entry()}}, indent=2)


HOME = pathlib.Path.home()
MCP_TARGETS = {
    # name: (path, kind)   kind: json → {"mcpServers": {...}}, toml → [mcp_servers.lightrag]
    "Claude Desktop": (HOME / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json", "json"),
    "Claude Code": (HOME / ".claude.json", "json"),
    "Cursor": (HOME / ".cursor" / "mcp.json", "json"),
    "Codex": (HOME / ".codex" / "config.toml", "toml"),
}


def install_mcp(name):
    """Merge the lightrag MCP server into an agent's config. Returns path written."""
    import re
    path, kind = MCP_TARGETS[name]
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "json":
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
        except Exception:
            data = {}
        data.setdefault("mcpServers", {})["lightrag"] = mcp_entry()
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    else:
        text = path.read_text() if path.exists() else ""
        # drop existing [mcp_servers.lightrag] section: header line up to the next table header
        text = re.sub(r"^\[mcp_servers\.lightrag\]\n(?:(?!^\[).*\n?)*", "", text, flags=re.M)
        text = text.rstrip() + "\n" if text.strip() else ""
        args = ", ".join(json.dumps(a) for a in mcp_entry()["args"])
        text += f'\n[mcp_servers.lightrag]\ncommand = {json.dumps(PYTHON)}\nargs = [{args}]\n'
        path.write_text(text)
    log(f"MCP installed for {name}: {path}")
    return path


# ─────────────────────────────────────────────────────────
# Process manager
# ─────────────────────────────────────────────────────────

class Services:
    """Owns the ollama and lightrag child processes."""

    def __init__(self):
        self.ollama_proc = None
        self.ollama_external = False   # someone else's Ollama already on the port
        self.lightrag_proc = None
        self.pull_progress = None      # None | 0..100
        self._lock = threading.Lock()

    # ── Ollama ──

    def start_ollama(self):
        _kill_stale("ollama")
        if port_open(OLLAMA_URL, "/api/tags"):
            self.ollama_external = self.ollama_proc is None
            return True
        if not os.path.exists(OLLAMA_BIN) and not shutil.which(OLLAMA_BIN):
            log(f"ollama binary not found: {OLLAMA_BIN}")
            return False
        env = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{OLLAMA_PORT}",
                   OLLAMA_MODELS=str(OLLAMA_MODELS), OLLAMA_KEEP_ALIVE="10m")
        out = open(LOG_DIR / "ollama.log", "a")
        self.ollama_proc = subprocess.Popen([OLLAMA_BIN, "serve"], env=env, stdout=out, stderr=out,
                                            start_new_session=True)
        _write_pid("ollama", self.ollama_proc.pid)
        self.ollama_external = False
        for _ in range(30):
            if port_open(OLLAMA_URL, "/api/tags"):
                return True
            time.sleep(1)
        return False

    def embed_model_present(self):
        try:
            tags = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3).json().get("models", [])
            return any(m.get("name", "").split(":")[0] == EMBED_MODEL for m in tags)
        except Exception:
            return False

    def pull_embed_model(self, on_progress=None):
        """Stream /api/pull; report percent via on_progress(int)."""
        self.pull_progress = 0
        try:
            with httpx.stream("POST", f"{OLLAMA_URL}/api/pull",
                              json={"name": EMBED_MODEL, "stream": True}, timeout=None) as r:
                for line in r.iter_lines():
                    if not line:
                        continue
                    d = json.loads(line)
                    if d.get("total"):
                        pct = int(d.get("completed", 0) * 100 / d["total"])
                        if pct != self.pull_progress:
                            self.pull_progress = pct
                            if on_progress:
                                on_progress(pct)
                    if d.get("status") == "success":
                        break
            ok = self.embed_model_present()
        except Exception as e:
            log(f"pull failed: {e}")
            ok = False
        self.pull_progress = None
        return ok

    # ── LightRAG ──

    def start_lightrag(self):
        with self._lock:
            _kill_stale("lightrag")
            if self.lightrag_alive():
                log("lightrag already answering on port — reusing external instance")
                return True
            env = dict(os.environ)
            env.update(read_env())
            env.update(WORKING_DIR=str(DATA / "rag_storage"), INPUT_DIR=str(DATA / "inputs"),
                       HOST="127.0.0.1", PORT=str(LIGHTRAG_PORT),
                       EMBEDDING_BINDING_HOST=OLLAMA_URL, PYTHONUNBUFFERED="1")
            out = open(LOG_DIR / "server.log", "a")
            self.lightrag_proc = subprocess.Popen(
                [PYTHON, "-c", "from lightrag.api.lightrag_server import main; main()"],
                cwd=str(DATA), env=env, stdout=out, stderr=out, start_new_session=True)
            _write_pid("lightrag", self.lightrag_proc.pid)
        for _ in range(60):
            if self.lightrag_alive():
                return True
            if self.lightrag_proc.poll() is not None:
                return False
            time.sleep(1)
        return False

    def lightrag_alive(self):
        try:
            return httpx.get(f"{LIGHTRAG_URL}/health", timeout=3).json().get("status") == "healthy"
        except Exception:
            return False

    def stop_lightrag(self):
        self._kill(self.lightrag_proc)
        self.lightrag_proc = None

    def restart_lightrag(self):
        self.stop_lightrag()
        time.sleep(1)
        return self.start_lightrag()

    def stop_all(self):
        self.stop_lightrag()
        if not self.ollama_external:
            self._kill(self.ollama_proc)
            self.ollama_proc = None

    @staticmethod
    def _kill(proc):
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=10)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass


# ─────────────────────────────────────────────────────────
# Settings window
# ─────────────────────────────────────────────────────────

class SettingsDelegate(NSObject):
    app = objc.ivar()

    @objc.python_method
    def initWithApp_(self, app):
        self = objc.super(SettingsDelegate, self).init()
        if self is None:
            return None
        self.app = app
        self.window = None
        self.model_popup = None
        self.key_field = None
        self.login_checkbox = None
        return self

    @objc.python_method
    def show(self):
        if self.window is not None:
            self._refresh()
            self.window.makeKeyAndOrderFront_(None)
            NSApp.activateIgnoringOtherApps_(True)
            return

        W, H = 480, 470
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(200, 200, W, H), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered, False)
        self.window.setTitle_(f"{APP_NAME} {VERSION} — Settings")
        self.window.setReleasedWhenClosed_(False)
        cv = self.window.contentView()
        y = H - 40

        cv.addSubview_(make_label("DeepSeek API key", NSMakeRect(20, y, 300, 20), bold=True))
        y -= 30
        self.key_field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(20, y, 370, 24))
        self.key_field.setPlaceholderString_("sk-...")
        cv.addSubview_(self.key_field)
        cv.addSubview_(make_button("Get", NSMakeRect(395, y - 2, 65, 28), self, "openDeepSeekKeys:"))
        y -= 40

        cv.addSubview_(make_label("LLM model", NSMakeRect(20, y, 200, 20), bold=True))
        y -= 30
        self.model_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(20, y, 440, 26), False)
        for mid, desc in LLM_MODELS:
            self.model_popup.addItemWithTitle_(f"{mid}  —  {desc}")
        cv.addSubview_(self.model_popup)
        y -= 34
        cv.addSubview_(make_label(f"Embeddings: {EMBED_MODEL} via bundled Ollama (local, free)",
                                  NSMakeRect(20, y, 440, 16), size=11.0))
        y -= 30

        sep = NSBox.alloc().initWithFrame_(NSMakeRect(20, y, W - 40, 1))
        sep.setBoxType_(2)
        cv.addSubview_(sep)
        y -= 30

        self.login_checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(20, y, 300, 20))
        self.login_checkbox.setButtonType_(NSButtonTypeSwitch)
        self.login_checkbox.setTitle_("Start BrainAI at login")
        cv.addSubview_(self.login_checkbox)
        y -= 40

        cv.addSubview_(make_label("Connect agents (MCP)", NSMakeRect(20, y, 300, 20), bold=True))
        y -= 32
        bw = 105
        for i, (title, sel) in enumerate([
            ("Claude Desktop", "installClaudeDesktop:"),
            ("Claude Code", "installClaudeCode:"),
            ("Cursor", "installCursor:"),
            ("Codex", "installCodex:"),
        ]):
            cv.addSubview_(make_button(title, NSMakeRect(20 + i * (bw + 7), y, bw, 28), self, sel))
        y -= 34
        cv.addSubview_(make_label("Writes the lightrag server into each app's MCP config; restart the app afterwards.",
                                  NSMakeRect(20, y, 440, 16), size=11.0))
        y -= 34
        cv.addSubview_(make_button("📋 Copy config", NSMakeRect(20, y, 140, 28), self, "copyMcp:"))
        cv.addSubview_(make_button("📘 API Docs", NSMakeRect(170, y, 110, 28), self, "openDocs:"))
        cv.addSubview_(make_button("📂 Data", NSMakeRect(290, y, 80, 28), self, "openData:"))
        cv.addSubview_(make_button("📋 Log", NSMakeRect(380, y, 80, 28), self, "openLogs:"))

        apply_btn = make_button("Apply", NSMakeRect(W - 110, 15, 90, 32), self, "applySettings:")
        apply_btn.setKeyEquivalent_("\r")
        cv.addSubview_(apply_btn)

        self._refresh()
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    @objc.python_method
    def _refresh(self):
        env = read_env()
        self.key_field.setStringValue_(env.get("LLM_BINDING_API_KEY", ""))
        cur = env.get("LLM_MODEL", LLM_MODELS[0][0])
        for i, (mid, _) in enumerate(LLM_MODELS):
            if mid == cur:
                self.model_popup.selectItemAtIndex_(i)
        self.login_checkbox.setState_(1 if LAUNCH_AGENT.exists() else 0)

    # ── actions ──

    @objc.IBAction
    def openDeepSeekKeys_(self, sender):
        webbrowser.open("https://platform.deepseek.com/api_keys")

    @objc.IBAction
    def openDocs_(self, sender):
        webbrowser.open(f"{LIGHTRAG_URL}/docs")

    @objc.IBAction
    def openData_(self, sender):
        subprocess.run(["open", str(DATA)])

    @objc.IBAction
    def openLogs_(self, sender):
        subprocess.run(["open", "-a", "Console", str(LOG_DIR / "server.log")])

    @objc.IBAction
    def openEnv_(self, sender):
        subprocess.run(["open", "-t", str(ENV_FILE)])

    @objc.IBAction
    def testNotify_(self, sender):
        notify(APP_NAME, "Test notification", "Notifications are working!")

    @objc.python_method
    def _install(self, name):
        try:
            p = install_mcp(name)
            notify(APP_NAME, f"MCP added to {name}", f"{p.name} updated — restart {name}")
        except Exception as e:
            notify(APP_NAME, f"{name}: failed", str(e)[:120])

    @objc.IBAction
    def installClaudeDesktop_(self, sender):
        self._install("Claude Desktop")

    @objc.IBAction
    def installClaudeCode_(self, sender):
        self._install("Claude Code")

    @objc.IBAction
    def installCursor_(self, sender):
        self._install("Cursor")

    @objc.IBAction
    def installCodex_(self, sender):
        self._install("Codex")

    @objc.IBAction
    def copyMcp_(self, sender):
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(mcp_config(), NSPasteboardTypeString)
        notify(APP_NAME, "MCP config copied", "Paste into Cursor / Claude MCP settings")

    @objc.IBAction
    def applySettings_(self, sender):
        env = read_env()
        changed = False

        key = self.key_field.stringValue().strip()
        if key and key != env.get("LLM_BINDING_API_KEY", ""):
            write_env_value("LLM_BINDING_API_KEY", key)
            changed = True

        idx = self.model_popup.indexOfSelectedItem()
        model = LLM_MODELS[idx][0] if 0 <= idx < len(LLM_MODELS) else None
        if model and model != env.get("LLM_MODEL"):
            write_env_value("LLM_MODEL", model)
            changed = True

        want_login = bool(self.login_checkbox.state())
        if want_login != LAUNCH_AGENT.exists():
            self.app.set_login_item(want_login)

        self.window.close()
        if changed:
            notify(APP_NAME, "Settings saved", "Restarting server…")
            threading.Thread(target=self.app.restart_server, daemon=True).start()
        else:
            notify(APP_NAME, "Settings saved", "No server changes")


# ─────────────────────────────────────────────────────────
# Tray app
# ─────────────────────────────────────────────────────────

class BrainAIApp(rumps.App):
    def __init__(self):
        super().__init__(APP_NAME, quit_button=None)
        ensure_data_dirs()
        self.services = Services()

        def item(text):
            m = rumps.MenuItem(text, callback=None)
            m.set_callback(None)
            return m

        self.header = item(f"🧠 {APP_NAME} {VERSION}")
        self.status_item = item("  Starting…")
        self.api_item = item("  DeepSeek API: —")
        self.ollama_item = item("  Ollama (bge-m3): —")
        self.model_item = item("  Model: —")
        self.docs_item = item("  Documents: —")
        self.entities_item = item("  Entities: —")
        self.ram_item = item("  RAM: —")
        self.toggle_item = rumps.MenuItem("⏹ Stop Server", callback=self.toggle_server)
        self.webui_item = rumps.MenuItem("🌐 Open WebUI", callback=self.open_webui)
        self.settings_item = rumps.MenuItem("⚙️ Settings…", callback=self.open_settings)
        self.quit_item = rumps.MenuItem(f"Quit {APP_NAME}", callback=self.quit_app)

        self.menu = [
            self.header, rumps.separator,
            self.status_item, self.api_item, self.ollama_item, self.model_item,
            self.docs_item, self.entities_item, rumps.separator,
            self.ram_item, rumps.separator,
            self.toggle_item, self.webui_item, rumps.separator,
            self.settings_item, self.quit_item,
        ]
        self.header._menuitem.setAttributedTitle_(make_text(f"🧠 {APP_NAME} {VERSION}", COLOR_LABEL, NSFont.boldSystemFontOfSize_(13.0)))

        self._alive = False
        self._api_alive = False
        self._ollama_alive = False
        self._user_stopped = False
        self._last_docs = None
        self._last_entities = None
        self._last_status_counts = {}
        self._total_ram = psutil.virtual_memory().total / (1024 ** 3)
        self._settings = SettingsDelegate.alloc().initWithApp_(self)

        threading.Thread(target=self._bootstrap, daemon=True).start()

    # ── lifecycle ──

    def _bootstrap(self):
        self.status_item.title = "  ⏳ Starting Ollama…"
        if not self.services.start_ollama():
            self.status_item.title = "  🔴 Ollama failed to start"
            notify(APP_NAME, "Ollama failed", "See logs/ollama.log")
            return
        if not self.services.embed_model_present():
            notify(APP_NAME, "First run", f"Downloading {EMBED_MODEL} (~1.2 GB)…")

            def prog(p):
                self.status_item.title = f"  ⬇️ Downloading {EMBED_MODEL}: {p}%"
            if not self.services.pull_embed_model(prog):
                self.status_item.title = "  🔴 Model download failed"
                notify(APP_NAME, "Download failed", "Check internet connection and relaunch")
                return
            notify(APP_NAME, "Ready", f"{EMBED_MODEL} downloaded")

        if not read_env().get("LLM_BINDING_API_KEY"):
            self.status_item.title = "  ⚠️ DeepSeek API key required"
            notify(APP_NAME, "Setup required", "Enter your DeepSeek API key")
            rumps.Timer(lambda _: self._settings.show(), 1).start()
            return
        self.start_server()

    def start_server(self):
        self.status_item.title = "  ⏳ Starting LightRAG…"
        self._user_stopped = False
        ok = self.services.start_lightrag()
        if not ok:
            notify(APP_NAME, "LightRAG failed to start", "See Settings → Server log")
        self._check_status()

    def restart_server(self):
        self.status_item.title = "  ⏳ Restarting…"
        self.services.restart_lightrag()
        self._check_status()

    def set_login_item(self, enable):
        if enable:
            exe = str(APP_BUNDLE / "Contents" / "MacOS" / APP_NAME) if IN_BUNDLE else f"{PYTHON} {__file__}"
            args = [exe] if IN_BUNDLE else [PYTHON, __file__]
            plist = {"Label": "com.brainai.app", "ProgramArguments": args, "RunAtLoad": True}
            import plistlib
            LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
            with open(LAUNCH_AGENT, "wb") as f:
                plistlib.dump(plist, f)
        else:
            try:
                LAUNCH_AGENT.unlink()
            except FileNotFoundError:
                pass

    # ── timers ──

    @rumps.timer(CHECK_INTERVAL)
    def periodic_check(self, _):
        threading.Thread(target=self._check_status, daemon=True).start()

    @rumps.timer(DOC_POLL_INTERVAL)
    def doc_poll(self, _):
        if self._alive:
            threading.Thread(target=self._check_documents, daemon=True).start()

    # ── checks ──

    def _check_status(self):
        was = self._alive
        self._alive = self.services.lightrag_alive()
        if not was and self._alive:
            notify(APP_NAME, "LightRAG is ready", f"{LIGHTRAG_URL}")
        elif was and not self._alive and not self._user_stopped:
            notify(APP_NAME, "LightRAG stopped", "Server is no longer responding")

        self._ollama_alive = port_open(OLLAMA_URL, "/api/tags")

        key = read_env().get("LLM_BINDING_API_KEY", "")
        try:
            r = httpx.get(f"{DEEPSEEK_URL}/models", timeout=5, headers={"Authorization": f"Bearer {key}"})
            self._api_alive = r.status_code == 200
        except Exception:
            self._api_alive = False

        try:
            used = psutil.virtual_memory().used / (1024 ** 3)
            self.ram_item._menuitem.setAttributedTitle_(make_bar("RAM", used, self._total_ram))
        except Exception:
            pass
        self._update_ui()

    def _check_documents(self):
        try:
            d = httpx.post(f"{LIGHTRAG_URL}/documents/paginated", json={"page": 1, "page_size": 10}, timeout=5).json()
            total = d.get("pagination", {}).get("total_count", 0)
            counts = d.get("status_counts", {})
            if self._last_docs is not None:
                if total > self._last_docs:
                    n = total - self._last_docs
                    notify(APP_NAME, "New document", f"{n} added ({total} total)")
                for st, c in counts.items():
                    old = self._last_status_counts.get(st, 0)
                    if c > old:
                        sl = st.lower()
                        if sl in ("processed", "completed"):
                            notify(APP_NAME, "Document indexed", f"{c - old} completed")
                        elif sl == "failed":
                            notify(APP_NAME, "Processing failed", f"{c - old} failed")
            self._last_docs, self._last_status_counts = total, dict(counts)
            self.docs_item.title = f"  📄 Documents: {total}"
        except Exception as e:
            log(f"doc poll: {e}")
        try:
            pop = httpx.get(f"{LIGHTRAG_URL}/graph/label/popular", params={"limit": 1000}, timeout=5).json()
            n = len(pop) if isinstance(pop, list) else 0
            if self._last_entities is not None and n > self._last_entities:
                notify(APP_NAME, "Knowledge graph updated", f"+{n - self._last_entities} entities ({n} total)")
            self._last_entities = n
            self.entities_item.title = f"  🔗 Entities: {n}"
        except Exception:
            pass

    def _update_ui(self):
        if self.services.pull_progress is not None:
            self.title = "⬇️"
        elif self._alive:
            self.title = "🧠"
            self.status_item.title = "  🟢 LightRAG running"
            self.toggle_item.title = "⏹ Stop Server"
        else:
            self.title = "💤"
            if self._user_stopped:
                self.status_item.title = "  🔴 LightRAG stopped"
            elif not read_env().get("LLM_BINDING_API_KEY"):
                self.status_item.title = "  ⚠️ DeepSeek API key required"
            elif not self.status_item.title.strip().startswith(("⏳", "⬇️", "🔴")):
                self.status_item.title = "  🔴 LightRAG not running"
            self.toggle_item.title = "▶ Start Server"
        self.api_item.title = "  🟢 DeepSeek API ok" if self._api_alive else "  🔴 DeepSeek API unreachable / bad key"
        self.ollama_item.title = f"  🟢 Ollama ({EMBED_MODEL}) running" if self._ollama_alive else f"  🔴 Ollama ({EMBED_MODEL}) stopped"
        self.model_item.title = f"  🤖 Model: {read_env().get('LLM_MODEL', '—')}"

    # ── actions ──

    def toggle_server(self, _):
        if self._alive:
            self._user_stopped = True
            self.services.stop_lightrag()
            self._alive = False
            self._update_ui()
        else:
            threading.Thread(target=self.start_server, daemon=True).start()

    def open_webui(self, _):
        webbrowser.open(LIGHTRAG_URL)

    def open_settings(self, _):
        self._settings.show()

    def quit_app(self, _):
        self.services.stop_all()
        rumps.quit_application()


if __name__ == "__main__":
    ensure_data_dirs()
    acquire_single_instance()
    app = BrainAIApp()
    try:
        app.run()
    finally:
        app.services.stop_all()
