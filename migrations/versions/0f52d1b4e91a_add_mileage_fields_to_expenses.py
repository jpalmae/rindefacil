"""add mileage fields to expenses

Revision ID: 0f52d1b4e91a
Revises: b4d6a7c9e2f1
Create Date: 2026-03-12 15:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0f52d1b4e91a"
down_revision = "b4d6a7c9e2f1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("expenses", sa.Column("distance_km", sa.Numeric(precision=10, scale=2), nullable=True))
    op.add_column("expenses", sa.Column("fuel_price_per_liter", sa.Numeric(precision=10, scale=2), nullable=True))
    op.add_column("expenses", sa.Column("vehicle_efficiency_km_l", sa.Numeric(precision=10, scale=2), nullable=True))
    op.add_column("expenses", sa.Column("correction_factor", sa.Numeric(precision=6, scale=4), nullable=True))


def downgrade():
    op.drop_column("expenses", "correction_factor")
    op.drop_column("expenses", "vehicle_efficiency_km_l")
    op.drop_column("expenses", "fuel_price_per_liter")
    op.drop_column("expenses", "distance_km")
