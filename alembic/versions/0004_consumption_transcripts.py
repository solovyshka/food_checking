"""Queue of consumption transcripts awaiting parse.

Revision ID: 0004_consumption_transcripts
Revises: 0003_consumption_kcal
Create Date: 2026-09-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_consumption_transcripts"
down_revision: Union[str, None] = "0003_consumption_kcal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consumption_transcripts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("meal_type", sa.String(32), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("telegram_message_id", sa.String(64), nullable=True),
        sa.Column("stt_backend", sa.String(32), nullable=True),
        sa.Column("parse_batch_id", sa.String(36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_consumption_transcripts_parse_batch_id",
        "consumption_transcripts",
        ["parse_batch_id"],
    )
    op.create_index(
        "ix_consumption_transcripts_status_period",
        "consumption_transcripts",
        ["status", "entry_date", "meal_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_consumption_transcripts_status_period",
        table_name="consumption_transcripts",
    )
    op.drop_index(
        "ix_consumption_transcripts_parse_batch_id",
        table_name="consumption_transcripts",
    )
    op.drop_table("consumption_transcripts")
