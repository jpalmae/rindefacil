from flask_mail import Message
from app.extensions import mail
from flask import current_app, has_request_context
from flask_login import current_user
import requests

EMAIL_EVENT_SETTINGS = {
    'report_created': 'email_notify_report_created',
    'report_submitted': 'email_notify_report_submitted',
    'approval_needed': 'email_notify_approval_needed',
    'report_approved': 'email_notify_report_approved',
    'report_rejected': 'email_notify_report_rejected',
    'report_info_requested': 'email_notify_report_info_requested',
    'report_paid': 'email_notify_report_paid',
}

EMAIL_EVENT_DEFAULTS = {
    'email_notify_report_created': False,
    'email_notify_report_submitted': True,
    'email_notify_approval_needed': True,
    'email_notify_report_approved': True,
    'email_notify_report_rejected': True,
    'email_notify_report_info_requested': True,
    'email_notify_report_paid': True,
}


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


def _company_email_settings(company=None):
    settings = {}
    if company is not None:
        settings = dict(company.settings or {})
    elif has_request_context() and current_user.is_authenticated:
        settings = dict(current_user.company.settings or {})

    return {
        'enabled': bool(settings.get('email_enabled')),
        'provider': (settings.get('email_provider') or 'resend').strip().lower(),
        'from_name': (settings.get('email_from_name') or settings.get('brand_app_name') or current_app.config.get('APP_NAME', 'Rinde Fácil')).strip(),
        'from_address': (settings.get('email_from_address') or current_app.config.get('MAIL_DEFAULT_SENDER') or '').strip(),
        'reply_to': (settings.get('email_reply_to') or '').strip(),
        'resend_api_key': (settings.get('email_resend_api_key') or '').strip(),
        'test_recipient': (settings.get('email_test_recipient') or '').strip(),
        **{key: bool(settings.get(key, default)) for key, default in EMAIL_EVENT_DEFAULTS.items()},
    }


def get_company_email_settings_view(company):
    config = _company_email_settings(company)
    return {
        **config,
        'has_api_key': bool(config['resend_api_key']),
        'masked_api_key': _mask_secret(config['resend_api_key']),
    }


def _mask_secret(value):
    if not value:
        return ''
    if len(value) <= 8:
        return '*' * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def company_email_event_enabled(company, event_name):
    config = _company_email_settings(company)
    setting_key = EMAIL_EVENT_SETTINGS.get(event_name)
    if not config['enabled'] or not setting_key:
        return False
    return bool(config.get(setting_key))


def _build_sender(from_name, from_address):
    return f"{from_name} <{from_address}>" if from_name else from_address


def _send_via_resend(company, subject, recipients, body_text, body_html=None, reply_to=None):
    config = _company_email_settings(company)
    api_key = config['resend_api_key']
    if not api_key:
        current_app.logger.warning('Resend API key missing. Skip sending email.')
        return False
    if not config['from_address']:
        current_app.logger.warning('Resend sender address missing. Skip sending email.')
        return False

    payload = {
        'from': _build_sender(config['from_name'], config['from_address']),
        'to': recipients,
        'subject': subject,
        'text': body_text,
    }
    if body_html:
        payload['html'] = body_html
    if reply_to or config['reply_to']:
        payload['reply_to'] = reply_to or config['reply_to']

    try:
        response = requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=20,
        )
        if response.status_code not in (200, 201):
            current_app.logger.error(f'Resend error {response.status_code}: {response.text}')
            return False
        return True
    except Exception as e:
        current_app.logger.error(f'Error sending email via Resend: {str(e)}')
        return False


def _send_via_smtp(subject, recipients, body_text, body_html=None):
    if not current_app.config.get('MAIL_SERVER'):
        current_app.logger.warning('SMTP email service not configured. Skip sending.')
        return False

    try:
        msg = Message(
            subject=subject,
            recipients=recipients,
            body=body_text,
            html=body_html,
        )
        mail.send(msg)
        return True
    except Exception as e:
        current_app.logger.error(f'Error sending email via SMTP: {str(e)}')
        return False


