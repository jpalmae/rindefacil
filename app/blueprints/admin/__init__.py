import os
import time
from urllib.parse import urlparse

from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_
from app.extensions import db
from app.models import User, UserRole, ApprovalFlow, ApprovalStep, CostCenter, AuditLog
from werkzeug.utils import secure_filename
from app.services.email_service import get_company_email_settings_view, send_test_email
from app.services.secrets_service import can_encrypt_settings, encrypt_setting
from app.services.ocr_settings_service import (
    get_company_ocr_config_view,
    test_ocr_connection,
    local_provider_presets,
    DEFAULT_CLOUD_PROMPT,
    DEFAULT_LOCAL_PROMPT,
    OCR_DEFAULT_CLOUD_MODEL,
    OCR_DEFAULT_LOCAL_BASE_URL,
    OCR_DEFAULT_LOCAL_MODEL,
    OCR_DEFAULT_TIMEOUT_SECONDS,
)
from app.models.oidc_provider import OidcProvider
from app.services import oidc_service


# Presets de OIDC para el admin
OIDC_PRESETS = {
    'google': {
        'name': 'Google Workspace',
        'discovery_url': 'https://accounts.google.com',
        'scopes': 'openid profile email',
        'icon_slug': 'google',
    },
    'microsoft': {
        'name': 'Microsoft Entra ID',
        'discovery_url': 'https://login.microsoftonline.com/common/v2.0',
        'scopes': 'openid profile email',
        'icon_slug': 'microsoft',
    },
}

admin_bp = Blueprint('admin', __name__)

ALLOWED_LOGO_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.svg'}
ALLOWED_ICON_EXTENSIONS = {'.ico', '.png', '.jpg', '.jpeg', '.webp', '.svg'}


def _sanitize_domain(value):
    domain = (value or '').strip().lower()
    if not domain:
        return ''
    domain = domain.replace('http://', '').replace('https://', '')
    domain = domain.strip('/').split('/')[0]
    return domain


def _normalize_user_email(raw_email, default_domain=''):
    email = (raw_email or '').strip().lower()
    if not email:
        return email
    if '@' in email:
        return email
    if default_domain:
        return f"{email}@{default_domain}"
    return email


def _sanitize_public_app_url(value):
    raw = (value or '').strip()
    if not raw:
        return ''

    candidate = raw if '://' in raw else f'https://{raw}'
    parsed = urlparse(candidate)
    if parsed.scheme != 'https' or not parsed.netloc:
        return None

    normalized = f'https://{parsed.netloc}{parsed.path or ""}'.rstrip('/')
    return normalized


def _finance_permissions_from_form(form):
    can_mark_reimbursements_paid = bool(form.get('can_mark_reimbursements_paid'))
    can_view_approved_reports = bool(form.get('can_view_approved_reports')) or can_mark_reimbursements_paid
    return can_view_approved_reports, can_mark_reimbursements_paid

@admin_bp.before_request
@login_required
def ensure_admin():
    if not current_user.has_role(UserRole.ADMIN) and not current_user.has_role(UserRole.SUPERADMIN):
        flash('No tienes permisos de administrador.', 'danger')
        return redirect(url_for('dashboard.index'))

@admin_bp.route('/')
def index():
    return render_template('admin/index.html')

@admin_bp.route('/users')
def users():
    search = (request.args.get('q') or '').strip()
    query = User.query.filter_by(company_id=current_user.company_id)
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                User.full_name.ilike(like),
                User.email.ilike(like),
            )
        )
    users_list = query.order_by(User.full_name.asc()).all()
    return render_template('admin/users.html', users=users_list, search=search)

