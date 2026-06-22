from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from flask_login import UserMixin
from app.extensions import db

class UserRole:
    SUPERADMIN = 'superadmin'
    ADMIN = 'admin'
    MANAGER = 'manager'
    APPROVER = 'approver'
    REVIEWER = 'reviewer'
    EMPLOYEE = 'employee'

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = db.Column(UUID(as_uuid=True), db.ForeignKey('companies.id'), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default=UserRole.EMPLOYEE)
    cost_center_id = db.Column(UUID(as_uuid=True), db.ForeignKey('cost_centers.id'), nullable=True)
    manager_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=True)
    monthly_limit = db.Column(db.Numeric(14, 2), nullable=True)
    can_view_approved_reports = db.Column(db.Boolean, nullable=False, default=False)
    can_mark_reimbursements_paid = db.Column(db.Boolean, nullable=False, default=False)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    mfa_enabled = db.Column(db.Boolean, nullable=False, default=False)
    avatar_url = db.Column(db.String(500))
    signature_url = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    # Relaciones
    company = db.relationship('Company', back_populates='users')
    cost_center = db.relationship('CostCenter', back_populates='users', foreign_keys=[cost_center_id])
    
    # Manager / Subordinates
    manager = db.relationship('User', remote_side=[id], backref=db.backref('subordinates', lazy='dynamic'))
    
    # Expenses
    # Expenses and Reports
    expenses = db.relationship('Expense', back_populates='user', foreign_keys='Expense.user_id')
    reports = db.relationship('Report', back_populates='user', lazy='dynamic')
    api_keys = db.relationship('UserApiKey', back_populates='user', lazy='dynamic', cascade='all, delete-orphan')

    def check_password(self, password):
        import bcrypt
        return bcrypt.checkpw(password.encode('utf-8'), self.password_hash.encode('utf-8'))

    def set_password(self, password):
        import bcrypt
        self.password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    @property
    def is_admin(self):
        return self.role in [UserRole.SUPERADMIN, UserRole.ADMIN]

    @property
    def has_finance_report_access(self):
        return self.is_admin or self.can_view_approved_reports or self.can_mark_reimbursements_paid

    @property
    def can_process_reimbursements(self):
        return self.is_admin or self.can_mark_reimbursements_paid

    def has_role(self, role_name):
        return self.role == role_name

    def __repr__(self):
        return f'<User {self.email}>'
