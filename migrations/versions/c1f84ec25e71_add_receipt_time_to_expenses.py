"""add receipt time to expenses

Revision ID: c1f84ec25e71
Revises: 8c9f5b2c8f31
Create Date: 2026-03-08 20:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c1f84ec25e71"
down_revision = "8c9f5b2c8f31"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("expenses", sa.Column("receipt_time", sa.Time(), nullable=True))


def downgrade():
    op.drop_column("expenses", "receipt_time")
