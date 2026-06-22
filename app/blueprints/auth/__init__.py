from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy.exc import IntegrityError
from app.extensions import db, limiter
from app.models.company import Company
from app.models.user import User, UserRole
from app.models.api_key import UserApiKey
from app.models.notification import Notification
from app.models.report import Report
from app.models.password_reset_token import PasswordResetToken
from app.models.mfa_code import (
    MfaCode,
    MFA_CODE_MAX_ATTEMPTS,
    MFA_CODE_PURPOSE_LOGIN,
    MFA_CODE_PURPOSE_SETUP,
)
from app.services.email_service import send_mfa_code_email, send_password_reset_email

auth_bp = Blueprint('auth', __name__)


def _temporary_password_value():
    return (current_app.config.get('TEMP_PASSWORD') or '').strip()


def _build_reset_url(company, raw_token):
    settings = (company.settings or {}) if company else {}
    base_url = (settings.get('brand_app_url') or current_app.config.get('APP_URL') or '').rstrip('/')
    path = url_for('auth.reset_password', token=raw_token)
    return f'{base_url}{path}'


def _invalidate_user_reset_tokens(user):
    now = datetime.now(timezone.utc)
    for token in PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).all():
        token.used_at = now


def _invalidate_user_mfa_codes(user, purpose=None):
    now = datetime.now(timezone.utc)
    query = MfaCode.query.filter_by(user_id=user.id, consumed_at=None)
    if purpose:
        query = query.filter_by(purpose=purpose)
    for code in query.all():
        code.consumed_at = now


def _can_request_new_mfa_code(user, purpose):
    last = (
        MfaCode.query
        .filter_by(user_id=user.id, purpose=purpose)
        .order_by(MfaCode.created_at.desc())
        .first()
    )
    if not last or not last.created_at:
        return True
    created_at = last.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    elapsed = datetime.now(timezone.utc) - created_at
    return elapsed.total_seconds() >= 60


