from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from app.extensions import db

class ApprovalFlow(db.Model):
    __tablename__ = 'approval_flows'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = db.Column(UUID(as_uuid=True), db.ForeignKey('companies.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    
    # Rules dict. e.g {"min_amount": 100000} dictating when this flow triggers
    trigger_rules = db.Column(JSONB, default=dict)
    
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    company = db.relationship('Company', backref='approval_flows')
    steps = db.relationship('ApprovalStep', back_populates='flow', cascade='all, delete-orphan', order_by='ApprovalStep.step_number')
    reports = db.relationship('Report', back_populates='approval_flow')


class ApprovalStep(db.Model):
    __tablename__ = 'approval_steps'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    flow_id = db.Column(UUID(as_uuid=True), db.ForeignKey('approval_flows.id'), nullable=False)
    step_number = db.Column(db.Integer, nullable=False)
    
    # Options: 'role' (e.g. 'manager', 'admin'), 'user' (specific user ID), 'department_head'
    approver_type = db.Column(db.String(50), nullable=False, default='role') 
    
    # Depending on the approver_type, this holds the target (e.g. 'admin' or 'uuid')
    approver_target = db.Column(db.String(255), nullable=False) 
    
    is_required = db.Column(db.Boolean, default=True)
    
    # Relationships
    flow = db.relationship('ApprovalFlow', back_populates='steps')


class ApprovalDecision(db.Model):
    """Immutable audit log of decisions made on reports"""
    __tablename__ = 'approval_decisions'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id = db.Column(UUID(as_uuid=True), db.ForeignKey('reports.id'), nullable=False)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    step_number = db.Column(db.Integer, nullable=False)
    
    # e.g 'approved', 'rejected'
    decision = db.Column(db.String(50), nullable=False)
    comments = db.Column(db.Text)
    
    decided_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    report = db.relationship('Report', back_populates='decisions')
    user = db.relationship('User')
