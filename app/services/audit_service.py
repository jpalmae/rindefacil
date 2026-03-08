from flask import request
from flask_login import current_user
from app.extensions import db
from app.models.audit import AuditLog

def log_action(action, entity_type=None, entity_id=None, description=None, changes=None):
    """
    Utility function to log an action in the audit trail.
    """
    try:
        log = AuditLog(
            company_id=current_user.company_id,
            user_id=current_user.id if current_user.is_authenticated else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            description=description,
            changes=changes,
            ip_address=request.remote_addr if request else None,
            user_agent=request.user_agent.string if request and request.user_agent else None
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        # We don't want audit logging failures to crash the main process
        print(f"Failed to log audit action {action}: {e}")
        db.session.rollback()
