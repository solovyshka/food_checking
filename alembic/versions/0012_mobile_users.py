"""Personal demographics and scoped callbacks; diaries live in separate schemas."""
from alembic import op
import sqlalchemy as sa

revision = "0012_mobile_users"
down_revision = "0011_resting_energy"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("mobile_person",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("sex", sa.String(8), nullable=True))
    # Existing owner keeps his previously configured demographic values.
    if op.get_context().opts.get("version_table_schema") is None:
        from datetime import date
        op.bulk_insert(sa.table("mobile_person", sa.column("id", sa.Integer()),
            sa.column("name", sa.String()), sa.column("birth_date", sa.Date()), sa.column("sex", sa.String())),
            [{"id":1,"name":"Мой дневник","birth_date":date(1994,8,27),"sex":"male"}])
    for table in ("mobile_parse_jobs", "mobile_barcode_label_jobs"):
        op.add_column(table, sa.Column("owner_id", sa.String(36), nullable=False, server_default="primary"))


def downgrade():
    for table in ("mobile_parse_jobs", "mobile_barcode_label_jobs"):
        op.drop_column(table, "owner_id")
    op.drop_table("mobile_person")
