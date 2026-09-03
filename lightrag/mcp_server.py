#!/usr/bin/env python3
"""
LightRAG MCP Server (BrainAI)
=============================
Exposes LightRAG functionality via the Model Context Protocol (MCP),
allowing Claude and other MCP clients to query the knowledge graph,
insert documents, and manage the graph through tool calls.

Every MCP process is bound to exactly one BrainAI *project*: all requests carry
the ``LIGHTRAG-WORKSPACE`` header, and the BrainAI server keeps each project's
documents, vectors and graph in its own directory. Without a project id the
server refuses to start (fail-closed) so two projects can never share a graph
by accident. Put the id into the project-scoped MCP config of each agent
(Claude Code ``.mcp.json``, Cursor ``.cursor/mcp.json``, Codex ``.codex/config.toml``)
or use BrainAI Settings → Connect agents.

Requires a running BrainAI/LightRAG server (default: http://localhost:9621).

Usage:
    python mcp_server.py --project <id> [--lightrag-url http://localhost:9621]
    BRAINAI_PROJECT=<id> python mcp_server.py
"""

import argparse
import json
import os
import re
import sys
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_LIGHTRAG_URL = "http://localhost:9621"
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
PROJECT_HEADER = "LIGHTRAG-WORKSPACE"

parser = argparse.ArgumentParser(description="LightRAG MCP Server (BrainAI)")
parser.add_argument(
    "--lightrag-url",
    default=DEFAULT_LIGHTRAG_URL,
    help=f"Base URL of the LightRAG API server (default: {DEFAULT_LIGHTRAG_URL})",
)
parser.add_argument(
    "--project",
    default=os.environ.get("BRAINAI_PROJECT", ""),
    help="BrainAI project id this MCP process is bound to ([a-z0-9_], max 64). "
         "Required; may also come from BRAINAI_PROJECT.",
)
# Parse known args only so MCP transport flags don't cause errors
args, _ = parser.parse_known_args()

LIGHTRAG_URL = args.lightrag_url.rstrip("/")
PROJECT = args.project.strip()

if not PROJECT:
    print(
        "BrainAI MCP: no project id. Start with `--project <id>` (or BRAINAI_PROJECT=<id>) "
        "from a project-scoped MCP config, or use BrainAI Settings → Connect agents.",
        file=sys.stderr,
    )
    sys.exit(2)
if not PROJECT_RE.match(PROJECT):
    print(
        f"BrainAI MCP: invalid project id {PROJECT!r}: lowercase [a-z0-9_], max 64 chars, "
        "must start with a letter or digit.",
        file=sys.stderr,
    )
    sys.exit(2)

