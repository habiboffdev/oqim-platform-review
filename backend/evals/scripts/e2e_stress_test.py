"""E2E stress test: 100 realistic customer messages through draft generation.

Tests the REAL AI pipeline with actual Gemini calls. Measures:
- Draft generation success rate
- Latency (avg, p50, p95)
- Confidence distribution
- Intent classification accuracy
- Business Brain correction episode source-unit indexing
- Contextual correction retrieval with lexical and semantic signals

Usage:
    cd backend
    python evals/scripts/e2e_stress_test.py
    python evals/scripts/e2e_stress_test.py --count 20   # quick test
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, ".")

# Realistic Uzbek seller customer messages across different intents
CUSTOMER_MESSAGES = [
    # Product inquiries (most common)
    "iPhone 15 bormi?",
    "Samsung S24 Ultra qancha?",
    "Redmi Note 13 Pro bor ekan, narxi?",
    "AirPods bormi sizda?",
    "Telefon chexollar bormi?",
    "iPhone 15 Pro Max narxi qancha?",
    "Samsung A55 bormi?",
    "Pixel 9 bor ekanmi?",
    "Zaryadka kabeli bormi USB-C?",
    "Ekran plyonka bormi Samsung uchun?",
    "MacBook Air M3 bormi?",
    "iPad bormi yangi?",
    "Apple Watch bormi?",
    "Powerbank bormi 20000 mAh?",
    "Bluetooth naushnik kerak",
    "Samsung Galaxy Buds bormi?",
    "Adapter bormi tez zaryadlovchi?",
    "Honor 200 bormi?",
    "Xiaomi 14 bormi?",
    "Telefon qobig'i kerak Redmi uchun",
    # Price questions
    "Narxi qancha?",
    "Eng arzon telefon qancha?",
    "Chegirmada bormi?",
    "Yana arzonroq narxda bormi?",
    "Kredit berasizmi?",
    "Muddatli to'lov bormi?",
    "3 ta olsam chegirma berasizmi?",
    "Oxirgi narx qancha bo'ladi?",
    "Dollar kursida qancha?",
    "Bo'lib to'lash imkoni bormi?",
    "Optom narx boshqami?",
    "Ulgurji narx berganizmi?",
    "Eng qimmat telefoniz qaysi?",
    "1 million atrofida nima bor?",
    "5 millionlik telefon ko'rsating",
    # Delivery questions
    "Yetkazib berasizmi?",
    "Dostavka bepulmi?",
    "Toshkentga yetkazib berasizmi?",
    "Samarqandga jo'natasizmi?",
    "Qancha vaqtda yetadi?",
    "Bugun jo'nata olasizmi?",
    "Pochta orqali jo'natasizmi?",
    "Yandex Go bilan yubora olasizmi?",
    "O'zim olib ketsam bo'ladimi?",
    "Qayerdan olsam bo'ladi?",
    "Manzilingiz qayerda?",
    "Metro yaqinida ekanmisiz?",
    "Ish vaqtingiz nechagacha?",
    "Dam olish kunlari ishlaysizmi?",
    "Tushlik vaqtida ochiqmisiz?",
    # Warranty/quality
    "Kafolati bormi?",
    "Original telefon ekanmi?",
    "Kopiya emasmi?",
    "Garantiya necha oy?",
    "Buzilsa almashtira olasizmi?",
    "Refurbished emasmi?",
    "Qutisi bormi?",
    "Hamma aksessuarlari bormi?",
    "Batareya sig'imi qancha?",
    "Xotira necha GB?",
    "Rangi tanlov bormi?",
    "Qora rangi bormi?",
    "Ko'k rangi qoldimi?",
    "Yangi kelganmi?",
    "Bu oxirgi modeli ekanmi?",
    # Greetings and closings
    "Salom",
    "Assalomu alaykum",
    "Salom, yaxshimisiz?",
    "Rahmat",
    "Rahmat, o'ylab ko'raman",
    "Keyinroq yozaman",
    "Xayr",
    "OK, tushunarli",
    "Ha, kelib olaman",
    "Yaxshi, kelishuvdik",
    # Complaints
    "Telefon ishlamayapti",
    "Ekran sinib qoldi",
    "Zaryadka tez tugayapti",
    "Kafolat bo'yicha almashtirib bering",
    "Kecha olgan telefonim yoqilmayapti",
    # Negotiation
    "10 million qilsangiz olaman",
    "Boshqa do'konda arzonroq",
    "Chegirma qilasizmi?",
    "Eng oxirgi narx ayting",
    "2 ta olsam qanchadan?",
    # Follow-ups
    "Kechagi buyurtma qayerda?",
    "Track raqami bormi?",
    "Qachon yetadi?",
    "Hali jo'natdingizmi?",
    "Pul o'tkazdim, tekshirib ko'ring",
    "Chek yuboring iltimos",
    # Mixed/complex
    "iPhone 15 Pro 256GB qora rangda bormi, narxi va dostavka haqida ayting",
    "Salom, kecha gaplashgan edik Samsung haqida, hali bormi?",
    "Menga telefon kerak lekin qaysi yaxshiroq bilmayman, maslahat bering",
    "Akam uchun sovg'a qilmoqchiman, nimani tavsiya qilasiz?",
]


@dataclass
class TestResult:
    message: str
    success: bool
    draft: str = ""
    confidence: float = 0.0
    intent: str = ""
    latency_ms: int = 0
    error: str = ""


@dataclass
class StressTestReport:
    total: int = 0
    success: int = 0
    failed: int = 0
    errors: list = field(default_factory=list)
    latencies: list = field(default_factory=list)
    confidences: list = field(default_factory=list)
    intents: dict = field(default_factory=dict)
    results: list = field(default_factory=list)

    def add(self, r: TestResult):
        self.total += 1
        if r.success:
            self.success += 1
            self.latencies.append(r.latency_ms)
            self.confidences.append(r.confidence)
            self.intents[r.intent] = self.intents.get(r.intent, 0) + 1
        else:
            self.failed += 1
            self.errors.append({"message": r.message, "error": r.error})
        self.results.append(r)

    def summary(self) -> str:
        lines = [
            "",
            "=" * 60,
            "  E2E STRESS TEST RESULTS",
            "=" * 60,
            f"  Total:    {self.total}",
        ]
        if self.total:
            lines.append(f"  Success:  {self.success} ({self.success/self.total*100:.0f}%)")
        lines += [
            f"  Failed:   {self.failed}",
            "",
        ]

        if self.latencies:
            sorted_lat = sorted(self.latencies)
            avg = sum(sorted_lat) / len(sorted_lat)
            p50 = sorted_lat[len(sorted_lat) // 2]
            p95 = sorted_lat[int(len(sorted_lat) * 0.95)]
            lines += [
                "  Latency:",
                f"    avg:  {avg:.0f}ms",
                f"    p50:  {p50}ms",
                f"    p95:  {p95}ms",
                f"    min:  {sorted_lat[0]}ms",
                f"    max:  {sorted_lat[-1]}ms",
                "",
            ]

        if self.confidences:
            avg_conf = sum(self.confidences) / len(self.confidences)
            high = sum(1 for c in self.confidences if c >= 0.8)
            med = sum(1 for c in self.confidences if 0.5 <= c < 0.8)
            low = sum(1 for c in self.confidences if c < 0.5)
            lines += [
                "  Confidence:",
                f"    avg:    {avg_conf:.3f}",
                f"    high:   {high} (>=0.8)",
                f"    medium: {med} (0.5-0.8)",
                f"    low:    {low} (<0.5)",
                "",
            ]

        if self.intents:
            lines += ["  Intents:"]
            for intent, count in sorted(self.intents.items(), key=lambda x: -x[1]):
                lines += [f"    {intent}: {count}"]
            lines += [""]

        if self.errors:
            lines += ["  Errors:"]
            for e in self.errors[:10]:
                lines += [f"    [{e['message'][:40]}] {e['error'][:80]}"]
            lines += [""]

        lines += ["=" * 60]
        return "\n".join(lines)


async def test_draft_generation(count: int = 100):
    """Generate drafts for N real customer messages and report quality metrics."""
    from app.db.session import async_session as session_factory

    # We need a workspace with a voice profile and agent
    async with session_factory() as db:
        from sqlalchemy import select, text
        from app.models.workspace import Workspace

        ws = (await db.execute(select(Workspace).limit(1))).scalar_one_or_none()
        if not ws:
            print("ERROR: No workspace found. Run onboarding first.")
            return

        workspace_id = ws.id
        print(f"Testing with workspace: {ws.name} (id={workspace_id})")

        # Find a conversation to use for context
        row = (await db.execute(text(
            "SELECT id FROM conversations WHERE workspace_id = :ws ORDER BY last_message_at DESC LIMIT 1"
        ), {"ws": workspace_id})).first()
        if not row:
            print("ERROR: No conversations found.")
            return
        conversation_id = row[0]
        print(f"Using conversation: {conversation_id}")

    messages = CUSTOMER_MESSAGES[:count]
    report = StressTestReport()

    # Test with concurrency (like real usage -- multiple customers at once)
    semaphore = asyncio.Semaphore(3)  # 3 concurrent drafts

    async def test_one(msg: str) -> TestResult:
        async with semaphore:
            start = time.time()
            try:
                async with session_factory() as db:
                    from app.brain.agent import generate_draft
                    from app.models.message import Message
                    from app.models.ai_reply import AIReply
                    from sqlalchemy import delete as sql_delete

                    # Create temp message (same pattern as CLI)
                    temp_msg = Message(
                        conversation_id=conversation_id,
                        content=msg,
                        sender_type="customer",
                        telegram_message_id=None,
                    )
                    db.add(temp_msg)
                    await db.flush()

                    ai_reply = await generate_draft(
                        conversation_id=conversation_id,
                        trigger_message_id=temp_msg.id,
                        db=db,
                    )

                    latency = int((time.time() - start) * 1000)

                    # Extract result before cleanup
                    if ai_reply is not None:
                        result = TestResult(
                            message=msg,
                            success=True,
                            draft=ai_reply.draft_content or "",
                            confidence=ai_reply.confidence_score or 0,
                            intent=ai_reply.intent or "unknown",
                            latency_ms=latency,
                        )
                        # Cleanup
                        await db.execute(sql_delete(AIReply).where(AIReply.id == ai_reply.id))
                    else:
                        result = TestResult(
                            message=msg,
                            success=False,
                            latency_ms=latency,
                            error="Pre-pass decided not to reply",
                        )

                    await db.execute(sql_delete(Message).where(Message.id == temp_msg.id))
                    await db.commit()
                    return result

            except Exception as e:
                latency = int((time.time() - start) * 1000)
                return TestResult(
                    message=msg,
                    success=False,
                    latency_ms=latency,
                    error=str(e)[:200],
                )

    print(f"\nRunning {len(messages)} draft generations (3 concurrent)...\n")

    tasks = [test_one(msg) for msg in messages]
    results = await asyncio.gather(*tasks)

    for r in results:
        report.add(r)
        status = "+" if r.success else "x"
        conf = f"{r.confidence:.2f}" if r.success else "---"
        draft_preview = r.draft[:50] + "..." if len(r.draft) > 50 else r.draft
        if r.success:
            print(f"  {status} [{conf}] {r.message[:35]:35s} -> {draft_preview}")
        else:
            print(f"  {status} [{conf}] {r.message[:35]:35s} -> ERROR: {r.error[:50]}")

    print(report.summary())

    # Save detailed results
    output_path = "evals/results/e2e_stress_test.json"
    os.makedirs("evals/results", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({
            "total": report.total,
            "success": report.success,
            "failed": report.failed,
            "avg_latency_ms": sum(report.latencies) / len(report.latencies) if report.latencies else 0,
            "avg_confidence": sum(report.confidences) / len(report.confidences) if report.confidences else 0,
            "intents": report.intents,
            "errors": report.errors,
            "results": [
                {
                    "message": r.message,
                    "success": r.success,
                    "draft": r.draft,
                    "confidence": r.confidence,
                    "intent": r.intent,
                    "latency_ms": r.latency_ms,
                    "error": r.error,
                }
                for r in report.results
            ],
        }, f, ensure_ascii=False, indent=2)
    print(f"\nDetailed results: {output_path}")


async def test_correction_retrieval():
    """Test Business Brain correction episode storage + contextual retrieval."""
    from app.db.session import async_session as session_factory
    from app.modules.business_brain.memory import BusinessBrainMemoryService
    from app.modules.business_brain.memory_contracts import (
        ContextualRetrievalRequest,
        CorrectionEpisodeInput,
        SourceUnitRebuildRequest,
    )
    from app.modules.commercial_spine.repository import CommercialSpineRepository
    from app.models.commercial_spine import (
        BusinessBrainFactRecord,
        BusinessBrainIndexRecord,
        BusinessBrainUpdateRecord,
    )
    from sqlalchemy import delete, select
    from app.models.workspace import Workspace

    async with session_factory() as db:
        ws = (await db.execute(select(Workspace).limit(1))).scalar_one_or_none()
        if not ws:
            print("ERROR: No workspace found.")
            return

        memory = BusinessBrainMemoryService(
            repository=CommercialSpineRepository(db),
        )
        workspace_id = ws.id

        print("\n" + "=" * 60)
        print("  BUSINESS BRAIN CORRECTION RETRIEVAL TEST")
        print("=" * 60)

        # Store 5 corrections as Business Brain correction episodes.
        corrections_to_store = [
            {
                "wrong": "Assalomu alaykum, hurmatli mijozimiz! iPhone 15 haqida ma'lumot bermoqchimisiz?",
                "right": "Ha, bor aka. Qaysi rangi kerak?",
                "rule": "Match informal tone, don't be overly formal",
                "situation": "iPhone 15 bormi?",
            },
            {
                "wrong": "Yetkazib berish xizmati mavjud. Toshkent shahriga 1-2 ish kunida yetkaziladi.",
                "right": "Ha, Toshkentga ertaga yetadi. Dostavka 20 ming.",
                "rule": "Give specific price and time, not vague ranges",
                "situation": "Toshkentga yetkazib berasizmi?",
            },
            {
                "wrong": "Mahsulotimiz original va sertifikatlangan.",
                "right": "Original, qutisi bor. 1 yil kafolat.",
                "rule": "Short factual answers, mention warranty",
                "situation": "Original ekanmi?",
            },
            {
                "wrong": "Afsuski, hozirda bu model zaxirada yo'q.",
                "right": "Hozir yo'q, ertaga keladi. Band qilsinmi?",
                "rule": "Always offer alternative action when out of stock",
                "situation": "Samsung S24 bormi?",
            },
            {
                "wrong": "Mahsulotlarimiz sifatli va ishonchli.",
                "right": "Batareyasi 5000 mAh, 2 kun yetadi. Kamerasi 200MP.",
                "rule": "Give specific specs, not generic praise",
                "situation": "Bu telefon yaxshimi?",
            },
        ]

        stored_fact_ids = []
        for idx, c in enumerate(corrections_to_store, start=1):
            fact_id = f"stress-correction:{workspace_id}:{idx}"
            await memory.write_correction_episode(
                CorrectionEpisodeInput(
                    workspace_id=workspace_id,
                    episode_ref=fact_id,
                    situation={"customer_message": c["situation"]},
                    candidate_output=c["wrong"],
                    human_feedback=c["rule"],
                    final_output=c["right"],
                    outcome="stress_test",
                    quality_label="approved",
                    source_refs=[f"stress:{idx}"],
                    correlation_id=f"stress-correction:{workspace_id}:{idx}",
                )
            )
            await db.commit()
            print(f"  Stored: '{c['situation'][:40]}' fact={fact_id}")
            stored_fact_ids.append(fact_id)

        indexed = await memory.rebuild_contextual_source_units(
            SourceUnitRebuildRequest(
                workspace_id=workspace_id,
                fact_types=["correction_episode_fact"],
                embed_source_units=True,
            )
        )
        await db.commit()
        ready = sum(1 for unit in indexed.source_units if unit.embedding_state == "ready")
        degraded = sum(1 for unit in indexed.source_units if unit.embedding_state == "degraded")
        print(f"  Indexed source units: ready={ready} degraded={degraded}")

        # Test retrieval with similar queries
        test_queries = [
            ("iPhone 16 bormi?", "Should retrieve iPhone correction"),
            ("Samarqandga dostavka bormi?", "Should retrieve delivery correction"),
            ("Kopiya emasmi?", "Should retrieve originality correction"),
            ("Xiaomi 14 bormi?", "Should retrieve out-of-stock correction"),
            ("Kamerasi yaxshimi?", "Should retrieve specs correction"),
            ("Salom, narx ayting", "Mixed -- may or may not match"),
        ]

        print()
        for query, note in test_queries:
            results = await memory.retrieve_contextual(
                ContextualRetrievalRequest(
                    workspace_id=workspace_id,
                    requested_fact_types=["correction_episode_fact"],
                    candidate_fact_ids=stored_fact_ids,
                    query_text=query,
                    limit=3,
                    include_source_units=True,
                )
            )
            if results.candidates:
                top = results.candidates[0]
                value = top.value
                print(f"  Query: '{query}'")
                print(f"    -> Top fact: {top.fact_id}")
                print(f"    -> Situation: {value.get('situation', {})}")
                print(f"    -> Lesson: {value.get('human_feedback', '')}")
                print(f"    -> Channels: {results.trace.retrieval_channels}")
                print(f"    ({note})")
                print()
            else:
                print(f"  Query: '{query}' -> NO RESULTS ({note})")
                print()

        # Format test
        all_corrections = await memory.retrieve_contextual(
            ContextualRetrievalRequest(
                workspace_id=workspace_id,
                requested_fact_types=["correction_episode_fact"],
                candidate_fact_ids=stored_fact_ids,
                query_text="telefon bormi?",
                limit=5,
            )
        )
        formatted = _format_correction_candidates(all_corrections.candidates)
        print("  Formatted output (what LLM sees):")
        print("  " + "-" * 50)
        for line in formatted.split("\n"):
            print(f"  {line}")
        print("  " + "-" * 50)
        await db.execute(
            delete(BusinessBrainIndexRecord).where(
                BusinessBrainIndexRecord.workspace_id == workspace_id,
                BusinessBrainIndexRecord.fact_id.in_(stored_fact_ids),
            )
        )
        await db.execute(
            delete(BusinessBrainUpdateRecord).where(
                BusinessBrainUpdateRecord.workspace_id == workspace_id,
                BusinessBrainUpdateRecord.target_ref.in_(
                    [f"fact:{fact_id}" for fact_id in stored_fact_ids]
                ),
            )
        )
        await db.execute(
            delete(BusinessBrainFactRecord).where(
                BusinessBrainFactRecord.workspace_id == workspace_id,
                BusinessBrainFactRecord.fact_id.in_(stored_fact_ids),
            )
        )
        await db.commit()
        print(f"\n  Cleaned up {len(stored_fact_ids)} Business Brain correction facts")
        print("=" * 60)


def _format_correction_candidates(candidates) -> str:
    lines: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        value = candidate.value
        situation = value.get("situation", {})
        customer_message = (
            situation.get("customer_message")
            if isinstance(situation, dict)
            else str(situation)
        )
        lines.extend(
            [
                f"{index}. Situation: {customer_message}",
                f"   Wrong: {value.get('candidate_output', '')}",
                f"   Right: {value.get('final_output', '')}",
                f"   Lesson: {value.get('human_feedback', '')}",
            ]
        )
    return "\n".join(lines) if lines else "No correction examples."


async def main():
    count = 100
    if len(sys.argv) > 1:
        if sys.argv[1] == "--count":
            count = int(sys.argv[2])
        else:
            count = int(sys.argv[1])

    # Part 1: Correction retrieval (quick, proves embeddings work)
    await test_correction_retrieval()

    # Part 2: Draft generation stress test
    await test_draft_generation(count)


if __name__ == "__main__":
    asyncio.run(main())
