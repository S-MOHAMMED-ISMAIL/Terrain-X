"""add point_slope measurement type

Revision ID: c3a9d5e7f1b2
Revises: b7e2f1c4d9a3
Create Date: 2026-09-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c3a9d5e7f1b2'
down_revision: Union[str, None] = 'b7e2f1c4d9a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # P1-4 slope-at-point. Additive only: no table change, existing
    # measurement rows are untouched.
    op.execute("ALTER TYPE measurement_type ADD VALUE IF NOT EXISTS 'point_slope'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums — the same limitation every
    # earlier enum-extending migration in this project documents.
    pass
