from sqlalchemy.dialects.postgresql import UUID
import uuid
from app.extensions import db

class CostCenter(db.Model):
    __tablename__ = 'cost_centers'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = db.Column(UUID(as_uuid=True), db.ForeignKey('companies.id'), nullable=False)
    code = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    parent_id = db.Column(UUID(as_uuid=True), db.ForeignKey('cost_centers.id'), nullable=True)
    monthly_budget = db.Column(db.Numeric(14, 2), default=0)

    # Relaciones
    company = db.relationship('Company', back_populates='cost_centers')
    
    # Self-referential relationship for parent-child hierarchy
    children = db.relationship('CostCenter', backref=db.backref('parent', remote_side=[id]))
    
    users = db.relationship('User', back_populates='cost_center', foreign_keys='User.cost_center_id')
    expenses = db.relationship('Expense', back_populates='cost_center')

    def __repr__(self):
        return f'<CostCenter {self.code} - {self.name}>'
