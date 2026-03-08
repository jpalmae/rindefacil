from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.sql import func
import uuid
from app.extensions import db

class ExpenseStatus:
    DRAFT = 'draft'
    SUBMITTED = 'submitted'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    PAID = 'paid'

class ExpenseType:
    RECEIPT = 'receipt'
    PER_DIEM = 'per_diem'
    ADVANCE = 'advance'

class Expense(db.Model):
    __tablename__ = 'expenses'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    company_id = db.Column(UUID(as_uuid=True), db.ForeignKey('companies.id'), nullable=False)
    category_id = db.Column(UUID(as_uuid=True), db.ForeignKey('categories.id'), nullable=True)
    cost_center_id = db.Column(UUID(as_uuid=True), db.ForeignKey('cost_centers.id'), nullable=True)
    report_id = db.Column(UUID(as_uuid=True), db.ForeignKey('reports.id'), nullable=True)
    
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    currency = db.Column(db.String(3), default='CLP')
    exchange_rate = db.Column(db.Numeric(10, 4), default=1)
    date = db.Column(db.Date, nullable=False)
    receipt_time = db.Column(db.Time)
    description = db.Column(db.Text)
    merchant = db.Column(db.String(255))
    client_partner = db.Column(db.String(255))
    
    receipt_url = db.Column(db.String(500))
    receipt_hash = db.Column(db.String(64))
    ocr_raw_data = db.Column(JSONB)

    gps_latitude = db.Column(db.Numeric(10, 7))
    gps_longitude = db.Column(db.Numeric(10, 7))
    gps_accuracy_m = db.Column(db.Numeric(10, 2))
    gps_captured_at = db.Column(db.DateTime(timezone=True))
    gps_address = db.Column(db.String(500))
    gps_validation_status = db.Column(db.String(20), nullable=False, default='unknown')
    gps_validation_score = db.Column(db.Numeric(4, 2), nullable=False, default=0)
    gps_validation_reason = db.Column(db.String(120))
    gps_validation_meta = db.Column(JSONB)
    
    is_duplicate = db.Column(db.Boolean, default=False)
    duplicate_of_id = db.Column(UUID(as_uuid=True), db.ForeignKey('expenses.id'), nullable=True)
    
    status = db.Column(db.String(30), default=ExpenseStatus.DRAFT)
    expense_type = db.Column(db.String(30), default=ExpenseType.RECEIPT)
    tags = db.Column(ARRAY(db.String))
    metadata_json = db.Column(JSONB) # named metadata_json since metadata is reserved in SQLAlchemy
    
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relaciones
    user = db.relationship('User', back_populates='expenses', foreign_keys=[user_id])
    company = db.relationship('Company', back_populates='expenses')
    category = db.relationship('Category', back_populates='expenses')
    cost_center = db.relationship('CostCenter', back_populates='expenses')

    # Self reference for duplicates
    duplicate_of = db.relationship('Expense', remote_side=[id], backref='duplicated_by')

    def __repr__(self):
        return f'<Expense {self.id} - {self.amount} {self.currency}>'
