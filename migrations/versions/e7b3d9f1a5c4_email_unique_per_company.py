"""email unique per company (multi-account)

Revision ID: e7b3d9f1a5c4
Revises: d5f8a2c6e4b1
Create Date: 2026-08-27 15:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e7b3d9f1a5c4"
down_revision = "d5f8a2c6e4b1"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("users_email_key", "users", type_="unique")
    op.create_unique_constraint("uq_users_company_email", "users", ["company_id", "email"])


def downgrade():
    op.drop_constraint("uq_users_company_email", "users", type_="unique")
    op.create_unique_constraint("users_email_key", "users", ["email"])
