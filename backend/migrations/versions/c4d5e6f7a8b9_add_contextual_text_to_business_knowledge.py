"""add contextual_text to business_knowledge

Revision ID: c4d5e6f7a8b9
Revises: ab4d1c9e8f77
Create Date: 2026-04-08 10:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "ab4d1c9e8f77"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "business_knowledge",
        sa.Column("contextual_text", sa.Text(), nullable=True),
    )

    op.execute(
        """
        UPDATE business_knowledge
        SET contextual_text = concat_ws(
            E'\n',
            CASE
                WHEN coalesce(title, '') <> '' THEN 'Title: ' || title
                ELSE NULL
            END,
            concat(
                'Context: Category: ', coalesce(category, 'general'),
                CASE
                    WHEN coalesce(source, '') <> '' THEN '; Source: ' || replace(source, '_', ' ')
                    ELSE ''
                END,
                CASE
                    WHEN confirmed IS TRUE THEN '; Status: confirmed'
                    ELSE ''
                END,
                CASE
                    WHEN frequency IS NOT NULL AND frequency > 0 THEN '; Observed frequency: ' || frequency::text
                    ELSE ''
                END
            ),
            coalesce(content, '')
        )
        WHERE contextual_text IS NULL OR btrim(contextual_text) = ''
        """
    )


def downgrade() -> None:
    op.drop_column("business_knowledge", "contextual_text")
