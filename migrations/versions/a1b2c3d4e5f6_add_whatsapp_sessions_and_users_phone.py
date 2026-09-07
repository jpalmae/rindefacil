"""add whatsapp sessions and users.phone

Revision ID: a1b2c3d4e5f6
Revises: f6a9c3e5b7d2
Create Date: 2026-09-07 15:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "f6a9c3e5b7d2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("phone", sa.String(20), nullable=True))
    op.create_index("ix_users_phone", "users", ["phone"])

    op.create_table(
        "whatsapp_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("active_company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=True),
        sa.Column("state", sa.String(40), nullable=False, server_default="idle"),
        sa.Column("state_data", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_interaction_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_whatsapp_sessions_phone", "whatsapp_sessions", ["phone"], unique=True)

    op.create_table(
        "whatsapp_processed_events",
        sa.Column("event_key", sa.String(80), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("whatsapp_processed_events")
    op.drop_index("ix_whatsapp_sessions_phone", table_name="whatsapp_sessions")
    op.drop_table("whatsapp_sessions")
    op.drop_index("ix_users_phone", table_name="users")
    op.drop_column("users", "phone")
