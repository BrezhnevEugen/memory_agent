#!/usr/bin/env python3
"""
BrainAI LightRAG server: one process, one fully isolated LightRAG instance per project.

Upstream LightRAG (1.5.x) binds a single workspace to the whole server process; the
``LIGHTRAG-WORKSPACE`` header is only honoured by ``/health``. This wrapper follows the
upstream multi-workspace design (docs/design/LR2-multi-workspace-phase1.md on the
``plan/multi-workspace-authz`` branch) without patching site-packages:

  * every request is routed by ``LIGHTRAG-WORKSPACE`` to a ``LightRAG`` instance that is
    permanently bound to that project;
  * each project has its own documents, vectors, graph, doc status, LLM cache and input
    directory under ``WORKING_DIR/<project>/`` and ``INPUT_DIR/<project>/``;
  * requests without the header (the bundled WebUI, the tray) use the *UI project* chosen
    in the BrainAI tray (``BRAINAI_UI_PROJECT``); an invalid header is rejected with 400.

Extra routes for the tray:
  GET  /brainai/projects            {"ui_project": ..., "projects": [{"id", "loaded"}]}
  POST /brainai/ui-project          {"project": "<id>"}

Usage: python brainai_server.py   (same environment/.env as lightrag-server)
"""

import asyncio
import contextvars
import json
import os
import re
import sys
from pathlib import Path

import lightrag.api.lightrag_server as srv
from lightrag import LightRAG
from lightrag.api.routers import document_routes
from lightrag.kg.shared_storage import set_default_workspace
from lightrag.utils import logger

PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
HEADER = b"lightrag-workspace"
DEFAULT_PROJECT = "default"

_current_project: contextvars.ContextVar = contextvars.ContextVar("brainai_project", default=None)
_pool = None            # ProjectPool, created when create_app() constructs LightRAG
_input_dir = None       # captured from create_app()'s DocumentManager(...) call


def valid_project(value) -> bool:
    return isinstance(value, str) and bool(PROJECT_RE.match(value))


def _initialized(rag: LightRAG) -> bool:
    return getattr(rag, "_storages_status", None) is not None and rag._storages_status.name == "INITIALIZED"


class ProjectPool:
    """Owns one LightRAG + DocumentManager pair per project, created lazily."""

    def __init__(self, rag_kwargs: dict, input_dir: str, ui_project: str):
        self._kwargs = dict(rag_kwargs)
        self._kwargs.pop("workspace", None)
        self._input_dir = input_dir
        self.working_dir = Path(self._kwargs["working_dir"])
        self.ui_project = ui_project
        self._rags: dict[str, LightRAG] = {}
        self._docs: dict[str, document_routes.DocumentManager] = {}
        self._role_builder = None
        self._lock = None  # asyncio.Lock, created inside the event loop
        # The UI project is constructed synchronously so create_app() can inspect it;
        # its storages are initialised from the lifespan through the proxy.
        self._construct(ui_project)

    def _construct(self, project: str) -> LightRAG:
        rag = LightRAG(**self._kwargs, workspace=project)
        if self._role_builder is not None:
            rag.register_role_llm_builder(self._role_builder)
        self._rags[project] = rag
        self._docs[project] = document_routes.DocumentManager(self._input_dir, workspace=project)
        logger.info(f"BrainAI: project '{project}' → {self.working_dir / project}")
        return rag

    async def ensure(self, project: str) -> LightRAG:
        """Return an initialised instance for the project, creating it on first use."""
        rag = self._rags.get(project)
        if rag is not None and _initialized(rag):
            return rag
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            rag = self._rags.get(project) or self._construct(project)
            if not _initialized(rag):
                await rag.initialize_storages()
                await rag.check_and_migrate_data()
            return rag

    async def finalize_all(self):
        for project, rag in list(self._rags.items()):
            try:
                await rag.finalize_storages()
            except Exception as e:  # keep shutting the others down
                logger.warning(f"BrainAI: finalize '{project}' failed: {e}")

    def register_role_llm_builder(self, builder):
        self._role_builder = builder
        for rag in self._rags.values():
            rag.register_role_llm_builder(builder)

    def current_project(self) -> str:
        return _current_project.get() or self.ui_project

    def rag(self) -> LightRAG:
        return self._rags[self.current_project()]

    def docs(self) -> document_routes.DocumentManager:
        return self._docs[self.current_project()]

    def is_loaded(self, project: str) -> bool:
        return project in self._rags

    def known_projects(self) -> list[str]:
        ids = set(self._rags)
        try:
            for p in self.working_dir.iterdir():
                if p.is_dir() and valid_project(p.name):
                    ids.add(p.name)
        except OSError:
            pass
        return sorted(ids)

    async def set_ui_project(self, project: str):
        await self.ensure(project)
        self.ui_project = project
        # /health and other header-less namespace lookups follow the UI project.
        set_default_workspace(project)


