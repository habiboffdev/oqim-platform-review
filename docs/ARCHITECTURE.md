# Architecture

This document is the technical map of the OQIM platform for a reviewer. It explains the
shape of the system, the request/turn lifecycle, the major subsystems, and the design
properties that matter most.

---

## 1. The core idea: one kernel, two planes, a hard authority boundary

OQIM is built on a forked agent kernel (the **Hermes** agent loop) wrapped by an
OQIM-owned platform. The split of responsibilities is deliberate and load-bearing:

- **Hermes (the kernel) owns** the agent loop: reasoning, tool-calling, the channel
  loop where granted, and — on the owner plane — native skills, MCP, and personal memory.
- **OQIM owns** tenant/workspace identity, knowledge *truth*, the production toolset,
  policy, approvals, message delivery, audit, and the UI.

On top of that one kernel run **two planes**:

| | Seller plane | Owner plane |
|---|---|---|
| Audience | end customers | the business owner |
| Channel | Telegram / Instagram DM | Telegram control bot |
| Tuning | thin & hot-path (multi-tenant latency) | rich & native (skills, MCP, memory) |
| Tools | locked, OQIM-wrapped production tools | broader, approval-gated config tools |
| Profile | `interactive` (and `action`) | `setup` |

**The authority boundary is the most important invariant:** the model and its memory
are *never* the source of truth for price, stock, policy, or business rules. Those facts
are typed, owner-approved, and stored in OQIM. The agent reads them; it cannot invent or
silently mutate them.

---

## 2. Turn lifecycle (seller plane)

A customer message becomes an agent reply through a well-defined pipeline:

```
inbound message
   → channel layer            normalize, dedupe, burst-coalesce a window of messages
   → message intake           persist, attach to a conversation
   → dispatcher               build a turn for the Generic Agent Runtime
   → Generic Agent Runtime    profile-compiled prompt + tool schemas
        ├─ grounding          retrieve knowledge / catalog as needed
        ├─ tool-calling loop  the model calls talk + business tools
        └─ finalization       guard, normalize output, record the turn
   → delivery routing         channel-correct send (Telegram sidecar / Graph API)
   → durable turn record      full context + tool trace persisted
```

Key properties:

- **Burst coalescing** — when a customer fires several messages quickly, the dispatched
  turn carries the *whole window*, not just the last message.
- **Forced talk output** — whenever talk tools are granted, the runtime forces the model
  to emit a real chat turn; a silent plain-text wall is structurally impossible.
- **Never-drop / never-leak** — engine fallbacks are classified into safe customer
  acknowledgements; internal errors never reach the customer.
- **Durable record** — every turn stores its compiled context, grounding, and tool-call
  trace, so a turn can be inspected or replayed.

---

## 3. The Generic Agent Runtime & profiles

There is **one** runtime. Differentiation is by *profile*, compiled per agent:

- **Profile kind:** `agent`
- **Execution modes:** `interactive` (live chat), `action` (background business
  actions), `setup` (the owner plane).

A `RuntimeProfileCompiler` produces, for a given agent + mode, the prompt material, the
scoped tool schemas, and the runtime knobs. Behavior differentiation lives in the
**managed prompt layer**, not in code branches:

