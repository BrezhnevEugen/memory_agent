#!/usr/bin/env python3
"""
BrainAI Menu Bar App for macOS
================================
Shows LightRAG, DeepSeek API & Ollama (embeddings) status in the menu bar with controls
to start/stop services, open WebUI, and view logs.
Notifications via osascript for reliable macOS delivery.
Native colored progress bars via PyObjC NSAttributedString.
Settings window via PyObjC NSWindow.
"""

import subprocess
import threading
import time
import webbrowser

import rumps
import httpx
import psutil

from AppKit import (
    NSColor, NSFont, NSForegroundColorAttributeName, NSFontAttributeName,
    NSWindow, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSBackingStoreBuffered, NSTextField, NSButton,
    NSPopUpButton, NSBox, NSApp, NSObject, NSSecureTextField,
    NSBezelStyleRounded,
)
from Foundation import NSMutableAttributedString, NSDictionary, NSMakeRect

LIGHTRAG_URL = "http://localhost:9621"
DEEPSEEK_URL = "https://api.deepseek.com"
OLLAMA_URL = "http://localhost:11434"
LIGHTRAG_PLIST = "com.lightrag.server"
import pathlib
BASE_DIR = pathlib.Path(__file__).resolve().parent
ENV_FILE = str(BASE_DIR / ".env")
LOG_DIR = BASE_DIR / "logs"
CHECK_INTERVAL = 10  # seconds
DOC_POLL_INTERVAL = 10  # seconds

# Available DeepSeek models: (id, display name, description)
LLM_MODELS = [
    ("deepseek-v4-flash", "deepseek-v4-flash", "Fast & cheap — indexing"),
    ("deepseek-v4-pro", "deepseek-v4-pro", "Best quality — slower, pricier"),
]

# Bar config
BAR_WIDTH = 20
BAR_FILLED = "█"
BAR_EMPTY = "░"
MONO_FONT = NSFont.fontWithName_size_("Menlo", 11.0)
LABEL_FONT = NSFont.fontWithName_size_("Menlo", 11.0)

# Colors
COLOR_GREEN = NSColor.colorWithSRGBRed_green_blue_alpha_(0.30, 0.75, 0.35, 1.0)
COLOR_YELLOW = NSColor.colorWithSRGBRed_green_blue_alpha_(0.90, 0.75, 0.10, 1.0)
COLOR_ORANGE = NSColor.colorWithSRGBRed_green_blue_alpha_(0.95, 0.55, 0.10, 1.0)
COLOR_RED = NSColor.colorWithSRGBRed_green_blue_alpha_(0.90, 0.25, 0.20, 1.0)
COLOR_GRAY = NSColor.colorWithSRGBRed_green_blue_alpha_(0.78, 0.78, 0.78, 1.0)
COLOR_DIMGRAY = NSColor.colorWithSRGBRed_green_blue_alpha_(0.55, 0.55, 0.55, 1.0)
COLOR_LABEL = NSColor.labelColor()


def notify(title, subtitle, message):
    """Send macOS notification via osascript (works without permission issues)."""
    script = f'display notification "{message}" with title "{title}" subtitle "{subtitle}"'
    subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _home():
    import pathlib
    return str(pathlib.Path.home())


def _color_for_ratio(ratio):
    if ratio < 0.50:
        return COLOR_GREEN
    elif ratio < 0.70:
        return COLOR_YELLOW
    elif ratio < 0.85:
        return COLOR_ORANGE
    return COLOR_RED


