#!/usr/bin/env python3
"""
BrainAI — self-contained macOS menu bar app.

Manages three things without launchd or system-wide installs:
  * bundled Ollama (embeddings only, model bge-m3)
  * bundled LightRAG server (LLM = DeepSeek API)
  * MCP server config for agents (Cowork / Cursor / Claude Code)

Layout inside BrainAI.app/Contents/Resources:
  brainai.py, brainai_server.py, mcp_server.py, updater.py, update_ui.py, env.default
  python/            relocatable CPython with lightrag-hku[api], rumps, pyobjc…
  ollama/ollama      Ollama binary

User data (never inside the bundle):
  ~/Library/Application Support/BrainAI/{.env, rag_storage/<project>, inputs/<project>, logs, ollama/models}

Projects: every MCP client is bound to one project id ([a-z0-9_]); the server keeps each
project's documents, vectors and graph under rag_storage/<project>/. The tray picks the
project shown in the WebUI and counters (BRAINAI_UI_PROJECT in .env).

Runs from source too (dev mode): python3 brainai.py — uses sys.executable and `ollama` from PATH.
"""

import fcntl
import json
import os
import pathlib
import re
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
    NSPasteboardTypeString, NSButtonTypeSwitch, NSOpenPanel, NSModalResponseOK,
    NSScrollView, NSTableView, NSTableColumn, NSBezelBorder, NSAlert, NSAlertFirstButtonReturn, NSView,
    NSTabView, NSTabViewItem, NSTextView,
)
from Foundation import NSMutableAttributedString, NSDictionary, NSMakeRect
from PyObjCTools import AppHelper

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
SERVER_SCRIPT = str(RES / "brainai_server.py")
ENV_DEFAULT = RES / "env.default"

PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
DEFAULT_PROJECT = "default"
try:
    VERSION = (RES / "VERSION").read_text().strip()
except Exception:
    VERSION = "dev"

DATA = pathlib.Path.home() / "Library" / "Application Support" / APP_NAME
ENV_FILE = DATA / ".env"
LOG_DIR = DATA / "logs"
OLLAMA_MODELS = DATA / "ollama" / "models"
UPDATE_STATE_FILE = DATA / "update-state.json"
LAUNCH_AGENT = pathlib.Path.home() / "Library" / "LaunchAgents" / "com.brainai.app.plist"

RELEASES_API_URL = "https://api.github.com/repos/BrezhnevEugen/memory_agent/releases/latest"
RELEASES_URL = "https://github.com/BrezhnevEugen/memory_agent/releases"
UPDATE_CHECK_INTERVAL = 6 * 3600

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


# ── projects ──

def valid_project(value):
    return isinstance(value, str) and bool(PROJECT_RE.match(value))


def project_id_from_name(name):
    """Derive a project id from a folder name: lowercase, [a-z0-9_] only."""
    pid = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")[:64]
    if not pid:
        pid = "project"  # never fall back to the shared default project by accident
    return pid[:64]


def ui_project():
    value = read_env().get("BRAINAI_UI_PROJECT", "")
    return value if valid_project(value) else DEFAULT_PROJECT


PROJECTS_FILE = DATA / "projects.json"


def _disk_projects():
    ids = {ui_project()}
    try:
        for p in (DATA / "rag_storage").iterdir():
            if p.is_dir() and valid_project(p.name):
                ids.add(p.name)
    except OSError:
        pass
    return ids


def load_projects():
    """Project registry {id: {"name": display name, "folders": [linked folders]}}.

    Merged with the project directories on disk, so projects created by an MCP
    client with a new id show up (name = id) without being registered first.
    """
    try:
        data = json.loads(PROJECTS_FILE.read_text())
    except Exception:
        data = {}
    reg = {}
    for pid, v in (data.items() if isinstance(data, dict) else []):
        if valid_project(pid) and isinstance(v, dict):
            reg[pid] = {"name": str(v.get("name") or pid).strip() or pid,
                        "folders": [f for f in v.get("folders", []) if isinstance(f, str)]}
    for pid in _disk_projects():
        reg.setdefault(pid, {"name": pid, "folders": []})
    return dict(sorted(reg.items()))


def save_projects(reg):
    PROJECTS_FILE.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n")


def project_label(pid, reg=None):
    name = ((reg or load_projects()).get(pid) or {}).get("name", pid)
    return pid if name == pid else f"{name} ({pid})"


def list_projects():
    return list(load_projects())


def migrate_legacy_storage():
    """Move a pre-project (flat) rag_storage into rag_storage/default/. Returns True if moved."""
    root = DATA / "rag_storage"
    target = root / DEFAULT_PROJECT
    legacy = [p for p in root.iterdir() if p.is_file() and p.suffix in (".json", ".graphml")]
    if not legacy or target.exists():
        return False
    target.mkdir()
    for p in legacy:
        p.rename(target / p.name)
    log(f"migrated {len(legacy)} legacy storage files into project '{DEFAULT_PROJECT}'")
    return True


def load_update_state():
    try:
        data = json.loads(UPDATE_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_update_state(state):
    """Atomically persist non-sensitive updater state."""
    DATA.mkdir(parents=True, exist_ok=True)
    temporary = UPDATE_STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, UPDATE_STATE_FILE)


