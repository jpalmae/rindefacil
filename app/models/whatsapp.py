import uuid
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.extensions import db


class WhatsappSession(db.Model):
    """Estado de conversación por número de WhatsApp.

    Un teléfono se vincula a una cuenta de usuario (fila users). Si la persona
    tiene cuentas en varias empresas, active_company_id indica el contexto
    activo y user_id apunta a la fila users correspondiente.
    """

    __tablename__ = "whatsapp_sessions"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone = db.Column(db.String(20), nullable=False, unique=True, index=True)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=True)
    active_company_id = db.Column(UUID(as_uuid=True), db.ForeignKey("companies.id"), nullable=True)
    state = db.Column(db.String(40), nullable=False, default="idle")
    state_data = db.Column(JSONB, nullable=False, default=dict, server_default="{}")
    linked_at = db.Column(db.DateTime(timezone=True))
    last_interaction_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = db.relationship("User", foreign_keys=[user_id])
    company = db.relationship("Company", foreign_keys=[active_company_id])

    # Estados del bot
    STATE_IDLE = "idle"
    STATE_LINK_EMAIL = "link_awaiting_email"
    STATE_LINK_OTP = "link_awaiting_otp"

    def touch(self):
        self.last_interaction_at = datetime.now(timezone.utc)


class WhatsappProcessedEvent(db.Model):
    """Idempotencia de webhooks: Kapso reintenta si no recibe 200 a tiempo."""

    __tablename__ = "whatsapp_processed_events"

    event_key = db.Column(db.String(80), primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
