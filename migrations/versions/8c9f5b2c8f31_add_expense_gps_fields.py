"""add expense gps fields

Revision ID: 8c9f5b2c8f31
Revises: 34af5f22e8a1
Create Date: 2026-03-08 19:45:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "8c9f5b2c8f31"
down_revision = "34af5f22e8a1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("expenses", sa.Column("gps_latitude", sa.Numeric(precision=10, scale=7), nullable=True))
    op.add_column("expenses", sa.Column("gps_longitude", sa.Numeric(precision=10, scale=7), nullable=True))
    op.add_column("expenses", sa.Column("gps_accuracy_m", sa.Numeric(precision=10, scale=2), nullable=True))
    op.add_column("expenses", sa.Column("gps_captured_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("expenses", sa.Column("gps_address", sa.String(length=500), nullable=True))
    op.add_column(
        "expenses",
        sa.Column("gps_validation_status", sa.String(length=20), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "expenses",
        sa.Column("gps_validation_score", sa.Numeric(precision=4, scale=2), nullable=False, server_default="0"),
    )
    op.add_column("expenses", sa.Column("gps_validation_reason", sa.String(length=120), nullable=True))
    op.add_column("expenses", sa.Column("gps_validation_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.alter_column("expenses", "gps_validation_status", server_default=None)
    op.alter_column("expenses", "gps_validation_score", server_default=None)


def downgrade():
    op.drop_column("expenses", "gps_validation_meta")
    op.drop_column("expenses", "gps_validation_reason")
    op.drop_column("expenses", "gps_validation_score")
    op.drop_column("expenses", "gps_validation_status")
    op.drop_column("expenses", "gps_address")
    op.drop_column("expenses", "gps_captured_at")
    op.drop_column("expenses", "gps_accuracy_m")
    op.drop_column("expenses", "gps_longitude")
    op.drop_column("expenses", "gps_latitude")