RUN_DIR = DATA / "run"
_instance_lock = None


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
    """Hold an OS lock for the lifetime of the app and reject duplicate launches."""
    global _instance_lock
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(RUN_DIR / "brainai.lock", "a+")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.seek(0)
        pid = lock.read().strip() or "unknown"
        lock.close()
        log(f"already running pid={pid}, exiting")
        notify(APP_NAME, "Already running", "BrainAI is in the menu bar")
        sys.exit(0)
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    _instance_lock = lock
    _write_pid("brainai", os.getpid())


def release_single_instance():
    """Remove only per-run state; preserve .env, tokens, models and knowledge data."""
    global _instance_lock
    pid_file = RUN_DIR / "brainai.pid"
    try:
        if int(pid_file.read_text()) == os.getpid():
            pid_file.unlink()
    except (FileNotFoundError, ValueError):
        pass
    if _instance_lock is not None:
        lock_path = RUN_DIR / "brainai.lock"
        try:
            # Unlink only the inode we have locked. A replacement lock created by
            # a new process must never be removed by this process while exiting.
            path_stat = lock_path.stat()
            fd_stat = os.fstat(_instance_lock.fileno())
            if (path_stat.st_dev, path_stat.st_ino) == (fd_stat.st_dev, fd_stat.st_ino):
                lock_path.unlink()
        except FileNotFoundError:
            pass
        finally:
            fcntl.flock(_instance_lock.fileno(), fcntl.LOCK_UN)
            _instance_lock.close()
            _instance_lock = None
    try:
        RUN_DIR.rmdir()
    except OSError:
        pass


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


def mcp_entry(project):
    if not valid_project(project):
        raise ValueError(f"invalid project id {project!r}: lowercase [a-z0-9_], max 64 chars")
    return {"command": PYTHON, "args": [MCP_SERVER, "--lightrag-url", LIGHTRAG_URL, "--project", project]}


def mcp_config(project):
    return json.dumps({"mcpServers": {"lightrag": mcp_entry(project)}}, indent=2)


def _tilde(path):
    """Show paths under the home directory as ~/… (no user name in the UI)."""
    path = str(path)
    home = str(pathlib.Path.home())
    return "~" + path[len(home):] if path.startswith(home) else path


def readme_text():
    """Quick start shown in Settings → Readme (paragraphs wrap in the text view)."""
    data = _tilde(DATA)
    return f"""BrainAI — persistent memory for AI agents, one isolated base per project.

FIRST 5 MINUTES
1. General: paste your DeepSeek API key → Apply. Embeddings run locally (bundled Ollama, bge-m3).
2. Projects → ＋ New…: give the project a name; the id is derived from it (“ESMO” → esmo).
3. Projects → Link folder…: choose the folder with your code. BrainAI writes the MCP config for Claude Code (.mcp.json), Cursor (.cursor/mcp.json) and Codex (.codex/config.toml) into that folder. Nothing to configure inside the agents.
4. Open Claude Code, Cursor or Codex in that folder. Claude Code asks once to trust the “lightrag” server from .mcp.json; Codex reads .codex/config.toml only for trusted folders.
5. Claude Desktop has no folders: “Claude Desktop →” binds its global config to the selected project.

HOW PROJECTS WORK
• Folder → project id → storage. Many folders may share one project; a folder always has exactly one.
• Each project is a separate LightRAG instance with its own files under {data}/rag_storage/<id>/ (documents, chunks, vectors, graph, LLM cache) and {data}/inputs/<id>/ (uploads).
• The MCP server refuses to start without --project, so nothing can fall into a shared base by accident.
• Tray → Open WebUI lists the projects; pick one to browse its graph in the browser.

WHAT AGENTS GET
MCP tools: query, query_data, insert_text, list_documents, delete_document, create_entity, create_relation, delete_entity, get_entity, search_graph, get_graph_labels, health_check. Tell the agent when to use them, e.g. with a “memory” skill: query at the start of a task, save decisions, bugs and configs afterwards.

MANUAL CONFIG (other agents, or by hand) — the same JSON as “Copy config”:
{{ "mcpServers": {{ "lightrag": {{
  "command": "{PYTHON}",
  "args": ["{MCP_SERVER}", "--lightrag-url", "{LIGHTRAG_URL}", "--project", "<id>"]
}}}}}}

FILES
Config: {_tilde(ENV_FILE)}
Registry: {_tilde(PROJECTS_FILE)}
Logs: {_tilde(LOG_DIR)}
Server: {LIGHTRAG_URL}  ·  API docs: {LIGHTRAG_URL}/docs
"""


HOME = pathlib.Path.home()
# name: (relative config path, kind, scope)
#   kind:  json → {"mcpServers": {...}}, toml → [mcp_servers.lightrag]
#   scope: global → path under $HOME; project → path under the chosen project folder
MCP_TARGETS = {
    "Claude Desktop": (pathlib.Path("Library/Application Support/Claude/claude_desktop_config.json"), "json", "global"),
    "Claude Code": (pathlib.Path(".mcp.json"), "json", "project"),
    "Cursor": (pathlib.Path(".cursor/mcp.json"), "json", "project"),
    "Codex": (pathlib.Path(".codex/config.toml"), "toml", "project"),
}


