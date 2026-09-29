"""add depth pipeline stages

Revision ID: 9af84d3fba2a
Revises: a883d44def98
Create Date: 2026-09-14 01:03:22.021652

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9af84d3fba2a'
down_revision: Union[str, None] = 'a883d44def98'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Alembic/SQLAlchemy autogenerate does not diff native Postgres enum
    # member lists, so this migration is hand-written. New AnalysisStage
    # values for the Phase 3 depth pipeline, inserted in pipeline order
    # between 'validating_input' and the legacy 'executing' value (kept for
    # backward compatibility with historical rows — see
    # app/models/analysis_job.py).
    op.execute("ALTER TYPE analysis_stage ADD VALUE IF NOT EXISTS 'loading_model' AFTER 'validating_input'")
    op.execute("ALTER TYPE analysis_stage ADD VALUE IF NOT EXISTS 'preprocessing' AFTER 'loading_model'")
    op.execute("ALTER TYPE analysis_stage ADD VALUE IF NOT EXISTS 'inference' AFTER 'preprocessing'")
    op.execute("ALTER TYPE analysis_stage ADD VALUE IF NOT EXISTS 'writing_depth' AFTER 'inference'")


def downgrade() -> None:
    # Postgres cannot drop individual enum values (only recreating the whole
    # type can remove one), so this migration is one-directional. Downgrading
    # would require: create a new enum type without these values, cast the
    # column over (failing if any row already uses one of these values),
    # drop the old type, rename the new one into place — not implemented, as
    # it's destructive and these values are additive/backward-compatible.
    raise NotImplementedError(
        "Cannot downgrade: Postgres does not support dropping enum values. "
        "See this migration's upgrade() docstring."
    )