@admin_bp.route('/users/new', methods=['GET', 'POST'])
def user_new():
    default_user_domain = (
        (current_user.company.settings or {}).get('brand_user_default_domain')
        or (current_user.company.settings or {}).get('brand_default_domain')
        or ''
    )
    if request.method == 'POST':
        full_name = request.form.get('full_name')
        email = _normalize_user_email(request.form.get('email'), default_user_domain)
        password = request.form.get('password')
        role = request.form.get('role')
        manager_id = request.form.get('manager_id')
        cost_center_id = request.form.get('cost_center_id')
        can_view_approved_reports, can_mark_reimbursements_paid = _finance_permissions_from_form(request.form)

        if '@' not in email:
            flash('Debes ingresar un email válido o configurar dominio por defecto.', 'danger')
            return redirect(url_for('admin.user_new'))
        
        user = User(
            company_id=current_user.company_id,
            full_name=full_name,
            email=email,
            role=role,
            manager_id=manager_id if manager_id else None,
            cost_center_id=cost_center_id if cost_center_id else None,
            can_view_approved_reports=can_view_approved_reports,
            can_mark_reimbursements_paid=can_mark_reimbursements_paid,
            must_change_password=True,
        )
        user.set_password(password)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('Ya existe un usuario con ese email.', 'danger')
            return redirect(url_for('admin.user_new'))
        
        from app.services.audit_service import log_action
        log_action('user_created', entity_type='user', entity_id=user.id, description=f"Usuario {email} creado por admin.")
        
        flash('Usuario creado correctamente.', 'success')
        return redirect(url_for('admin.users'))
        
    all_users = User.query.filter_by(company_id=current_user.company_id).all()
    cost_centers = CostCenter.query.filter_by(company_id=current_user.company_id).all()
    return render_template(
        'admin/user_form.html',
        all_users=all_users,
        cost_centers=cost_centers,
        user=None,
        user_default_domain=default_user_domain,
    )

@admin_bp.route('/users/<uuid:user_id>/edit', methods=['GET', 'POST'])
def user_edit(user_id):
    user = User.query.get_or_404(user_id)
    default_user_domain = (
        (current_user.company.settings or {}).get('brand_user_default_domain')
        or (current_user.company.settings or {}).get('brand_default_domain')
        or ''
    )
    if request.method == 'POST':
        user.full_name = request.form.get('full_name')
        user.email = _normalize_user_email(request.form.get('email'), default_user_domain)
        user.role = request.form.get('role')
        user.manager_id = request.form.get('manager_id') if request.form.get('manager_id') else None
        user.cost_center_id = request.form.get('cost_center_id') if request.form.get('cost_center_id') else None
        (
            user.can_view_approved_reports,
            user.can_mark_reimbursements_paid,
        ) = _finance_permissions_from_form(request.form)

        if '@' not in user.email:
            flash('Debes ingresar un email válido o configurar dominio por defecto.', 'danger')
            return redirect(url_for('admin.user_edit', user_id=user_id))
        
        password = request.form.get('password')
        if password:
            user.set_password(password)
            user.must_change_password = True
            
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('Ya existe otro usuario con ese email.', 'danger')
            return redirect(url_for('admin.user_edit', user_id=user_id))
        
        from app.services.audit_service import log_action
        log_action('user_updated', entity_type='user', entity_id=user.id, description=f"Usuario {user.email} editado por admin.")
        
        flash('Usuario actualizado correctamente.', 'success')
        return redirect(url_for('admin.users'))
        
    all_users = User.query.filter_by(company_id=current_user.company_id).all()
    cost_centers = CostCenter.query.filter_by(company_id=current_user.company_id).all()
    return render_template(
        'admin/user_form.html',
        user=user,
        all_users=all_users,
        cost_centers=cost_centers,
        user_default_domain=default_user_domain,
    )

@admin_bp.route('/flows')
def flows():
    flows_list = ApprovalFlow.query.filter_by(company_id=current_user.company_id).all()
    return render_template('admin/flows.html', flows=flows_list)

@admin_bp.route('/cost-centers')
def cost_centers():
    centers = CostCenter.query.filter_by(company_id=current_user.company_id).all()
    return render_template('admin/cost_centers.html', cost_centers=centers)

