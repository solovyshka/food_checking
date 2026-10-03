"""Mobile diary metadata and retry protection; existing entries stay intact."""
from alembic import op
import sqlalchemy as sa

revision = "0006_mobile_diary"
down_revision = "0005_inventory_transcripts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("mobile_entry_details",
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("consumption_entries.id"), primary_key=True),
        sa.Column("meal", sa.String(16), nullable=False),
        sa.Column("nutrition_source", sa.String(16), nullable=False))
    op.create_table("mobile_submissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_consumption_entries_status_date", "consumption_entries", ["status", "entry_date"])


def downgrade():
    op.drop_index("ix_consumption_entries_status_date", table_name="consumption_entries")
    op.drop_table("mobile_submissions")
    op.drop_table("mobile_entry_details")
