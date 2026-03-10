from app.models import Notification, User
from app.extensions import db
from flask import url_for
from app.services.email_service import send_approval_request_email, send_report_status_email

def create_notification(user_id, type, title, message, link=None):
    """
    Creates an in-app notification for a user.
    """
    try:
        notification = Notification(
            user_id=user_id,
            type=type,
            title=title,
            message=message,
            link=link
        )
        db.session.add(notification)
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        print(f"Error creating notification: {str(e)}")
        return False

def notify_report_submitted(report):
    """
    Notify potential approvers that a report is pending.
    """
    pass

def notify_approval_needed(user_id, report):
    notification = create_notification(
        user_id=user_id,
        type='approval_pending',
        title='Aprobación Pendiente',
        message=f'El informe "{report.title}" de {report.user.full_name} requiere tu revisión.',
        link=url_for('reports.show', id=report.id)
    )
    # Email alert
    user = User.query.get(user_id)
    if user:
        send_approval_request_email(user, report)
    return notification

def notify_report_approved(report):
    notification = create_notification(
        user_id=report.user_id,
        type='report_approved',
        title='Informe Aprobado',
        message=f'Tu informe "{report.title}" ha sido aprobado completamente.',
        link=url_for('reports.show', id=report.id)
    )
    # Email alert
    send_report_status_email(report.user, report, 'aprobado')
    return notification

def notify_report_rejected(report, reason):
    notification = create_notification(
        user_id=report.user_id,
        type='report_rejected',
        title='Informe Rechazado',
        message=f'Tu informe "{report.title}" fue rechazado. Motivo: {reason}',
        link=url_for('reports.show', id=report.id)
    )
    # Email alert
    send_report_status_email(report.user, report, 'rechazado', reason)
    return notification


def notify_report_info_requested(report, reason):
    notification = create_notification(
        user_id=report.user_id,
        type='report_info_requested',
        title='Antecedentes Adicionales Solicitados',
        message=f'Tu rendición "{report.title}" requiere antecedentes adicionales. Motivo: {reason}',
        link=url_for('reports.show', id=report.id)
    )
    send_report_status_email(report.user, report, 'observada para antecedentes adicionales', reason)
    return notification
