"""Keep legacy total expenditure, add training and dated body measurements."""
from alembic import op
import sqlalchemy as sa

revision = "0011_resting_energy"
down_revision = "0010_daily_energy"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("mobile_daily_energy", "spent_kcal", existing_type=sa.Numeric(9, 2), nullable=True)
    op.add_column("mobile_daily_energy", sa.Column("training_kcal", sa.Numeric(9, 2), nullable=True))
    op.create_table("mobile_energy_profiles",
        sa.Column("effective_date", sa.Date(), primary_key=True),
        sa.Column("weight_kg", sa.Numeric(5, 2), nullable=False),
        sa.Column("height_cm", sa.Numeric(5, 2), nullable=False))


def downgrade():
    # Training records cannot be represented in the legacy schema.
    op.execute("DELETE FROM mobile_daily_energy WHERE spent_kcal IS NULL")
    op.drop_table("mobile_energy_profiles")
    op.drop_column("mobile_daily_energy", "training_kcal")
    op.alter_column("mobile_daily_energy", "spent_kcal", existing_type=sa.Numeric(9, 2), nullable=False)
