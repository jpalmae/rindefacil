import uuid

from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.extensions import db


class OidcProvider(db.Model):
    """Configuración de un proveedor OIDC (Google, Microsoft, etc.) por empresa."""
    __tablename__ = "oidc_providers"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = db.Column(UUID(as_uuid=True), db.ForeignKey("companies.id"), nullable=False, index=True)
    slug = db.Column(db.String(64), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    client_id = db.Column(db.String(512), nullable=False)
    # client_secret se guarda cifrado con Fernet (prefijo 'enc:'), ver secrets_service.
    client_secret = db.Column(db.String(1024), nullable=False)
    discovery_url = db.Column(db.String(1024), nullable=False)
    scopes = db.Column(db.String(512), nullable=False, default="openid profile email")
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    auto_provision = db.Column(db.Boolean, nullable=False, default=False)
    allowed_domains = db.Column(db.Text, nullable=True)
    icon_slug = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    company = db.relationship("Company", backref=db.backref("oidc_providers", lazy="dynamic", cascade="all, delete-orphan"))

    __table_args__ = (
        db.UniqueConstraint("company_id", "slug", name="uq_oidc_providers_company_slug"),
    )

    def __repr__(self):
        return f"<OidcProvider {self.slug} company={self.company_id}>"
