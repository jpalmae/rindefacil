"""add subscriptions category

Revision ID: b4d6a7c9e2f1
Revises: 9d1b7c4e2a11
Create Date: 2026-03-12 13:30:00.000000
"""

from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision = "b4d6a7c9e2f1"
down_revision = "9d1b7c4e2a11"
branch_labels = None
depends_on = None


companies = sa.table(
    "companies",
    sa.column("id", UUID(as_uuid=True)),
)

categories = sa.table(
    "categories",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("company_id", UUID(as_uuid=True)),
    sa.column("name", sa.String),
    sa.column("is_active", sa.Boolean),
)

expenses = sa.table(
    "expenses",
    sa.column("category_id", UUID(as_uuid=True)),
)


def upgrade():
    bind = op.get_bind()

    company_ids = [row[0] for row in bind.execute(sa.select(companies.c.id))]
    for company_id in company_ids:
        exists = bind.execute(
            sa.select(categories.c.id).where(
                categories.c.company_id == company_id,
                categories.c.name == "Suscripciones",
            )
        ).first()
        if exists:
            continue

        bind.execute(
            sa.insert(categories).values(
                id=uuid4(),
                company_id=company_id,
                name="Suscripciones",
                is_active=True,
            )
        )


def downgrade():
    bind = op.get_bind()

    orphan_ids = [
        row[0]
        for row in bind.execute(
            sa.select(categories.c.id).where(
                categories.c.name == "Suscripciones",
                ~sa.exists(
                    sa.select(expenses.c.category_id).where(
                        expenses.c.category_id == categories.c.id
                    )
                ),
            )
        )
    ]
    if orphan_ids:
        bind.execute(sa.delete(categories).where(categories.c.id.in_(orphan_ids)))
