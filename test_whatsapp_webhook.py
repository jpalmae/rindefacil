"""Test del webhook de WhatsApp: firma, idempotencia (evento + wamid), batch."""
import hashlib
import hmac
import json
import os
import sys
import time

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


def post(body, key, wamid='wamid.1', event='whatsapp.message.received'):
    raw = json.dumps(body).encode()
    sig = hmac.new(b'test-secret', raw, hashlib.sha256).hexdigest()
    return client.post('/api/whatsapp/webhook', data=raw, content_type='application/json',
                       headers={'X-Webhook-Signature': sig, 'X-Webhook-Event': event,
                                'X-Idempotency-Key': key})


def msg(wamid='wamid.1', text='hola'):
    return {"message": {"id": wamid, "type": "text", "from": "56912345678",
                        "text": {"body": text}, "kapso": {"direction": "inbound"}},
            "conversation": {"id": "c1", "phone_number": "56912345678"},
            "phone_number_id": "123"}


with app.app_context():
    # 1. Firma
    r = client.post('/api/whatsapp/webhook', data=b'{}', content_type='application/json')
    check('sin firma rechazado 401', r.status_code == 401)

    # 2. Mensaje válido → despacha
    r = post(msg(), 'k1')
    check('mensaje aceptado 200', r.status_code == 200)
    time.sleep(0.4)
    check('despachado', len(dispatched) == 1)

    # 3. Mismo idempotency key, OTRO wamid → duplicado de evento
    r = post(msg(wamid='wamid.2'), 'k1')
    time.sleep(0.4)
    check('evento duplicado descartado', len(dispatched) == 1)

    # 4. OTRO event key, MISMO wamid → redelivery de Kapso con key nueva
    r = post(msg(wamid='wamid.1'), 'k2')
    time.sleep(0.4)
    check('redelivery por wamid descartado', len(dispatched) == 1)

    # 5. Evento nuevo con wamid nuevo → despacha
    r = post(msg(wamid='wamid.3'), 'k3')
    time.sleep(0.4)
    check('mensaje nuevo despachado', len(dispatched) == 2)

    # 6. Doble envío CONCURRENTE del mismo mensaje (carrera entre workers)
    import threading
    threads = [threading.Thread(target=lambda: post(msg(wamid='wamid.9'), f'race-{i}'))
               for i in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    time.sleep(0.6)
    check('carrera: mismo wamid despachado una sola vez', len(dispatched) == 3)

    # 7. Batch
    batch = {"batch": True, "data": [msg(wamid='wamid.b1'), msg(wamid='wamid.b2')]}
    raw = json.dumps(batch).encode()
    sig = hmac.new(b'test-secret', raw, hashlib.sha256).hexdigest()
    r = client.post('/api/whatsapp/webhook', data=raw, content_type='application/json',
                    headers={'X-Webhook-Signature': sig, 'X-Webhook-Event': 'whatsapp.message.received',
                             'X-Idempotency-Key': 'kb1'})
    time.sleep(0.4)
    check('batch despacha ambos', len(dispatched) == 5)

    # Limpieza
    from app.models.whatsapp import WhatsappProcessedEvent
    for prefix in ('evt:k', 'wa:wamid', 'evt:race', 'evt:kb1'):
        for row in WhatsappProcessedEvent.query.filter(WhatsappProcessedEvent.event_key.startswith(prefix)).all():
            _db.session.delete(row)
    _db.session.commit()

print(f"\n{PASS} pasaron, {FAIL} fallaron")
sys.exit(1 if FAIL else 0)
