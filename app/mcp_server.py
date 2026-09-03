#!/usr/bin/env python3
"""
LightRAG MCP Server
===================
Exposes LightRAG functionality via the Model Context Protocol (MCP),
allowing Claude and other MCP clients to query the knowledge graph,
insert documents, and manage the graph through tool calls.

Requires a running LightRAG API server (default: http://localhost:9621).

Usage:
    python mcp_server.py [--lightrag-url http://localhost:9621]
"""

import argparse
import json
import sys
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_LIGHTRAG_URL = "http://localhost:9621"

parser = argparse.ArgumentParser(description="LightRAG MCP Server")
parser.add_argument(
    "--lightrag-url",
    default=DEFAULT_LIGHTRAG_URL,
    help=f"Base URL of the LightRAG API server (default: {DEFAULT_LIGHTRAG_URL})",
)
# Parse known args only so MCP transport flags don't cause errors
args, _ = parser.parse_known_args()

LIGHTRAG_URL = args.lightrag_url.rstrip("/")

mcp = MCPServer(
    "LightRAG",
    instructions=(
        "LightRAG MCP server provides tools to interact with a graph-based "
        "RAG (Retrieval-Augmented Generation) system. You can query the "
        "knowledge base, insert documents, and manage the knowledge graph."
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=LIGHTRAG_URL, timeout=300)


async def _post(path: str, payload: dict) -> dict:
    async with _client() as c:
        r = await c.post(path, json=payload)
        r.raise_for_status()
        return r.json()


async def _get(path: str, params: dict | None = None) -> Any:
    async with _client() as c:
        r = await c.get(path, params=params)
        r.raise_for_status()
        return r.json()


async def _delete(path: str, payload: dict | None = None) -> dict:
    async with _client() as c:
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
    """Query the LightRAG knowledge base.

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
    """Insert text content into the LightRAG knowledge base.

    The text will be chunked, entities and relations will be extracted,
    and the knowledge graph will be updated.

    Args:
        text: The text content to insert (at least a few sentences).
        description: Optional description of the document.
    """
    payload = {"text": text}
    if description:
        payload["description"] = description
    result = await _post("/documents/text", payload)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def list_documents(
    page: int = 1,
    page_size: int = 20,
    status: str = "",
) -> str:
    """List documents in the LightRAG knowledge base.

    Args:
        page: Page number (starting from 1).
        page_size: Number of documents per page.
        status: Filter by status — "" (all), "pending", "processing",
                "processed", "failed".
    """
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if status:
        params["status"] = status
    result = await _get("/documents/paginated", params)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def delete_document(doc_id: str) -> str:
    """Delete a document from the knowledge base by its ID.

    This removes the document and its associated entities/relations
    from the knowledge graph.

    Args:
        doc_id: The document ID to delete.
    """
    result = await _delete("/documents", {"ids": [doc_id]})
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
    """Search the knowledge graph for entities or relations.

    Args:
        label: Entity or relation type/label to search within.
        search_text: Text to search for (searches names and descriptions).
        max_items: Maximum number of results to return.
    """
    params: dict[str, Any] = {"label": label, "max_items": max_items}
    if search_text:
        params["search"] = search_text
    result = await _get("/graph/search", params)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_entity(entity_name: str) -> str:
    """Get detailed information about a specific entity in the knowledge graph.

    Returns the entity's properties, description, and connected relations.

    Args:
        entity_name: Name of the entity to look up.
    """
    result = await _get("/graph/entity/exist", params={"entity_name": entity_name})
    return json.dumps(result, ensure_ascii=False, indent=2)


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
        "entity_type": entity_type,
        "description": description,
        "source_id": source_id,
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

    Args:
        src_entity: Name of the source entity.
        tgt_entity: Name of the target entity.
        description: Description of the relationship.
        keywords: Comma-separated keywords for the relation.
        source_id: Source identifier for tracking.
    """
    payload = {
        "src_id": src_entity,
        "tgt_id": tgt_entity,
        "description": description,
        "keywords": keywords,
        "source_id": source_id,
    }
    result = await _post("/graph/relation/create", payload)
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
    """Check if the LightRAG server is running and get its configuration.

    Returns server status, LLM/embedding configuration, and storage info.
    """
    result = await _get("/health")
    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@mcp.resource("lightrag://status")
async def server_status() -> str:
    """Current LightRAG server status and configuration."""
    result = await _get("/health")
    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
