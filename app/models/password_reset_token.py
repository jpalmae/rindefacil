import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.extensions import db


RESET_TOKEN_TTL = timedelta(minutes=30)


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    user = db.relationship("User", backref=db.backref("password_reset_tokens", lazy="dynamic"))

    @staticmethod
    def generate_raw_token():
        return secrets.token_urlsafe(32)

    @staticmethod
    def hash_raw_token(raw_token):
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @classmethod
    def build_for_user(cls, user):
        raw = cls.generate_raw_token()
        now = datetime.now(timezone.utc)
        instance = cls(
            user_id=user.id,
            token_hash=cls.hash_raw_token(raw),
            expires_at=now + RESET_TOKEN_TTL,
        )
        return instance, raw

    @property
    def is_expired(self):
        if self.expires_at is None:
            return True
        tz = self.expires_at.tzinfo or timezone.utc
        return datetime.now(tz) >= self.expires_at

    @property
    def is_consumed(self):
        return self.used_at is not None

    @property
    def is_valid(self):
        return not self.is_consumed and not self.is_expired

    def matches(self, raw_token):
        expected = self.hash_raw_token(raw_token)
        return hmac.compare_digest(self.token_hash, expected)
