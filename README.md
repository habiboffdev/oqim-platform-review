# OQIM — Agentic Commerce Platform

OQIM is a **multi-tenant platform for AI sales & operations agents** that live inside
the channels where customers already are (Telegram, Instagram DM). A business connects
its channels and knowledge, and an agent sells, answers, captures leads, and syncs to
the CRM — autonomously, with a human-style chat voice — while every business-changing
action stays behind an owner approval, policy, and audit plane.

The same runtime also powers an **Owner Agent**: instead of clicking through a dashboard,
a business owner can *talk to their workspace* in plain language to configure the agent,
upload media, plan campaigns, and manage integrations.

> This repository is a **technical-review snapshot**. Production hosts, credentials,
> internal planning docs, and customer data have been removed; seed data is fictional.

---

## What it does (capability map)

Status legend: **✅ Ready** · **🟡 In testing** · **🔧 In progress** · **🗺️ Planned / runtime-supported**

| Capability | What it means | Status |
|---|---|---|
| **Seller agent** | Customer-facing chat seller: discovery → value → close → lead handoff, in the customer's language, with a human voice (never a plain-text bot wall). Fully test-driven. | ✅ Ready |
| **Multimodal agentic runtime** | One Generic Agent Runtime; behavior is a *profile* (interactive / action / setup), not a fork. Tool-calling loop with grounding, finalization, and durable turn records. | ✅ Ready |
| **Native + hot-path Hermes (two planes)** | A **seller plane** runs a thin, hot-path agent loop tuned for multi-tenant latency. An **owner plane** runs the same kernel in a richer, Hermes-native mode (skills, MCP, personal memory). | ✅ Seller · 🔧 Owner-native |
| **Multimodal contextual RAG** | Retrieval over business knowledge + typed catalog, with native perception of **voice, photo, and stickers** (not just transcripts) fed into the model. | ✅ Ready |
| **Universal source intake** | Pull business truth from many sources — Telegram channels, uploaded PDFs, manual entry, chat history — into a single source-fact pipeline. | ✅ Ready |
| **Universal extractor** | A model-driven extraction runtime turns raw source material into reviewable, typed facts (create/update proposals) regardless of source shape. | ✅ Ready |
| **Catalog** | Typed Commerce Catalog Core: products, offers, prices, stock, media — projected from *approved* source facts, with conflict/missing-field signals. | ✅ Ready |
| **Agnostic, agent-managed CRM** | Config-blob multi-CRM engine (amoCRM connector live). Pipelines/stages/fields are discovered and mapped automatically; deterministic forward-only lead sync with a human-touch latch. | ✅ amoCRM · 🗺️ more providers |
| **Skill learning from conversation history** | The platform can distill playbooks and business facts from real conversations and source material, surfacing them for owner approval. | 🟡 In testing |
| **Owner Agent (talk-to-manage)** | Manage the whole workspace by chatting with it: edit the agent's instructions, store/send media, plan campaigns, wire integrations — each mutation routed through an approval card. | 🟡 In testing · UI 🔧 in progress |
| **Personal / specialized agents** | The runtime already supports agent profiles beyond *seller* (assistants, internal/dev agents with broader native tools). Not yet validated end to end. | 🗺️ Runtime-supported |
| **Prompt management** | Prompts are owned, versioned, and budget-guarded in a managed layer (single reply contract + per-kind playbooks + rendered per-business instructions), with a prompt-size report. | ✅ Ready |
| **MCP & Skill hub** | Per-workspace skills (file-drop `SKILL.md`) and MCP tools for the owner plane, isolated per tenant. Discovery/indexing proven; full per-workspace MCP isolation in progress. | 🔧 In progress |
| **Model-agnostic provider gateway** | All generation flows through one seam (Gemini today). Per-provider adapters (tool-call forcing, temperature, caching) are the path to other models. | 🔧 In progress |
| **Sandbox / dev agents** | Non-production agents can be granted broader, native runtime tools for experimentation, separate from the locked-down production toolset. | 🗺️ Planned |

---

## Architecture at a glance

```
                 Customer (Telegram / Instagram DM)
                              │
                  ┌───────────▼────────────┐
                  │  Channel layer +        │   inbound events, burst-coalescing,
                  │  message intake         │   delivery routing, idempotency
                  └───────────┬────────────┘
                              │
                  ┌───────────▼────────────┐
                  │  Generic Agent Runtime  │   one kernel, many profiles
                  │  (seller / action /     │   tool-calling loop · grounding ·
                  │   setup profiles)       │   finalization · durable turn record
                  └─────┬───────────┬───────┘
       grounding ◄──────┘           └──────► tools
   ┌──────────────────┐        ┌──────────────────────────────┐
   │ Retrieval / RAG  │        │ Agent Control (approval plane)│
   │ Catalog · KB ·   │        │ every business-mutating action│
   │ multimodal       │        │ → card → execute → audit      │
   └──────────────────┘        └───────────────┬──────────────┘
                                                │
                              ┌─────────────────▼─────────────────┐
                              │ CRM sync · Catalog writes ·        │
                              │ Campaigns · Media vault · Triggers │
                              └────────────────────────────────────┘

   Owner (Telegram control bot) ──► Owner Agent profile ──► same approval plane
```

The defining idea: **Hermes (the agent kernel) owns reasoning, tools, and the channel
loop; OQIM owns tenant identity, knowledge truth, policy, approvals, delivery, and
audit.** Memory and the model never become the authority for price, stock, or business
rules — those are typed, approved, and owner-gated.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full technical map.

---

## Repository layout

