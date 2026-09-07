"""Test del webhook de WhatsApp: verificación de firma, idempotencia y dispatch."""
import hashlib
import hmac
import json
import os
import sys

os.environ.setdefault('KAPSO_WEBHOOK_SECRET', 'test-secret')

from app import create_app
from app.extensions import db as _db

PASS = 0
FAIL = 0


def check(name, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name}")


app = create_app(os.environ.get('RINDE_TEST_ENV', 'development'))
app.config['KAPSO_WEBHOOK_SECRET'] = os.environ.get('KAPSO_WEBHOOK_SECRET', 'test-secret')
app.config['TESTING'] = True

dispatched = []


def fake_handler(payload):
    dispatched.append(payload)


import app.blueprints.whatsapp_bot as wb
wb.handle_incoming_message = fake_handler

client = app.test_client()

body = {
    "message": {"id": "wamid.1", "type": "text", "from": "56912345678",
                "text": {"body": "hola"},
                "kapso": {"direction": "inbound"}},
    "conversation": {"id": "c1", "phone_number": "56912345678"},
    "phone_number_id": "123",
}
raw = json.dumps(body).encode()
sig = hmac.new(b'test-secret', raw, hashlib.sha256).hexdigest()

with app.app_context():
    # 1. Sin firma → 401
    r = client.post('/api/whatsapp/webhook', data=raw, content_type='application/json')
    check('sin firma rechazado 401', r.status_code == 401)

    # 2. Firma inválida → 401
    r = client.post('/api/whatsapp/webhook', data=raw, content_type='application/json',
                    headers={'X-Webhook-Signature': 'deadbeef', 'X-Webhook-Event': 'whatsapp.message.received'})
    check('firma invalida rechazada 401', r.status_code == 401)

    # 3. Firma válida → 200 y dispatch (dar tiempo al thread)
    r = client.post('/api/whatsapp/webhook', data=raw, content_type='application/json',
                    headers={'X-Webhook-Signature': sig, 'X-Webhook-Event': 'whatsapp.message.received',
                             'X-Idempotency-Key': 'evt-1'})
    check('firma valida aceptada 200', r.status_code == 200)

    import time
    time.sleep(0.5)
    check('mensaje despachado al handler', len(dispatched) == 1)
    check('telefono extraido', dispatched and dispatched[0]['message']['from'] == '56912345678')

    # 4. Idempotencia: mismo event key → no re-despacha
    r = client.post('/api/whatsapp/webhook', data=raw, content_type='application/json',
                    headers={'X-Webhook-Signature': sig, 'X-Webhook-Event': 'whatsapp.message.received',
                             'X-Idempotency-Key': 'evt-1'})
    check('reintento aceptado 200', r.status_code == 200)
    time.sleep(0.5)
    check('evento duplicado NO re-despachado', len(dispatched) == 1)

    # 5. Evento distinto → despacha
    body2 = dict(body, message=dict(body['message'], id='wamid.2'))
    raw2 = json.dumps(body2).encode()
    sig2 = hmac.new(b'test-secret', raw2, hashlib.sha256).hexdigest()
    r = client.post('/api/whatsapp/webhook', data=raw2, content_type='application/json',
                    headers={'X-Webhook-Signature': sig2, 'X-Webhook-Event': 'whatsapp.message.received',
                             'X-Idempotency-Key': 'evt-2'})
    time.sleep(0.5)
    check('evento nuevo despachado', len(dispatched) == 2)

    # 6. Batch envelope
    batch = {"batch": True, "data": [body, body2]}
    rawb = json.dumps(batch).encode()
    sigb = hmac.new(b'test-secret', rawb, hashlib.sha256).hexdigest()
    r = client.post('/api/whatsapp/webhook', data=rawb, content_type='application/json',
                    headers={'X-Webhook-Signature': sigb, 'X-Webhook-Event': 'whatsapp.message.received',
                             'X-Idempotency-Key': 'evt-b1'})
    check('batch aceptado 200', r.status_code == 200)

    # Limpieza de eventos de test
    from app.models.whatsapp import WhatsappProcessedEvent
    for key in ('evt-1', 'evt-2', 'evt-b1#0', 'evt-b1#1'):
        existing = _db.session.get(WhatsappProcessedEvent, key)
        if existing:
            _db.session.delete(existing)
    _db.session.commit()

print(f"\n{PASS} pasaron, {FAIL} fallaron")
sys.exit(1 if FAIL else 0)
