"""v5_recreate_dropped_indexes_and_hnsw

Revision ID: 5b1140b35a01
Revises: f96fc6aec185
Create Date: 2026-03-31 03:18:30.739441

Recreates indexes dropped by f02b301eaf7d (autogenerate noise) and adds the
missing HNSW index for learning_signals.embedding that was dropped in e3da56800571
(vector dimension upgrade) but never recreated.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '5b1140b35a01'
down_revision: Union[str, None] = 'f96fc6aec185'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Recreate indexes dropped by f02b301eaf7d (autogenerate dropped them without cause).
    # Use IF NOT EXISTS so this is safe to run on prod where indexes may already exist.

    for idx_name, table, columns in [
        ("ix_orders_workspace_id", "orders", "workspace_id"),
        ("ix_orders_status", "orders", "status"),
        ("ix_orders_customer_id", "orders", "customer_id"),
        ("ix_orders_conversation_id", "orders", "conversation_id"),
        ("ix_journey_events_workspace_customer", "customer_journey_events", "workspace_id, customer_id, created_at"),
        ("ix_journey_events_conversation_id", "customer_journey_events", "conversation_id"),
        ("ix_message_insights_workspace_id", "message_insights", "workspace_id"),
        ("ix_message_insights_conversation_id", "message_insights", "conversation_id"),
    ]:
        op.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({columns})")

    # M17/M18: HNSW index for learning_signals.embedding (3072-dim halfvec).
    # Dropped in e3da56800571 (vector upgrade) but never recreated.
    # Matches the pattern used for catalog_items in that same migration.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_learning_signals_embedding
        ON learning_signals
        USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_learning_signals_embedding")
    op.execute("DROP INDEX IF EXISTS ix_message_insights_conversation_id")
    op.execute("DROP INDEX IF EXISTS ix_message_insights_workspace_id")
    op.execute("DROP INDEX IF EXISTS ix_journey_events_conversation_id")
    op.execute("DROP INDEX IF EXISTS ix_journey_events_workspace_customer")
    op.execute("DROP INDEX IF EXISTS ix_orders_conversation_id")
    op.execute("DROP INDEX IF EXISTS ix_orders_customer_id")
    op.execute("DROP INDEX IF EXISTS ix_orders_status")
    op.execute("DROP INDEX IF EXISTS ix_orders_workspace_id")