@admin_bp.route('/cost-centers/new', methods=['GET', 'POST'])
def cost_center_new():
    if request.method == 'POST':
        name = request.form.get('name')
        code = request.form.get('code')
        budget = request.form.get('monthly_budget', 0)
        
        center = CostCenter(
            company_id=current_user.company_id,
            name=name,
            code=code,
            monthly_budget=float(budget)
        )
        db.session.add(center)
        db.session.commit()
        
        from app.services.audit_service import log_action
        log_action('cost_center_created', entity_type='cost_center', entity_id=center.id, description=f"Centro de costo {code} creado.")
        
        flash('Centro de costo creado con éxito.', 'success')
        return redirect(url_for('admin.cost_centers'))
        
    return render_template('admin/cost_center_form.html', center=None)

@admin_bp.route('/cost-centers/<uuid:id>/edit', methods=['GET', 'POST'])
def cost_center_edit(id):
    center = CostCenter.query.get_or_404(id)
    if request.method == 'POST':
        center.name = request.form.get('name')
        center.code = request.form.get('code')
        center.monthly_budget = float(request.form.get('monthly_budget', 0))
        
        db.session.commit()
        
        from app.services.audit_service import log_action
        log_action('cost_center_updated', entity_type='cost_center', entity_id=center.id, description=f"Centro de costo {center.code} actualizado.")
        
        flash('Centro de costo actualizado.', 'success')
        return redirect(url_for('admin.cost_centers'))
        
    return render_template('admin/cost_center_form.html', center=center)

@admin_bp.route('/flows/new', methods=['GET', 'POST'])
def flow_new():
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        min_amount = request.form.get('min_amount', 0)
        
        flow = ApprovalFlow(
            company_id=current_user.company_id,
            name=name,
            description=description,
            trigger_rules={'min_amount': float(min_amount)}
        )
        db.session.add(flow)
        db.session.commit()
        
        flash('Flujo creado. Ahora añade los pasos.', 'success')
        return redirect(url_for('admin.flow_steps', flow_id=flow.id))
        
    return render_template('admin/flow_form.html')

@admin_bp.route('/flows/<uuid:flow_id>/steps', methods=['GET', 'POST'])
def flow_steps(flow_id):
    flow = ApprovalFlow.query.get_or_404(flow_id)
    if request.method == 'POST':
        approver_type = request.form.get('approver_type')
        approver_target = request.form.get('approver_target')
        step_number = len(flow.steps) + 1
        
        step = ApprovalStep(
            flow_id=flow.id,
            step_number=step_number,
            approver_type=approver_type,
            approver_target=approver_target
        )
        db.session.add(step)
        db.session.commit()
        flash('Paso añadido correctamente.', 'success')
        
    users = User.query.filter_by(company_id=current_user.company_id).all()
    return render_template('admin/flow_steps.html', flow=flow, users=users)

@admin_bp.route('/users/<uuid:user_id>/delete', methods=['POST'])
def user_delete(user_id):
    if user_id == current_user.id:
        flash('No puedes desactivarte a ti mismo.', 'danger')
        return redirect(url_for('admin.users'))

    user = User.query.get_or_404(user_id)
    if not user.is_active:
        flash('Este usuario ya estaba desactivado.', 'info')
        return redirect(url_for('admin.users'))

    # Soft delete: preserva la fila para integridad de gastos, rendiciones,
    # decisiones de aprobación y auditoría. Solo se revoca el acceso y se
    # libera el email anonimizándolo (para permitir reutilizarlo si se crea
    # un nuevo usuario con la misma dirección).
    import uuid as _uuid
    user.is_active = False
    user.must_change_password = True
    user.mfa_enabled = False
    user.password_hash = '!'  # invalida cualquier password previo
    original_email = user.email
    user.email = f"inactive_{_uuid.uuid4().hex[:16]}@inactive.local"

    # Revocar API keys activas y sesiones MFA / reset tokens pendientes
    from app.models.api_key import UserApiKey
    from app.models.mfa_code import MfaCode
    from app.models.password_reset_token import PasswordResetToken
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    for key in UserApiKey.query.filter_by(user_id=user.id, revoked_at=None).all():
        key.revoked_at = now
    for code in MfaCode.query.filter_by(user_id=user.id, consumed_at=None).all():
        code.consumed_at = now
    for token in PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).all():
        token.used_at = now

    # Si el usuario es manager de otros, avisamos al admin pero no tocamos
    # la asignación (el flujo de aprobación ya omite managers inexistentes).
    subordinates_count = user.subordinates.count() if hasattr(user, 'subordinates') else 0

    db.session.commit()

    message = f'Usuario "{user.full_name}" desactivado. El correo {original_email} quedó libre.'
    if subordinates_count:
        message += f' Ten en cuenta que todavía es manager de {subordinates_count} usuario(s); revisa sus asignaciones si corresponde.'
    flash(message, 'warning')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/<uuid:user_id>/activate', methods=['POST'])
