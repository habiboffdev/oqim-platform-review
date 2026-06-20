"""Runtime proof ledger used by `oqim audit runtime`."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class RuntimeProofGate:
    plane: str
    command: str
    status: str
    level: str
    purpose: str
    remaining_gap: str


PROOF_GATES: tuple[RuntimeProofGate, ...] = (
    RuntimeProofGate(
        plane="runtime-zero",
        command="oqim test runtime-zero",
        status="implemented",
        level="L0",
        purpose="Local DB, Redis, sidecar, health, and optional browser cache reset are truthful.",
        remaining_gap="Run with --reset --cleanup-sidecar --browser before fresh manual smoke.",
    ),
    RuntimeProofGate(
        plane="local-reality",
        command="oqim test local-reality",
        status="implemented",
        level="L2",
        purpose="Live dependencies, core APIs, reconnect proof, browser cache reset, app smoke, and Telegram intake browser smoke pass together.",
        remaining_gap="This is a local deterministic/browser gate, not a production load or real Telegram provider certification.",
    ),
    RuntimeProofGate(
        plane="live-chat-truth",
        command="oqim test live-chat-truth --workspace-id <id>",
        status="implemented",
        level="L2",
        purpose="Existing local workspace chat list/detail/messages APIs and the browser route agree on real canonical messages.",
        remaining_gap="This proves render truth for one existing workspace/conversation; it does not send Telegram messages or certify provider delivery.",
    ),
    RuntimeProofGate(
        plane="telegram-intake",
        command="oqim test telegram-intake --browser",
        status="implemented",
        level="L2/L3",
        purpose="GramJS-shaped Telegram events append to EventSpine, drain through authoritative projections, surface through conversation APIs, trigger Seller Agent reply dispatch, and optionally render in browser smoke.",
        remaining_gap="This proves local mocked Telegram intake and sidecar contract; real-account provider soak and long-running reconnect storms remain pilot gates.",
    ),
    RuntimeProofGate(
        plane="harness-truth",
        command="oqim test harness-parallel",
        status="implemented",
        level="L0/L1",
        purpose="DB-backed proof suites can run concurrently without dropping or creating the same test database.",
        remaining_gap="This proves harness isolation, not production workload capacity.",
    ),
    RuntimeProofGate(
        plane="truth/replay",
        command="oqim test replay",
        status="implemented",
        level="L1",
        purpose="Canonical EventSpine lifecycle fixtures replay into empty projections.",
        remaining_gap="Durable all-lifecycle production retention still needs broader load proof.",
    ),
    RuntimeProofGate(
        plane="state",
        command="oqim test conversation-tail",
        status="implemented",
        level="L1",
        purpose="Conversation list/detail tail, unread, revisions, and gap state share canonical projection.",
        remaining_gap="Keep normal read repair disabled while smoke testing real Telegram history.",
    ),
    RuntimeProofGate(
        plane="delivery",
        command="oqim test delivery-chaos",
        status="implemented",
        level="L3",
        purpose="Route retry, timeout-to-unknown, echo reconciliation, and scheduled reclaim are idempotent.",
        remaining_gap="Real Telegram sidecar restart soak is still a pilot gate.",
    ),
    RuntimeProofGate(
        plane="media",
        command="oqim test media-chaos",
        status="implemented",
        level="L3",
        purpose="Media states, retry exhaustion, lease reclaim, unsupported semantics, and range streaming are explicit.",
        remaining_gap="Browser playback smoke for real stickers/GIF/circle notes remains a manual proof.",
    ),
    RuntimeProofGate(
        plane="frontend-sync",
        command="oqim test reconnect",
        status="implemented",
        level="L2/L3",
        purpose="Reconnect reconciliation matches canonical reload semantics.",
        remaining_gap="Keep shrinking websocket cache mutation ownership.",
    ),
    RuntimeProofGate(
        plane="onboarding",
        command="oqim test onboarding-chaos",
        status="implemented",
        level="L3",
        purpose="QR expiry, 2FA failure, revoked/stale sessions, sidecar runtime ownership, onboarding PDF Files API routing, stale source-learning recovery, and rate-limit repair state are explicit.",
        remaining_gap="Real QR/2FA browser smoke and production provider/tenant soak can still fail due provider/session behavior.",
    ),
    RuntimeProofGate(
        plane="live-telegram-onboarding",
        command="oqim test live-telegram-onboarding --workspace-id <id>",
        status="implemented",
        level="L2",
        purpose="Live GramJS channel discovery and channel-post reads produce the same read-only Telegram source payload used by onboarding source learning.",
        remaining_gap="This does not send messages or write Business Brain facts into the seller workspace; run source-learning evals separately for quality.",
    ),
    RuntimeProofGate(
        plane="embedding/rag",
        command="oqim test embedding-chaos",
        status="implemented",
        level="L3",
        purpose="Provider failure, wrong dimensions, duplicate ingestion, semantic fallback, and tenant isolation are safe.",
        remaining_gap="Quality eval thresholds for sales replies are separate from outage safety.",
    ),
    RuntimeProofGate(
        plane="retrieval-core-quality",
        command="oqim eval retrieval-core --max-p95-ms 5000",
        status="implemented",
        level="L1/L4",
        purpose="Retrieval Core agentic search, query rewrite, rerank, media alias recall, and workspace isolation are measured through the shared boundary.",
        remaining_gap="Deterministic quality gate is green; live-provider agentic-search load and chaos proof remain before pilot certification.",
    ),
    RuntimeProofGate(
        plane="retrieval-rerank-provider",
        command="oqim eval retrieval-core --live-rerank-provider --max-p95-ms 5000",
        status="implemented",
        level="L1",
        purpose="Configured external reranker must return relevance scores through the real Retrieval Core boundary.",
        remaining_gap="Discovery Engine API and IAM are configured locally; keep this live provider gate in pilot checks so config drift fails loudly.",
    ),
    RuntimeProofGate(
        plane="company-brain-source-quality",
        command="oqim eval company-brain --max-p95-ms 10000",
        status="implemented",
        level="L1/L4",
        purpose="Business Brain source learning across text, website, PDF, screenshot, Telegram channel, spreadsheet, voice note, and past conversations is measured with retrieval and source latency.",
        remaining_gap="Deterministic mixed-source gate is green; run the live provider gate before release candidates.",
    ),
    RuntimeProofGate(
        plane="company-brain-live-source-quality",
        command="oqim eval company-brain --live --semantic --contextual-source-units --max-p95-ms 60000",
        status="implemented",
        level="L1",
        purpose="Live Gemini plus Gemini embedding-2 source learning, contextual source units, semantic retrieval, and mixed company/PDF/source shapes are measured through the shared Business Brain boundary.",
        remaining_gap="Live source-quality gate is green; authenticated browser onboarding, production provider rate-limit, and tenant soak remain release gates.",
    ),
    RuntimeProofGate(
        plane="multi-tenant",
        command="oqim test tenants --workspaces 1000",
        status="implemented",
        level="L4",
        purpose="Workspace identity scale, Source Intake/source-learning isolation, Seller Agent scheduler fairness/backpressure, dispatch fairness, DLQ, signals, p95 guard.",
        remaining_gap="Deterministic 1000-workspace proof is green; production load certification and live provider tenant soak remain pilot/infra gates.",
    ),
    RuntimeProofGate(
        plane="reply-quality",
        command="oqim eval replies --seed-workspace --concurrency 2 --max-p95-ms 45000",
        status="implemented",
        level="L5",
        purpose="Golden seller conversations score Seller Agent reply quality before pilot users see regressions.",
        remaining_gap="Seeded live LLM golden cases cover chaotic multi-message tails, media, support policy, code-switching, pricing, warranty grounding, and payment safety. Keep real workspace evals as release-candidate proof.",
    ),
    RuntimeProofGate(
        plane="sales-crm",
        command="oqim eval sales",
        status="implemented",
        level="L5",
        purpose="CRM stage movement, follow-up timing, and buyer-intent handling are measurable.",
        remaining_gap="Deterministic CRM/action eval covers attention, media blocking, follow-up timing, AI-off readiness, and settled states. Seller Agent reply quality remains covered by reply-quality evals.",
    ),
    RuntimeProofGate(
        plane="buyer-intent-extraction",
        command="oqim eval buyer-intent --live --concurrency 2 --max-p95-ms 45000",
        status="implemented",
        level="L1",
        purpose="Universal Extraction buyer-intent profile is measured against live LLM Gateway output across vertical-neutral chaotic buyer tails.",
        remaining_gap="Live bounded-concurrency quality proof is green; broader production distribution and chaos proof still remain.",
    ),
    RuntimeProofGate(
        plane="adapter-parity",
        command="oqim test adapter-contract",
        status="implemented",
        level="L4",
        purpose="Telegram and mocked Instagram satisfy the same adapter contract before new platforms.",
        remaining_gap="Telegram send/read, history, dialogs, media hydration, media streaming, custom emoji, and route mark-read now use the adapter seam. Telegram auth/session routes remain Telegram-specific by design.",
    ),
)


def runtime_audit_report() -> dict:
    gates = [asdict(gate) for gate in PROOF_GATES]
    counts: dict[str, int] = {}
    for gate in gates:
        counts[gate["status"]] = counts.get(gate["status"], 0) + 1
    blockers = [
        gate
        for gate in gates
        if gate["status"] in {"partial", "target"}
    ]
    return {
        "passed": not blockers,
        "summary": {
            "total": len(gates),
            "implemented": counts.get("implemented", 0),
            "partial": counts.get("partial", 0),
            "target": counts.get("target", 0),
        },
        "gates": gates,
        "blockers": blockers,
        "next_actions": [
            "Run implemented harnesses together before pilot smoke.",
            "For release candidates, also run `oqim eval replies --workspace <real_workspace_id> --json`.",
            "Run production-like tenant soak before paid pilot launch.",
        ],
    }
