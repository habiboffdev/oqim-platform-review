"""backfill canonical crm state from legacy conversation fields

Revision ID: 9c4b6a7d8e1f
Revises: 8f1a2b3c4d5e
Create Date: 2026-04-23 10:45:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "9c4b6a7d8e1f"
down_revision = "8f1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE conversations
        SET crm_state = jsonb_strip_nulls(
            COALESCE(crm_state, '{}'::jsonb)
            || CASE
                WHEN (crm_state IS NULL OR NOT (crm_state ? 'pipeline_stage'))
                  AND pipeline_stage IS NOT NULL
                THEN jsonb_build_object('pipeline_stage', pipeline_stage)
                ELSE '{}'::jsonb
               END
            || CASE
                WHEN (crm_state IS NULL OR NOT (crm_state ? 'products_interested'))
                  AND jsonb_typeof(COALESCE(products_mentioned, '[]'::jsonb)) = 'array'
                  AND jsonb_array_length(COALESCE(products_mentioned, '[]'::jsonb)) > 0
                THEN jsonb_build_object('products_interested', products_mentioned)
                ELSE '{}'::jsonb
               END
        )
        WHERE (crm_state IS NULL OR NOT (crm_state ? 'pipeline_stage'))
           OR (
                (crm_state IS NULL OR NOT (crm_state ? 'products_interested'))
                AND jsonb_typeof(COALESCE(products_mentioned, '[]'::jsonb)) = 'array'
                AND jsonb_array_length(COALESCE(products_mentioned, '[]'::jsonb)) > 0
           )
        """
    )


def downgrade() -> None:
    # Data backfill is intentionally irreversible: once canonical state has
    # been populated and consumed, stripping it back out would risk deleting
    # newer authoritative values.
    pass