def user_activate(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_active:
        flash('Este usuario ya estaba activo.', 'info')
        return redirect(url_for('admin.users'))

    user.is_active = True
    user.must_change_password = True  # obliga a definir nueva contraseña al reactivar
    db.session.commit()
    flash(f'Usuario "{user.full_name}" reactivado. Define una nueva contraseña desde la edición del usuario.', 'success')
    return redirect(url_for('admin.user_edit', user_id=user.id))

@admin_bp.route('/cost-centers/<uuid:id>/delete', methods=['POST'])
def cost_center_delete(id):
    center = CostCenter.query.get_or_404(id)
    # Check if users are assigned
    if center.users.first():
        flash('No se puede eliminar un centro de costo con usuarios asignados.', 'danger')
        return redirect(url_for('admin.cost_centers'))
        
    db.session.delete(center)
    db.session.commit()
    flash('Centro de costo eliminado.', 'warning')
    return redirect(url_for('admin.cost_centers'))

@admin_bp.route('/flows/<uuid:flow_id>/edit', methods=['GET', 'POST'])
def flow_edit(flow_id):
    flow = ApprovalFlow.query.get_or_404(flow_id)
    if request.method == 'POST':
        flow.name = request.form.get('name')
        flow.description = request.form.get('description')
        min_amount = request.form.get('min_amount', 0)
        flow.trigger_rules = {'min_amount': float(min_amount)}
        
        db.session.commit()
        flash('Flujo actualizado.', 'success')
        return redirect(url_for('admin.flows'))
        
    return render_template('admin/flow_form.html', flow=flow)

@admin_bp.route('/flows/<uuid:flow_id>/delete', methods=['POST'])
def flow_delete(flow_id):
    flow = ApprovalFlow.query.get_or_404(flow_id)
    db.session.delete(flow)
    db.session.commit()
    flash('Flujo de aprobación eliminado.', 'warning')
    return redirect(url_for('admin.flows'))

@admin_bp.route('/flows/<uuid:flow_id>/steps/<uuid:step_id>/delete', methods=['POST'])
def step_delete(flow_id, step_id):
    step = ApprovalStep.query.get_or_404(step_id)
    if step.flow_id != flow_id:
        flash('El paso no pertenece al flujo indicado.', 'danger')
        return redirect(url_for('admin.flows'))

    db.session.delete(step)

    db.session.flush()
    remaining_steps = ApprovalStep.query.filter(
        ApprovalStep.flow_id == flow_id,
        ApprovalStep.id != step_id,
    ).order_by(ApprovalStep.step_number.asc()).all()
    for index, remaining_step in enumerate(remaining_steps, start=1):
        remaining_step.step_number = index

    db.session.commit()
    flash('Paso eliminado.', 'warning')
    return redirect(url_for('admin.flow_steps', flow_id=flow_id))

@admin_bp.route('/audit-logs')
def audit_logs():
    logs = AuditLog.query.filter_by(company_id=current_user.company_id).order_by(AuditLog.created_at.desc()).limit(100).all()
    return render_template('admin/audit.html', logs=logs)


@admin_bp.route('/branding', methods=['GET', 'POST'])
def branding():
    company = current_user.company
    settings = dict(company.settings or {})
    allowed_themes = {'executive', 'paper', 'midnight', 'rose'}

    if request.method == 'POST':
        app_name = (request.form.get('app_name') or '').strip()
        default_domain = _sanitize_domain(request.form.get('default_domain'))
        app_url = _sanitize_public_app_url(request.form.get('app_url'))
        brand_theme = (request.form.get('brand_theme') or 'executive').strip()

        if not app_name:
            flash('El nombre de la app es obligatorio.', 'danger')
            return redirect(url_for('admin.branding'))
        if app_url is None:
            flash('La URL pública debe ser https y tener un dominio válido.', 'danger')
            return redirect(url_for('admin.branding'))
        if brand_theme not in allowed_themes:
            brand_theme = 'executive'

        settings['brand_app_name'] = app_name
        settings['brand_user_default_domain'] = default_domain
        settings['brand_app_url'] = app_url or ''
        settings['brand_theme'] = brand_theme
        settings.pop('brand_default_domain', None)

        if request.form.get('remove_logo') == '1':
            settings.pop('brand_logo_url', None)
        if request.form.get('remove_icon') == '1':
            settings.pop('brand_icon_url', None)

        if 'logo' in request.files:
            logo = request.files['logo']
            if logo and logo.filename:
                ext = os.path.splitext(logo.filename)[1].lower()
                if ext not in ALLOWED_LOGO_EXTENSIONS:
                    flash('Formato de logo no permitido. Usa PNG, JPG, WEBP o SVG.', 'danger')
                    return redirect(url_for('admin.branding'))

                branding_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'branding')
                os.makedirs(branding_dir, exist_ok=True)
                safe_name = secure_filename(logo.filename)
                filename = f"company_{company.id}_{int(time.time())}_{safe_name}"
                file_path = os.path.join(branding_dir, filename)
                logo.save(file_path)
                settings['brand_logo_url'] = f"/static/uploads/branding/{filename}"

        if 'icon' in request.files:
            icon = request.files['icon']
            if icon and icon.filename:
                ext = os.path.splitext(icon.filename)[1].lower()
                if ext not in ALLOWED_ICON_EXTENSIONS:
                    flash('Formato de ícono no permitido. Usa ICO, PNG, JPG, WEBP o SVG.', 'danger')
                    return redirect(url_for('admin.branding'))

                branding_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'branding')
                os.makedirs(branding_dir, exist_ok=True)
                safe_name = secure_filename(icon.filename)
                filename = f"icon_{company.id}_{int(time.time())}_{safe_name}"
                file_path = os.path.join(branding_dir, filename)
                icon.save(file_path)
                settings['brand_icon_url'] = f"/static/uploads/branding/{filename}"

        company.settings = settings
        db.session.commit()
        flash('Branding actualizado correctamente.', 'success')
        return redirect(url_for('admin.branding'))

    return render_template(
        'admin/branding.html',
        company=company,
        branding=settings,
    )


