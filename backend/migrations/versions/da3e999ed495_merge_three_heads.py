"""merge three heads

Revision ID: da3e999ed495
Revises: 68da3840154b, a3c1e7f92d04, e1f2a3b4c5d6
Create Date: 2026-04-14
"""
from typing import Sequence, Union


revision: str = "da3e999ed495"
down_revision: Union[str, Sequence[str]] = (
    "68da3840154b",
    "a3c1e7f92d04",
    "e1f2a3b4c5d6",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
