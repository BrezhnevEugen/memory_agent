---
name: memory
description: "Persistent project memory using LightRAG MCP. Use this skill proactively whenever you work on a project with LightRAG MCP tools available (insert_text, query, create_entity, create_relation, etc.). This means: at the START of any non-trivial task, query memory for relevant context. After completing work that produced decisions, bug fixes, architecture choices, or learned project facts, save them to memory. Also trigger when the user says 'remember this', 'save to memory', 'what did we decide', 'recall', or references past work ('like we discussed', 'remember when we'). Even if the user doesn't mention memory explicitly, use it whenever LightRAG MCP tools are connected."
---

# BrainAI — Persistent Personal Knowledge Base via LightRAG

BrainAI is a personal knowledge base that spans across all sessions, agents (Cowork, Cursor, etc.), and life domains. Every session is ephemeral, but the knowledge graph is not. By saving important facts during work and querying them at the start of new tasks, you build a rich personal context that compounds over time.

## Knowledge Domains

All entries MUST include a domain prefix in the `description` field. This enables targeted retrieval across different life areas.

| Domain | Scope | Example description |
|--------|-------|-------------------|
| `work` | Work projects, employer tasks, team decisions | `work/architecture-auth-service` |
| `personal-project` | Own projects (BrainAI, side projects) | `personal-project/setup-lightrag` |
| `hobby-esp32` | ESP32, IoT, sensors, hardware | `hobby-esp32/bug-fix-i2c-sensor` |
| `hobby-automotive` | Car programming, ECU, OBD, tuning | `hobby-automotive/config-obd-protocol` |
| `personal` | Preferences, skills, personal notes | `personal/preference-code-style` |

When querying, include domain context for better results:
```
query("ESP32 I2C sensor configuration", mode="hybrid")
query("work authentication architecture decisions", mode="hybrid")
```

## Entity Types

Use consistent entity types across all domains:

| Type | Purpose | Examples |
|------|---------|---------|
| `Project` | Any project or product | BrainAI, WorkAppName, ESP32-WeatherStation |
| `Technology` | Languages, frameworks, tools, hardware | Ollama, React, ESP32-S3, STM32 |
| `Component` | Modules, services, files, circuits | lightrag_tray.py, MCP Server, I2C Bus |
| `Decision` | Architecture and design choices | "Use qwen2.5:32b for LLM" |
| `Bug` | Bugs with root cause and solution | "Starlette on_startup TypeError" |
| `Convention` | Rules and standards | "English in memory entries" |
| `Person` | People (colleagues, contacts) | Eugen, TeamLead |
| `Preference` | User preferences and habits | "Prefers TypeScript over JavaScript" |
| `Environment` | Hardware, OS, configs | "MacBook M3 Pro 36GB", "ESP32-S3 DevKit" |
| `Snippet` | Reusable code patterns | "ESP32 deep sleep wakeup pattern" |
| `Resource` | Useful links, docs, references | "ESP32 datasheet", "OBD-II PID list" |

## Relation Patterns

Standard relation types for consistent graph structure:

| Pattern | Example |
|---------|---------|
| Project → uses → Technology | BrainAI → uses → Ollama |
| Component → belongs_to → Project | lightrag_tray.py → belongs_to → BrainAI |
| Decision → affects → Component | "Use 32b model" → affects → LightRAG Server |
| Bug → found_in → Component | "on_startup error" → found_in → lightrag_server |
| Person → prefers → Preference | Eugen → prefers → "Russian communication" |
| Person → works_on → Project | Eugen → works_on → BrainAI |
| Technology → compatible_with → Technology | ESP32-S3 → compatible_with → Arduino IDE |
| Snippet → applies_to → Technology | "deep sleep pattern" → applies_to → ESP32 |
| Project → depends_on → Technology | BrainAI → depends_on → LightRAG |

## Description Format

The `description` field in `insert_text` follows the pattern: `domain/category-topic`

Categories:

| Category | Purpose |
|----------|---------|
| `architecture-*` | Design and structure decisions |
| `bug-fix-*` | Bugs: symptoms, root cause, solution |
| `config-*` | Configuration, settings, environment |
| `convention-*` | Rules, standards, patterns |
| `preference-*` | Personal preferences |
| `setup-*` | Installation, deployment |
| `api-*` | API contracts, endpoints, protocols |
| `meeting-*` | Meeting notes, discussion outcomes |
| `research-*` | Research findings, comparisons |
| `snippet-*` | Code patterns, recipes |
| `hardware-*` | Hardware specs, pinouts, wiring |
| `protocol-*` | Communication protocols, data formats |

Examples:
- `work/architecture-microservices-auth`
- `hobby-esp32/hardware-s3-pinout`
- `hobby-automotive/protocol-obd2-pids`
- `personal-project/config-brainai-ollama`
- `personal/preference-dev-tools`

