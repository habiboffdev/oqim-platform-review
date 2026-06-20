"""Export learning_signals to eval dataset JSONL.

Usage:
    cd backend
    python evals/scripts/mine_corrections.py
    # or with a custom output path:
    python evals/scripts/mine_corrections.py evals/datasets/my_corrections.jsonl
"""
import asyncio
import json
import sys

sys.path.insert(0, ".")


async def main(output_path: str = "evals/datasets/corrections.jsonl"):
    from app.db.session import async_session_factory
    from app.models.learning_signal import LearningSignal
    from sqlalchemy import select

    async with async_session_factory() as db:
        result = await db.execute(
            select(LearningSignal)
            .where(LearningSignal.signal_type.in_(["voice_correction", "fact_correction", "dismiss_correction"]))
            .order_by(LearningSignal.created_at.desc())
            .limit(200)
        )
        signals = result.scalars().all()

        with open(output_path, "w") as f:
            for s in signals:
                try:
                    data = json.loads(s.correction)
                except (json.JSONDecodeError, TypeError):
                    data = {"wrong": "", "right": s.correction, "rule": "", "situation": s.context}
                f.write(json.dumps({
                    "id": s.id,
                    "workspace_id": s.workspace_id,
                    "wrong": data.get("wrong", ""),
                    "right": data.get("right", ""),
                    "rule": data.get("rule", ""),
                    "situation": data.get("situation", ""),
                }, ensure_ascii=False) + "\n")

        print(f"Exported {len(signals)} corrections to {output_path}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "evals/datasets/corrections.jsonl"
    asyncio.run(main(path))