def install_mcp(name, project, folder=None):
    """Merge the lightrag MCP server (bound to `project`) into an agent's config. Returns path written.

    Project-scoped agents (Claude Code, Cursor, Codex) get the config inside `folder`, so each
    project folder carries its own project id; Claude Desktop has no project notion and gets a
    global config bound to `project`.
    """
    rel, kind, scope = MCP_TARGETS[name]
    if scope == "project":
        if folder is None:
            raise ValueError(f"{name} needs a project folder")
        path = pathlib.Path(folder) / rel
    else:
        path = HOME / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "json":
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
        except Exception:
            data = {}
        data.setdefault("mcpServers", {})["lightrag"] = mcp_entry(project)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    else:
        text = path.read_text() if path.exists() else ""
        # drop existing [mcp_servers.lightrag] section: header line up to the next table header
        text = re.sub(r"^\[mcp_servers\.lightrag\]\n(?:(?!^\[).*\n?)*", "", text, flags=re.M)
        text = text.rstrip() + "\n" if text.strip() else ""
        args = ", ".join(json.dumps(a) for a in mcp_entry(project)["args"])
        text += f'\n[mcp_servers.lightrag]\ncommand = {json.dumps(PYTHON)}\nargs = [{args}]\n'
        path.write_text(text)
    log(f"MCP installed for {name} (project '{project}'): {path}")
    return path


def uninstall_mcp(name, folder):
    """Remove the lightrag MCP server from a project-scoped agent config (file deleted if left empty)."""
    rel, kind, scope = MCP_TARGETS[name]
    if scope != "project":
        raise ValueError(f"{name} is not project-scoped")
    path = pathlib.Path(folder) / rel
    if not path.exists():
        return
    if kind == "json":
        try:
            data = json.loads(path.read_text())
        except Exception:
            return
        data.get("mcpServers", {}).pop("lightrag", None)
        if not data.get("mcpServers"):
            data.pop("mcpServers", None)
        if data:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        else:
            path.unlink()
    else:
        text = re.sub(r"^\[mcp_servers\.lightrag\]\n(?:(?!^\[).*\n?)*", "", path.read_text(), flags=re.M)
        if text.strip():
            path.write_text(text.rstrip() + "\n")
        else:
            path.unlink()
    log(f"MCP removed for {name}: {path}")


PROJECT_AGENTS = ("Claude Code", "Cursor", "Codex")


def link_folder(project, folder):
    """Bind a folder to a project: write all project-scoped agent configs and record the link."""
    folder = str(pathlib.Path(folder).resolve())
    reg = load_projects()
    for name in PROJECT_AGENTS:
        install_mcp(name, project, folder)
    entry = reg.setdefault(project, {"name": project, "folders": []})
    if folder not in entry["folders"]:
        entry["folders"].append(folder)
    save_projects(reg)


