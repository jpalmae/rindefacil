"""Webhook de Kapso (WhatsApp) — entrada de mensajes.

Recibe POST /api/whatsapp/webhook con eventos de Kapso:
- Verifica firma HMAC-SHA256 (X-Webhook-Signature) contra el body crudo
- Idempotencia doble: por X-Idempotency-Key y por message.id (wamid),
  reclamada ANTES de despachar (evita dobles-procesos por redelivery o
  carrera entre workers)
- Responde 200 inmediatamente y procesa en un thread aparte (límite 10s de Kapso)
"""
import hashlib
import hmac
import threading
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.whatsapp import WhatsappProcessedEvent
from app.services.whatsapp_bot_service import handle_incoming_message

whatsapp_bot_bp = Blueprint("whatsapp_bot", __name__, url_prefix="/api/whatsapp")

EVENT_TTL = timedelta(days=7)


def _verify_signature(raw_body: bytes, signature: str) -> bool:
    secret = current_app.config.get("KAPSO_WEBHOOK_SECRET") or ""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _keys_for(item, event_key, is_message_event):
    """Claves de idempotencia (hasheadas, longitud constante ~67 chars):
    la del evento + el wamid del mensaje."""
    keys = ["evt:" + hashlib.sha256(event_key.encode()).hexdigest()]
    if is_message_event:
        wamid = (item.get("message") or {}).get("id") or ""
        if wamid:
            keys.append("wa:" + hashlib.sha256(wamid.encode()).hexdigest())
    return keys


def _claim(keys):
    """Reclama las claves. True = somos los primeros (procesar).
    False = alguna ya existía (duplicado o carrera con otro worker)."""
    for key in keys:
        if db.session.get(WhatsappProcessedEvent, key) is not None:
            return False
    for key in keys:
        db.session.add(WhatsappProcessedEvent(event_key=key))
    try:
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False
    except Exception:
        # Cualquier otro fallo de BD al reclamar: no despachar (evita dobles)
        db.session.rollback()
        current_app.logger.exception("No se pudo reclamar evento WhatsApp %s", keys)
        return False


def _cleanup_old_events():
    try:
        cutoff = datetime.now(timezone.utc) - EVENT_TTL
        WhatsappProcessedEvent.query.filter(
            WhatsappProcessedEvent.created_at < cutoff
        ).delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()


@whatsapp_bot_bp.route("/webhook", methods=["POST"])
def webhook():
    raw_body = request.get_data(cache=True) or b""
    signature = request.headers.get("X-Webhook-Signature", "")
    event_name = request.headers.get("X-Webhook-Event", "")

    if not _verify_signature(raw_body, signature):
        current_app.logger.warning("Webhook WhatsApp con firma inválida")
        return {"error": "invalid signature"}, 401

    payload = request.get_json(silent=True) or {}

    # Batch envelope (buffering activado): {"batch": true, "data": [...]}
    payloads = payload.get("data") if payload.get("batch") else [payload]
    if not payloads:
        return {"ok": True}, 200

    is_batch = bool(payload.get("batch"))
    for index, item in enumerate(payloads):
        event_key = request.headers.get("X-Idempotency-Key", "") if not is_batch else ""
        if is_batch:
            event_key = f"{request.headers.get('X-Idempotency-Key', '')}#{index}"
        if not event_key:
            event_key = hashlib.sha256(raw_body + str(index).encode()).hexdigest()

        is_message_event = (
            event_name == "whatsapp.message.received"
            or item.get("event") == "whatsapp.message.received"
        )

        keys = _keys_for(item, event_key, is_message_event)
        if not _claim(keys):
            current_app.logger.info("Webhook WhatsApp duplicado descartado: %s", keys)
            continue

        if not is_message_event:
            continue  # reclamar basta para eventos sin mensaje

        app = current_app._get_current_object()
        threading.Thread(
            target=_process_async,
            args=(app, item),
            daemon=True,
        ).start()

    _cleanup_old_events()
    return {"ok": True}, 200


def _process_async(app, payload):
    # test_request_context provee request para url_for (notificaciones usan
    # links); sin esto, url_for fuera de request exige SERVER_NAME y falla.
    with app.test_request_context("/"):
        try:
            handle_incoming_message(payload)
        except Exception:
            current_app.logger.exception("Error en procesamiento async de webhook WhatsApp")
            db.session.rollback()