@admin_bp.route('/email-settings', methods=['GET', 'POST'])
def email_settings():
    company = current_user.company
    settings = dict(company.settings or {})

    if request.method == 'POST':
        settings['email_enabled'] = bool(request.form.get('email_enabled'))
        settings['email_provider'] = 'resend'
        settings['email_from_name'] = (request.form.get('from_name') or '').strip()
        settings['email_from_address'] = (request.form.get('from_address') or '').strip().lower()
        settings['email_reply_to'] = (request.form.get('reply_to') or '').strip().lower()
        settings['email_test_recipient'] = (request.form.get('test_recipient') or '').strip().lower()

        resend_api_key = (request.form.get('resend_api_key') or '').strip()
        if resend_api_key:
            if not can_encrypt_settings():
                flash('Falta SETTINGS_ENCRYPTION_KEY en el servidor para guardar secretos de email.', 'danger')
                return redirect(url_for('admin.email_settings'))
            settings['email_resend_api_key'] = encrypt_setting(resend_api_key)
        elif request.form.get('remove_resend_api_key') == '1':
            settings.pop('email_resend_api_key', None)

        for key in (
            'email_notify_report_created',
            'email_notify_report_submitted',
            'email_notify_approval_needed',
            'email_notify_report_approved',
            'email_notify_report_rejected',
            'email_notify_report_info_requested',
            'email_notify_report_paid',
        ):
            settings[key] = bool(request.form.get(key))

        if settings['email_enabled']:
            if not settings.get('email_from_address'):
                flash('Debes ingresar un correo remitente para habilitar emails.', 'danger')
                return redirect(url_for('admin.email_settings'))
            if not settings.get('email_resend_api_key'):
                flash('Debes ingresar una API key de Resend para habilitar emails.', 'danger')
                return redirect(url_for('admin.email_settings'))

        company.settings = settings
        db.session.commit()
        flash('Configuración de email actualizada.', 'success')
        return redirect(url_for('admin.email_settings'))

    return render_template(
        'admin/email_settings.html',
        company=company,
        email_settings=get_company_email_settings_view(company),
    )