def _make_attributed_bar(label, used, total, suffix=""):
    ratio = min(used / total, 1.0) if total > 0 else 0.0
    filled = round(ratio * BAR_WIDTH)
    empty = BAR_WIDTH - filled
    pct = int(ratio * 100)
    bar_color = _color_for_ratio(ratio)

    s = NSMutableAttributedString.alloc().init()

    lbl = label.ljust(8)
    attrs_label = NSDictionary.dictionaryWithObjects_forKeys_(
        [LABEL_FONT, COLOR_DIMGRAY], [NSFontAttributeName, NSForegroundColorAttributeName])
    s.appendAttributedString_(
        NSMutableAttributedString.alloc().initWithString_attributes_(lbl, attrs_label))

    if filled > 0:
        attrs_filled = NSDictionary.dictionaryWithObjects_forKeys_(
            [MONO_FONT, bar_color], [NSFontAttributeName, NSForegroundColorAttributeName])
        s.appendAttributedString_(
            NSMutableAttributedString.alloc().initWithString_attributes_(BAR_FILLED * filled, attrs_filled))

    if empty > 0:
        attrs_empty = NSDictionary.dictionaryWithObjects_forKeys_(
            [MONO_FONT, COLOR_GRAY], [NSFontAttributeName, NSForegroundColorAttributeName])
        s.appendAttributedString_(
            NSMutableAttributedString.alloc().initWithString_attributes_(BAR_EMPTY * empty, attrs_empty))

    value_text = f"  {used:.1f}/{total:.0f} GB  {pct}%" if total >= 1 else f"  {used:.1f} GB  {pct}%"
    if suffix:
        value_text += f"  {suffix}"
    attrs_value = NSDictionary.dictionaryWithObjects_forKeys_(
        [LABEL_FONT, COLOR_LABEL], [NSFontAttributeName, NSForegroundColorAttributeName])
    s.appendAttributedString_(
        NSMutableAttributedString.alloc().initWithString_attributes_(value_text, attrs_value))

    return s


def _make_attributed_text(text, color=None, font=None):
    color = color or COLOR_LABEL
    font = font or NSFont.menuFontOfSize_(13.0)
    attrs = NSDictionary.dictionaryWithObjects_forKeys_(
        [font, color], [NSFontAttributeName, NSForegroundColorAttributeName])
    return NSMutableAttributedString.alloc().initWithString_attributes_(text, attrs)


# ─────────────────────────────────────────────────────────
# Settings Window (NSObject subclass for proper ObjC actions)
# ─────────────────────────────────────────────────────────

def _make_label(text, frame, bold=False, size=13.0):
    label = NSTextField.alloc().initWithFrame_(frame)
    label.setStringValue_(text)
    label.setBezeled_(False)
    label.setDrawsBackground_(False)
    label.setEditable_(False)
    label.setSelectable_(False)
    label.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    return label


import objc

