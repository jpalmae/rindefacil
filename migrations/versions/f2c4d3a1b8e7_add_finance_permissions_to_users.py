"""add finance permissions to users

Revision ID: f2c4d3a1b8e7
Revises: 7bb5df46f8d1
Create Date: 2026-03-10 11:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f2c4d3a1b8e7"
down_revision = "7bb5df46f8d1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("can_view_approved_reports", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users",
        sa.Column("can_mark_reimbursements_paid", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("users", "can_view_approved_reports", server_default=None)
    op.alter_column("users", "can_mark_reimbursements_paid", server_default=None)


def downgrade():
    op.drop_column("users", "can_mark_reimbursements_paid")
    op.drop_column("users", "can_view_approved_reports")