mcp = MCPServer(
    "LightRAG",
    instructions=(
        f"LightRAG MCP server bound to BrainAI project '{PROJECT}'. Provides tools to "
        "interact with a graph-based RAG (Retrieval-Augmented Generation) memory: "
        "query the knowledge base, insert documents, and manage the knowledge graph. "
        "Everything you read or write here stays inside this project; other projects "
        "have their own isolated graphs."
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_isolation_verified = False


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=LIGHTRAG_URL, timeout=300, headers={PROJECT_HEADER: PROJECT}
    )


async def _verify_isolation(c: httpx.AsyncClient) -> None:
    """Fail closed if the server is not the BrainAI project-aware build.

    A plain lightrag-server ignores the workspace header on every data route and
    would silently mix all projects into one graph.
    """
    global _isolation_verified
    if _isolation_verified:
        return
    try:
        r = await c.get("/brainai/projects", timeout=10)
    except httpx.ConnectError as e:
        raise RuntimeError(
            f"BrainAI server is not running at {LIGHTRAG_URL} (start BrainAI.app)."
        ) from e
    if r.status_code != 200:
        raise RuntimeError(
            f"Server at {LIGHTRAG_URL} does not support per-project isolation "
            f"(/brainai/projects → {r.status_code}); refusing to use project '{PROJECT}'. "
            "Update BrainAI.app or start the server with brainai_server.py."
        )
    _isolation_verified = True


async def _post(path: str, payload: dict) -> dict:
    async with _client() as c:
        await _verify_isolation(c)
        r = await c.post(path, json=payload)
        r.raise_for_status()
        return r.json()


async def _get(path: str, params: dict | None = None) -> Any:
    async with _client() as c:
        await _verify_isolation(c)
        r = await c.get(path, params=params)
        r.raise_for_status()
        return r.json()


async def _delete(path: str, payload: dict | None = None) -> dict:
    async with _client() as c:
        await _verify_isolation(c)
        if payload:
            r = await c.request("DELETE", path, json=payload)
        else:
            r = await c.delete(path)
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Tools — Query
# ---------------------------------------------------------------------------

@mcp.tool()
async def query(
    question: str,
    mode: str = "hybrid",
    top_k: int = 40,
    only_need_context: bool = False,
    include_references: bool = True,
) -> str:
    """Query the LightRAG knowledge base of the current project.

    Searches the knowledge graph and text chunks to answer questions
    using retrieval-augmented generation.

    Args:
        question: The question to ask.
        mode: Search mode — "local" (entity-focused), "global" (broad summaries),
              "hybrid" (both), "naive" (vector search only), "mix" (graph+vector).
        top_k: Number of entities/relations to retrieve.
        only_need_context: If True, return only the retrieved context without LLM answer.
        include_references: If True, include source references.
    """
    payload = {
        "query": question,
        "mode": mode,
        "top_k": top_k,
        "only_need_context": only_need_context,
        "include_references": include_references,
    }
    result = await _post("/query", payload)
    response = result.get("response", "")
    refs = result.get("references")
    if refs:
        ref_lines = "\n\nReferences:"
        for ref in refs:
            ref_lines += f"\n- [{ref.get('reference_id', '?')}] {ref.get('file_path', '')}"
        response += ref_lines
    return response


@mcp.tool()
async def query_data(
    question: str,
    mode: str = "hybrid",
    top_k: int = 40,
) -> str:
    """Query LightRAG and return structured data (entities, relations, chunks).

    Unlike the regular query, this returns raw retrieval data in JSON format
    instead of a generated answer. Useful for inspecting what the knowledge
    graph contains for a given query.

    Args:
        question: The question to search for.
        mode: Search mode — "local", "global", "hybrid", "naive", "mix".
        top_k: Number of top items to retrieve.
    """
    payload = {"query": question, "mode": mode, "top_k": top_k}
    result = await _post("/query/data", payload)
    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Tools — Documents
# ---------------------------------------------------------------------------

@mcp.tool()
async def insert_text(
    text: str,
    description: str = "",
) -> str:
    """Insert text content into the current project's LightRAG knowledge base.

    The text will be chunked, entities and relations will be extracted,
    and the knowledge graph will be updated.

    Args:
        text: The text content to insert (at least a few sentences).
        description: Optional label/source of the document (shown as its file path).
    """
    payload = {"text": text}
    if description:
        payload["file_source"] = description
    result = await _post("/documents/text", payload)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def list_documents(
    page: int = 1,
    page_size: int = 20,
    status: str = "",
) -> str:
    """List documents in the current project's knowledge base.

    Args:
        page: Page number (starting from 1).
        page_size: Number of documents per page.
        status: Filter by status — "" (all), "pending", "processing",
                "processed", "failed".
    """
    payload: dict[str, Any] = {"page": page, "page_size": max(10, min(page_size, 200))}
    if status:
        payload["status_filter"] = status
    result = await _post("/documents/paginated", payload)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def delete_document(doc_id: str) -> str:
    """Delete a document from the knowledge base by its ID.

    This removes the document and its associated entities/relations
    from the knowledge graph.

    Args:
        doc_id: The document ID to delete.
    """
    result = await _delete("/documents", {"doc_ids": [doc_id]})
    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Tools — Knowledge Graph
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_graph_labels() -> str:
    """Get all entity and relation labels (types) in the knowledge graph.

    Returns the available node and edge types for exploring the graph.
    """
    result = await _get("/graph/label/list")
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def search_graph(
    label: str,
    search_text: str = "",
    max_items: int = 50,
) -> str:
    """Search the knowledge graph: entity names matching a text, or the subgraph around an entity.

    Args:
        label: Entity name to start from; returns its neighbourhood (nodes + edges).
               Use "*" together with search_text to search entity names only.
        search_text: Text to search for in entity names (fuzzy). When given, the
               matching names are returned in addition to the subgraph.
        max_items: Maximum number of nodes / matches to return.
    """
    result: dict[str, Any] = {}
    if search_text:
        result["matches"] = await _get("/graph/label/search", {"q": search_text, "limit": max_items})
    if label and label != "*":
        result["subgraph"] = await _get("/graphs", {"label": label, "max_depth": 2, "max_nodes": max_items})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_entity(entity_name: str) -> str:
    """Get detailed information about a specific entity in the knowledge graph.

    Returns the entity's properties, description, and connected relations.

    Args:
        entity_name: Name of the entity to look up.
    """
    exists = await _get("/graph/entity/exists", params={"name": entity_name})
    if not exists.get("exists"):
        return json.dumps({"exists": False, "entity_name": entity_name}, ensure_ascii=False)
    graph = await _get("/graphs", {"label": entity_name, "max_depth": 1, "max_nodes": 50})
    node = next((n for n in graph.get("nodes", []) if n.get("id") == entity_name), None)
    return json.dumps({"exists": True, "entity": node, "edges": graph.get("edges", [])},
                      ensure_ascii=False, indent=2)


@mcp.tool()
async def create_entity(
    entity_name: str,
    entity_type: str = "Concept",
    description: str = "",
    source_id: str = "mcp-manual",
) -> str:
    """Create a new entity in the knowledge graph.

    Args:
        entity_name: Name for the new entity.
        entity_type: Type/category (e.g. "Person", "Organization", "Concept", "Location").
        description: Description of the entity.
        source_id: Source identifier for tracking.
    """
    payload = {
        "entity_name": entity_name,
        "entity_data": {
            "entity_type": entity_type,
            "description": description,
            "source_id": source_id,
        },
    }
    result = await _post("/graph/entity/create", payload)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def create_relation(
    src_entity: str,
    tgt_entity: str,
    description: str,
    keywords: str = "",
    source_id: str = "mcp-manual",
) -> str:
    """Create a relation (edge) between two entities in the knowledge graph.

    Both entities must already exist (create them with create_entity first).

    Args:
        src_entity: Name of the source entity.
        tgt_entity: Name of the target entity.
        description: Description of the relationship.
        keywords: Comma-separated keywords for the relation.
        source_id: Source identifier for tracking.
    """
    payload = {
        "source_entity": src_entity,
        "target_entity": tgt_entity,
        "relation_data": {
            "description": description,
            "keywords": keywords,
            "source_id": source_id,
        },
    }
    result = await _post("/graph/relation/create", payload)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def update_entity(
    entity_name: str,
    description: str = "",
    entity_type: str = "",
    new_name: str = "",
) -> str:
    """Update an existing entity in place (keeps its relations). Use this instead of
    create_entity when a fact changed: overwrite the description, fix the type or rename.

    Args:
        entity_name: Current name of the entity.
        description: New description (replaces the old one; put the date of the change inside).
        entity_type: New type, if it should change.
        new_name: Rename the entity (relations follow the new name).
    """
    updated: dict[str, Any] = {}
    if description:
        updated["description"] = description
    if entity_type:
        updated["entity_type"] = entity_type
    if new_name:
        updated["entity_name"] = new_name
    if not updated:
        return json.dumps({"status": "noop", "message": "nothing to update"})
    payload = {"entity_name": entity_name, "updated_data": updated, "allow_rename": bool(new_name)}
    result = await _post("/graph/entity/edit", payload)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def update_relation(
    src_entity: str,
    tgt_entity: str,
    description: str = "",
    keywords: str = "",
) -> str:
    """Update an existing relation between two entities (description and/or keywords).

    Args:
        src_entity: Source entity name.
        tgt_entity: Target entity name.
        description: New description of the relationship.
        keywords: New comma-separated keywords.
    """
    updated: dict[str, Any] = {}
    if description:
        updated["description"] = description
    if keywords:
        updated["keywords"] = keywords
    if not updated:
        return json.dumps({"status": "noop", "message": "nothing to update"})
    payload = {"source_id": src_entity, "target_id": tgt_entity, "updated_data": updated}
    result = await _post("/graph/relation/edit", payload)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def delete_entity(entity_name: str) -> str:
    """Delete an entity and all its relations from the knowledge graph.

    Args:
        entity_name: Name of the entity to delete.
    """
    result = await _delete("/graph/entity/delete", {"entity_name": entity_name})
    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Tools — System
# ---------------------------------------------------------------------------

@mcp.tool()
async def health_check() -> str:
    """Check if the BrainAI/LightRAG server is running and get its configuration.

    Returns the bound project, server status, LLM/embedding configuration
    and storage info.
    """
    result = await _get("/health")
    return json.dumps({"project": PROJECT, "server": result}, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@mcp.resource("lightrag://status")
async def server_status() -> str:
    """Current LightRAG server status and configuration for this project."""
    result = await _get("/health")
    return json.dumps({"project": PROJECT, "server": result}, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