@admin_bp.route('/email-settings/test', methods=['POST'])
def email_settings_test():
    company = current_user.company
    recipient = (request.form.get('test_recipient') or '').strip().lower()
    if not recipient:
        recipient = (get_company_email_settings_view(company).get('test_recipient') or current_user.email or '').strip().lower()

    if not recipient:
        flash('Debes indicar un destinatario para la prueba.', 'warning')
        return redirect(url_for('admin.email_settings'))

    if send_test_email(company, recipient):
        flash(f'Correo de prueba enviado a {recipient}.', 'success')
    else:
        flash('No fue posible enviar el correo de prueba. Revisa la configuración de Resend.', 'danger')

    return redirect(url_for('admin.email_settings'))


@admin_bp.route('/security', methods=['GET', 'POST'])
def security():
    company = current_user.company
    settings = dict(company.settings or {})

    if request.method == 'POST':
        settings['mfa_enforced'] = bool(request.form.get('mfa_enforced'))
        company.settings = settings
        db.session.commit()
        if settings['mfa_enforced']:
            flash('Verificación en dos pasos obligatoria activada. A partir del próximo inicio de sesión, todos los usuarios deberán completar el código por correo.', 'success')
        else:
            flash('Verificación en dos pasos obligatoria desactivada. Cada usuario decide si activarla.', 'success')
        return redirect(url_for('admin.security'))

    total_users = User.query.filter_by(company_id=company.id, is_active=True).count()
    users_with_mfa = User.query.filter_by(company_id=company.id, is_active=True, mfa_enabled=True).count()
    return render_template(
        'admin/security.html',
        company=company,
        mfa_enforced=bool(settings.get('mfa_enforced')),
        total_users=total_users,
        users_with_mfa=users_with_mfa,
    )


