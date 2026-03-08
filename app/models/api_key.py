import hashlib
import hmac
import secrets
import uuid

from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.extensions import db


class UserApiKey(db.Model):
    __tablename__ = "user_api_keys"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    company_id = db.Column(UUID(as_uuid=True), db.ForeignKey("companies.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False, default="Agente IA")
    key_prefix = db.Column(db.String(32), nullable=False)
    key_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    last_used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    user = db.relationship("User", back_populates="api_keys")
    company = db.relationship("Company")

    @property
    def is_active(self):
        return self.revoked_at is None

    @staticmethod
    def generate_raw_key():
        return f"rfk_{secrets.token_urlsafe(40)}"

    @staticmethod
    def hash_raw_key(raw_key):
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @classmethod
    def build_for_user(cls, user, name):
        raw_key = cls.generate_raw_key()
        instance = cls(
            user_id=user.id,
            company_id=user.company_id,
            name=name or "Agente IA",
            key_prefix=raw_key[:16],
            key_hash=cls.hash_raw_key(raw_key),
        )
        return instance, raw_key

    def matches(self, raw_key):
        expected = self.hash_raw_key(raw_key)
        return hmac.compare_digest(self.key_hash, expected)

