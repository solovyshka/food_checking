"""Manual daily expenditure; no changes to food or barcode records."""
from alembic import op
import sqlalchemy as sa

revision = "0010_daily_energy"
down_revision = "0009_barcode_catalog"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("mobile_daily_energy",
        sa.Column("entry_date", sa.Date(), primary_key=True),
        sa.Column("spent_kcal", sa.Numeric(9, 2), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))


def downgrade():
    op.drop_table("mobile_daily_energy")