- a single generic reply contract for every agent,
- kind-scoped playbook assets (e.g. a seller playbook for seller kinds),
- and the **rendered per-business instructions** (assembled from approved document
  sections — the business's own facts and rules).

This is why adding a new agent *kind* is mostly a prompt/profile concern, not a new code
path.

---

## 4. Knowledge, retrieval & the catalog (RAG)

Retrieval is multi-layered and multimodal:

- **Retrieval Core** — vector search (pgvector) over indexed source units, with a
  dirty-flag reconciler that auto-embeds new/changed facts.
- **Knowledge MCP** — the agent-facing search surface (knowledge + catalog + source
  explanation tools).
- **Commerce Catalog Core** — *typed* products, offers, prices, stock, and media,
  projected only from **approved** source facts, with explicit conflict and
  missing-field signals.
- **Catalog Authority** — resolves price/stock conflicts and demotes stale facts so old
  prices stop surfacing.
- **Multimodal perception** — voice, photo, and stickers are fed to the model as native
  media parts (not just transcripts), and media OCR / visual summaries are searchable.

The boundary again: chat history and retrieval can *answer* historical questions, but a
fact only becomes catalog/price/policy authority once it is extracted, approved, and
typed.

---

## 5. Source intake & the universal extractor

Business truth enters through one funnel regardless of shape:

- **Universal source intake** — Telegram channels, uploaded PDFs, manual entries, and
  conversation history all become canonical *source facts*.
- **Extraction runtime** — a model-driven extractor turns raw source material into
  reviewable, typed **create/update proposals**, independent of the source format.
- **Onboarding / source learning** — distills playbooks and business facts; edits to a
  watched source (e.g. an edited channel post) produce versioned update proposals.

Everything reviewable flows to the owner for approval before it becomes authority.

---

## 6. The control plane (approval · policy · audit)

`agent_control` is the universal gate for anything that changes business state — replies
that need review, tool calls, catalog/rule changes, automations, and integration writes.

The pattern is uniform and reused:

```
agent wants to mutate state
   → tool marked `proposal_required`
   → CommercialActionProposal created
   → owner approval card (Telegram)
   → action_runtime executor runs the change
   → audit record
```

Marking a tool `proposal_required` is enough to route it through the existing card →
execute → audit flow. The same machinery serves seller-side handoffs and owner-side
config edits.

---

## 7. The owner plane (talk-to-manage)

The owner interacts through a Telegram **control bot**. Free-text owner messages are
routed to an **Owner Agent** running the `setup` profile of the same runtime:

```
owner free text
   → telegram_control_bot (bound-owner branch)
   → owner-turn dispatch (no customer/conversation required)
   → setup-profile tools: edit instructions · store/list media · plan campaigns · wire integrations
   → each business-mutating tool → approval card → executor → audit
```

This is how an owner configures the workspace by *talking to it*. The owner plane is
also where the Hermes-native capabilities live: per-workspace **skills** (file-drop
`SKILL.md`), **MCP** tools, and **personal memory** — isolated per tenant via a
per-workspace runtime home, so concurrent workspaces don't bleed into each other.

The **media vault** is a representative owner feature: an asset is uploaded once to a
private per-workspace store; the platform persists a pointer and the seller agent can
later re-send it by handle with zero re-upload.

---

## 8. CRM: agnostic & agent-managed

The CRM layer is a **config-blob engine**, not a hard-coded integration:

- A provider connector (amoCRM today) handles OAuth and the provider API.
- **Schema discovery** auto-reads the connected account's pipelines, stages, and custom
  fields and maps them — re-syncable without reconnecting.
- A supervised sync worker advances leads **monotonically forward-only**, with a
  permanent **human-touch latch** (read-before-write), phone dedup, crash-window
  commit-per-record, and backoff → degraded state with idempotent owner alerts.
- Token refresh is row-locked and single-use to survive concurrent 401s.

Adding a provider is implementing the connector contract; the desired-state plane and
worker are shared.

---

## 9. Channels & delivery

- **Inbound** — channel adapters normalize Telegram / Instagram events into one event
  spine.
- **Outbound** — a delivery service routes each send to the correct channel: the
  **GramJS sidecar** (a Node MTProto *user* client) for Telegram, the Graph API for
  Instagram. Delivery is idempotent and ordered within a burst.
- **Media** — outbound media resolves through a layered resolver, including the owner
  media vault (re-send by stable document reference, refreshing the file reference
  just-in-time).

The sidecar is a separate process precisely because MTProto user sessions have different
operational constraints than the stateless API.

---

## 10. Prompt management

Prompts are treated as owned, versioned assets, not string literals scattered in code:

- a single reply **contract** shared by all agents,
- kind-scoped **playbooks** composed only for the relevant agent kinds,
- the **rendered per-business instructions** assembled from approved document sections,
- **budget guards** that cap prompt size (with tests that fail if a section grows past
  its budget),
- and a **prompt report** for visibility into what actually ships to the model.

Tool *names* live in tool schemas, never in seed prose — one concern, one home — to
avoid drift across the prompt surface.

---

## 11. The provider seam (model-agnostic direction)

All text/chat generation goes through one centralized client
(`backend/app/brain/llm.py`) with fallback chains; embeddings go through one embedding
service. Today the chains are Gemini. The path to other models is **per-provider
adapters** at this seam that reproduce the behavioral quirks that matter — most
critically the *forced tool-call* guarantee the seller relies on (Gemini's
function-calling mode), which a new provider must reproduce (e.g. an equivalent
"required tool" mode) or the safety property regresses.

---

## 12. Data & infrastructure

- **PostgreSQL** is the system of record; **pgvector** stores embeddings for retrieval.
- **Redis** carries pub/sub (WebSocket fan-out) and background queues.
- **Alembic** manages schema migrations; migrations import models and avoid SQL
  patterns that break the async driver.
- Background work runs under a **supervisor** (CRM sync, token refresh, index
  reconciler, promoter drip, source learning) — each idempotent and safe to restart.

---

## 13. Testing philosophy

- Backend tests run against a **real PostgreSQL** (never SQLite), with per-test
  transaction rollback for isolation. The LLM and Redis are mocked; the database is not.
- Every endpoint is expected to cover happy path, validation, auth, not-found, and
  **workspace isolation** (other-workspace resources return 404, never leaking
  existence).
- Critical agent properties (forced talk output, never-drop/leak, prompt budgets,
  catalog authority, CRM sync safety, channel delivery idempotency) have focused suites.
- Evals (`backend/app/modules/evals`, runnable via the `oqim` CLI) provide
  scenario-level proof for catalog, channel delivery, source intake, and runtime
  profiles.

---

## Where to start reading

1. `backend/app/modules/agent_runtime_v2/` — the runtime, dispatcher, and owner turn.
2. `backend/app/modules/agent_control/` + `commerce_catalog/` — the authority boundary.
3. `backend/app/modules/retrieval_core/` + `knowledge_mcp/` — RAG.
4. `backend/app/modules/crm_connector/` — the config-blob CRM engine.
5. `frontend/src/` — the operator web app.
