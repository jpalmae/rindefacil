"""add company timezone and base currency

Revision ID: d5f8a2c6e4b1
Revises: c4d8e2f7b1a3
Create Date: 2026-08-27 15:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d5f8a2c6e4b1"
down_revision = "c4d8e2f7b1a3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "companies",
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="America/Santiago"),
    )
    op.add_column(
        "companies",
        sa.Column("base_currency", sa.String(length=3), nullable=False, server_default="CLP"),
    )


def downgrade():
    op.drop_column("companies", "base_currency")
    op.drop_column("companies", "timezone")