## When to READ from memory

Query the knowledge base automatically (without being asked) in these situations:

**At the start of complex tasks.** Before diving into architecture changes, refactoring, debugging, or any multi-step work, run a quick query to check if there's relevant prior context.

```
query("context and decisions about <topic>", mode="hybrid")
```

**When the user references past work.** Phrases like "like we discussed", "remember when we", "what did we decide about", "continue from where we left off" — query memory before responding.

**When making choices that might conflict with past decisions.** Check whether a prior decision exists that constrains the current choice.

**When switching domains.** If the user mentions ESP32 after discussing work projects, query the ESP32 domain for relevant context.

Keep queries focused. "ESP32 I2C sensor configuration decisions" retrieves better results than "what do we know about hardware".

## When to WRITE to memory

Save information after you encounter or produce it — not preemptively, not speculatively. The goal is to capture facts valuable in a future session.

**What to save:**

- **Architecture decisions** — the choice AND reasoning. "Chose qwen2.5:32b over 7b because LightRAG recommends minimum 32B params for quality entity extraction, and M3 Pro 36GB has enough RAM" is much more useful than "using 32b model".
- **Bug fixes** — root cause, symptoms, solution. Include the domain so it's findable.
- **Configuration quirks** — non-obvious settings that took time to figure out.
- **Hardware discoveries** — pinouts, wiring that works, sensor calibration values, protocol specifics.
- **API contracts** — endpoint signatures, request/response shapes.
- **User preferences** — code style, tools, language, naming conventions.
- **Project conventions** — file structure, naming patterns, testing approaches.
- **Reusable code snippets** — patterns that work, especially for ESP32/automotive where boilerplate is common.
- **Key decisions from discussions** — meeting outcomes, strategic choices.

**What NOT to save:**

- Trivial fixes (typos, formatting)
- Temporary debugging info
- Information already well-documented in the codebase
- Speculative plans that weren't confirmed

## How to SAVE

### Structured facts → `create_entity` + `create_relation`

For discrete facts that connect to other things:

```
create_entity(
  entity_name="qwen2.5:32b",
  entity_type="Technology",
  description="LLM model for BrainAI. 32B params, ~20GB RAM, recommended minimum by LightRAG project."
)

create_relation(
  src_entity="BrainAI",
  tgt_entity="qwen2.5:32b",
  description="Uses as primary LLM for entity extraction and queries",
  keywords="llm, model, ollama, brainai"
)
```

### Rich context → `insert_text`

For longer explanations, multi-paragraph decisions, research findings:

```
insert_text(
  text="[2026-04-10] Decision: LLM Model Upgrade for BrainAI\n\nUpgraded from qwen2.5:7b to qwen2.5:32b. The LightRAG project recommends minimum 32B parameters for quality entity extraction. On MacBook M3 Pro with 36GB RAM, the 32b model uses ~20GB when loaded, leaving ~16GB for system. Model loads on demand and unloads after OLLAMA_KEEP_ALIVE timeout (set to 1m). Trade-off: slower responses (5-15s vs 2-3s) but significantly better knowledge graph quality.",
  description="personal-project/architecture-brainai-llm-model"
)
```

### Content conventions

- **Always include the date**: `[YYYY-MM-DD]` at the start of text
- **Always include domain prefix** in description: `domain/category-topic`
- **Write in English** for consistent retrieval quality regardless of conversation language
- **Be specific over general** — include numbers, versions, concrete details
- **Include the WHY** — reasoning behind decisions, not just the what

## Retrieval Strategy

Default to `mode="hybrid"` — combines entity-focused local search with broader global summaries.

Use `mode="local"` for specific entities or relationships: "what technologies does BrainAI use".

Use `mode="mix"` when reranker is enabled.

Use `only_need_context=true` when you just want raw facts without LightRAG's LLM generating an answer.

## Cross-Agent Consistency

This knowledge base is shared between all agents (Cowork, Cursor, and any future tools). To maintain consistency:

1. **Same taxonomy everywhere** — use the entity types and description format from this document
2. **No agent-specific entries** — save facts about the work, not about the agent doing it
3. **Domain prefixes are mandatory** — every `insert_text` description must start with a domain
4. **English only** — ensures retrieval works regardless of which agent or language the user is working in
5. **Query before writing** — check if similar information already exists to avoid duplicates

## Session Workflow

1. **Start of session**: Query memory for context relevant to the user's first message.
2. **During work**: Work normally. Save significant decisions, discoveries, and fixes as they happen.
3. **End of significant work**: Consider what from this session should be preserved. A few high-quality entries beat dozens of trivial ones.

Think: "Would a fresh session in any agent benefit from knowing this?" If yes, save it.
