"""Add canonical unit to products.

Revision ID: 0002_product_unit
Revises: 0001_initial
Create Date: 2026-09-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_product_unit"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("unit", sa.String(32), nullable=False, server_default="шт"),
    )
    op.alter_column("products", "unit", server_default=None)


def downgrade() -> None:
    op.drop_column("products", "unit")
