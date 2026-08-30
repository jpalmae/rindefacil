"""add index on users.email for multi-account lookups

Revision ID: f6a9c3e5b7d2
Revises: e7b3d9f1a5c4
Create Date: 2026-08-30 02:30:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "f6a9c3e5b7d2"
down_revision = "e7b3d9f1a5c4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_users_email", "users", ["email"])


def downgrade():
    op.drop_index("ix_users_email", table_name="users")
