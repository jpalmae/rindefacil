from flask_mail import Message
from app.extensions import mail
from flask import current_app, has_request_context
from flask_login import current_user
import requests
from app.services.secrets_service import decrypt_setting

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

EMAIL_THEME_PALETTES = {
    'executive': {
        'header_bg': '#0f766e',
        'header_bg_2': '#115e59',
        'eyebrow_color': '#ecfdf5',
        'title_color': '#ffffff',
        'button_bg': '#0f766e',
    },
    'paper': {
        'header_bg': '#9a5d21',
        'header_bg_2': '#7c4212',
        'eyebrow_color': '#fffbeb',
        'title_color': '#ffffff',
        'button_bg': '#9a5d21',
    },
    'midnight': {
        'header_bg': '#0a1222',
        'header_bg_2': '#101a2e',
        'eyebrow_color': '#e0f2fe',
        'title_color': '#ffffff',
        'button_bg': '#0ea5b8',
    },
    'rose': {
        'header_bg': '#d14d87',
        'header_bg_2': '#b5366f',
        'eyebrow_color': '#fff1f7',
        'title_color': '#ffffff',
        'button_bg': '#d14d87',
    },
}


def _company_branding(company=None):
    app_name = current_app.config.get('APP_NAME', 'Rinde Fácil')
    base_url = current_app.config.get('APP_URL', 'http://localhost:5000')
    logo_url = ''

    settings = {}
    if company is not None:
        settings = company.settings or {}
    elif has_request_context() and current_user.is_authenticated:
        settings = current_user.company.settings or {}

    app_name = settings.get('brand_app_name') or app_name
    base_url = settings.get('brand_app_url') or base_url
    logo_url = settings.get('brand_logo_url') or ''

    return app_name, base_url.rstrip('/'), _absolute_brand_asset_url(logo_url, base_url)


def _company_email_theme(company=None):
    settings = {}
    if company is not None:
        settings = company.settings or {}
    elif has_request_context() and current_user.is_authenticated:
        settings = current_user.company.settings or {}

    theme = settings.get('brand_theme') or 'executive'
    return EMAIL_THEME_PALETTES.get(theme, EMAIL_THEME_PALETTES['executive'])


