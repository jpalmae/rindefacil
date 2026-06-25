"""Servicio OIDC (porteo síncrono desde HermesHQ).

Maneja discovery + JWKS con caché, validación de id_token, resolución/creación
de usuario y construcción de URLs de autorización. Soporta cualquier IdP que
cumpla OIDC (Google, Microsoft Entra, Auth0, Okta, etc.).
"""

import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import jwt
import requests
from flask import current_app

from app.extensions import db
from app.models.oidc_provider import OidcProvider
from app.models.user import User, UserRole
from app.services.secrets_service import decrypt_setting, encrypt_setting

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
STATE_TOKEN_EXPIRY_MINUTES = 10
_DISCOVERY_CACHE_TTL = 3600  # 1 hora
_MAX_CACHE_ENTRIES = 50

_discovery_cache: dict[str, dict] = {}
_jwks_cache: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Caché de discovery + JWKS
# ---------------------------------------------------------------------------
def _evict_cache(cache: dict[str, dict]) -> None:
    now = time.time()
    expired = [k for k, v in cache.items() if (now - v.get("_fetched_at", 0)) >= _DISCOVERY_CACHE_TTL]
    for k in expired:
        cache.pop(k, None)
    while len(cache) > _MAX_CACHE_ENTRIES:
        oldest_key = min(cache, key=lambda k: cache[k].get("_fetched_at", 0), default=None)
        if oldest_key:
            cache.pop(oldest_key)
        else:
            break


def _fetch_discovery(discovery_url: str) -> dict:
    _evict_cache(_discovery_cache)
    now = time.time()
    cached = _discovery_cache.get(discovery_url)
    if cached and (now - cached["_fetched_at"]) < _DISCOVERY_CACHE_TTL:
        return cached
    url = f"{discovery_url.rstrip('/')}/.well-known/openid-configuration"
    resp = requests.get(url, timeout=15.0)
    resp.raise_for_status()
    doc = resp.json()
    doc["_fetched_at"] = now
    _discovery_cache[discovery_url] = doc
    return doc


def _fetch_jwks(jwks_uri: str) -> list[dict]:
    _evict_cache(_jwks_cache)
    now = time.time()
    cached = _jwks_cache.get(jwks_uri)
    if cached and (now - cached["_fetched_at"]) < _DISCOVERY_CACHE_TTL:
        return cached["keys"]
    resp = requests.get(jwks_uri, timeout=15.0)
    resp.raise_for_status()
    data = resp.json()
    _jwks_cache[jwks_uri] = {"keys": data.get("keys", []), "_fetched_at": now}
    return data.get("keys", [])


# ---------------------------------------------------------------------------
# Helpers para secrets
# ---------------------------------------------------------------------------
def get_provider_secret(provider: OidcProvider) -> str:
    raw = provider.client_secret or ""
    if not raw:
        return ""
    try:
        return decrypt_setting(raw) or ""
    except RuntimeError:
        return ""


# ---------------------------------------------------------------------------
# State JWT (incluye provider_slug + company_id)
# ---------------------------------------------------------------------------
def create_state_token(provider_slug: str, company_id: str) -> str:
    payload = {
        "provider": provider_slug,
        "company_id": str(company_id),
        "nonce": secrets.token_urlsafe(16),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=STATE_TOKEN_EXPIRY_MINUTES),
    }
    return jwt.encode(payload, current_app.config["SECRET_KEY"], algorithm="HS256")


def verify_state_token(state: str) -> dict | None:
    try:
        decoded = jwt.decode(state, current_app.config["SECRET_KEY"], algorithms=["HS256"])
        if not decoded.get("provider") or not decoded.get("company_id"):
            return None
        return decoded
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        logger.warning("OIDC state token inválido", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Authorization URL builder
# ---------------------------------------------------------------------------
def build_authorization_url(provider: OidcProvider, redirect_uri: str, state: str) -> str:
    discovery = _fetch_discovery(provider.discovery_url)
    auth_endpoint = discovery.get("authorization_endpoint")
    if not auth_endpoint:
        raise ValueError("OIDC discovery sin authorization_endpoint")
    params = urlencode({
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": provider.scopes or "openid profile email",
        "state": state,
    })
    return f"{auth_endpoint}?{params}"


# ---------------------------------------------------------------------------
# Code exchange + claims
# ---------------------------------------------------------------------------
def exchange_code_and_get_claims(provider: OidcProvider, code: str, redirect_uri: str) -> dict:
    discovery = _fetch_discovery(provider.discovery_url)
    token_endpoint = discovery.get("token_endpoint")
    if not token_endpoint:
        raise ValueError("OIDC discovery sin token_endpoint")

    client_secret = get_provider_secret(provider)

    # 1) Exchange code → tokens
    token_resp = requests.post(
        token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": provider.client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20.0,
    )
    token_resp.raise_for_status()
    token_payload = token_resp.json()

    claims: dict = {}

    # 2) Validar id_token si viene
    id_token = token_payload.get("id_token")
    if id_token and isinstance(id_token, str):
        try:
            claims = _validate_id_token(id_token, provider, discovery)
        except Exception as exc:
            logger.warning("No se pudo validar id_token para %s: %s", provider.slug, exc)

    # 3) Traer claims extra del userinfo endpoint
    access_token = token_payload.get("access_token")
    userinfo_endpoint = discovery.get("userinfo_endpoint")
    if userinfo_endpoint and access_token:
        try:
            ui_resp = requests.get(
                userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15.0,
            )
            ui_resp.raise_for_status()
            userinfo = ui_resp.json()
            claims = {**claims, **userinfo}
        except (requests.RequestException, ValueError) as exc:
            logger.warning("No se pudo traer userinfo de %s: %s", provider.slug, exc)

    if not claims.get("sub"):
        raise ValueError("OIDC claims no incluyen 'sub'")

    return claims


def _validate_id_token(id_token: str, provider: OidcProvider, discovery: dict) -> dict:
    jwks_uri = discovery.get("jwks_uri")
    if not jwks_uri:
        logger.warning("Discovery sin jwks_uri para %s; salto validación de firma", provider.slug)
        try:
            return jwt.decode(id_token, options={"verify_signature": False})
        except jwt.PyJWTError:
            return {}

    keys = _fetch_jwks(jwks_uri)
    if not keys:
        return {}

    issuer = discovery.get("issuer")
    audience = provider.client_id
    algorithms = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]

    for key_data in keys:
        try:
            kty = str(key_data.get("kty", "")).upper()
            if kty == "RSA":
                public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
            elif kty == "EC":
                public_key = jwt.algorithms.ECAlgorithm.from_jwk(key_data)
            else:
                continue
            return jwt.decode(
                id_token,
                key=public_key,
                algorithms=algorithms,
                audience=audience,
                issuer=issuer,
                options={"verify_exp": True},
            )
        except jwt.ExpiredSignatureError:
            raise
        except (jwt.PyJWTError, ValueError, KeyError, TypeError):
            continue

    logger.warning("No se pudo validar id_token con ninguna llave JWKS para %s", provider.slug)
    return {}


