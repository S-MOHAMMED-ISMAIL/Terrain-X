"""add ground filter (DTM/nDSM) support

Revision ID: b7e2f1c4d9a3
Revises: a0d3ac929883
Create Date: 2026-09-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'b7e2f1c4d9a3'
down_revision: Union[str, None] = 'a0d3ac929883'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # op.add_column on an existing table does not create a new enum type —
    # it must exist first (same precedent as semantic_status in c0b7e6dffad9).
    sa.Enum(
        'not_requested', 'processing', 'completed', 'failed', name='ground_filter_status'
    ).create(op.get_bind(), checkfirst=True)

    op.add_column('analysis_jobs', sa.Column('ground_filter_status', sa.Enum('not_requested', 'processing', 'completed', 'failed', name='ground_filter_status'), server_default='not_requested', nullable=False))
    op.add_column('analysis_jobs', sa.Column('ground_filter_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    # Hand-written: autogenerate does not diff native Postgres enum member
    # lists. New P1-3 AnalysisStage values, in pipeline order right after
    # Phase 4's 'validating_results' (and so before Phase 6's
    # 'loading_semantic_model').
    op.execute("ALTER TYPE analysis_stage ADD VALUE IF NOT EXISTS 'filtering_ground' AFTER 'validating_results'")
    op.execute("ALTER TYPE analysis_stage ADD VALUE IF NOT EXISTS 'writing_dtm' AFTER 'filtering_ground'")
    op.execute("ALTER TYPE analysis_stage ADD VALUE IF NOT EXISTS 'writing_ndsm' AFTER 'writing_dtm'")


def downgrade() -> None:
    op.drop_column('analysis_jobs', 'ground_filter_metadata')
    op.drop_column('analysis_jobs', 'ground_filter_status')
    sa.Enum(name='ground_filter_status').drop(op.get_bind(), checkfirst=True)
    # The three analysis_stage values added in upgrade() cannot be removed
    # (Postgres has no DROP VALUE for enums) — same limitation as every
    # earlier stage-adding migration.
