from app.models import Notification, User
from app.extensions import db
from flask import url_for
from app.services.email_service import (
    company_email_event_enabled,
    send_approval_request_email,
    send_report_created_email,
    send_report_paid_email,
    send_report_status_email,
    send_report_submitted_email,
)


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


def notify_report_created(report):
    notification = create_notification(
        user_id=report.user_id,
        type='report_created',
        title='Rendición Creada',
        message=f'Tu rendición "{report.title}" fue creada en borrador.',
        link=url_for('reports.show', id=report.id)
    )
    if company_email_event_enabled(report.company, 'report_created'):
        send_report_created_email(report.user, report)
    return notification


def notify_report_submitted(report):
    notification = create_notification(
        user_id=report.user_id,
        type='report_submitted',
        title='Rendición Enviada',
        message=f'Tu rendición "{report.title}" fue enviada al flujo de aprobación.',
        link=url_for('reports.show', id=report.id)
    )
    if company_email_event_enabled(report.company, 'report_submitted'):
        send_report_submitted_email(report.user, report)
    return notification


def notify_approval_needed(user_id, report):
    notification = create_notification(
        user_id=user_id,
        type='approval_pending',
        title='Aprobación Pendiente',
        message=f'La rendición "{report.title}" de {report.user.full_name} requiere tu revisión.',
        link=url_for('reports.show', id=report.id)
    )
    user = User.query.get(user_id)
    if user and company_email_event_enabled(report.company, 'approval_needed'):
        send_approval_request_email(user, report)
    return notification


def notify_report_approved(report):
    notification = create_notification(
        user_id=report.user_id,
        type='report_approved',
        title='Rendición Aprobada',
        message=f'Tu rendición "{report.title}" ha sido aprobada completamente.',
        link=url_for('reports.show', id=report.id)
    )
    if company_email_event_enabled(report.company, 'report_approved'):
        send_report_status_email(report.user, report, 'aprobada')
    return notification


def notify_report_rejected(report, reason):
    notification = create_notification(
        user_id=report.user_id,
        type='report_rejected',
        title='Rendición Rechazada',
        message=f'Tu rendición "{report.title}" fue rechazada. Motivo: {reason}',
        link=url_for('reports.show', id=report.id)
    )
    if company_email_event_enabled(report.company, 'report_rejected'):
        send_report_status_email(report.user, report, 'rechazada', reason)
    return notification



def notify_report_info_requested(report, reason):
    notification = create_notification(
        user_id=report.user_id,
        type='report_info_requested',
        title='Antecedentes Adicionales Solicitados',
        message=f'Tu rendición "{report.title}" requiere antecedentes adicionales. Motivo: {reason}',
        link=url_for('reports.show', id=report.id)
    )
    if company_email_event_enabled(report.company, 'report_info_requested'):
        send_report_status_email(report.user, report, 'observada para antecedentes adicionales', reason)
    return notification


def notify_report_paid(report):
    notification = create_notification(
        user_id=report.user_id,
        type='report_paid',
        title='Rendición Pagada',
        message=f'Tu rendición "{report.title}" fue marcada como pagada.',
        link=url_for('reports.show', id=report.id)
    )
    if company_email_event_enabled(report.company, 'report_paid'):
        send_report_paid_email(report.user, report)
    return notification