@admin_bp.route('/ocr-settings', methods=['GET', 'POST'])
def ocr_settings():
    company = current_user.company
    settings = dict(company.settings or {})

    if request.method == 'POST':
        enabled = bool(request.form.get('ocr_enabled'))
        provider = (request.form.get('ocr_provider') or 'openrouter').strip().lower()
        if provider not in ('openrouter', 'local'):
            provider = 'openrouter'

        settings['ocr_enabled'] = enabled
        settings['ocr_provider'] = provider

        # OpenRouter
        settings['ocr_openrouter_model'] = (request.form.get('openrouter_model') or '').strip() or OCR_DEFAULT_CLOUD_MODEL
        settings['ocr_openrouter_model_fallback'] = (request.form.get('openrouter_model_fallback') or '').strip()
        settings['ocr_openrouter_prompt'] = (request.form.get('openrouter_prompt') or '').strip() or DEFAULT_CLOUD_PROMPT

        openrouter_api_key = (request.form.get('openrouter_api_key') or '').strip()
        if openrouter_api_key:
            if not can_encrypt_settings():
                flash('Falta SETTINGS_ENCRYPTION_KEY en el servidor para guardar la API key.', 'danger')
                return redirect(url_for('admin.ocr_settings'))
            settings['ocr_openrouter_api_key'] = encrypt_setting(openrouter_api_key)
        elif request.form.get('remove_openrouter_api_key') == '1':
            settings.pop('ocr_openrouter_api_key', None)

        # Local
        settings['ocr_local_base_url'] = (request.form.get('local_base_url') or '').strip() or OCR_DEFAULT_LOCAL_BASE_URL
        settings['ocr_local_model'] = (request.form.get('local_model') or '').strip() or OCR_DEFAULT_LOCAL_MODEL
        settings['ocr_local_model_fallback'] = (request.form.get('local_model_fallback') or '').strip()
        try:
            timeout_value = int(request.form.get('local_timeout') or OCR_DEFAULT_TIMEOUT_SECONDS)
        except (TypeError, ValueError):
            timeout_value = OCR_DEFAULT_TIMEOUT_SECONDS
        settings['ocr_local_timeout'] = max(5, min(timeout_value, 600))
        settings['ocr_local_prompt'] = (request.form.get('local_prompt') or '').strip() or DEFAULT_LOCAL_PROMPT

        local_api_key = (request.form.get('local_api_key') or '').strip()
        if local_api_key:
            if not can_encrypt_settings():
                flash('Falta SETTINGS_ENCRYPTION_KEY en el servidor para guardar la API key.', 'danger')
                return redirect(url_for('admin.ocr_settings'))
            settings['ocr_local_api_key'] = encrypt_setting(local_api_key)
        elif request.form.get('remove_local_api_key') == '1':
            settings.pop('ocr_local_api_key', None)

        if enabled:
            if provider == 'openrouter' and not settings.get('ocr_openrouter_api_key'):
                flash('Para OpenRouter necesitas ingresar la API key.', 'danger')
                return redirect(url_for('admin.ocr_settings'))
            if provider == 'local' and not settings.get('ocr_local_base_url'):
                flash('Para inferencia local necesitas indicar la URL del servidor.', 'danger')
                return redirect(url_for('admin.ocr_settings'))

        company.settings = settings
        db.session.commit()
        flash('Configuración de OCR actualizada.', 'success')
        return redirect(url_for('admin.ocr_settings'))

    return render_template(
        'admin/ocr_settings.html',
        company=company,
        ocr_settings=get_company_ocr_config_view(company),
        presets=local_provider_presets(),
        default_cloud_prompt=DEFAULT_CLOUD_PROMPT,
        default_local_prompt=DEFAULT_LOCAL_PROMPT,
        can_encrypt=can_encrypt_settings(),
    )


@admin_bp.route('/ocr-settings/test', methods=['POST'])
def ocr_settings_test():
    company = current_user.company
    ok, message, data = test_ocr_connection(company)
    if ok:
        flash(f'Conexión exitosa. {message}', 'success')
        if data:
            flash(f'Respuesta del modelo: {data}', 'info')
    else:
        flash(f'No se pudo conectar: {message}', 'danger')
    return redirect(url_for('admin.ocr_settings'))


# ---------------------------------------------------------------------------
# Single Sign-On (OIDC providers) — Google Workspace, Microsoft Entra, etc.
# ---------------------------------------------------------------------------
@admin_bp.route('/oidc-providers')
def oidc_providers():
    providers = OidcProvider.query.filter_by(company_id=current_user.company_id).order_by(OidcProvider.name.asc()).all()
    return render_template(
        'admin/oidc_providers.html',
        providers=providers,
        presets=OIDC_PRESETS,
        can_encrypt=can_encrypt_settings(),
        redirect_uri_example=url_for('auth.oidc_callback', _external=True),
    )


