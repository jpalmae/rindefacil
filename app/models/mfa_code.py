import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.extensions import db


MFA_CODE_TTL = timedelta(minutes=10)
MFA_CODE_MAX_ATTEMPTS = 5

MFA_CODE_PURPOSE_LOGIN = "login"
MFA_CODE_PURPOSE_SETUP = "setup"


class MfaCode(db.Model):
    __tablename__ = "mfa_codes"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    code_hash = db.Column(db.String(64), nullable=False, index=True)
    purpose = db.Column(db.String(32), nullable=False, default=MFA_CODE_PURPOSE_LOGIN)
    attempts = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    consumed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    user = db.relationship("User", backref=db.backref("mfa_codes", lazy="dynamic"))

    @staticmethod
    def generate_raw_code():
        return f"{secrets.randbelow(1_000_000):06d}"

    @staticmethod
    def hash_raw_code(raw_code):
        return hashlib.sha256(raw_code.encode("utf-8")).hexdigest()

    @classmethod
    def build_for_user(cls, user, purpose=MFA_CODE_PURPOSE_LOGIN):
        raw = cls.generate_raw_code()
        now = datetime.now(timezone.utc)
        instance = cls(
            user_id=user.id,
            code_hash=cls.hash_raw_code(raw),
            purpose=purpose,
            attempts=0,
            expires_at=now + MFA_CODE_TTL,
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
        return self.consumed_at is not None

    @property
    def is_maxed_attempts(self):
        return (self.attempts or 0) >= MFA_CODE_MAX_ATTEMPTS

    @property
    def is_valid(self):
        return not self.is_consumed and not self.is_expired and not self.is_maxed_attempts

    def matches(self, raw_code):
        expected = self.hash_raw_code(raw_code)
        return hmac.compare_digest(self.code_hash, expected)