# ---------------------------------------------------------------------------
# Resolución / creación de usuario
# ---------------------------------------------------------------------------
def _extract_claim(data, *names):
    for name in names:
        if isinstance(data, dict) and data.get(name) is not None:
            return data.get(name)
    return None


def _normalize_email(email):
    if not email:
        return None
    normalized = email.strip().lower()
    return normalized or None


def _derive_display_name(claims: dict) -> str:
    name = _extract_claim(claims, "name", "preferred_username")
    if isinstance(name, str) and name.strip():
        return name.strip()[:255]
    first = _extract_claim(claims, "given_name")
    last = _extract_claim(claims, "family_name")
    full = " ".join(p.strip() for p in [str(first or ""), str(last or "")] if p.strip()).strip()
    if full:
        return full[:255]
    email = _normalize_email(_extract_claim(claims, "email"))
    if email:
        return email.split("@", 1)[0][:255]
    sub = str(_extract_claim(claims, "sub") or "oidc-user")
    return sub[:255]


def _check_allowed_domains(email: str, provider: OidcProvider) -> None:
    if not provider.allowed_domains or not email:
        return
    allowed = [d.strip().lower() for d in provider.allowed_domains.split(",") if d.strip()]
    if not allowed:
        return
    domain = email.split("@")[-1].lower()
    if domain not in allowed:
        raise PermissionError(
            f"El dominio '{domain}' no está permitido para este provider. Permitidos: {', '.join(allowed)}"
        )


def resolve_or_create_user(company, claims: dict, provider: OidcProvider) -> User:
    """Busca usuario existente por oidc_subject o email; crea si auto_provision."""
    subject = str(_extract_claim(claims, "sub") or "").strip()
    email = _normalize_email(_extract_claim(claims, "email"))
    display_name = _derive_display_name(claims)

    _check_allowed_domains(email, provider)

    composite_sub = f"{provider.slug}:{subject}" if subject else None

    user: User | None = None
    if composite_sub:
        user = User.query.filter_by(oidc_subject=composite_sub).first()

    if not user and email:
        user = User.query.filter_by(email=email).first()

    if not user and not provider.auto_provision:
        raise PermissionError(
            "Esta identidad no está registrada en la empresa. Contacta al administrador."
        )

    if not user:
        # Auto-provision como employee de la empresa
        user = User(
            company_id=company.id,
            email=email or f"oidc_{secrets.token_hex(8)}@noemail.local",
            full_name=display_name,
            role=UserRole.EMPLOYEE,
            is_active=True,
            must_change_password=False,
            password_hash="!",  # sin password local válida
            auth_source="oidc",
            oidc_subject=composite_sub,
        )
        db.session.add(user)
        db.session.commit()
        return user

    # Actualizar usuario existente
    if email and user.email != email:
        user.email = email
    if display_name and user.full_name != display_name:
        user.full_name = display_name
    if composite_sub and user.oidc_subject != composite_sub:
        user.oidc_subject = composite_sub
    user.auth_source = "oidc"
    db.session.commit()
    return user


# ---------------------------------------------------------------------------
# Logout social (opcional)
# ---------------------------------------------------------------------------
def get_logout_url(provider: OidcProvider, post_logout_uri: str) -> str | None:
    slug = (provider.slug or "").lower()
    if slug == "google":
        return f"https://accounts.google.com/Logout?continue={post_logout_uri}"
    discovery_url = (provider.discovery_url or "").lower()
    if "microsoftonline" in discovery_url or slug == "microsoft":
        parts = provider.discovery_url.split("/")
        for i, p in enumerate(parts):
            if "microsoftonline" in p and i + 1 < len(parts):
                tenant = parts[i + 1]
                return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/logout?post_logout_redirect_uri={post_logout_uri}"
    return None


# ---------------------------------------------------------------------------
# Helpers para admin (encripción del secret)
# ---------------------------------------------------------------------------
def save_provider_secret(provider: OidcProvider, raw_secret: str) -> None:
    """Cifra y guarda el client_secret en el provider."""
    if not raw_secret:
        return
    provider.client_secret = encrypt_setting(raw_secret)
