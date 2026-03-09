import os

from flask import Flask, render_template
from flask_login import current_user

from app.config import config
from app.extensions import db, login_manager, migrate, mail, cors, limiter


def create_app(config_name=None):
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'default')

    app = Flask(__name__)
    app.config.from_object(config[config_name])

    # Inicializar directorio de uploads
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # Init extensions
    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    mail.init_app(app)
    cors.init_app(app)
    limiter.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        from app.models.user import User
        return db.session.get(User, user_id)

    # Import and register blueprints
    from app.blueprints.auth import auth_bp
    from app.blueprints.dashboard import dashboard_bp
    from app.blueprints.expenses import expenses_bp
    from app.blueprints.reports import reports_bp
    from app.blueprints.admin import admin_bp
    from app.blueprints.api import api_bp

    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(expenses_bp, url_prefix='/expenses')
    app.register_blueprint(reports_bp, url_prefix='/reports')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(api_bp, url_prefix='/api/v1')

    # Inject common template context once per request to avoid repeated DB work in Jinja
    @app.context_processor
    def inject_template_context():
        from app.models.notification import Notification

        css_path = os.path.join(app.static_folder, 'css', 'uno.css')
        css_version = int(os.path.getmtime(css_path)) if os.path.exists(css_path) else 1

        unread_count = 0
        last_notifications = []
        brand_app_name = app.config.get('APP_NAME', 'Rinde Fácil')
        brand_logo_url = None
        brand_icon_url = None
        brand_default_domain = ''
        if current_user.is_authenticated:
            notifications_q = Notification.query.filter_by(user_id=current_user.id)
            unread_count = notifications_q.filter_by(is_read=False).count()
            last_notifications = notifications_q.order_by(Notification.created_at.desc()).limit(5).all()

            company_settings = current_user.company.settings or {}
            brand_app_name = company_settings.get('brand_app_name') or brand_app_name
            brand_logo_url = company_settings.get('brand_logo_url') or None
            brand_icon_url = company_settings.get('brand_icon_url') or None
            brand_default_domain = (
                company_settings.get('brand_user_default_domain')
                or company_settings.get('brand_default_domain')
                or ''
            )

        return dict(
            css_version=css_version,
            unread_count=unread_count,
            last_notifications=last_notifications,
            brand_app_name=brand_app_name,
            brand_logo_url=brand_logo_url,
            brand_icon_url=brand_icon_url,
            brand_default_domain=brand_default_domain,
        )

    # Security Headers
    @app.after_request
    def set_security_headers(response):
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob: *; "
            "connect-src 'self' https://openrouter.ai;"
        )
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response

    # Global Error Handlers
    @app.errorhandler(401)
    def unauthorized_error(error):
        return render_template('errors/401.html'), 401

    @app.errorhandler(403)
    def forbidden_error(error):
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found_error(error):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        return render_template('errors/500.html'), 500

    return app
