"""Personal products and recipes with immutable diary references."""
from alembic import op
import sqlalchemy as sa

revision = "0013_food_library"
down_revision = "0012_mobile_users"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("mobile_library_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.add_column("mobile_entry_details", sa.Column("library_ref", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("mobile_entry_details", "library_ref")
    op.drop_table("mobile_library_items")
