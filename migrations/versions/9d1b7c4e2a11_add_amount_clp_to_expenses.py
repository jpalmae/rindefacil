"""add amount_clp to expenses

Revision ID: 9d1b7c4e2a11
Revises: f2c4d3a1b8e7
Create Date: 2026-03-12 11:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9d1b7c4e2a11"
down_revision = "f2c4d3a1b8e7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "expenses",
        sa.Column("amount_clp", sa.Numeric(precision=14, scale=2), nullable=True),
    )

    op.execute("UPDATE expenses SET currency = COALESCE(currency, 'CLP')")
    op.execute("UPDATE expenses SET exchange_rate = COALESCE(exchange_rate, 1)")
    op.execute(
        """
        UPDATE expenses
        SET amount_clp = CASE
            WHEN currency = 'USD' THEN ROUND(amount * exchange_rate, 2)
            ELSE amount
        END
        """
    )

    op.alter_column("expenses", "amount_clp", nullable=False)


def downgrade():
    op.drop_column("expenses", "amount_clp")
