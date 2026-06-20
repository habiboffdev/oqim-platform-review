"""create conversation_pairs table

Revision ID: 00de7306b68e
Revises: e3da56800571
Create Date: 2026-03-30 16:16:01.054273
"""
from typing import Sequence, Union

from alembic import op
import pgvector.sqlalchemy.vector
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '00de7306b68e'
down_revision: Union[str, None] = 'e3da56800571'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('conversation_pairs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False),
    sa.Column('customer_turn', sa.Text(), nullable=False),
    sa.Column('seller_turn', sa.Text(), nullable=False),
    sa.Column('context_prefix', sa.Text(), nullable=True),
    sa.Column('previous_turns', sa.Text(), nullable=True),
    sa.Column('has_media', sa.Boolean(), nullable=False),
    sa.Column('media_type', sa.String(length=20), nullable=True),
    sa.Column('media_bytes', sa.LargeBinary(), nullable=True),
    sa.Column('media_description', sa.Text(), nullable=True),
    sa.Column('intent', sa.String(length=50), nullable=True),
    sa.Column('pair_timestamp', sa.DateTime(timezone=True), nullable=False),
    sa.Column('customer_msg_count', sa.Integer(), nullable=False),
    sa.Column('seller_msg_count', sa.Integer(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=3072), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('conversation_id', 'pair_timestamp', name='uq_pair_conv_ts')
    )
    op.create_index('idx_cp_conversation', 'conversation_pairs', ['conversation_id'], unique=False)
    op.create_index('idx_cp_customer', 'conversation_pairs', ['customer_id'], unique=False)
    op.create_index('idx_cp_timestamp', 'conversation_pairs', ['pair_timestamp'], unique=False)
    op.create_index('idx_cp_workspace', 'conversation_pairs', ['workspace_id'], unique=False)

    # HNSW halfvec index — Alembic can't autogenerate the halfvec cast
    op.execute("""
        CREATE INDEX idx_cp_embedding ON conversation_pairs
        USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)


def downgrade() -> None:
    op.drop_index('idx_cp_embedding', table_name='conversation_pairs')
    op.drop_index('idx_cp_workspace', table_name='conversation_pairs')
    op.drop_index('idx_cp_timestamp', table_name='conversation_pairs')
    op.drop_index('idx_cp_customer', table_name='conversation_pairs')
    op.drop_index('idx_cp_conversation', table_name='conversation_pairs')
    op.drop_table('conversation_pairs')
