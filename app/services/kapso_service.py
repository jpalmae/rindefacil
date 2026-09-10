"""Cliente de la API de Kapso (proxy Meta WhatsApp Cloud API).

Docs: https://docs.kapso.ai — Meta Proxy API
Base: https://api.kapso.ai/meta/whatsapp/v24.0
Auth: header X-API-Key
"""
import requests
from flask import current_app

KAPSO_BASE_URL = "https://api.kapso.ai/meta/whatsapp/v24.0"
KAPSO_MEDIA_BASE_URL = "https://api.kapso.ai/media"
KAPSO_TIMEOUT = 15


def _config():
    api_key = current_app.config.get("KAPSO_API_KEY")
    phone_number_id = current_app.config.get("KAPSO_PHONE_NUMBER_ID")
    return api_key, phone_number_id


def is_enabled():
    api_key, phone_number_id = _config()
    return bool(api_key and phone_number_id)


def _headers():
    api_key, _ = _config()
    return {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }


def _post_message(payload, _retried=False):
    _, phone_number_id = _config()
    response = requests.post(
        f"{KAPSO_BASE_URL}/{phone_number_id}/messages",
        headers=_headers(),
        json=payload,
        timeout=KAPSO_TIMEOUT,
    )
    if response.status_code in (409, 429) and not _retried:
        # mensaje in-flight al mismo destinatario o rate limit: esperar y reintentar una vez
        import time
        time.sleep(1.2)
        return _post_message(payload, _retried=True)
    if response.status_code >= 400:
        current_app.logger.error(
            "Kapso send failed %s: %s | payload_type=%s",
            response.status_code,
            response.text[:300],
            payload.get("type"),
        )
        return None
    return response.json()


def _base(to):
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
    }


def send_text(to, text):
    """Envía texto plano. preview_url activa previews de links."""
    return _post_message({
        **_base(to),
        "type": "text",
        "text": {"body": text, "preview_url": "true"},
    })


def send_buttons(to, body, buttons, header=None, footer=None):
    """Botones de respuesta rápida. Máximo 3 (límite Meta).

    buttons: lista de (id, title) — title máx 20 chars.
    """
    if len(buttons) > 3:
        current_app.logger.warning("send_buttons con %d botones (máx 3), recortando: %s", len(buttons), [b[1] for b in buttons])
        buttons = buttons[:3]
    interactive = {
        "type": "button",
        "body": {"text": body},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": btn_id, "title": title[:20]}}
                for btn_id, title in buttons
            ]
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    if footer:
        interactive["footer"] = {"text": footer[:60]}
    return _post_message({
        **_base(to),
        "type": "interactive",
        "interactive": interactive,
    })


def send_list(to, body, button_text, sections, header=None, footer=None):
    """Lista de opciones. Máx 10 filas en total.

    sections: lista de {"title": str, "rows": [{"id","title","description?"}]}
    """
    interactive = {
        "type": "list",
        "body": {"text": body},
        "action": {"button": button_text[:20], "sections": sections},
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    if footer:
        interactive["footer"] = {"text": footer[:60]}
    return _post_message({
        **_base(to),
        "type": "interactive",
        "interactive": interactive,
    })


def send_location_request(to, body_text):
    """Pide al usuario compartir su ubicación (botón 'Enviar ubicación')."""
    return _post_message({
        **_base(to),
        "type": "interactive",
        "interactive": {
            "type": "location_request_message",
            "body": {"text": body_text},
            "action": {"name": "send_location"},
        },
    })


def send_image(to, link, caption=None):
    payload = {
        **_base(to),
        "type": "image",
        "image": {"link": link},
    }
    if caption:
        payload["image"]["caption"] = caption[:1024]
    return _post_message(payload)


def send_template(to, template_name, lang="es", body_params=None, buttons_params=None):
    """Envía template aprobado por Meta (necesario fuera de ventana 24h)."""
    template = {"name": template_name, "language": {"code": lang}}
    components = []
    if body_params:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": str(p)} for p in body_params],
        })
    if buttons_params:
        for index, params in enumerate(buttons_params):
            if params is None:
                continue
            components.append({
                "type": "button",
                "sub_type": "quick_reply",
                "index": str(index),
                "parameters": [{"type": "payload", "payload": str(p)} for p in params],
            })
    if components:
        template["components"] = components
    return _post_message({
        **_base(to),
        "type": "template",
        "template": template,
    })


def mark_read(message_id, typing=True):
    """Marca mensaje como leído (doble check azul) + indicador escribiendo."""
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    if typing:
        payload["typing_indicator"] = {"type": "text"}
    return _post_message(payload)


def download_media(media_url, timeout=60):
    """Descarga el archivo de media desde la URL firmada del webhook."""
    api_key, _ = _config()
    response = requests.get(
        media_url,
        headers={"X-API-Key": api_key} if api_key else {},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.content
