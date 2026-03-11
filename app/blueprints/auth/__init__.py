from datetime import datetime
from urllib.parse import urlparse

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models.company import Company
from app.models.user import User, UserRole
from app.models.api_key import UserApiKey
from app.models.notification import Notification
from app.models.report import Report

auth_bp = Blueprint('auth', __name__)


def _temporary_password_value():
    return (current_app.config.get('TEMP_PASSWORD') or 'Sixman123.,').strip()


@auth_bp.before_app_request
def enforce_temporary_password_change():
    if not current_user.is_authenticated:
        return None
    if not session.get('must_change_password'):
        return None

    endpoint = request.endpoint or ''
    if endpoint in {'auth.force_password_change', 'auth.logout'}:
        return None
    if endpoint.startswith('static'):
        return None
    return redirect(url_for('auth.force_password_change'))


@auth_bp.route('/login', methods=['GET', 'POST'])
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

            login_user(user, remember=remember)
            user.last_login = datetime.utcnow()
            db.session.commit()

            if password == _temporary_password_value():
                session['must_change_password'] = True
                flash('Debes cambiar tu contraseña temporal antes de continuar.', 'warning')
                return redirect(url_for('auth.force_password_change'))

            session.pop('must_change_password', None)
            flash('Sesión iniciada correctamente.', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard.index'))
            
        flash('Email o contraseña incorrectos.', 'danger')
        
    return render_template('auth/login.html')


@auth_bp.route('/force-password-change', methods=['GET', 'POST'])
@login_required
def force_password_change():
    if not session.get('must_change_password'):
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
        db.session.commit()
        session.pop('must_change_password', None)
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
