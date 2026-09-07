"""Webhook de Kapso (WhatsApp) — entrada de mensajes.

Recibe POST /api/whatsapp/webhook con eventos de Kapso:
- Verifica firma HMAC-SHA256 (X-Webhook-Signature) contra el body crudo
- Idempotencia por X-Idempotency-Key (tabla whatsapp_processed_events)
- Responde 200 inmediatamente y procesa en un thread aparte (límite 10s de Kapso)
"""
import hashlib
import hmac
import threading

from flask import Blueprint, current_app, request

from app.extensions import db
from app.models.whatsapp import WhatsappProcessedEvent
from app.services.whatsapp_bot_service import handle_incoming_message

whatsapp_bot_bp = Blueprint("whatsapp_bot", __name__, url_prefix="/api/whatsapp")


def _verify_signature(raw_body: bytes, signature: str) -> bool:
    secret = current_app.config.get("KAPSO_WEBHOOK_SECRET") or ""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _already_processed(event_key: str) -> bool:
    existing = db.session.get(WhatsappProcessedEvent, event_key)
    return existing is not None


def _mark_processed(event_key: str):
    try:
        db.session.add(WhatsappProcessedEvent(event_key=event_key))
        db.session.commit()
    except Exception:
        db.session.rollback()
        # Duplicate PK en carrera entre workers → ya procesado por otro worker
        current_app.logger.info("Webhook WhatsApp duplicado (race): %s", event_key)


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

        if _already_processed(event_key):
            continue

        if (event_name == "whatsapp.message.received" or item.get("event") == "whatsapp.message.received"):
            app = current_app._get_current_object()
            thread = threading.Thread(
                target=_process_async,
                args=(app, item, event_key),
                daemon=True,
            )
            thread.start()
        else:
            _mark_processed(event_key)

    return {"ok": True}, 200


def _process_async(app, payload, event_key):
    with app.app_context():
        try:
            handle_incoming_message(payload)
        except Exception:
            current_app.logger.exception("Error en procesamiento async de webhook WhatsApp")
            db.session.rollback()
        finally:
            _mark_processed(event_key)