```
backend/          FastAPI service — the platform core (see modules below)
  app/
    api/          HTTP routes (cookie auth, CSRF, workspace isolation)
    models/       SQLAlchemy ORM (PostgreSQL + pgvector)
    schemas/      Pydantic request/response contracts
    services/     cross-module services
    brain/        centralized LLM + embedding clients (provider seam)
    modules/      the domain — runtime, retrieval, catalog, CRM, control plane…
  migrations/     Alembic
  seed_data/      fictional sample company seed
  tests/          pytest against real PostgreSQL
frontend/         React 19 + Vite + TanStack — the owner/operator web app
gramjs-sidecar/   Node + GramJS (Telegram MTProto user client) for delivery & media
cli/              `oqim` — Typer CLI for ops & inspection
deploy/           nginx + systemd example configs (placeholders)
```

**Backend module groups** (`backend/app/modules/`):

- **Runtime** — `agent_runtime_v2` (Generic Agent Runtime, dispatcher, owner turn),
  `hermes_runtime`, `agent_runtime_context`, `agent_sessions`, `conversation_turns`,
  `conversation_core`, `agent_conversation_state`
- **Knowledge & RAG** — `retrieval_core`, `knowledge_mcp`, `business_brain`,
  `catalog_authority`, `commerce_catalog`
- **Intake & extraction** — `channel_runtime`, `channel_layer`, `message_intake`,
  `extraction_runtime`, `onboarding_learning`
- **Channels & talk** — `telegram_tools`, `telegram_control_bot`, `agent_talking`
- **Control plane** — `agent_control`, `action_runtime`, `tool_grants`, `tool_catalog`,
  `triggers`, `commercial_spine`, `agent_business_actions`
- **Owner plane** — `workspace_os`, `agent_documents` (the rendered per-business
  instructions), `agent_memory`
- **Growth & CRM** — `crm_connector`, `bi_promoter` (paced outreach)
- **Quality** — `evals`

---

## Tech stack

| Layer | Stack |
|---|---|
| Backend | Python · FastAPI 0.128 · SQLAlchemy 2.0 (async) + asyncpg · Alembic |
| Data | PostgreSQL + **pgvector** (embeddings) · Redis 7 (pub/sub, queues) |
| LLM | Google Gemini via `google-genai` (centralized client w/ fallback chains) |
| Frontend | React 19 · Vite 7 · TanStack Query/Router/Virtual · Tailwind 4 · zustand · framer-motion · shadcn/base-ui · Geist |
| Telegram | GramJS (MTProto user client) in a Node sidecar |
| Tooling | pytest (real Postgres) · Vitest · Ruff · pre-commit |

---

## Quick start (local)

> Requires Docker (Postgres + Redis), Python 3.12+, Node 20+.

```bash
# 1. Config
cp .env.example .env          # fill in TELEGRAM_API_ID/HASH, a Gemini key, secrets

# 2. Infra (Postgres + Redis)
docker compose up -d postgres redis     # `docker compose up -d` also builds the Telegram sidecar

# 3. Backend  →  http://localhost:8001
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python -m scripts.seed_pilot_company        # loads the fictional demo company
uvicorn app.main:app --reload --port 8001

# 4. Frontend  →  http://localhost:4200
cd frontend
npm install
npm run dev

# 5. Telegram sidecar (optional, for live delivery)
cd gramjs-sidecar
npm install && npm start
```

**Ports** (project standard): frontend `4200` · backend `8001` · sidecar `3100`.

---

## Testing

```bash
# Backend — real PostgreSQL on port 5434 (never SQLite), transaction-rollback isolation
cd backend && pytest -q

# Frontend — lint + Playwright end-to-end suites
cd frontend && npm run lint
cd frontend && npm run test:e2e
```

The seller path, runtime profiles, catalog, CRM sync, channel delivery, and prompt
budgets are all covered by focused suites. Tests mock the LLM and Redis but **never**
the database.

---

## Roadmap / TODO

**In testing → next to land**
- [ ] Owner Agent: full talk-to-manage acceptance (config edits, media, campaigns, integrations) under the approval plane
- [ ] New owner/operator **UI** for the agentic control plane (in progress)
- [ ] Conversation-history skill learning → owner-approved playbooks
- [ ] Per-workspace **MCP** tool isolation (skill index + discovery proven; full isolation pending)

**In progress**
- [ ] Model-agnostic provider gateway — per-provider adapters (tool-call forcing, caching) beyond Gemini
- [ ] MCP & Skill hub: owner-authored skills with a curator that prunes/merges idle skills
- [ ] Faithfulness gate: claim-vs-grounding enforcement at reply time (currently observable, not blocking)

**Planned / runtime-supported**
- [ ] Personal & specialized agent profiles (assistants, dev/sandbox agents) — validated end to end
- [ ] Sandbox mode with broader native tools for non-production agents
- [ ] Additional CRM providers on the config-blob engine
- [ ] More channels on the universal intake pipeline

---

## Notes for reviewers

- **Two planes, one kernel.** The seller loop is deliberately *thin* for hot-path
  latency; the owner loop is deliberately *rich*. They are profiles over the same
  runtime — start in `backend/app/modules/agent_runtime_v2/`.
- **Authority vs. memory.** Look at how `agent_control` + `commerce_catalog` keep the
  model from becoming the source of truth for price/stock/policy. This is the core
  trust property.
- **Honest seller behavior.** The agent is built to never silently drop a customer,
  never leak internal errors, and always emit a real chat turn — see the talk/
  finalization path.
- This snapshot omits production infrastructure and uses **fictional seed data**; any
  company/person/price you see in `seed_data` is invented for demos.
