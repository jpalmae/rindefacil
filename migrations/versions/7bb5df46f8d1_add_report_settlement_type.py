"""add report settlement type

Revision ID: 7bb5df46f8d1
Revises: c1f84ec25e71
Create Date: 2026-03-09 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7bb5df46f8d1"
down_revision = "c1f84ec25e71"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "reports",
        sa.Column(
            "settlement_type",
            sa.String(length=50),
            nullable=False,
            server_default="employee_reimbursement",
        ),
    )
    op.alter_column("reports", "settlement_type", server_default=None)


def downgrade():
    op.drop_column("reports", "settlement_type")