class SettingsDelegate(NSObject):
    """NSObject subclass to handle button actions in Settings window."""

    app = objc.ivar()

    @objc.python_method
    def initWithApp_(self, app):
        self = objc.super(SettingsDelegate, self).init()
        if self is None:
            return None
        self.app = app
        self.window = None
        self.model_popup = None
        self.deepseek_field = None
        return self

    @objc.python_method
    def _read_env_value(self, key, default=""):
        try:
            with open(ENV_FILE, "r") as f:
                for line in f:
                    if line.strip().startswith(f"{key}="):
                        return line.strip().split("=", 1)[1].strip()
        except Exception:
            pass
        return default

    @objc.python_method
    def _write_env_value(self, key, value):
        try:
            with open(ENV_FILE, "r") as f:
                lines = f.readlines()
            found = False
            with open(ENV_FILE, "w") as f:
                for line in lines:
                    if line.strip().startswith(f"{key}="):
                        f.write(f"{key}={value}\n")
                        found = True
                    else:
                        f.write(line)
                if not found:
                    f.write(f"{key}={value}\n")
        except Exception as e:
            print(f"[BrainAI] Failed to write {key}: {e}")

    @objc.python_method
    def _add_key_field(self, cv, label, env_key, y, get_key_action):
        cv.addSubview_(_make_label(label, NSMakeRect(20, y + 3, 80, 20)))
        field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(100, y, 290, 24))
        btn = NSButton.alloc().initWithFrame_(NSMakeRect(395, y - 2, 50, 28))
        btn.setTitle_("Get")
        btn.setBezelStyle_(NSBezelStyleRounded)
        btn.setToolTip_(f"Open {label} API keys page")
        btn.setTarget_(self)
        btn.setAction_(get_key_action)
        cv.addSubview_(btn)
        field.setStringValue_(self._read_env_value(env_key, ""))
        field.setPlaceholderString_("sk-...")
        cv.addSubview_(field)
        return field

    @objc.python_method
    def _save_key(self, field, env_key):
        """Write key to .env if changed. Returns True if changed."""
        new = field.stringValue().strip()
        if new and new != self._read_env_value(env_key, ""):
            self._write_env_value(env_key, new)
            return True
        return False

    @objc.python_method
    def show(self):
        if self.window is not None:
            self.window.makeKeyAndOrderFront_(None)
            NSApp.activateIgnoringOtherApps_(True)
            return

        W, H = 460, 390
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(200, 200, W, H), style, NSBackingStoreBuffered, False)
        self.window.setTitle_("BrainAI Settings")
        self.window.setReleasedWhenClosed_(False)

        cv = self.window.contentView()
        y = H - 40

        # ── LLM Model ──
        cv.addSubview_(_make_label("LLM Model", NSMakeRect(20, y, 200, 20), bold=True))
        y -= 25
        cv.addSubview_(_make_label(
            "Pro extracts better entities but costs more per token",
            NSMakeRect(20, y, 420, 16), size=11.0))
        y -= 30

        self.model_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(20, y, 400, 26), False)
        model_titles = []
        current_idx = 0
        for i, (mid, display, desc) in enumerate(LLM_MODELS):
            title = f"{mid}  —  {desc}"
            model_titles.append(title)
            self.model_popup.addItemWithTitle_(title)
            if mid == self.app._current_model:
                current_idx = i
        self.model_popup.selectItemAtIndex_(current_idx)
        cv.addSubview_(self.model_popup)
        y -= 40

        # ── API Keys ──
        cv.addSubview_(_make_label("API Keys", NSMakeRect(20, y, 200, 20), bold=True))
        y -= 28
        self.deepseek_field = self._add_key_field(cv, "DeepSeek", "LLM_BINDING_API_KEY", y, "openDeepSeekKeys:")
        y -= 32
        y -= 4

        # ── Separator ──
        sep1 = NSBox.alloc().initWithFrame_(NSMakeRect(20, y, W - 40, 1))
        sep1.setBoxType_(2)
        cv.addSubview_(sep1)
        y -= 25

        # ── Separator ──
        sep2 = NSBox.alloc().initWithFrame_(NSMakeRect(20, y, W - 40, 1))
        sep2.setBoxType_(2)
        cv.addSubview_(sep2)
        y -= 25

        # ── Quick Actions ──
        cv.addSubview_(_make_label("Quick Actions", NSMakeRect(20, y, 200, 20), bold=True))
        y -= 32

        for i, (title, sel) in enumerate([
            ("📋 Server Log", "openLogs:"),
            ("⚠️ Error Log", "openErrorLogs:"),
            ("🔔 Test Notify", "testNotify:"),
        ]):
            btn = NSButton.alloc().initWithFrame_(NSMakeRect(20 + i * 145, y, 135, 28))
            btn.setTitle_(title)
            btn.setBezelStyle_(NSBezelStyleRounded)
            btn.setTarget_(self)
            btn.setAction_(sel)
            cv.addSubview_(btn)

        y -= 36

        for i, (title, sel) in enumerate([
            ("📝 Edit .env", "openEnv:"),
            ("📘 API Docs", "openDocs:"),
        ]):
            btn = NSButton.alloc().initWithFrame_(NSMakeRect(20 + i * 145, y, 135, 28))
            btn.setTitle_(title)
            btn.setBezelStyle_(NSBezelStyleRounded)
            btn.setTarget_(self)
            btn.setAction_(sel)
            cv.addSubview_(btn)

        # ── Apply ──
        btn_apply = NSButton.alloc().initWithFrame_(NSMakeRect(W - 110, 15, 90, 32))
        btn_apply.setTitle_("Apply")
        btn_apply.setBezelStyle_(NSBezelStyleRounded)
        btn_apply.setKeyEquivalent_("\r")
        btn_apply.setTarget_(self)
        btn_apply.setAction_("applySettings:")
        cv.addSubview_(btn_apply)

        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    # ── ObjC action methods (must take sender argument) ──

    @objc.IBAction
    def openLogs_(self, sender):
        subprocess.run(["open", "-a", "Console",
                        str(LOG_DIR / "server.log")])

    @objc.IBAction
    def openErrorLogs_(self, sender):
        subprocess.run(["open", "-a", "Console",
                        str(LOG_DIR / "server-error.log")])

    @objc.IBAction
    def openDeepSeekKeys_(self, sender):
        webbrowser.open("https://platform.deepseek.com/api_keys")

    @objc.IBAction
    def testNotify_(self, sender):
        notify("BrainAI", "Test notification", "Notifications are working!")

    @objc.IBAction
    def openEnv_(self, sender):
        subprocess.run(["open", "-t", ENV_FILE])

    @objc.IBAction
    def openDocs_(self, sender):
        webbrowser.open(f"{LIGHTRAG_URL}/docs")

    @objc.IBAction
    def applySettings_(self, sender):
        need_restart = False

        # Get selected model
        idx = self.model_popup.indexOfSelectedItem()
        if 0 <= idx < len(LLM_MODELS):
            model_id = LLM_MODELS[idx][0]
            if model_id != self.app._current_model:
                old_model = self.app._current_model
                self.app._current_model = model_id
                self._write_env_value("LLM_MODEL", model_id)
                self.app._rebuild_model_submenu()
                need_restart = True
                notify("BrainAI", "Model changed", f"{old_model} → {model_id}")

        # Save API keys
        keys_changed = self._save_key(self.deepseek_field, "LLM_BINDING_API_KEY")
        if keys_changed:
            need_restart = True
            notify("BrainAI", "API keys saved", "Restarting server...")

        if need_restart:

            def restart():
                # Restart LightRAG server
                agents_dir = f"{_home()}/Library/LaunchAgents"
                subprocess.run(["launchctl", "unload", f"{agents_dir}/{LIGHTRAG_PLIST}.plist"],
                               capture_output=True)
                time.sleep(2)
                subprocess.run(["launchctl", "load", f"{agents_dir}/{LIGHTRAG_PLIST}.plist"],
                               capture_output=True)
                time.sleep(5)
                self.app._check_status()
                notify("BrainAI", "Server restarted", f"Model: {self.app._current_model}")

            threading.Thread(target=restart, daemon=True).start()
        else:
            notify("BrainAI", "Settings saved", "Configuration updated")

        self.window.close()


