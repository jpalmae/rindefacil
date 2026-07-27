from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from app.extensions import db

class Company(db.Model):
    __tablename__ = 'companies'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(255), nullable=False)
    rut = db.Column(db.String(12))
    plan = db.Column(db.String(50), default='basic')
    settings = db.Column(JSONB, default=lambda: {})
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
