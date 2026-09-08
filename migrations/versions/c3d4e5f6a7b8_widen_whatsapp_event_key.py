"""widen whatsapp_processed_events.event_key to 255

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-09-08 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "whatsapp_processed_events",
        "event_key",
        existing_type=sa.String(80),
        type_=sa.String(255),
        existing_nullable=False,
    )


def downgrade():
    op.alter_column(
        "whatsapp_processed_events",
        "event_key",
        existing_type=sa.String(255),
        type_=sa.String(80),
        existing_nullable=False,
    )
