"""Barcode catalog and isolated label-reading jobs; preserve all diary records."""
from alembic import op
import sqlalchemy as sa

revision = "0009_barcode_catalog"
down_revision = "0008_food_macros"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("mobile_entry_details", sa.Column("barcode", sa.String(14), nullable=True))
    op.create_table("mobile_barcode_products",
        sa.Column("barcode", sa.String(14), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("unit", sa.String(2), nullable=False),
        sa.Column("kcal_per_100g", sa.Numeric(6, 2)),
        sa.Column("protein_per_100g", sa.Numeric(5, 2)),
        sa.Column("fat_per_100g", sa.Numeric(5, 2)),
        sa.Column("carbs_per_100g", sa.Numeric(5, 2)),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_url", sa.String(255)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table("mobile_barcode_label_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("barcode", sa.String(14), nullable=False),
        sa.Column("image_id", sa.String(36), sa.ForeignKey("mobile_images.id"), nullable=False),
        sa.Column("image_url", sa.Text()),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("nonce", sa.String(36), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result", sa.JSON()), sa.Column("result_hash", sa.String(64)), sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("mobile_barcode_label_jobs")
    op.drop_table("mobile_barcode_products")
    op.drop_column("mobile_entry_details", "barcode")
