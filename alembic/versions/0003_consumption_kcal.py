"""Optional kcal per 100g on consumption entries.

Revision ID: 0003_consumption_kcal
Revises: 0002_product_unit
Create Date: 2026-09-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_consumption_kcal"
down_revision: Union[str, None] = "0002_product_unit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "consumption_entries",
        sa.Column("kcal_per_100g", sa.Numeric(8, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("consumption_entries", "kcal_per_100g")
