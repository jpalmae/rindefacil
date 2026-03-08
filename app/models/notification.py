from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from app.extensions import db

class Notification(db.Model):
    __tablename__ = 'notifications'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    
    # Types: 'approval_pending', 'report_approved', 'report_rejected', 'policy_violation'
    type = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text, nullable=False)
    link = db.Column(db.String(500)) # Link to the relevant resource (e.g. report show page)
    
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = db.relationship('User', backref=db.backref('notifications', lazy='dynamic', cascade='all, delete-orphan'))

    def __repr__(self):
        return f'<Notification {self.type} to {self.user_id}>'