@auth_bp.before_app_request
def enforce_temporary_password_change():
    if not current_user.is_authenticated:
        return None
    if not current_user.must_change_password:
        return None

    endpoint = request.endpoint or ''
    if endpoint in {'auth.force_password_change', 'auth.logout'}:
        return None
    if endpoint.startswith('static'):
        return None
    return redirect(url_for('auth.force_password_change'))


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit('10/minute;30/hour')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        remember = request.form.get('remember', False) == 'on'

        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            if not user.is_active:
                flash('Tu cuenta está deshabilitada. Contacta al administrador.', 'danger')
                return render_template('auth/login.html')

            temp_password = _temporary_password_value()
            force_change_on_success = bool(
                user.must_change_password or (temp_password and password == temp_password)
            )

            if user.mfa_enabled:
                _invalidate_user_mfa_codes(user, MFA_CODE_PURPOSE_LOGIN)
                code, raw_code = MfaCode.build_for_user(user, MFA_CODE_PURPOSE_LOGIN)
                db.session.add(code)
                db.session.commit()
                send_mfa_code_email(user, user.company, raw_code, purpose=MFA_CODE_PURPOSE_LOGIN)
                session['mfa_user_id'] = str(user.id)
                session['mfa_remember'] = remember
                session['mfa_force_password_change'] = force_change_on_success
                flash('Enviamos un código de verificación a tu correo.', 'info')
                return redirect(url_for('auth.mfa_verify'))

            login_user(user, remember=remember)
            user.last_login = datetime.now(timezone.utc)
            if force_change_on_success:
                user.must_change_password = True
                db.session.commit()
                flash('Debes cambiar tu contraseña temporal antes de continuar.', 'warning')
                return redirect(url_for('auth.force_password_change'))

            db.session.commit()
            flash('Sesión iniciada correctamente.', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard.index'))

        flash('Email o contraseña incorrectos.', 'danger')

    return render_template('auth/login.html')


@auth_bp.route('/mfa-verify', methods=['GET', 'POST'])
@limiter.limit('20/minute;60/hour')
def mfa_verify():
    user_id = session.get('mfa_user_id')
    if not user_id:
        return redirect(url_for('auth.login'))

    user = db.session.get(User, user_id)
    if not user:
        session.pop('mfa_user_id', None)
        session.pop('mfa_remember', None)
        session.pop('mfa_force_password_change', None)
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        code_input = (request.form.get('code') or '').strip()

        if not code_input:
            flash('Debes ingresar el código de verificación.', 'danger')
            return render_template('auth/mfa_verify.html')

        candidate = (
            MfaCode.query
            .filter_by(user_id=user.id, purpose=MFA_CODE_PURPOSE_LOGIN, consumed_at=None)
            .order_by(MfaCode.created_at.desc())
            .first()
        )

        if not candidate or not candidate.is_valid:
            session.pop('mfa_user_id', None)
            session.pop('mfa_remember', None)
            session.pop('mfa_force_password_change', None)
            flash('El código expiró o no es válido. Inicia sesión nuevamente.', 'warning')
            return redirect(url_for('auth.login'))

        if not candidate.matches(code_input):
            candidate.attempts = (candidate.attempts or 0) + 1
            db.session.commit()
            remaining = MFA_CODE_MAX_ATTEMPTS - candidate.attempts
            if remaining <= 0:
                _invalidate_user_mfa_codes(user, MFA_CODE_PURPOSE_LOGIN)
                session.pop('mfa_user_id', None)
                session.pop('mfa_remember', None)
                session.pop('mfa_force_password_change', None)
                flash('Demasiados intentos fallidos. Inicia sesión nuevamente.', 'danger')
                return redirect(url_for('auth.login'))
            flash(f'Código incorrecto. Intentos restantes: {remaining}.', 'danger')
            return render_template('auth/mfa_verify.html')

        candidate.consumed_at = datetime.now(timezone.utc)
        _invalidate_user_mfa_codes(user, MFA_CODE_PURPOSE_LOGIN)
        remember = session.pop('mfa_remember', False)
        force_change_on_success = session.pop('mfa_force_password_change', False)
        session.pop('mfa_user_id', None)

        login_user(user, remember=remember)
        user.last_login = datetime.now(timezone.utc)
        if force_change_on_success:
            user.must_change_password = True
            db.session.commit()
            flash('Debes cambiar tu contraseña temporal antes de continuar.', 'warning')
            return redirect(url_for('auth.force_password_change'))

        db.session.commit()
        flash('Sesión verificada correctamente.', 'success')
        next_page = request.args.get('next')
        return redirect(next_page or url_for('dashboard.index'))

    return render_template('auth/mfa_verify.html')


@auth_bp.route('/mfa/resend', methods=['POST'])
@limiter.limit('3/minute;10/hour')
def mfa_resend():
    user_id = session.get('mfa_user_id')
    if not user_id:
        return redirect(url_for('auth.login'))

    user = db.session.get(User, user_id)
    if not user:
        session.pop('mfa_user_id', None)
        session.pop('mfa_remember', None)
        session.pop('mfa_force_password_change', None)
        return redirect(url_for('auth.login'))

    if not _can_request_new_mfa_code(user, MFA_CODE_PURPOSE_LOGIN):
        flash('Debes esperar unos segundos antes de pedir un nuevo código.', 'warning')
        return redirect(url_for('auth.mfa_verify'))

    _invalidate_user_mfa_codes(user, MFA_CODE_PURPOSE_LOGIN)
    code, raw_code = MfaCode.build_for_user(user, MFA_CODE_PURPOSE_LOGIN)
    db.session.add(code)
    db.session.commit()
    send_mfa_code_email(user, user.company, raw_code, purpose=MFA_CODE_PURPOSE_LOGIN)
    flash('Enviamos un nuevo código a tu correo.', 'info')
    return redirect(url_for('auth.mfa_verify'))


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit('5/minute;15/hour')
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        user = User.query.filter_by(email=email).first() if email else None

        if user and user.is_active:
            _invalidate_user_reset_tokens(user)
            token, raw_token = PasswordResetToken.build_for_user(user)
            db.session.add(token)
            db.session.commit()
            reset_url = _build_reset_url(user.company, raw_token)
            send_password_reset_email(user, user.company, reset_url)

        flash(
            'Si el correo existe y está activo, recibirás un enlace para '
            'restablecer tu contraseña en unos minutos.',
            'info',
        )
        return redirect(url_for('auth.login'))

    return render_template('auth/forgot_password.html')


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
@limiter.limit('10/minute;30/hour')
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    token_hash = PasswordResetToken.hash_raw_token(token)
    reset_token = PasswordResetToken.query.filter_by(token_hash=token_hash).first()

    if not reset_token or not reset_token.is_valid:
        flash('El enlace no es válido o ha expirado. Solicita uno nuevo.', 'danger')
        return redirect(url_for('auth.forgot_password'))

    user = reset_token.user

    if request.method == 'POST':
        new_password = request.form.get('new_password') or ''
        confirm_password = request.form.get('confirm_password') or ''
        temp_password = _temporary_password_value()

        if not new_password or len(new_password) < 8:
            flash('La contraseña debe tener al menos 8 caracteres.', 'danger')
            return render_template('auth/reset_password.html', token=token)
        if new_password != confirm_password:
            flash('Las contraseñas no coinciden.', 'danger')
            return render_template('auth/reset_password.html', token=token)
        if temp_password and new_password == temp_password:
            flash('La contraseña no puede ser la temporal de la empresa.', 'danger')
            return render_template('auth/reset_password.html', token=token)

        user.set_password(new_password)
        user.must_change_password = False
        reset_token.used_at = datetime.now(timezone.utc)
        _invalidate_user_reset_tokens(user)
        db.session.commit()
        flash('Tu contraseña fue actualizada. Ya puedes iniciar sesión.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/reset_password.html', token=token)


@auth_bp.route('/force-password-change', methods=['GET', 'POST'])
@login_required
def force_password_change():
    if not current_user.must_change_password:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        current_password = request.form.get('current_password') or ''
        new_password = request.form.get('new_password') or ''
        confirm_password = request.form.get('confirm_password') or ''
        temp_password = _temporary_password_value()

        if not current_user.check_password(current_password):
            flash('La contraseña temporal ingresada no es correcta.', 'danger')
            return render_template('auth/force_password_change.html')
        if not new_password:
            flash('Debes ingresar una nueva contraseña.', 'danger')
            return render_template('auth/force_password_change.html')
        if new_password != confirm_password:
            flash('La confirmación no coincide con la nueva contraseña.', 'danger')
            return render_template('auth/force_password_change.html')
        if new_password == temp_password:
            flash('La nueva contraseña no puede ser la contraseña temporal.', 'danger')
            return render_template('auth/force_password_change.html')

        current_user.set_password(new_password)
        current_user.must_change_password = False
        db.session.commit()
        flash('Contraseña actualizada correctamente.', 'success')
        return redirect(url_for('dashboard.index'))

    return render_template('auth/force_password_change.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))
        
    if request.method == 'POST':
        company_name = request.form.get('company_name')
        rut = request.form.get('rut')
        full_name = request.form.get('full_name')
        email = request.form.get('email')
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')
        
        if password != password_confirm:
            flash('Las contraseñas no coinciden.', 'danger')
            return render_template('auth/register.html')
            
        try:
            # Create company
            company = Company(name=company_name, rut=rut)
            db.session.add(company)
            db.session.flush() # get id
            
            # Create first user as admin
            user = User(
                company_id=company.id,
                email=email,
                full_name=full_name,
                role=UserRole.ADMIN
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            
            flash('Empresa y usuario creados con éxito. Ahora puedes iniciar sesión.', 'success')
            return redirect(url_for('auth.login'))
            
        except IntegrityError:
            db.session.rollback()
            flash('El email o RUT ya está registrado.', 'warning')
        except Exception as e:
            db.session.rollback()
            flash(f'Error al registrar: {str(e)}', 'danger')
            
    return render_template('auth/register.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Has cerrado la sesión.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/user-guide')
@login_required
def user_guide():
    return render_template('auth/user_guide.html')


def _notification_target_is_available(notification):
    if not notification.link:
        return False

    path = urlparse(notification.link).path or ''
    parts = [part for part in path.split('/') if part]
    if len(parts) >= 2 and parts[0] == 'reports':
        return Report.query.filter_by(id=parts[1]).first() is not None

    return True


@auth_bp.route('/notifications')
@login_required
def notifications():
    notifications_list = current_user.notifications.order_by(Notification.created_at.desc()).all()
    return render_template('auth/notifications.html', notifications=notifications_list)


@auth_bp.route('/notifications/mark-all-read', methods=['POST'])
@login_required
def mark_all_notifications_read():
    unread_notifications = current_user.notifications.filter_by(is_read=False).all()
    if unread_notifications:
        for notification in unread_notifications:
            notification.is_read = True
        db.session.commit()
        flash('Todas las notificaciones quedaron marcadas como leídas.', 'success')
    else:
        flash('No tienes notificaciones pendientes por marcar.', 'info')

    return redirect(url_for('auth.notifications'))


@auth_bp.route('/notifications/<uuid:notification_id>/open')
@login_required
def open_notification(notification_id):
    notification = Notification.query.filter_by(id=notification_id, user_id=current_user.id).first_or_404()
    if not notification.is_read:
        notification.is_read = True
        db.session.commit()

    if not notification.link:
        flash('La notificación no tiene un destino asociado.', 'warning')
        return redirect(url_for('auth.notifications'))

    if not _notification_target_is_available(notification):
        flash('La notificación apunta a un elemento que ya no existe.', 'warning')
        return redirect(url_for('auth.notifications'))

    return redirect(notification.link)

@auth_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'GET':
        new_api_key = session.pop('new_api_key', None)
        api_keys = current_user.api_keys.order_by(UserApiKey.created_at.desc()).all()
        return render_template('auth/profile.html', api_keys=api_keys, new_api_key=new_api_key)

    if request.method == 'POST':
        full_name = request.form.get('full_name')
        password = request.form.get('password')
        
        current_user.full_name = full_name
        
        if password:
            current_user.set_password(password)
            current_user.must_change_password = False
            
        # Handle Avatar/Signature uploads
        from werkzeug.utils import secure_filename
        import os
        from flask import current_app
        
        for field in ['avatar', 'signature']:
            if field in request.files:
                file = request.files[field]
                if file and file.filename != '':
                    filename = secure_filename(f"{field}_{current_user.id}_{file.filename}")
                    upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
                    file.save(upload_path)
                    setattr(current_user, f"{field}_url", f"/static/uploads/{filename}")
        
        db.session.commit()
        flash('Perfil actualizado correctamente.', 'success')
        return redirect(url_for('auth.profile'))

    return render_template('auth/profile.html')


@auth_bp.route('/profile/api-keys', methods=['POST'])
@login_required
def create_api_key():
    key_name = (request.form.get('key_name') or '').strip() or 'Agente IA'

    api_key, raw_key = UserApiKey.build_for_user(current_user, key_name)
    db.session.add(api_key)
    db.session.commit()

    session['new_api_key'] = raw_key
    flash('API key generada. Copia y guarda esta clave ahora; luego no se volverá a mostrar.', 'success')
    return redirect(url_for('auth.profile'))


@auth_bp.route('/profile/api-keys/<uuid:key_id>/revoke', methods=['POST'])
@login_required
def revoke_api_key(key_id):
    api_key = UserApiKey.query.filter_by(id=key_id, user_id=current_user.id).first_or_404()
    if api_key.revoked_at:
        flash('Esta API key ya estaba revocada.', 'info')
        return redirect(url_for('auth.profile'))

    api_key.revoked_at = datetime.utcnow()
    db.session.commit()
    flash('API key revocada correctamente.', 'warning')
    return redirect(url_for('auth.profile'))


@auth_bp.route('/profile/mfa/setup', methods=['GET', 'POST'])
@login_required
@limiter.limit('5/minute;15/hour')
def mfa_setup():
    if current_user.mfa_enabled:
        flash('La verificación en dos pasos ya está activada.', 'info')
        return redirect(url_for('auth.profile'))

    if request.method == 'POST':
        action = request.form.get('action') or 'verify'
        if action == 'send':
            if not _can_request_new_mfa_code(current_user, MFA_CODE_PURPOSE_SETUP):
                flash('Debes esperar unos segundos antes de pedir un nuevo código.', 'warning')
                return render_template('auth/mfa_setup.html')
            _invalidate_user_mfa_codes(current_user, MFA_CODE_PURPOSE_SETUP)
            code, raw_code = MfaCode.build_for_user(current_user, MFA_CODE_PURPOSE_SETUP)
            db.session.add(code)
            db.session.commit()
            send_mfa_code_email(current_user, current_user.company, raw_code, purpose=MFA_CODE_PURPOSE_SETUP)
            flash('Enviamos un código de verificación a tu correo.', 'info')
            return render_template('auth/mfa_setup.html')

        code_input = (request.form.get('code') or '').strip()
        candidate = (
            MfaCode.query
            .filter_by(user_id=current_user.id, purpose=MFA_CODE_PURPOSE_SETUP, consumed_at=None)
            .order_by(MfaCode.created_at.desc())
            .first()
        )
        if not candidate or not candidate.is_valid or not candidate.matches(code_input):
            if candidate:
                candidate.attempts = (candidate.attempts or 0) + 1
                db.session.commit()
            flash('El código no es correcto o expiró.', 'danger')
            return render_template('auth/mfa_setup.html')

        candidate.consumed_at = datetime.now(timezone.utc)
        _invalidate_user_mfa_codes(current_user, MFA_CODE_PURPOSE_SETUP)
        current_user.mfa_enabled = True
        db.session.commit()
        flash('Verificación en dos pasos activada.', 'success')
        return redirect(url_for('auth.profile'))

    return render_template('auth/mfa_setup.html')


@auth_bp.route('/profile/mfa/disable', methods=['POST'])
@login_required
def mfa_disable():
    password = request.form.get('password') or ''
    if not current_user.check_password(password):
        flash('La contraseña ingresada no es correcta.', 'danger')
        return redirect(url_for('auth.profile'))
    current_user.mfa_enabled = False
    _invalidate_user_mfa_codes(current_user)
    db.session.commit()
    flash('Verificación en dos pasos desactivada.', 'warning')
    return redirect(url_for('auth.profile'))
