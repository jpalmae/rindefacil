"""drop oidc_providers.auto_provision

Revision ID: c4d8e2f7b1a3
Revises: b8c2d4f6a9e1
Create Date: 2026-07-27 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c4d8e2f7b1a3"
down_revision = "b8c2d4f6a9e1"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("oidc_providers", "auto_provision")


def downgrade():
    op.add_column(
        "oidc_providers",
        sa.Column("auto_provision", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
