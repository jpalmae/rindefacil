from sqlalchemy.dialects.postgresql import UUID
import uuid
from app.extensions import db

class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = db.Column(UUID(as_uuid=True), db.ForeignKey('companies.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    icon = db.Column(db.String(50))
    account_code = db.Column(db.String(20))
    is_active = db.Column(db.Boolean, default=True)

    # Relaciones
    company = db.relationship('Company', back_populates='categories')
    expenses = db.relationship('Expense', back_populates='category')

    def __repr__(self):
        return f'<Category {self.name}>'