def _company_email_settings(company=None):
    settings = {}
    if company is not None:
        settings = dict(company.settings or {})
    elif has_request_context() and current_user.is_authenticated:
        settings = dict(current_user.company.settings or {})

    raw_resend_api_key = settings.get('email_resend_api_key') or ''
    try:
        resend_api_key = (decrypt_setting(raw_resend_api_key) or '').strip()
    except RuntimeError as exc:
        current_app.logger.error('Could not decrypt Resend API key: %s', exc)
        resend_api_key = ''

    return {
        'enabled': bool(settings.get('email_enabled')),
        'provider': (settings.get('email_provider') or 'resend').strip().lower(),
        'from_name': (settings.get('email_from_name') or settings.get('brand_app_name') or current_app.config.get('APP_NAME', 'Rinde Fácil')).strip(),
        'from_address': (settings.get('email_from_address') or current_app.config.get('MAIL_DEFAULT_SENDER') or '').strip(),
        'reply_to': (settings.get('email_reply_to') or '').strip(),
        'resend_api_key': resend_api_key,
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


def _absolute_brand_asset_url(asset_url, base_url):
    if not asset_url:
        return ''
    if asset_url.startswith('http://') or asset_url.startswith('https://'):
        return asset_url

    normalized_base = (base_url or '').rstrip('/')
    if not normalized_base:
        return asset_url
    if asset_url.startswith('/'):
        return f'{normalized_base}{asset_url}'
    return f'{normalized_base}/{asset_url}'


def _render_email_html(
    company,
    *,
    title,
    greeting,
    paragraphs,
    action_url=None,
    action_label=None,
    facts=None,
    preheader=None,
    eyebrow=None,
):
    app_name, _, logo_url = _company_branding(company)
    return current_app.jinja_env.get_template('emails/notification.html').render(
        app_name=app_name,
        logo_url=logo_url,
        theme=_company_email_theme(company),
        title=title,
        greeting=greeting,
        paragraphs=paragraphs or [],
        action_url=action_url,
        action_label=action_label,
        facts=facts or [],
        preheader=preheader or title,
        eyebrow=eyebrow or app_name,
    )


def _append_email_disclaimer(body_text, body_html=None, app_name='Rinde Fácil'):
    text_disclaimer = (
        '---\n'
        f'Este es un correo automático de {app_name}. '
        'No respondas a este mensaje, ya que esta casilla no es monitoreada.'
    )
    html_disclaimer = (
        '<hr style="margin:24px 0;border:none;border-top:1px solid #e5e7eb;">'
        '<p style="margin:0;color:#6b7280;font-size:12px;line-height:1.6;">'
        f'Este es un correo automático de <strong>{app_name}</strong>. '
        'No respondas a este mensaje, ya que esta casilla no es monitoreada.'
        '</p>'
    )
    text = (body_text or '').rstrip()
    html = body_html.rstrip() if body_html else None

    if text_disclaimer not in text:
        text = f'{text}\n\n{text_disclaimer}' if text else text_disclaimer

    if html is not None and html_disclaimer not in html:
        if '</body>' in html:
            html = html.replace('</body>', f'{html_disclaimer}</body>')
        else:
            html = f'{html}{html_disclaimer}'

    return text, html


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


def send_email(subject, recipients, body_text, body_html=None, company=None, reply_to=None, force_send=False):
    if not recipients:
        return False

    config = _company_email_settings(company)
    if not config['enabled'] and not force_send:
        current_app.logger.info('Company email notifications disabled. Skip sending email.')
        return False

    if force_send and not config['resend_api_key']:
        current_app.logger.warning('Forced email requested but Resend API key missing. Skip sending email.')
        return False
    if force_send and not config['from_address']:
        current_app.logger.warning('Forced email requested but sender address missing. Skip sending email.')
        return False

    app_name, _, _ = _company_branding(company)
    branded_subject = f'[{app_name}] {subject}'
    final_body_text, final_body_html = _append_email_disclaimer(body_text, body_html, app_name=app_name)

    if config['provider'] == 'resend':
        return _send_via_resend(
            company,
            branded_subject,
            recipients,
            final_body_text,
            body_html=final_body_html,
            reply_to=reply_to,
        )

    return _send_via_smtp(branded_subject, recipients, final_body_text, body_html=final_body_html)


def send_test_email(company, recipient):
    if not recipient:
        return False
    app_name, _, _ = _company_branding(company)
    subject = 'Prueba de configuración de email'
    body_text = (
        f'Hola,\n\n'
        f'Este es un correo de prueba enviado desde {app_name}.\n\n'
        f'Si recibiste este mensaje, la configuración de Resend quedó operativa.'
    )
    body_html = _render_email_html(
        company,
        title='Prueba de configuración de email',
        greeting='Hola,',
        paragraphs=[
            f'Este es un correo de prueba enviado desde {app_name}.',
            'Si recibiste este mensaje, la configuración de Resend quedó operativa.',
        ],
        preheader='Prueba de configuración de email',
    )
    return send_email(subject, [recipient], body_text, body_html=body_html, company=company)


def send_report_created_email(user, report):
    app_name, base_url, _ = _company_branding(report.company)
    subject = f'Rendición creada: {report.title}'
    path = f'/reports/{report.id}'
    action_url = f'{base_url}{path}'
    body_text = (
        f'Hola {user.full_name},\n\n'
        f'Tu rendición "{report.title}" fue creada en estado borrador.\n\n'
        f'Revísala en {app_name}: {action_url}'
    )
    body_html = _render_email_html(
        report.company,
        title='Rendición creada',
        greeting=f'Hola {user.full_name},',
        paragraphs=[
            f'Tu rendición "{report.title}" fue creada en estado borrador.',
            'Puedes revisarla, completar antecedentes y enviarla a aprobación cuando esté lista.',
        ],
        facts=[
            ('Rendición', report.title),
            ('Estado', 'Borrador'),
        ],
        action_url=action_url,
        action_label='Abrir rendición',
        preheader=f'Rendición creada: {report.title}',
    )
    return send_email(subject, [user.email], body_text, body_html=body_html, company=report.company)


def send_report_submitted_email(user, report):
    app_name, base_url, _ = _company_branding(report.company)
    subject = f'Rendición enviada: {report.title}'
    path = f'/reports/{report.id}'
    action_url = f'{base_url}{path}'
    body_text = (
        f'Hola {user.full_name},\n\n'
        f'Tu rendición "{report.title}" fue enviada al flujo de aprobación.\n\n'
        f'Ver detalle en {app_name}: {action_url}'
    )
    body_html = _render_email_html(
        report.company,
        title='Rendición enviada',
        greeting=f'Hola {user.full_name},',
        paragraphs=[
            f'Tu rendición "{report.title}" fue enviada al flujo de aprobación.',
            'Te notificaremos cuando exista un cambio de estado o una solicitud de antecedentes adicionales.',
        ],
        facts=[
            ('Rendición', report.title),
            ('Estado', 'En revisión'),
        ],
        action_url=action_url,
        action_label='Ver rendición',
        preheader=f'Rendición enviada: {report.title}',
    )
    return send_email(subject, [user.email], body_text, body_html=body_html, company=report.company)


def send_approval_request_email(user, report):
    app_name, base_url, _ = _company_branding(report.company)
    report_path = f'/reports/{report.id}'
    action_url = f'{base_url}{report_path}'
    subject = f'Acción requerida: Aprobación de rendición #{report.id.hex[:8]}'
    body_text = (
        f'Hola {user.full_name},\n\n'
        f'Tienes una rendición pendiente de aprobación: {report.title} de {report.user.full_name}.\n\n'
        f'Revisa en {app_name}: {action_url}'
    )
    body_html = _render_email_html(
        report.company,
        title='Aprobación pendiente',
        greeting=f'Hola {user.full_name},',
        paragraphs=[
            f'Tienes una rendición pendiente de aprobación: "{report.title}" de {report.user.full_name}.',
            'Revísala y decide si apruebas, rechazas o solicitas antecedentes adicionales.',
        ],
        facts=[
            ('Rendición', report.title),
            ('Solicitante', report.user.full_name),
        ],
        action_url=action_url,
        action_label='Gestionar aprobación',
        preheader=f'Aprobación pendiente: {report.title}',
    )
    return send_email(subject, [user.email], body_text, body_html=body_html, company=report.company)


def send_report_status_email(user, report, status, reason=None):
    app_name, base_url, _ = _company_branding(report.company)
    report_path = f'/reports/{report.id}'
    action_url = f'{base_url}{report_path}'
    subject = f'Tu rendición fue {status}'
    body_text = (
        f'Hola {user.full_name},\n\n'
        f'Tu rendición "{report.title}" fue {status}.\n\n'
        f'Ver detalle en {app_name}: {action_url}'
    )
    if reason:
        body_text += f'\nMotivo: {reason}'
    body_html = _render_email_html(
        report.company,
        title=f'Rendición {status}',
        greeting=f'Hola {user.full_name},',
        paragraphs=[f'Tu rendición "{report.title}" fue {status}.'] + ([f'Motivo: {reason}'] if reason else []),
        facts=[
            ('Rendición', report.title),
            ('Estado', status.capitalize()),
        ] + ([('Motivo', reason)] if reason else []),
        action_url=action_url,
        action_label='Ver detalle',
        preheader=f'Rendición {status}: {report.title}',
    )
    return send_email(subject, [user.email], body_text, body_html=body_html, company=report.company)


def send_report_paid_email(user, report):
    app_name, base_url, _ = _company_branding(report.company)
    report_path = f'/reports/{report.id}'
    action_url = f'{base_url}{report_path}'
    subject = f'Rendición pagada: {report.title}'
    body_text = (
        f'Hola {user.full_name},\n\n'
        f'Tu rendición "{report.title}" fue marcada como pagada.\n\n'
        f'Ver detalle en {app_name}: {action_url}'
    )
    body_html = _render_email_html(
        report.company,
        title='Rendición pagada',
        greeting=f'Hola {user.full_name},',
        paragraphs=[
            f'Tu rendición "{report.title}" fue marcada como pagada.',
            'Ya puedes revisarla en la aplicación para consultar el detalle.',
        ],
        facts=[
            ('Rendición', report.title),
            ('Estado', 'Pagada'),
        ],
        action_url=action_url,
        action_label='Ver rendición',
        preheader=f'Rendición pagada: {report.title}',
    )
    return send_email(subject, [user.email], body_text, body_html=body_html, company=report.company)


def send_password_reset_email(user, company, reset_url):
    app_name, _, _ = _company_branding(company)
    subject = 'Recuperación de contraseña'
    body_text = (
        f'Hola {user.full_name},\n\n'
        f'Recibimos una solicitud para restablecer tu contraseña en {app_name} '
        f'(cuenta: {company.name}).\n\n'
        f'Usa el siguiente enlace para crear una nueva contraseña (vence en 30 minutos):\n'
        f'{reset_url}\n\n'
        f'Si no solicitaste este cambio, puedes ignorar este correo: tu contraseña no será modificada.'
    )
    body_html = _render_email_html(
        company,
        title='Recupera tu contraseña',
        greeting=f'Hola {user.full_name},',
        paragraphs=[
            f'Recibimos una solicitud para restablecer la contraseña de tu cuenta en <strong>{company.name}</strong>.',
            'Haz clic en el botón para crear una nueva. El enlace vence en 30 minutos.',
            'Si no fuiste tú, ignora este correo: tu contraseña no será cambiada.',
        ],
        action_url=reset_url,
        action_label='Restablecer contraseña',
        preheader='Recupera tu contraseña',
        eyebrow='Seguridad',
    )
    return send_email(
        subject,
        [user.email],
        body_text,
        body_html=body_html,
        company=company,
        force_send=True,
    )


def send_mfa_code_email(user, company, code, purpose='login'):
    app_name, _, _ = _company_branding(company)
    if purpose == 'setup':
        title = 'Activa tu verificación en dos pasos'
        subject = 'Código para activar verificación en dos pasos'
        paragraphs = [
            'Usa el siguiente código para confirmar la activación de la verificación en dos pasos.',
            'Una vez activada, cada vez que inicies sesión pediremos un código como este a tu correo.',
        ]
    else:
        title = 'Tu código de verificación'
        subject = 'Código de verificación de acceso'
        paragraphs = [
            'Usa el siguiente código para completar tu acceso:',
        ]
    body_text = (
        f'Hola {user.full_name},\n\n'
        f'Tu código de verificación para {app_name} es: {code}\n\n'
        f'El código vence en 10 minutos. Si no solicitaste este correo, puedes ignorarlo.'
    )
    body_html = _render_email_html(
        company,
        title=title,
        greeting=f'Hola {user.full_name},',
        paragraphs=paragraphs,
        facts=[('Código', code), ('Vigencia', '10 minutos')],
        preheader=f'Tu código de verificación es {code}',
        eyebrow='Seguridad',
    )
    return send_email(
        subject,
        [user.email],
        body_text,
        body_html=body_html,
        company=company,
        force_send=True,
    )
