"""Persistent photos, Grok jobs and separate food/nutrition tables."""
from alembic import op
import sqlalchemy as sa

revision = "0007_grok_food_analysis"
down_revision = "0006_mobile_diary"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("mobile_images",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("mobile_transcript_details",
        sa.Column("transcript_id", sa.Integer(), sa.ForeignKey("consumption_transcripts.id"), primary_key=True),
        sa.Column("image_id", sa.String(36), sa.ForeignKey("mobile_images.id"), nullable=False))
    op.create_table("mobile_parse_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("nonce", sa.String(36), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("mobile_parsed_foods",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("mobile_parse_jobs.id"), nullable=False),
        sa.Column("transcript_id", sa.Integer(), sa.ForeignKey("consumption_transcripts.id"), nullable=False),
        sa.Column("local_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("amount", sa.Numeric(12, 3), nullable=True),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("amount_is_estimate", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("job_id", "transcript_id", "local_id", name="uq_mobile_food_job_local"))
    op.create_index("ix_mobile_parsed_foods_job_id", "mobile_parsed_foods", ["job_id"])
    op.create_table("mobile_parsed_nutrition",
        sa.Column("food_id", sa.Integer(), sa.ForeignKey("mobile_parsed_foods.id"), primary_key=True),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=True),
        sa.Column("unit", sa.String(2), nullable=False),
        sa.Column("kcal_per_100g", sa.Numeric(8, 2), nullable=True),
        sa.Column("nutrition_source", sa.String(16), nullable=False),
        sa.Column("portion_is_estimate", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False))


def downgrade():
    op.drop_table("mobile_parsed_nutrition")
    op.drop_index("ix_mobile_parsed_foods_job_id", table_name="mobile_parsed_foods")
    op.drop_table("mobile_parsed_foods")
    op.drop_table("mobile_parse_jobs")
    op.drop_table("mobile_transcript_details")
    op.drop_table("mobile_images")
