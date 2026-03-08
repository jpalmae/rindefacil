from flask_mail import Message
from app.extensions import mail
from flask import current_app, has_request_context
from flask_login import current_user


def _company_branding(company=None):
    app_name = current_app.config.get('APP_NAME', 'Rinde Fácil')
    base_url = current_app.config.get('APP_URL', 'http://localhost:5000')

    settings = {}
    if company is not None:
        settings = company.settings or {}
    elif has_request_context() and current_user.is_authenticated:
        settings = current_user.company.settings or {}

    app_name = settings.get('brand_app_name') or app_name

    return app_name, base_url.rstrip('/')

def send_email(subject, recipients, body_text, body_html=None):
    """
    Sends an email using Flask-Mail.
    """
    if not current_app.config.get('MAIL_SERVER'):
        current_app.logger.warning("Email service not configured. Skip sending.")
        return False

    try:
        app_name, _ = _company_branding()
        msg = Message(
            subject=f"[{app_name}] {subject}",
            recipients=recipients,
            body=body_text,
            html=body_html
        )
        mail.send(msg)
        return True
    except Exception as e:
        current_app.logger.error(f"Error sending email: {str(e)}")
        return False

def send_approval_request_email(user, report):
    app_name, base_url = _company_branding(report.company)
    report_path = f"/reports/{report.id}"
    subject = f"Acción requerida: Aprobación de Informe #{report.id.hex[:8]}"
    body_text = (
        f"Hola {user.full_name},\n\n"
        f"Tienes un informe de gastos pendiente de aprobación: {report.title} por {report.user.full_name}.\n\n"
        f"Revisa en {app_name}: {base_url}{report_path}"
    )
    # We could use a localized template here
    return send_email(subject, [user.email], body_text)

def send_report_status_email(user, report, status, reason=None):
    app_name, base_url = _company_branding(report.company)
    report_path = f"/reports/{report.id}"
    subject = f"Tu informe ha sido {status}"
    body_text = (
        f"Hola {user.full_name},\n\n"
        f"Tu informe {report.title} ha sido {status}.\n\n"
        f"Ver detalle en {app_name}: {base_url}{report_path}"
    )
    if reason:
        body_text += f"\nMotivo: {reason}"
    return send_email(subject, [user.email], body_text)
