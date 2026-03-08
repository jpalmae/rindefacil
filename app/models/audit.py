from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from app.extensions import db

class AuditLog(db.Model):
    __tablename__ = 'audit_logs'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = db.Column(UUID(as_uuid=True), db.ForeignKey('companies.id'), nullable=False)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=True)
    
    action = db.Column(db.String(100), nullable=False) # e.g., 'expense_created', 'report_approved'
    entity_type = db.Column(db.String(50)) # 'expense', 'report'
    entity_id = db.Column(UUID(as_uuid=True))
    
    description = db.Column(db.Text)
    changes = db.Column(JSONB) # { 'status': ['draft', 'submitted'] }
    
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    # Relaciones
    user = db.relationship('User', foreign_keys=[user_id])
    company = db.relationship('Company', foreign_keys=[company_id])

    def __repr__(self):
        return f'<AuditLog {self.action} by {self.user_id}>'