# ─────────────────────────────────────────────────────────
# Main Tray App
# ─────────────────────────────────────────────────────────

class LightRAGApp(rumps.App):
    def __init__(self):
        super().__init__("BrainAI", quit_button=None)

        # ── Status section ──
        self.header_title = rumps.MenuItem("🧠 BrainAI", callback=None)
        self.header_title.set_callback(None)

        self.status_item = rumps.MenuItem("  Checking...", callback=None)
        self.status_item.set_callback(None)

        self.api_status_item = rumps.MenuItem("  DeepSeek API: checking...", callback=None)
        self.api_status_item.set_callback(None)

        self.ollama_status_item = rumps.MenuItem("  Ollama (bge-m3): checking...", callback=None)
        self.ollama_status_item.set_callback(None)

        self.model_item = rumps.MenuItem("  Model: ...", callback=None)
        self.model_item.set_callback(None)

        self.docs_count_item = rumps.MenuItem("  Documents: —", callback=None)
        self.docs_count_item.set_callback(None)

        self.entities_count_item = rumps.MenuItem("  Entities: —", callback=None)
        self.entities_count_item.set_callback(None)

        # ── Memory section ──
        self.memory_bar_item = rumps.MenuItem("  RAM: —", callback=None)
        self.memory_bar_item.set_callback(None)

        self.swap_item = rumps.MenuItem("  Swap: —", callback=None)
        self.swap_item.set_callback(None)

        # ── Controls ──
        self.toggle_item = rumps.MenuItem("▶ Start Server", callback=self.toggle_server)
        self.ollama_toggle = rumps.MenuItem("▶ Start Ollama", callback=self.toggle_ollama)
        self.webui_item = rumps.MenuItem("🌐 Open WebUI", callback=self.open_webui)

        # ── Settings & Quit ──
        self.settings_item = rumps.MenuItem("⚙️ Settings...", callback=self.open_settings)
        self.quit_item = rumps.MenuItem("Quit BrainAI", callback=self.quit_app)

        self.menu = [
            self.header_title,
            rumps.separator,
            self.status_item,
            self.api_status_item,
            self.ollama_status_item,
            self.model_item,
            self.docs_count_item,
            self.entities_count_item,
            rumps.separator,
            self.memory_bar_item,
            self.swap_item,
            rumps.separator,
            self.toggle_item,
            self.ollama_toggle,
            self.webui_item,
            rumps.separator,
            self.settings_item,
            self.quit_item,
        ]

        self._lightrag_alive = False
        self._api_alive = False
        self._ollama_alive = False
        self._last_doc_count = None
        self._last_entity_count = None
        self._last_status_counts = {}
        self._current_model = self._read_current_model()
        self._total_ram_gb = psutil.virtual_memory().total / (1024 ** 3)
        self._settings_delegate = SettingsDelegate.alloc().initWithApp_(self)

        # Style header
        bold_font = NSFont.boldSystemFontOfSize_(13.0)
        self.header_title._menuitem.setAttributedTitle_(
            _make_attributed_text("🧠 BrainAI", COLOR_LABEL, bold_font))

        # Initial check
        self._check_status()

        if not self._api_key:
            notify("BrainAI", "Setup required", "Enter DeepSeek API key")
            threading.Timer(1, lambda: self._settings_delegate.show()).start()

    # ── Timers ──

    @rumps.timer(CHECK_INTERVAL)
    def periodic_check(self, _):
        threading.Thread(target=self._check_status, daemon=True).start()

    @rumps.timer(DOC_POLL_INTERVAL)
    def doc_poll(self, _):
        if self._lightrag_alive:
            threading.Thread(target=self._check_documents, daemon=True).start()

    # ── Status checks ──

    def _check_status(self):
        was_alive = self._lightrag_alive
        try:
            r = httpx.get(f"{LIGHTRAG_URL}/health", timeout=3)
            data = r.json()
            self._lightrag_alive = data.get("status") == "healthy"
        except Exception:
            self._lightrag_alive = False

        if not was_alive and self._lightrag_alive:
            notify("BrainAI", "LightRAG is ready", "Server started successfully")
        elif was_alive and not self._lightrag_alive:
            notify("BrainAI", "LightRAG stopped", "Server is no longer responding")

        try:
            r = httpx.get(f"{DEEPSEEK_URL}/models", timeout=5,
                          headers={"Authorization": f"Bearer {self._api_key}"})
            self._api_alive = r.status_code == 200
        except Exception:
            self._api_alive = False
        self._ollama_alive = False

        try:
            r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3)
            self._ollama_alive = r.status_code == 200
        except Exception:
            self._ollama_alive = False

        self._check_memory()
        self._update_ui()

    def _check_documents(self):
        try:
            r = httpx.post(f"{LIGHTRAG_URL}/documents/paginated",
                           json={"page": 1, "page_size": 10}, timeout=5)
            data = r.json()
            pagination = data.get("pagination", {})
            total = pagination.get("total_count", 0)
            status_counts = data.get("status_counts", {})

            if self._last_doc_count is None:
                self._last_doc_count = total
                self._last_status_counts = dict(status_counts)
            else:
                if total > self._last_doc_count:
                    new_count = total - self._last_doc_count
                    notify("BrainAI", "New document received",
                           f"{new_count} new document{'s' if new_count > 1 else ''} added ({total} total)")

                for status, count in status_counts.items():
                    old_count = self._last_status_counts.get(status, 0)
                    if count > old_count:
                        diff = count - old_count
                        sl = status.lower()
                        if sl in ("processed", "completed"):
                            notify("BrainAI", "Document completed",
                                   f"{diff} document{'s' if diff > 1 else ''} successfully indexed")
                        elif sl == "processing":
                            notify("BrainAI", "Processing started",
                                   f"{diff} document{'s' if diff > 1 else ''} being processed...")
                        elif sl == "failed":
                            notify("BrainAI", "Processing failed",
                                   f"{diff} document{'s' if diff > 1 else ''} failed to process!")

                self._last_doc_count = total
                self._last_status_counts = dict(status_counts)

            self.docs_count_item.title = f"  📄 Documents: {total}"
        except Exception as e:
            print(f"[BrainAI] doc poll error: {e}")

        try:
            r = httpx.get(f"{LIGHTRAG_URL}/graph/label/popular", params={"limit": 1000}, timeout=5)
            popular = r.json()
            entity_count = len(popular) if isinstance(popular, list) else 0

            if self._last_entity_count is None:
                self._last_entity_count = entity_count
            elif entity_count > self._last_entity_count:
                new_count = entity_count - self._last_entity_count
                notify("BrainAI", "Knowledge graph updated",
                       f"{new_count} new entit{'ies' if new_count > 1 else 'y'} extracted ({entity_count} total)")
                self._last_entity_count = entity_count

            self.entities_count_item.title = f"  🔗 Entities: {entity_count}"
        except Exception:
            pass

    def _check_memory(self):
        total = self._total_ram_gb

        try:
            mem = psutil.virtual_memory()
            used_gb = mem.used / (1024 ** 3)
            self.memory_bar_item._menuitem.setAttributedTitle_(
                _make_attributed_bar("RAM", used_gb, total))
        except Exception:
            self.memory_bar_item.title = "  RAM     error"

        try:
            swap = psutil.swap_memory()
            swap_used = swap.used / (1024 ** 3)
            swap_total = swap.total / (1024 ** 3)
            if swap_total > 0.1:
                self.swap_item._menuitem.setAttributedTitle_(
                    _make_attributed_bar("Swap", swap_used, swap_total))
            else:
                self.swap_item._menuitem.setAttributedTitle_(
                    _make_attributed_text("Swap     ✅ none", COLOR_GREEN, LABEL_FONT))
        except Exception:
            self.swap_item.title = "  Swap    error"

    # ── UI update ──

    def _update_ui(self):
        if self._lightrag_alive:
            self.title = "🧠"
            self.status_item.title = "  🟢 LightRAG running"
            self.toggle_item.title = "⏹ Stop Server"
            self.webui_item.set_callback(self.open_webui)
        else:
            self.title = "💤"
            self.status_item.title = "  🔴 LightRAG stopped"
            self.toggle_item.title = "▶ Start Server"
            self.webui_item.set_callback(None)

        if self._api_alive:
            self.api_status_item.title = "  🟢 DeepSeek API reachable"
        else:
            self.api_status_item.title = "  🔴 DeepSeek API unreachable"

        if self._ollama_alive:
            self.ollama_status_item.title = "  🟢 Ollama (bge-m3) running"
            self.ollama_toggle.title = "⏹ Stop Ollama"
        else:
            self.ollama_status_item.title = "  🔴 Ollama (bge-m3) stopped"
            self.ollama_toggle.title = "▶ Start Ollama"

        self.model_item.title = f"  🤖 Model: {self._current_model}"

    # ── Model ──

    @staticmethod
    def _read_env(key, default=""):
        try:
            with open(ENV_FILE, "r") as f:
                for line in f:
                    if line.strip().startswith(f"{key}="):
                        return line.strip().split("=", 1)[1].strip().strip("'\"")
        except Exception:
            pass
        return default

    def _read_current_model(self):
        return self._read_env("LLM_MODEL", "deepseek-v4-flash")

    @property
    def _api_key(self):
        return self._read_env("LLM_BINDING_API_KEY", "")

    def _rebuild_model_submenu(self):
        self.model_item.title = f"  🤖 Model: {self._current_model}"

    # ── Actions ──

    def toggle_server(self, _):
        if self._lightrag_alive:
            subprocess.run(["launchctl", "unload",
                            f"{_home()}/Library/LaunchAgents/{LIGHTRAG_PLIST}.plist"])
            self._lightrag_alive = False
        else:
            subprocess.run(["launchctl", "load",
                            f"{_home()}/Library/LaunchAgents/{LIGHTRAG_PLIST}.plist"])
            self._lightrag_alive = None
        threading.Timer(3, self._check_status).start()

    def toggle_ollama(self, _):
        if self._ollama_alive:
            subprocess.run(["brew", "services", "stop", "ollama"], capture_output=True)
        else:
            subprocess.run(["brew", "services", "start", "ollama"], capture_output=True)
        self._ollama_alive = None
        threading.Timer(5, self._check_status).start()

    def open_webui(self, _):
        webbrowser.open(LIGHTRAG_URL)

    def open_settings(self, _):
        self._settings_delegate.show()

    def quit_app(self, _):
        rumps.quit_application()


if __name__ == "__main__":
    LightRAGApp().run()
