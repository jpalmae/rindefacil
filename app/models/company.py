from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from zoneinfo import ZoneInfo
import uuid
from app.extensions import db

BASE_CURRENCY_CHOICES = {
    'CLP': 'Peso chileno (CLP)',
    'PEN': 'Sol peruano (PEN)',
}

CURRENCY_SYMBOLS = {
    'CLP': '$',
    'PEN': 'S/',
    'USD': 'US$',
}

COMMON_TIMEZONES = [
    'America/Santiago',
    'America/Lima',
    'America/Bogota',
    'America/Mexico_City',
    'America/Argentina/Buenos_Aires',
    'America/Sao_Paulo',
    'UTC',
]

class Company(db.Model):
    __tablename__ = 'companies'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(255), nullable=False)
    rut = db.Column(db.String(12))
    plan = db.Column(db.String(50), default='basic')
    settings = db.Column(JSONB, default=lambda: {})
    timezone = db.Column(db.String(64), nullable=False, default='America/Santiago', server_default='America/Santiago')
    base_currency = db.Column(db.String(3), nullable=False, default='CLP', server_default='CLP')
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    # Relaciones
    users = db.relationship('User', back_populates='company', cascade='all, delete-orphan')
    categories = db.relationship('Category', back_populates='company', cascade='all, delete-orphan')
    cost_centers = db.relationship('CostCenter', back_populates='company', cascade='all, delete-orphan')
    expenses = db.relationship('Expense', back_populates='company')
    reports = db.relationship('Report', back_populates='company')

    def __repr__(self):
        return f'<Company {self.name}>'

    @property
    def py_timezone(self):
        """ZoneInfo de la empresa, con fallback seguro a Santiago."""
        from zoneinfo import ZoneInfo as _ZI
        try:
            return _ZI(self.timezone or 'America/Santiago')
        except Exception:
            return _ZI('America/Santiago')

    @property
    def allowed_expense_currencies(self):
        """Monedas de gasto permitidas para la empresa: base + USD."""
        base = self.base_currency or 'CLP'
        if base == 'USD':
            return ['USD']
        return [base, 'USD']

    @property
    def currency_symbol(self):
        return CURRENCY_SYMBOLS.get(self.base_currency, '$')
    @property
    def domain_label(self):
        """Etiqueta corta derivada del dominio de branding (sin TLD),
        capitalizada. Fall back al nombre de la empresa si no hay dominio.
        Ej: 'sixmanager.io' → 'Sixmanager', 'midominio.cl' → 'Midominio'.
        """
        settings = self.settings or {}
        domain = (settings.get('brand_user_default_domain') or '').strip()
        if not domain:
            # Intentar extraerlo del brand_app_url
            url = (settings.get('brand_app_url') or '').strip()
            if url:
                domain = url.replace('https://', '').replace('http://', '').split('/')[0]
        if not domain:
            return self.name
        # Extraer el SLD (second-level domain)
        parts = [p for p in domain.split('.') if p]
        if len(parts) >= 2:
            sld = parts[-2]
        elif parts:
            sld = parts[0]
        else:
            return self.name
        # Capitalizar primera letra
        return sld[:1].upper() + sld[1:]