def unlink_folder(project, folder):
    reg = load_projects()
    for name in PROJECT_AGENTS:
        try:
            uninstall_mcp(name, folder)
        except Exception as e:
            log(f"unlink {name} in {folder}: {e}")
    entry = reg.get(project)
    if entry and folder in entry["folders"]:
        entry["folders"].remove(folder)
    save_projects(reg)


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
        self._lock = threading.RLock()
        self._stopping = threading.Event()

    @property
    def stopping(self):
        return self._stopping.is_set()

    @staticmethod
    def _remove_pid_file(name):
        try:
            (RUN_DIR / f"{name}.pid").unlink()
        except OSError:
            pass

    @staticmethod
    def _bundled_child_kind(proc):
        """Return the kind for a child launched from a BrainAI.app bundle."""
        try:
            cmdline = proc.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
        if not cmdline:
            return None
        executable = os.path.realpath(cmdline[0])
        marker = f"{os.sep}BrainAI.app{os.sep}Contents{os.sep}Resources{os.sep}"
        if marker not in executable:
            return None
        if executable.endswith(f"{os.sep}ollama{os.sep}ollama") and "serve" in cmdline[1:]:
            return "ollama"
        python_path = pathlib.Path(executable)
        is_bundled_python = (
            python_path.name.startswith("python3")
            and python_path.parent.name == "bin"
            and python_path.parent.parent.name == "python"
        )
        if is_bundled_python:
            command = " ".join(cmdline[1:])
            if ("from lightrag.api.lightrag_server import main; main()" in command
                    or command.endswith(f"{os.sep}brainai_server.py")):
                return "lightrag"
        return None

    @classmethod
    def _kill_bundled_children(cls, kinds=None):
        """Remove orphaned children from this or an older BrainAI.app instance."""
        for proc in psutil.process_iter(["pid"]):
            if proc.pid == os.getpid():
                continue
            kind = cls._bundled_child_kind(proc)
            if kind is None or (kinds is not None and kind not in kinds):
                continue
            log(f"stopping orphaned bundled {kind} pid={proc.pid}")
            cls._kill_pid(proc.pid)

    # ── Ollama ──

    def start_ollama(self):
        if self.stopping:
            return False
        _kill_stale("ollama")
        self._kill_bundled_children({"ollama"})
        if port_open(OLLAMA_URL, "/api/tags"):
            self.ollama_external = self.ollama_proc is None
            return True
        if not os.path.exists(OLLAMA_BIN) and not shutil.which(OLLAMA_BIN):
            log(f"ollama binary not found: {OLLAMA_BIN}")
            return False
        env = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{OLLAMA_PORT}",
                   OLLAMA_MODELS=str(OLLAMA_MODELS), OLLAMA_KEEP_ALIVE="10m")
        out = open(LOG_DIR / "ollama.log", "a")
        proc = subprocess.Popen([OLLAMA_BIN, "serve"], env=env, stdout=out, stderr=out,
                                start_new_session=True)
        with self._lock:
            if self.stopping:
                self._kill(proc)
                return False
            self.ollama_proc = proc
            self.ollama_external = False
            _write_pid("ollama", proc.pid)
        for _ in range(30):
            if self.stopping:
                return False
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
        if self.stopping:
            return False
        _kill_stale("lightrag")
        self._kill_bundled_children({"lightrag"})
        if self.lightrag_alive():
            log("lightrag already answering on port — reusing external instance")
            return True
        env = dict(os.environ)
        env.update(read_env())
        env.update(WORKING_DIR=str(DATA / "rag_storage"), INPUT_DIR=str(DATA / "inputs"),
                   HOST="127.0.0.1", PORT=str(LIGHTRAG_PORT),
                   EMBEDDING_BINDING_HOST=OLLAMA_URL, PYTHONUNBUFFERED="1",
                   BRAINAI_UI_PROJECT=ui_project())
        env.pop("WORKSPACE", None)  # projects are routed per request, never fixed per process
        out = open(LOG_DIR / "server.log", "a")
        proc = subprocess.Popen(
            [PYTHON, SERVER_SCRIPT],
            cwd=str(DATA), env=env, stdout=out, stderr=out, start_new_session=True)
        with self._lock:
            if self.stopping:
                self._kill(proc)
                return False
            self.lightrag_proc = proc
            _write_pid("lightrag", proc.pid)
        for _ in range(60):
            if self.stopping:
                return False
            if self.lightrag_alive():
                return True
            if proc.poll() is not None:
                return False
            time.sleep(1)
        return False

    def lightrag_alive(self):
        try:
            return httpx.get(f"{LIGHTRAG_URL}/health", timeout=3).json().get("status") == "healthy"
        except Exception:
            return False

    def stop_lightrag(self):
        with self._lock:
            proc = self.lightrag_proc
            self.lightrag_proc = None
        self._kill(proc)
        self._remove_pid_file("lightrag")

    def restart_lightrag(self):
        self.stop_lightrag()
        time.sleep(1)
        return self.start_lightrag()

    def stop_all(self):
        self._stopping.set()
        self.stop_lightrag()
        with self._lock:
            proc = self.ollama_proc
            self.ollama_proc = None
            ollama_external = self.ollama_external
        if not ollama_external:
            self._kill(proc)
        self._remove_pid_file("ollama")
        self._kill_bundled_children()

    @classmethod
    def _kill(cls, proc):
        if proc is None:
            return
        cls._kill_pid(proc.pid)

    @staticmethod
    def _kill_pid(pid):
        try:
            proc = psutil.Process(pid)
            if proc.status() == psutil.STATUS_ZOMBIE:
                proc.wait(timeout=0)
                return
            try:
                if os.getpgid(pid) == pid:
                    os.killpg(pid, signal.SIGTERM)
                else:
                    proc.terminate()
            except (ProcessLookupError, psutil.NoSuchProcess):
                return
            try:
                proc.wait(timeout=10)
                return
            except psutil.TimeoutExpired:
                if os.getpgid(pid) == pid:
                    os.killpg(pid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.wait(timeout=3)
        except (ProcessLookupError, psutil.NoSuchProcess, psutil.AccessDenied):
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
        self.project_popup = None
        self.name_field = None
        self.id_label = None
        self.folder_table = None
        self._project_ids = []
        self._folders = []
        return self

    @objc.python_method
    def show(self):
        if self.window is not None:
            self._refresh()
            self.window.makeKeyAndOrderFront_(None)
            NSApp.activateIgnoringOtherApps_(True)
            return

        W, H = 500, 420
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(200, 200, W, H), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered, False)
        self.window.setTitle_(f"{APP_NAME} {VERSION} — Settings")
        self.window.setReleasedWhenClosed_(False)
        cv = self.window.contentView()

        tabs = NSTabView.alloc().initWithFrame_(NSMakeRect(10, 52, W - 20, H - 62))
        cv.addSubview_(tabs)
        cr = tabs.contentRect()
        cw, ch = int(cr.size.width), int(cr.size.height)
        pages = {}
        for ident, title in (("general", "General"), ("projects", "Projects"), ("readme", "Readme")):
            item = NSTabViewItem.alloc().initWithIdentifier_(ident)
            item.setLabel_(title)
            page = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, cw, ch))
            item.setView_(page)
            tabs.addTabViewItem_(item)
            pages[ident] = page
        self.tabs = tabs
        L, R = 12, cw - 12          # left / right content edges inside a page
        width = R - L

        # ── General ──
        g = pages["general"]
        y = ch - 32
        g.addSubview_(make_label("DeepSeek API key", NSMakeRect(L, y, 300, 20), bold=True))
        y -= 30
        self.key_field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(L, y, width - 75, 24))
        self.key_field.setPlaceholderString_("sk-...")
        g.addSubview_(self.key_field)
        g.addSubview_(make_button("Get", NSMakeRect(R - 68, y - 2, 68, 28), self, "openDeepSeekKeys:"))
        y -= 42
        g.addSubview_(make_label("LLM model", NSMakeRect(L, y, 200, 20), bold=True))
        y -= 30
        self.model_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(L, y, width, 26), False)
        for mid, desc in LLM_MODELS:
            self.model_popup.addItemWithTitle_(f"{mid}  —  {desc}")
        g.addSubview_(self.model_popup)
        y -= 26
        g.addSubview_(make_label(f"Embeddings: {EMBED_MODEL} via bundled Ollama (local, free)",
                                 NSMakeRect(L, y, width, 16), size=11.0))
        y -= 28
        sep = NSBox.alloc().initWithFrame_(NSMakeRect(L, y, width, 1))
        sep.setBoxType_(2)
        g.addSubview_(sep)
        y -= 32
        self.login_checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(L, y, 300, 20))
        self.login_checkbox.setButtonType_(NSButtonTypeSwitch)
        self.login_checkbox.setTitle_("Start BrainAI at login")
        g.addSubview_(self.login_checkbox)
        g.addSubview_(make_button("📘 API Docs", NSMakeRect(L, 10, 110, 28), self, "openDocs:"))
        g.addSubview_(make_button("📂 Data folder", NSMakeRect(L + 116, 10, 120, 28), self, "openData:"))
        g.addSubview_(make_button("📋 Server log", NSMakeRect(L + 242, 10, 120, 28), self, "openLogs:"))

        # ── Projects ──
        p = pages["projects"]
        y = ch - 34
        self.project_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(L, y, width - 90, 26), False)
        self.project_popup.setTarget_(self)
        self.project_popup.setAction_("projectSelected:")
        p.addSubview_(self.project_popup)
        p.addSubview_(make_button("＋ New…", NSMakeRect(R - 82, y, 82, 28), self, "newProject:"))
        y -= 34
        p.addSubview_(make_label("Name", NSMakeRect(L, y + 2, 50, 20)))
        self.name_field = NSTextField.alloc().initWithFrame_(NSMakeRect(L + 50, y, 170, 24))
        p.addSubview_(self.name_field)
        self.id_label = make_label("id: —", NSMakeRect(L + 232, y + 2, width - 232, 20), size=12.0)
        p.addSubview_(self.id_label)
        y -= 28
        p.addSubview_(make_label("Linked folders — picked up automatically by Claude Code, Cursor and Codex:",
                                 NSMakeRect(L, y, width, 16), size=11.0))
        table_top, table_bottom = y - 6, 118
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(L, table_bottom, width, table_top - table_bottom))
        scroll.setBorderType_(NSBezelBorder)
        scroll.setHasVerticalScroller_(True)
        self.folder_table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, width, table_top - table_bottom))
        col = NSTableColumn.alloc().initWithIdentifier_("folder")
        col.setWidth_(width - 20)
        self.folder_table.addTableColumn_(col)
        self.folder_table.setHeaderView_(None)
        self.folder_table.setDataSource_(self)
        self.folder_table.setRowHeight_(18.0)
        scroll.setDocumentView_(self.folder_table)
        p.addSubview_(scroll)
        p.addSubview_(make_button("Link folder…", NSMakeRect(L, 84, 120, 28), self, "linkFolder:"))
        p.addSubview_(make_button("Unlink", NSMakeRect(L + 126, 84, 80, 28), self, "unlinkFolder:"))
        p.addSubview_(make_label("Additional configuration", NSMakeRect(L, 58, 300, 16), bold=True, size=11.0))
        p.addSubview_(make_label("Claude Desktop: no folders, global config · Copy config: JSON for other agents",
                                 NSMakeRect(L, 42, width, 16), size=11.0))
        p.addSubview_(make_button("Claude Desktop →", NSMakeRect(L, 8, 150, 28), self, "installClaudeDesktop:"))
        p.addSubview_(make_button("📋 Copy config", NSMakeRect(L + 156, 8, 130, 28), self, "copyMcp:"))

        # ── Readme ──
        r = pages["readme"]
        rscroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(L, 10, width, ch - 22))
        rscroll.setBorderType_(NSBezelBorder)
        rscroll.setHasVerticalScroller_(True)
        text = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, width - 16, ch - 22))
        text.setEditable_(False)
        text.setSelectable_(True)
        text.setFont_(NSFont.systemFontOfSize_(12.0))
        text.setTextContainerInset_((8.0, 8.0))
        text.setVerticallyResizable_(True)
        text.setHorizontallyResizable_(False)
        text.textContainer().setWidthTracksTextView_(True)
        text.setString_(readme_text())
        rscroll.setDocumentView_(text)
        r.addSubview_(rscroll)

        apply_btn = make_button("Apply", NSMakeRect(W - 110, 12, 90, 32), self, "applySettings:")
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
        self._refresh_projects()

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

    # ── projects ──

    @objc.python_method
    def _selected_project(self):
        i = self.project_popup.indexOfSelectedItem()
        return self._project_ids[i] if 0 <= i < len(self._project_ids) else None

    @objc.python_method
    def _refresh_projects(self, select=None):
        reg = load_projects()
        current = select or self._selected_project() or ui_project()
        self._project_ids = list(reg)
        self.project_popup.removeAllItems()
        for pid in self._project_ids:
            self.project_popup.addItemWithTitle_(project_label(pid, reg))
        if current in self._project_ids:
            self.project_popup.selectItemAtIndex_(self._project_ids.index(current))
        self._show_project(reg)

    @objc.python_method
    def _show_project(self, reg=None):
        reg = reg or load_projects()
        pid = self._selected_project()
        entry = reg.get(pid) or {"name": pid or "", "folders": []}
        self.name_field.setStringValue_(entry["name"])
        self.id_label.setStringValue_(f"id: {pid or '—'}  ·  rag_storage/{pid or '?'}/")
        self._folders = list(entry["folders"])
        self.folder_table.reloadData()

    @objc.python_method
    def _save_name(self):
        pid = self._selected_project()
        if not pid:
            return
        name = self.name_field.stringValue().strip() or pid
        reg = load_projects()
        if reg.get(pid, {}).get("name") != name:
            reg.setdefault(pid, {"name": pid, "folders": []})["name"] = name
            save_projects(reg)
            self.app.refresh_project_menu()

    def numberOfRowsInTableView_(self, table):
        return len(self._folders)

    def tableView_objectValueForTableColumn_row_(self, table, column, row):
        return self._folders[row]

    @objc.IBAction
    def projectSelected_(self, sender):
        self._show_project()

    @objc.IBAction
    def newProject_(self, sender):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("New project")
        alert.setInformativeText_("Name is what you see; id ([a-z0-9_]) names the storage folder and is used in MCP configs.")
        alert.addButtonWithTitle_("Create")
        alert.addButtonWithTitle_("Cancel")
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 300, 58))
        view.addSubview_(make_label("Name", NSMakeRect(0, 34, 50, 20)))
        name_f = NSTextField.alloc().initWithFrame_(NSMakeRect(50, 32, 250, 24))
        view.addSubview_(name_f)
        view.addSubview_(make_label("id", NSMakeRect(0, 4, 50, 20)))
        id_f = NSTextField.alloc().initWithFrame_(NSMakeRect(50, 2, 250, 24))
        id_f.setPlaceholderString_("empty = derived from name")
        view.addSubview_(id_f)
        alert.setAccessoryView_(view)
        alert.window().setInitialFirstResponder_(name_f)
        if alert.runModal() != NSAlertFirstButtonReturn:
            return
        name = name_f.stringValue().strip()
        pid = id_f.stringValue().strip() or project_id_from_name(name)
        if not name or not valid_project(pid):
            notify(APP_NAME, "New project: failed", f"invalid id {pid!r}: lowercase a-z, 0-9, _ (max 64)")
            return
        reg = load_projects()
        if pid in reg:
            notify(APP_NAME, "New project: failed", f"project '{pid}' already exists")
            return
        reg[pid] = {"name": name, "folders": []}
        save_projects(reg)
        self._refresh_projects(select=pid)
        self.app.refresh_project_menu()
        notify(APP_NAME, f"Project '{name}' created", f"id {pid} — link folders or bind Claude Desktop")

    @objc.IBAction
    def linkFolder_(self, sender):
        pid = self._selected_project()
        if not pid:
            return
        folder = self._choose_folder(f"Choose a folder to bind to project '{project_label(pid)}'")
        if folder is None:
            return
        try:
            link_folder(pid, folder)
        except Exception as e:
            notify(APP_NAME, "Link folder: failed", str(e)[:120])
            return
        self._refresh_projects(select=pid)
        notify(APP_NAME, f"Folder linked to '{pid}'",
               f"Restart Claude Code / Cursor / Codex in {pathlib.Path(folder).name} (Codex: project must be trusted)")

    @objc.IBAction
    def unlinkFolder_(self, sender):
        pid = self._selected_project()
        row = self.folder_table.selectedRow()
        if not pid or row < 0 or row >= len(self._folders):
            notify(APP_NAME, "Unlink", "Select a folder in the list first")
            return
        folder = self._folders[row]
        unlink_folder(pid, folder)
        self._refresh_projects(select=pid)
        notify(APP_NAME, f"Folder unlinked from '{pid}'", folder)

    @objc.python_method
    def _choose_folder(self, message):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setAllowsMultipleSelection_(False)
        panel.setMessage_(message)
        panel.setPrompt_("Use folder")
        if panel.runModal() != NSModalResponseOK:
            return None
        return panel.URLs()[0].path()

    @objc.IBAction
    def installClaudeDesktop_(self, sender):
        pid = self._selected_project()
        if not pid:
            return
        try:
            p = install_mcp("Claude Desktop", pid)
            notify(APP_NAME, f"Claude Desktop → '{project_label(pid)}'", f"restart Claude Desktop. {p.name}")
        except Exception as e:
            notify(APP_NAME, "Claude Desktop: failed", str(e)[:120])

    @objc.IBAction
    def copyMcp_(self, sender):
        pid = self._selected_project()
        if not pid:
            return
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(mcp_config(pid), NSPasteboardTypeString)
        notify(APP_NAME, f"MCP config copied (project '{pid}')", "Paste into the agent's project-scoped MCP config")

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
        self._save_name()

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
        # Keep the status item compact and visible from the first event-loop tick.
        # Falling back to APP_NAME makes a cold .app launch show a wide "BrainAI"
        # item until Ollama/LightRAG finish booting, which macOS may hide when the
        # menu bar is crowded. Direct launches looked fine because services were
        # already warm and _update_ui replaced it with the emoji almost at once.
        super().__init__(APP_NAME, title="🧠", quit_button=None)
        ensure_data_dirs()
        self.services = Services()
        self._shutdown_lock = threading.Lock()
        self._shutting_down = False
        self._pending_release = None
        self._update_in_progress = False
        self._update_progress_title = None
        rumps.events.before_quit.register(self.shutdown)

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
        self.webui_item = rumps.MenuItem("🌐 Open WebUI")  # submenu: one entry per project
        self.update_item = rumps.MenuItem("🔄 Check for updates…", callback=self.check_for_updates)
        self.settings_item = rumps.MenuItem("⚙️ Settings…", callback=self.open_settings)
        self.quit_item = rumps.MenuItem(f"Quit {APP_NAME}", callback=self.quit_app)

        self.menu = [
            self.header, rumps.separator,
            self.status_item, self.api_item, self.ollama_item, self.model_item,
            self.docs_item, self.entities_item, rumps.separator,
            self.ram_item, rumps.separator,
            self.toggle_item, self.webui_item, rumps.separator,
            self.update_item, self.settings_item, self.quit_item,
        ]
        self.header._menuitem.setAttributedTitle_(make_text(f"🧠 {APP_NAME} {VERSION}", COLOR_LABEL, NSFont.boldSystemFontOfSize_(13.0)))

        self._alive = False
        self._api_alive = False
        self._ollama_alive = False
        self._user_stopped = False
        self._last_docs = None
        self._last_entities = None
        self._last_status_counts = {}
        self._project_menu_ids = None
        self._total_ram = psutil.virtual_memory().total / (1024 ** 3)
        self._settings = SettingsDelegate.alloc().initWithApp_(self)
        self._refresh_project_menu(list_projects(), ui_project())

        threading.Thread(target=self._bootstrap, daemon=True).start()
        self._update_timer = threading.Timer(5.0, self._autocheck_updates_background)
        self._update_timer.daemon = True
        self._update_timer.start()

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
        try:
            if migrate_legacy_storage():
                notify(APP_NAME, "Storage migrated", f"Existing memory is now project '{DEFAULT_PROJECT}'")
        except Exception as e:
            log(f"legacy storage migration failed: {e}")
            notify(APP_NAME, "Storage migration failed", str(e)[:120])
            return
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

    def refresh_project_menu(self):
        """Re-read the registry (names may have changed) and rebuild the submenu."""
        self._project_menu_ids = None
        self._refresh_project_menu(list_projects(), ui_project())

    def _refresh_project_menu(self, ids, current):
        """Rebuild the Project submenu (only when the list or selection changed)."""
        key = (tuple(ids), current)
        if key == self._project_menu_ids:
            return
        if threading.current_thread() is not threading.main_thread():
            # NSMenu mutations must happen on the main thread (pollers run in threads).
            AppHelper.callAfter(self._refresh_project_menu, ids, current)
            return
        self._project_menu_ids = key
        reg = load_projects()
        if self.webui_item._menu is not None:  # rumps: clear() fails before a submenu exists
            self.webui_item.clear()
        self._project_menu_map = {}
        for pid in ids:
            label = project_label(pid, reg)
            self._project_menu_map[label] = pid
            mi = rumps.MenuItem(label, callback=self._open_webui_project)
            mi.state = 1 if pid == current else 0  # ✓ = project the WebUI currently shows
            self.webui_item.add(mi)

    def _open_webui_project(self, sender):
        """Point the WebUI (header-less client) at the chosen project, then open it."""
        pid = getattr(self, "_project_menu_map", {}).get(sender.title, sender.title)
        if not valid_project(pid):
            return
        if pid == ui_project():
            webbrowser.open(LIGHTRAG_URL)
            return

        def switch():
            try:
                r = httpx.post(f"{LIGHTRAG_URL}/brainai/ui-project", json={"project": pid}, timeout=30)
                r.raise_for_status()
            except Exception as e:
                notify(APP_NAME, "Project switch failed", str(e)[:120])
                return
            write_env_value("BRAINAI_UI_PROJECT", pid)
            self._last_docs = self._last_entities = None
            self._last_status_counts = {}
            self._refresh_project_menu(list_projects(), pid)
            self._check_documents()
            webbrowser.open(LIGHTRAG_URL)

        threading.Thread(target=switch, daemon=True).start()

    def _check_documents(self):
        try:
            info = httpx.get(f"{LIGHTRAG_URL}/brainai/projects", timeout=5).json()
            ids = sorted({p["id"] for p in info.get("projects", [])} | set(list_projects()))
            self._refresh_project_menu(ids, info.get("ui_project", ui_project()))
        except Exception as e:
            log(f"project poll: {e}")
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
            self.docs_item.title = f"  📄 Documents ({project_label(ui_project())}): {total}"
        except Exception as e:
            log(f"doc poll: {e}")
        try:
            labels = httpx.get(f"{LIGHTRAG_URL}/graph/label/list", timeout=10).json()
            n = len(labels) if isinstance(labels, list) else 0
            if self._last_entities is not None and n > self._last_entities:
                notify(APP_NAME, "Knowledge graph updated", f"+{n - self._last_entities} entities ({n} total)")
            self._last_entities = n
            self.entities_item.title = f"  🔗 Entities: {n}"
        except Exception:
            pass

    def _update_ui(self):
        if self._update_progress_title is not None:
            self.title = self._update_progress_title
        elif self.services.pull_progress is not None:
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

    def open_settings(self, _):
        self._settings.show()

    @staticmethod
    def _fetch_latest_release():
        """Fetch GitHub's latest published release with a bundled CA store."""
        import ssl
        import urllib.request

        import certifi

        request = urllib.request.Request(
            RELEASES_API_URL,
            headers={
                "User-Agent": f"BrainAI/{VERSION}",
                "Accept": "application/vnd.github+json",
            },
        )
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(request, timeout=10, context=context) as response:
            return json.load(response)

    @staticmethod
    def _is_newer_version(latest, current):
        def parts(value):
            core = str(value).strip().lstrip("v").split("-", 1)[0]
            try:
                parsed = tuple(int(part) for part in core.split("."))
            except ValueError:
                return ()
            return parsed + (0,) * (3 - len(parsed))

        latest_parts = parts(latest)
        current_parts = parts(current)
        return bool(latest_parts and current_parts and latest_parts > current_parts)

    def check_for_updates(self, _):
        """Manual update check invoked from the menu-bar menu."""
        if not IN_BUNDLE or VERSION == "dev":
            rumps.alert(
                title="Source build",
                message="Automatic installation is available only in the packaged BrainAI.app.",
            )
            return
        if self._update_in_progress:
            notify(APP_NAME, "Update in progress", "The new version is already downloading")
            return
        release = self._pending_release
        if not release:
            try:
                self.update_item.title = "🔄 Checking for updates…"
                release = self._fetch_latest_release()
            except Exception as exc:
                rumps.alert(title="Update check failed", message=str(exc))
                return
            finally:
                self.update_item.title = "🔄 Check for updates…"

        latest = str(release.get("tag_name") or "").lstrip("v").strip()
        if not latest:
            rumps.alert(title="Update check failed", message="The latest release has no version tag.")
            return
        if not self._is_newer_version(latest, VERSION):
            self._pending_release = None
            rumps.alert(
                title="BrainAI is up to date",
                message=f"BrainAI {VERSION} is the latest version.",
            )
            return

        self._pending_release = release
        from update_ui import INSTALL, OPEN_RELEASE, show_update_dialog

        choice = show_update_dialog(
            current_version=VERSION,
            latest_version=latest,
            release_body=release.get("body", "") or "",
            icon_path=RES / "BrainAI.icns",
        )
        if choice == INSTALL:
            self._update_in_progress = True
            threading.Thread(target=self._do_self_update, args=(release,), daemon=True).start()
        elif choice == OPEN_RELEASE:
            webbrowser.open(release.get("html_url") or RELEASES_URL)

    def _do_self_update(self, release):
        """Download and verify the update, then exit through normal cleanup."""
        from updater import UpdateError, install_update

        latest = str(release.get("tag_name") or "").lstrip("v") or "?"
        last_paint = [0.0]

        def on_progress(downloaded, total):
            if total <= 0:
                return
            now = time.monotonic()
            if now - last_paint[0] < 0.25 and downloaded < total:
                return
            last_paint[0] = now
            self._update_progress_title = f"↓ {int(downloaded * 100 / total)}%"
            self.title = self._update_progress_title

        try:
            self._update_progress_title = "↓ 0%"
            self.title = self._update_progress_title
            notify(APP_NAME, "Downloading update…", f"BrainAI {latest}")
            install_update(release, progress=on_progress)
            self._update_progress_title = "Installing…"
            self.title = self._update_progress_title
            notify(APP_NAME, "Update ready", "Restarting BrainAI…")
            time.sleep(1.0)
            os.kill(os.getpid(), signal.SIGTERM)
        except UpdateError as exc:
            log(f"update failed: {exc}")
            self._update_in_progress = False
            self._update_progress_title = None
            self._update_ui()
            notify(APP_NAME, "Update failed", str(exc)[:200])
        except Exception as exc:
            log(f"unexpected update failure: {exc}")
            self._update_in_progress = False
            self._update_progress_title = None
            self._update_ui()
            notify(APP_NAME, "Update failed", f"Unexpected error: {str(exc)[:180]}")

    def _autocheck_updates_background(self):
        """Silently check once per six hours and notify only when newer."""
        if not IN_BUNDLE or VERSION == "dev" or self._shutting_down:
            return
        state = load_update_state()
        try:
            last_check = float(state.get("last_update_check_at") or 0)
        except (TypeError, ValueError):
            last_check = 0
        if time.time() - last_check < UPDATE_CHECK_INTERVAL:
            return
        try:
            release = self._fetch_latest_release()
        except Exception as exc:
            log(f"automatic update check skipped: {exc}")
            return

        state["last_update_check_at"] = time.time()
        try:
            save_update_state(state)
        except Exception as exc:
            log(f"could not save update state: {exc}")

        latest = str(release.get("tag_name") or "").lstrip("v").strip()
        if latest and self._is_newer_version(latest, VERSION):
            self._pending_release = release
            notify(
                APP_NAME,
                f"Update {latest} available",
                "Open BrainAI → Check for updates… to install",
            )

    def quit_app(self, _):
        self.shutdown()
        rumps.quit_application()

    def shutdown(self, *_):
        """Stop processes and runtime files while preserving all persistent user data."""
        with self._shutdown_lock:
            if self._shutting_down:
                return
            self._shutting_down = True
        try:
            self.services.stop_all()
        finally:
            # NSApplication.terminate_ may exit the process without unwinding
            # app.run(), so runtime files must be removed before that call.
            release_single_instance()


def install_shutdown_signal_handlers(app):
    """Route shell, launchd and logout termination through graceful cleanup."""
    def handle(signum, _frame):
        log(f"received signal {signum}, shutting down")
        app.shutdown()
        rumps.quit_application()

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, handle)


if __name__ == "__main__":
    ensure_data_dirs()
    acquire_single_instance()
    app = None
    try:
        app = BrainAIApp()
        install_shutdown_signal_handlers(app)
        rumps.events.before_start.register(lambda: install_shutdown_signal_handlers(app))
        app.run()
    finally:
        if app is not None:
            app.shutdown()
        release_single_instance()
