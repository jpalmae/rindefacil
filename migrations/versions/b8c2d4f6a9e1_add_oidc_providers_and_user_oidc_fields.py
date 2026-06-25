"""add oidc providers and user oidc fields

Revision ID: b8c2d4f6a9e1
Revises: e7f9a1c2b3d4
Create Date: 2026-06-23 00:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b8c2d4f6a9e1"
down_revision = "e7f9a1c2b3d4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "oidc_providers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("client_id", sa.String(length=512), nullable=False),
        sa.Column("client_secret", sa.String(length=1024), nullable=False),
        sa.Column("discovery_url", sa.String(length=1024), nullable=False),
        sa.Column("scopes", sa.String(length=512), nullable=False, server_default="openid profile email"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_provision", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allowed_domains", sa.Text(), nullable=True),
        sa.Column("icon_slug", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "slug", name="uq_oidc_providers_company_slug"),
    )
    op.create_index(
        op.f("ix_oidc_providers_company_id"),
        "oidc_providers",
        ["company_id"],
    )

    op.add_column(
        "users",
        sa.Column("oidc_subject", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("auth_source", sa.String(length=32), nullable=False, server_default="local"),
    )
    op.create_index(
        op.f("ix_users_oidc_subject"),
        "users",
        ["oidc_subject"],
    )
    op.alter_column("users", "auth_source", server_default=None)


def downgrade():
    op.drop_index(op.f("ix_users_oidc_subject"), table_name="users")
    op.drop_column("users", "auth_source")
    op.drop_column("users", "oidc_subject")

    op.drop_index(op.f("ix_oidc_providers_company_id"), table_name="oidc_providers")
    op.drop_table("oidc_providers")
