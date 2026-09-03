---
name: memory
description: "Persistent project memory via the BrainAI / LightRAG MCP server (tools mcp__lightrag__*). Use proactively whenever these tools are connected: query memory at the START of any non-trivial task; after work that produced decisions, bug fixes, architecture choices, configs or learned facts, save them; when a saved fact changes, UPDATE the existing entity or replace the document instead of adding a duplicate. Also trigger on 'remember this', 'save to memory', 'what did we decide', 'recall', or references to past work."
---

# BrainAI memory — how to keep it current

The MCP server is bound to exactly one BrainAI project (the code folder you are in). Everything
you read or write stays inside that project. Sessions are ephemeral; the graph is not, so the
value of the graph depends on it being **current**, not merely large. Follow this cycle.

## 1. Read before you act

At the start of a non-trivial task run one or two queries with the task's key terms:

```
query("<component / feature / bug keywords>", mode="hybrid", top_k=20)
query_data("<the same>", mode="local", top_k=10)   # raw entities + relations, cheaper
```

Treat what comes back as the last known state, dated. If the code you see contradicts memory,
the code wins and memory must be corrected in step 3.

## 2. Save after you learn something durable

Save only what a future session cannot re-derive from the repository in a minute: decisions and
their reasons, root causes of bugs, non-obvious configuration, environment quirks, preferences,
external resources. Do not save code listings, git history or anything already in README/CLAUDE.md.

Two ways to write, pick by shape:

- **Document** (`insert_text`) for a narrative: a decision record, an incident, a how-to.
  One topic per document, 3–15 sentences, English, dated. Give it a stable `description`
  (used as the file path), e.g. `decision/auth-refresh-tokens`, `bug/i2c-timeout`,
  `config/deploy-vps`. LightRAG extracts entities and relations from it automatically.
- **Entity / relation** (`create_entity`, `create_relation`) for a fact that must be findable by
  exact name: a component, a person, a technology, a convention. Types: Project, Component,
  Technology, Decision, Bug, Convention, Person, Preference, Environment, Snippet, Resource.

Every description ends with the date: `… (2026-09-03)`.

## 3. Update, never duplicate

Before creating anything, check whether it already exists:

```
get_entity("<exact name>")                 # exists? current description + edges
search_graph("*", search_text="<name>")    # fuzzy name search
list_documents(page_size=50)               # find the document by its description/file_path
```

Then:

- Fact changed → `update_entity(name, description="<new state> (date)")`. Keep the old value
  inside the new description only when the history matters: `"was X until 2026-08, now Y"`.
- Relation changed → `update_relation(src, tgt, description=…)`.
- Component renamed → `update_entity(old, new_name=new)`; relations follow.
- A document is outdated → `insert_text` the new version with the **same** `description`,
  then `delete_document(old_id)` (get the id from `list_documents`). Do not leave both.
- Something no longer exists (removed feature, retired server) → `delete_entity`, or update the
  description to state that it was removed and when. Prefer an explicit "removed (date)" for
  things other sessions may still look up.

Never write a second entity that differs from an existing one only by case, spacing or wording.

## 4. Periodic audit (once a week, or when memory feels stale)

1. `list_documents(page_size=50)`: for each document older than the last big change in its
   area, open the topic in the code and confirm it is still true; replace or delete it.
2. `get_graph_labels()`: scan for near-duplicates (`Auth Service` / `auth-service`), merge by
   updating one and deleting the other.
3. `query("what is known about <area>")` for the areas you touched this week; correct anything
   that reads as stale.
4. Save one short document `audit/<date>` with what was corrected, so the next audit knows
   where it stopped.

## 5. Conventions

- English, third person, concrete nouns (component and file names as they appear in the repo).
- Prefix descriptions with an area when the project is large: `backend/`, `infra/`, `ui/`.
- One fact per entity; long context goes into a document that mentions the entity by name.
- Never store secrets, tokens or personal data of third parties.
- If the tools are not connected (no `mcp__lightrag__*`), say so once and continue without memory;
  do not guess what memory would have said.
