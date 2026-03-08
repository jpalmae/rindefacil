from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from app.extensions import db

class Policy(db.Model):
    __tablename__ = 'policies'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = db.Column(UUID(as_uuid=True), db.ForeignKey('companies.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    
    # Podría contener reglas como: max_amount_per_expense, require_receipt_above, etc.
    rules = db.Column(JSONB, default=lambda: {})
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    company = db.relationship('Company', backref=db.backref('policies', lazy='dynamic'))

    def __repr__(self):
        return f'<Policy {self.name}>'
