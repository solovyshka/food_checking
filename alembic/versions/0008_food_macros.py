"""Keep per-100 macronutrients for parsed and manually entered food."""
from alembic import op
import sqlalchemy as sa

revision = "0008_food_macros"
down_revision = "0007_grok_food_analysis"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("mobile_entry_details", "mobile_parsed_nutrition"):
        for field in ("protein_per_100g", "fat_per_100g", "carbs_per_100g"):
            op.add_column(table, sa.Column(field, sa.Numeric(5, 2), nullable=True))
        op.add_column(table, sa.Column("macros_source", sa.String(16), nullable=False, server_default="estimate"))


def downgrade():
    for table in ("mobile_entry_details", "mobile_parsed_nutrition"):
        for field in ("macros_source", "carbs_per_100g", "fat_per_100g", "protein_per_100g"):
            op.drop_column(table, field)
