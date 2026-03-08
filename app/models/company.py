from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from app.extensions import db

class Company(db.Model):
    __tablename__ = 'companies'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(255), nullable=False)
    rut = db.Column(db.String(12))
    plan = db.Column(db.String(50), default='basic')
    settings = db.Column(JSONB, default=lambda: {})
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    # Relaciones
    users = db.relationship('User', back_populates='company', cascade='all, delete-orphan')
    categories = db.relationship('Category', back_populates='company', cascade='all, delete-orphan')
    cost_centers = db.relationship('CostCenter', back_populates='company', cascade='all, delete-orphan')
    expenses = db.relationship('Expense', back_populates='company')
    reports = db.relationship('Report', back_populates='company')

    def __repr__(self):
        return f'<Company {self.name}>'