def send_email(subject, recipients, body_text, body_html=None, company=None, reply_to=None):
    if not recipients:
        return False

    config = _company_email_settings(company)
    if not config['enabled']:
        current_app.logger.info('Company email notifications disabled. Skip sending email.')
        return False

    app_name, _ = _company_branding(company)
    branded_subject = f'[{app_name}] {subject}'

    if config['provider'] == 'resend':
        return _send_via_resend(company, branded_subject, recipients, body_text, body_html=body_html, reply_to=reply_to)

    return _send_via_smtp(branded_subject, recipients, body_text, body_html=body_html)


def send_test_email(company, recipient):
    if not recipient:
        return False
    app_name, _ = _company_branding(company)
    subject = 'Prueba de configuración de email'
    body_text = (
        f'Hola,\n\n'
        f'Este es un correo de prueba enviado desde {app_name}.\n\n'
        f'Si recibiste este mensaje, la configuración de Resend quedó operativa.'
    )
    body_html = (
        f'<p>Hola,</p>'
        f'<p>Este es un correo de prueba enviado desde <strong>{app_name}</strong>.</p>'
        f'<p>Si recibiste este mensaje, la configuración de Resend quedó operativa.</p>'
    )
    return send_email(subject, [recipient], body_text, body_html=body_html, company=company)


def send_report_created_email(user, report):
    app_name, base_url = _company_branding(report.company)
    subject = f'Rendición creada: {report.title}'
    path = f'/reports/{report.id}'
    body_text = (
        f'Hola {user.full_name},\n\n'
        f'Tu rendición "{report.title}" fue creada en estado borrador.\n\n'
        f'Revísala en {app_name}: {base_url}{path}'
    )
    return send_email(subject, [user.email], body_text, company=report.company)


def send_report_submitted_email(user, report):
    app_name, base_url = _company_branding(report.company)
    subject = f'Rendición enviada: {report.title}'
    path = f'/reports/{report.id}'
    body_text = (
        f'Hola {user.full_name},\n\n'
        f'Tu rendición "{report.title}" fue enviada al flujo de aprobación.\n\n'
        f'Ver detalle en {app_name}: {base_url}{path}'
    )
    return send_email(subject, [user.email], body_text, company=report.company)


def send_approval_request_email(user, report):
    app_name, base_url = _company_branding(report.company)
    report_path = f'/reports/{report.id}'
    subject = f'Acción requerida: Aprobación de rendición #{report.id.hex[:8]}'
    body_text = (
        f'Hola {user.full_name},\n\n'
        f'Tienes una rendición pendiente de aprobación: {report.title} de {report.user.full_name}.\n\n'
        f'Revisa en {app_name}: {base_url}{report_path}'
    )
    return send_email(subject, [user.email], body_text, company=report.company)


def send_report_status_email(user, report, status, reason=None):
    app_name, base_url = _company_branding(report.company)
    report_path = f'/reports/{report.id}'
    subject = f'Tu rendición fue {status}'
    body_text = (
        f'Hola {user.full_name},\n\n'
        f'Tu rendición "{report.title}" fue {status}.\n\n'
        f'Ver detalle en {app_name}: {base_url}{report_path}'
    )
    if reason:
        body_text += f'\nMotivo: {reason}'
    return send_email(subject, [user.email], body_text, company=report.company)


def send_report_paid_email(user, report):
    app_name, base_url = _company_branding(report.company)
    report_path = f'/reports/{report.id}'
    subject = f'Rendición pagada: {report.title}'
    body_text = (
        f'Hola {user.full_name},\n\n'
        f'Tu rendición "{report.title}" fue marcada como pagada.\n\n'
        f'Ver detalle en {app_name}: {base_url}{report_path}'
    )
    return send_email(subject, [user.email], body_text, company=report.company)