class _RagProxy:
    """Stands in for the single ``rag`` that create_app() wires into every router."""

    def __getattr__(self, name):
        return getattr(_pool.rag(), name)

    def __setattr__(self, name, value):
        setattr(_pool.rag(), name, value)

    async def initialize_storages(self):
        await _pool.ensure(_pool.ui_project)

    async def check_and_migrate_data(self):
        return None  # done per instance in ProjectPool.ensure()

    async def finalize_storages(self):
        await _pool.finalize_all()

    def register_role_llm_builder(self, builder):
        _pool.register_role_llm_builder(builder)


class _DocsProxy:
    """Stands in for the single ``doc_manager`` given to the document routes."""

    def __getattr__(self, name):
        return getattr(_pool.docs(), name)

    def __setattr__(self, name, value):
        setattr(_pool.docs(), name, value)


# ─────────────────────────────────────────────────────────
# Hooks into lightrag_server.create_app()
# ─────────────────────────────────────────────────────────

def _ui_project_from_env() -> str:
    value = os.environ.get("BRAINAI_UI_PROJECT", "").strip() or DEFAULT_PROJECT
    if not valid_project(value):
        print(f"BrainAI: invalid BRAINAI_UI_PROJECT={value!r}", file=sys.stderr)
        sys.exit(2)
    return value


def _patched_document_manager(input_dir, workspace=""):
    # create_app() builds the DocumentManager before LightRAG: remember the input dir.
    global _input_dir
    _input_dir = input_dir
    return _DocsProxy()


def _patched_lightrag(**kwargs):
    global _pool
    if _pool is not None:
        raise RuntimeError("BrainAI: create_app() constructed LightRAG twice")
    _pool = ProjectPool(kwargs, _input_dir or kwargs["working_dir"], _ui_project_from_env())
    return _RagProxy()


async def _send_json(send, status: int, payload: dict):
    body = json.dumps(payload).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class ProjectMiddleware:
    """Pure ASGI middleware: resolve the project once per request, before any router runs.

    The contextvar set here is visible to the route, to Starlette background tasks (they
    run in the same task after the response) and to every asyncio task created during
    the request (asyncio copies the context on task creation).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        raw = ""
        for k, v in scope.get("headers", []):
            if k == HEADER:
                raw = v.decode("latin-1").strip()
                break
        if raw:
            if not valid_project(raw):
                return await _send_json(send, 400, {"detail": f"invalid project id {raw!r}: use [a-z0-9_], max 64 chars"})
            try:
                await _pool.ensure(raw)
            except Exception as e:
                logger.error(f"BrainAI: cannot open project '{raw}': {e}")
                return await _send_json(send, 500, {"detail": f"cannot open project '{raw}': {e}"})
            token = _current_project.set(raw)
        else:
            token = _current_project.set(None)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_project.reset(token)


_orig_create_app = srv.create_app


def create_app(args):
    from fastapi import HTTPException, Request

    app = _orig_create_app(args)
    if _pool is None:
        raise RuntimeError("BrainAI: LightRAG was not constructed through the project pool")

    @app.get("/brainai/projects", include_in_schema=False)
    async def brainai_projects():
        return {
            "ui_project": _pool.ui_project,
            "projects": [{"id": p, "loaded": _pool.is_loaded(p)} for p in _pool.known_projects()],
        }

    @app.post("/brainai/ui-project", include_in_schema=False)
    async def brainai_set_ui_project(request: Request):
        body = await request.json()
        project = (body or {}).get("project", "")
        if not valid_project(project):
            raise HTTPException(status_code=400, detail="invalid project id")
        await _pool.set_ui_project(project)
        return {"ui_project": _pool.ui_project}

    app.add_middleware(ProjectMiddleware)
    return app


srv.LightRAG = _patched_lightrag
srv.DocumentManager = _patched_document_manager
srv.create_app = create_app


if __name__ == "__main__":
    srv.main()