@admin_bp.route('/oidc-providers/new', methods=['GET', 'POST'])
def oidc_provider_new():
    if request.method == 'POST':
        if not can_encrypt_settings():
            flash('Falta SETTINGS_ENCRYPTION_KEY en el servidor para guardar el client secret.', 'danger')
            return redirect(url_for('admin.oidc_provider_new'))

        slug = (request.form.get('slug') or '').strip().lower()
        name = (request.form.get('name') or '').strip()
        client_id = (request.form.get('client_id') or '').strip()
        client_secret = (request.form.get('client_secret') or '').strip()
        discovery_url = (request.form.get('discovery_url') or '').strip()
        scopes = (request.form.get('scopes') or '').strip() or 'openid profile email'

        if not slug or not name or not client_id or not client_secret or not discovery_url:
            flash('Completa slug, nombre, client_id, client_secret y discovery URL.', 'danger')
            return redirect(url_for('admin.oidc_provider_new'))
        if OidcProvider.query.filter_by(company_id=current_user.company_id, slug=slug).first():
            flash(f'Ya existe un provider con slug "{slug}".', 'warning')
            return redirect(url_for('admin.oidc_provider_new'))

        provider = OidcProvider(
            company_id=current_user.company_id,
            slug=slug,
            name=name,
            client_id=client_id,
            client_secret=encrypt_setting(client_secret),
            discovery_url=discovery_url,
            scopes=scopes,
            enabled=bool(request.form.get('enabled')),
            auto_provision=bool(request.form.get('auto_provision')),
            allowed_domains=(request.form.get('allowed_domains') or '').strip() or None,
            icon_slug=(request.form.get('icon_slug') or '').strip() or None,
        )
        db.session.add(provider)
        db.session.commit()
        flash(f'Provider "{name}" creado.', 'success')
        return redirect(url_for('admin.oidc_providers'))

    preset = request.args.get('preset')
    return render_template(
        'admin/oidc_provider_form.html',
        provider=None,
        preset=OIDC_PRESETS.get(preset, {}),
        preset_key=preset or '',
        can_encrypt=can_encrypt_settings(),
        redirect_uri_example=url_for('auth.oidc_callback', _external=True),
    )


@admin_bp.route('/oidc-providers/<uuid:provider_id>/edit', methods=['GET', 'POST'])
def oidc_provider_edit(provider_id):
    provider = OidcProvider.query.filter_by(id=provider_id, company_id=current_user.company_id).first_or_404()

    if request.method == 'POST':
        provider.name = (request.form.get('name') or '').strip() or provider.name
        provider.client_id = (request.form.get('client_id') or '').strip() or provider.client_id
        provider.discovery_url = (request.form.get('discovery_url') or '').strip() or provider.discovery_url
        provider.scopes = (request.form.get('scopes') or '').strip() or 'openid profile email'
        provider.enabled = bool(request.form.get('enabled'))
        provider.auto_provision = bool(request.form.get('auto_provision'))
        provider.allowed_domains = (request.form.get('allowed_domains') or '').strip() or None
        provider.icon_slug = (request.form.get('icon_slug') or '').strip() or None

        new_secret = (request.form.get('client_secret') or '').strip()
        if new_secret:
            if not can_encrypt_settings():
                flash('Falta SETTINGS_ENCRYPTION_KEY para actualizar el client secret.', 'danger')
                return redirect(url_for('admin.oidc_provider_edit', provider_id=provider.id))
            provider.client_secret = encrypt_setting(new_secret)

        db.session.commit()
        flash(f'Provider "{provider.name}" actualizado.', 'success')
        return redirect(url_for('admin.oidc_providers'))

    return render_template(
        'admin/oidc_provider_form.html',
        provider=provider,
        preset={},
        preset_key='',
        can_encrypt=can_encrypt_settings(),
        redirect_uri_example=url_for('auth.oidc_callback', _external=True),
    )


@admin_bp.route('/oidc-providers/<uuid:provider_id>/delete', methods=['POST'])
def oidc_provider_delete(provider_id):
    provider = OidcProvider.query.filter_by(id=provider_id, company_id=current_user.company_id).first_or_404()
    name = provider.name
    db.session.delete(provider)
    db.session.commit()
    flash(f'Provider "{name}" eliminado.', 'warning')
    return redirect(url_for('admin.oidc_providers'))
