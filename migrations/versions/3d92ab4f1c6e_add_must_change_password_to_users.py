"""add must_change_password to users

Revision ID: 3d92ab4f1c6e
Revises: 0f52d1b4e91a
Create Date: 2026-04-20 17:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "3d92ab4f1c6e"
down_revision = "0f52d1b4e91a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("users", "must_change_password", server_default=None)


def downgrade():
    op.drop_column("users", "must_change_password")
