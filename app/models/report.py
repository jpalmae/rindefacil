from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from app.extensions import db

class ReportStatus:
    DRAFT = 'draft'
    SUBMITTED = 'submitted'
    UNDER_REVIEW = 'under_review'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    PAID = 'paid'

class Report(db.Model):
    __tablename__ = 'reports'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = db.Column(UUID(as_uuid=True), db.ForeignKey('companies.id'), nullable=False)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(30), default=ReportStatus.DRAFT)
    
    total_amount = db.Column(db.Numeric(14, 2), default=0)
    currency = db.Column(db.String(3), default='CLP')
    
    # Phase 3: Configurable Approval Flows
    approval_flow_id = db.Column(UUID(as_uuid=True), db.ForeignKey('approval_flows.id'), nullable=True)
    current_step = db.Column(db.Integer, default=0)
    
    submitted_at = db.Column(db.DateTime(timezone=True))
    approved_at = db.Column(db.DateTime(timezone=True))
    paid_at = db.Column(db.DateTime(timezone=True))
    
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relaciones
    user = db.relationship('User', back_populates='reports')
    company = db.relationship('Company', back_populates='reports')
    
    # 1 to N with Expense
    expenses = db.relationship('Expense', backref='report', lazy='dynamic')
    approval_flow = db.relationship('ApprovalFlow', back_populates='reports')
    decisions = db.relationship('ApprovalDecision', back_populates='report', cascade='all, delete-orphan', order_by='ApprovalDecision.decided_at.desc()')

    def __repr__(self):
        return f'<Report {self.title}>'
