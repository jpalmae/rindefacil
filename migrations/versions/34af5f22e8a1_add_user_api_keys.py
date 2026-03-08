"""add user api keys

Revision ID: 34af5f22e8a1
Revises: 0c0f21d62ab9
Create Date: 2026-03-08 14:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '34af5f22e8a1'
down_revision = '0c0f21d62ab9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'user_api_keys',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('key_prefix', sa.String(length=32), nullable=False),
        sa.Column('key_hash', sa.String(length=64), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key_hash'),
    )
    op.create_index(op.f('ix_user_api_keys_key_hash'), 'user_api_keys', ['key_hash'], unique=True)


def downgrade():
    op.drop_index(op.f('ix_user_api_keys_key_hash'), table_name='user_api_keys')
    op.drop_table('user_api_keys')

