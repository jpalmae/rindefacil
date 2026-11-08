# Especificación — Envío de correos en Rinde Fácil
## Opción A: Cloudflare (Worker + Send Email) · Opción B: SMTP por empresa

Versión 1.0 — 04-11-2026. Basado en `app/services/email_service.py` @ commit `a49d721` (main).

---

## Contexto actual (lo que existe)

- Dispatcher: `send_email()` → si `email_provider == 'resend'` → `_send_via_resend()`; cualquier otro valor → `_send_via_smtp()` (flask-mail, config GLOBAL de la app, no por empresa).
- Settings por empresa en `company.settings` (JSON): `email_enabled`, `email_provider`, `email_from_name`, `email_from_address`, `email_reply_to`, `email_resend_api_key` (encriptado vía `encrypt_setting`/`decrypt_setting`), interruptores por evento (`email_notify_*`).
- Logo embebido como attachment inline `cid:brand-logo` (base64).
- Admin UI: `app/templates/admin/email_settings.html` + blueprint admin (guardado de settings).

**Principio rector de esta especificación: TODO aditivo. Ningún flujo existente se modifica; el default `resend` sigue intacto.**

---

# OPCIÓN A — Cloudflare Email (Worker con binding Send Email)

## A.1 Por qué esta arquitectura

Cloudflare NO ofrece API transaccional estilo Resend. La vía oficial es un **Worker** con binding `send_email` que envía MIME crudo desde un dominio propio con **Email Routing habilitado**. El Worker actúa como "mini-API de Resend" propia: la app Flask le hace POST con el mismo formato de payload y él entrega.

Ventajas: sin costo (plan free), sin terceros, dominio propio ya gestionado en CF.
Cautelas: Send Email sigue en beta; sin webhooks de entrega/bounce; rate limits no publicados formalmente; **revisar en QA** si el destinatario requiere estar en destination addresses verificadas (limitación histórica de la beta — hoy permite cualquier destinatario con Email Routing activo en la zona, pero validar antes de producción).

## A.2 Prerrequisitos en Cloudflare

1. El dominio del remitente (ej. `rinde-facil.cl` o el dominio white-label del cliente) debe estar como **zona activa en CF**.
2. Habilitar **Email Routing** en esa zona (Dashboard → Email → Email Routing → Get started). CF agrega automáticamente los registros MX y SPF necesarios.
3. Verificar **al menos 1 destination address** (ej. una casilla real de la empresa) — requerido para completar el setup de Email Routing.
4. Tener `wrangler` CLI o usar el editor del dashboard.

## A.3 Worker completo (`rinde-mail`)

**`wrangler.toml`**
```toml
name = "rinde-mail"
main = "src/index.js"
compatibility_date = "2026-01-01"

send_email = [
  { name = "SEND_EMAIL" }
]

[observability]
enabled = true
```

Token de autorización como secret (NO en el toml):
```bash
wrangler secret put RINDE_TOKEN   # pegar token aleatorio largo, ej: openssl rand -hex 32
```

**`src/index.js`** (~110 líneas, sin dependencias externas)
```js
// rinde-mail — gateway de envío transaccional para Rinde Fácil
// POST /send  { from, to[], subject, text, html?, reply_to?, attachments?[] }
// Auth: Authorization: Bearer <RINDE_TOKEN>

const JSON_HEADERS = { 'Content-Type': 'application/json' };

function json(status, body) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

function buildMime(p) {
  const boundary = '----=_rinde_' + crypto.randomUUID();
  const altBoundary = '----=_rinde_alt_' + crypto.randomUUID();
  const h = [];
  const enc = (v) => (v || '').replace(/[\r\n]+/g, ' '); // anti header-injection

  h.push(`From: ${enc(p.from)}`);
  h.push(`To: ${Array.isArray(p.to) ? p.to.map(enc).join(', ') : enc(p.to)}`);
  h.push(`Subject: =?UTF-8?B?${btoa(unescape(encodeURIComponent(p.subject)))}?=`);
  if (p.reply_to) h.push(`Reply-To: ${enc(p.reply_to)}`);
  h.push('MIME-Version: 1.0');
  h.push(`Date: ${new Date().toUTCString()}`);
  h.push(`Message-ID: <${crypto.randomUUID()}@rinde-mail.workers.dev>`);

  const parts = [];
  // multipart/alternative: text + html
  let alt = [];
  alt.push(`--${altBoundary}`);
  alt.push('Content-Type: text/plain; charset=UTF-8');
  alt.push('Content-Transfer-Encoding: base64');
  alt.push('');
  alt.push(btoa(unescape(encodeURIComponent(p.text || ''))));
  if (p.html) {
    alt.push(`--${altBoundary}`);
    alt.push('Content-Type: text/html; charset=UTF-8');
    alt.push('Content-Transfer-Encoding: base64');
    alt.push('');
    alt.push(btoa(unescape(encodeURIComponent(p.html))));
  }
  alt.push(`--${altBoundary}--`);
  parts.push(`Content-Type: multipart/alternative; boundary="${altBoundary}"\r\n\r\n${alt.join('\r\n')}`);

  // attachments (incluye inline cid:brand-logo)
  for (const a of p.attachments || []) {
    const disp = a.content_id
      ? `inline; filename="${a.filename}";\r\nContent-ID: <${a.content_id}>`
      : `attachment; filename="${a.filename}"`;
    parts.push(
      `Content-Type: ${a.content_type || 'application/octet-stream'}\r\n` +
      `Content-Transfer-Encoding: base64\r\n` +
      `Content-Disposition: ${disp}\r\n\r\n${a.content}`
    );
  }

  const body = parts.length === 1 && !p.html
    ? parts[0]
    : `--${boundary}\r\n${parts.join(`\r\n--${boundary}\r\n`)}\r\n--${boundary}--`;

  if (parts.length === 1 && !p.html && !(p.attachments || []).length) {
    return `${h.join('\r\n')}\r\nContent-Type: text/plain; charset=UTF-8\r\nContent-Transfer-Encoding: base64\r\n\r\n${btoa(unescape(encodeURIComponent(p.text || '')))}`;
  }
  return `${h.join('\r\n')}\r\nContent-Type: multipart/mixed; boundary="${boundary}"\r\n\r\n${body}`;
}

export default {
  async fetch(request, env) {
    if (request.method !== 'POST') return json(405, { error: 'method not allowed' });

    const auth = request.headers.get('Authorization') || '';
    if (auth !== `Bearer ${env.RINDE_TOKEN}`) return json(401, { error: 'unauthorized' });

    let p;
    try { p = await request.json(); } catch { return json(400, { error: 'invalid json' }); }

    if (!p.from || !p.to || !p.subject) return json(400, { error: 'from, to y subject son obligatorios' });

    const { EmailMessage } = await import('cloudflare:email');
    const raw = buildMime(p);
    const msg = new EmailMessage(
      p.from,
      Array.isArray(p.to) ? p.to.join(', ') : p.to,
      raw
    );

    try {
      await env.SEND_EMAIL.send(msg);
      return json(200, { ok: true });
    } catch (e) {
      return json(502, { ok: false, error: String(e && e.message || e) });
    }
  },
};
```

Deploy:
```bash
npm create cloudflare@latest rinde-mail  # si parte de cero
wrangler secret put RINDE_TOKEN
wrangler deploy
# resultado: https://rinde-mail.<account>.workers.dev
```
Opcional (recomendado): ruta propia `https://mail.<dominio>/send` con un Custom Domain del Worker.

**Límite del Worker**: un `send()` por request (si `to` trae N direcciones van en un solo sobre MIME — para Rinde Fácil es irrelevante porque todos los envíos actuales son a 1 destinatario).

## A.4 Cambios en Flask — aditivos

### A.4.1 `_company_email_settings()` — añadir 2 claves
```python
    raw_cf_token = settings.get('email_cloudflare_token') or ''
    try:
        cf_token = (decrypt_setting(raw_cf_token) or '').strip()
    except RuntimeError:
        cf_token = ''

    return {
        ...existing...,
        'cloudflare_endpoint': (settings.get('email_cloudflare_endpoint') or '').strip(),
        'cloudflare_token': cf_token,
    }
```

### A.4.2 Nueva función `_send_via_cloudflare()` (clon estructural de `_send_via_resend`)
```python
def _send_via_cloudflare(company, subject, recipients, body_text, body_html=None, reply_to=None):
    config = _company_email_settings(company)
    if not config['cloudflare_endpoint'] or not config['cloudflare_token']:
        current_app.logger.warning('Cloudflare worker endpoint/token missing.')
        _set_last_email_error('Falta endpoint o token del Worker de Cloudflare.')
        return False
    if not config['from_address']:
        _set_last_email_error('Falta la dirección remitente (From).')
        return False

    payload = {
        'from': _build_sender(config['from_name'], config['from_address']),
        'to': recipients,
        'subject': subject,
        'text': body_text,
    }
    if body_html:
        payload['html'] = body_html
    if reply_to or config['reply_to']:
        payload['reply_to'] = reply_to or config['reply_to']

    # logo inline — MISMO bloque que Resend (attachments con content_id)
    try:
        logo_path = _local_logo_path(company)
        if logo_path and body_html and 'cid:brand-logo' in body_html:
            import base64, mimetypes
            with open(logo_path, 'rb') as fh:
                content_b64 = base64.b64encode(fh.read()).decode('ascii')
            payload['attachments'] = [{
                'filename': os.path.basename(logo_path),
                'content': content_b64,
                'content_type': mimetypes.guess_type(logo_path)[0] or 'image/png',
                'content_id': 'brand-logo',
            }]
    except Exception as exc:
        current_app.logger.warning('No se pudo incrustar el logo inline: %s', exc)

    try:
        url = config['cloudflare_endpoint'].rstrip('/') + '/send'
        response = requests.post(url, headers={
            'Authorization': f"Bearer {config['cloudflare_token']}",
            'Content-Type': 'application/json',
        }, json=payload, timeout=20)
        if response.status_code not in (200, 201):
            detail = response.text[:300]
            current_app.logger.error(f'Cloudflare worker error {response.status_code}: {detail}')
            _set_last_email_error(f'Cloudflare {response.status_code}: {detail}')
            return False
        return True
    except Exception as e:
        current_app.logger.error(f'Error sending via Cloudflare worker: {e}')
        _set_last_email_error(f'Error de conexión con el Worker: {e}')
        return False
```

### A.4.3 Dispatcher `send_email()` — un branch nuevo
```python
    if config['provider'] == 'resend':
        return _send_via_resend(...)
    if config['provider'] == 'cloudflare':
        return _send_via_cloudflare(
            company, branded_subject, recipients, final_body_text,
            body_html=final_body_html, reply_to=reply_to,
        )
    return _send_via_smtp(...)
```

### A.4.4 `send_test_email()` — parametrizar por provider
Hoy valida `resend_api_key` hardcodeado. Cambio: construir el requisito según `config['provider']`:
```python
    provider = config['provider']
    if provider == 'resend' and not config['resend_api_key']:
        return False, 'Falta guardar la API key de Resend.'
    if provider == 'cloudflare' and not (config['cloudflare_endpoint'] and config['cloudflare_token']):
        return False, 'Falta el endpoint o el token del Worker de Cloudflare.'
    if provider == 'smtp' and not config['smtp_host']:
        return False, 'Falta configurar el servidor SMTP.'
```
(los mensajes de éxito del test dicen "Resend quedó operativa" — parametrizar el texto según provider).

### A.4.5 Vista admin `get_company_email_settings_view()`
Añadir `has_cf_token` / `masked_cf_token` (reutilizar `_mask_secret`) y exponer `cloudflare_endpoint` para pintarlo en el form.

### A.4.6 UI `email_settings.html`
- Select `email_provider`: `Resend` (default) | `Cloudflare` | `SMTP`
- Sección condicional "Cloudflare Worker": campos `endpoint` (URL) y `token` (password, guardar encriptado con `encrypt_setting` igual que la key de Resend — dejar vacío = conservar el actual)
- Sección condicional "SMTP" (ver Opción B)

### A.4.7 Blueprint admin — persistencia
Al guardar: si `email_provider == 'cloudflare'` → `settings['email_cloudflare_endpoint']`, `settings['email_cloudflare_token'] = encrypt_setting(token)` si vino no-vacío. Mismo patrón existente de la key de Resend.

## A.5 QA / aceptación (en orden)

1. `curl -X POST https://rinde-mail.<account>.workers.dev/send -H "Authorization: Bearer $TOKEN" -d '{"from":"Rinde <no-reply@DOMINIO>","to":["dest@x.com"],"subject":"QA","text":"hola"}'` → `{"ok":true}` y el correo llega.
2. **Si el paso 1 falla con "destination address not verified"** → limitación beta activa en la cuenta: verificar el destinatario en Email Routing → Destination addresses, o contactar soporte CF para habilitar envío general.
3. Stack QA (`rindefacil-qa`): settings provider=cloudflare + endpoint + token → botón "Enviar correo de prueba" → llega con logo inline visible.
4. Disparar un evento real (report_submitted) en QA → correo llega con branding.
5. Volver a `resend` y repetir el test → regresión OK (nada roto).

---

# OPCIÓN B — SMTP con settings por empresa

## B.1 Cambio de código necesario

`_send_via_smtp()` hoy usa flask-mail con config GLOBAL (`MAIL_SERVER` del `.env`) — no sirve para multi-tenant. Especificación: reescribirlo con `smtplib` directo leyendo settings por empresa, con **fallback a la config global** si la empresa no define SMTP (compatibilidad hacia atrás).

### Settings nuevos (por empresa)
- `email_smtp_host`, `email_smtp_port` (int)
- `email_smtp_user`, `email_smtp_pass` (encriptado)
- `email_smtp_security`: `'starttls'` (default) | `'ssl'` | `'none'`

### Nueva función (reemplaza el cuerpo de `_send_via_smtp`, misma firma + company)
```python
def _send_via_smtp(subject, recipients, body_text, body_html=None, company=None, reply_to=None):
    import smtplib, ssl
    from email.message import EmailMessage as PyMsg

    s = (company.settings or {}) if company else {}
    host = s.get('email_smtp_host') or current_app.config.get('MAIL_SERVER')
    port = int(s.get('email_smtp_port') or current_app.config.get('MAIL_PORT') or 587)
    user = s.get('email_smtp_user') or current_app.config.get('MAIL_USERNAME')
    raw_pass = s.get('email_smtp_pass') or current_app.config.get('MAIL_PASSWORD')
    try:
        password = decrypt_setting(raw_pass) if raw_pass else (current_app.config.get('MAIL_PASSWORD') or '')
    except RuntimeError:
        password = ''
    security = (s.get('email_smtp_security') or 'starttls').lower()

    if not host:
        current_app.logger.warning('SMTP no configurado. Skip.')
        _set_last_email_error('Falta configurar el servidor SMTP.')
        return False

    from_name = s.get('email_from_name') or current_app.config.get('APP_NAME', 'Rinde Fácil')
    from_addr = s.get('email_from_address') or current_app.config.get('MAIL_DEFAULT_SENDER') or user
    if not from_addr:
        _set_last_email_error('Falta la dirección remitente (From).')
        return False

    msg = PyMsg()
    msg['From'] = _build_sender(from_name, from_addr)
    msg['To'] = ', '.join(recipients)
    msg['Subject'] = subject
    if reply_to or (s.get('email_reply_to') or ''):
        msg['Reply-To'] = reply_to or s['email_reply_to']
    msg.set_content(body_text or '')
    if body_html:
        msg.add_alternative(body_html, subtype='html')

    try:
        ctx = ssl.create_default_context()
        if security == 'ssl':
            server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            if security == 'starttls':
                server.starttls(context=ctx)
        with server:
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as e:
        current_app.logger.error(f'Error SMTP: {e}')
        _set_last_email_error(f'Error SMTP: {e}')
        return False
```
Nota: el logo inline por `cid:` en SMTP requiere adjuntar `msg.add_attachment(..., cid='brand-logo')` — añadir el mismo bloque de logo con `msg.get_payload()[1].add_related(...)` (o dejar el logo como URL remota en la rama SMTP, que es el comportamiento pre-a49d721). Decisión menor, documentada.

En el dispatcher, `provider == 'smtp'` (o cualquier valor no-resend/cloudflare) → esta función.

## B.2 Configuraciones típicas (cheat-sheet)

Puerto estándar: **587 = STARTTLS** (recomendado), **465 = SSL implícito**, 25 = solo relay interno.

**Brevo (ex-Sendinblue)** — gratis 300/día
- Host `smtp-relay.brevo.com` · 587 STARTTLS · user = email de la cuenta, pass = SMTP key del panel.

**Mailgun**
- Host `smtp.mailgun.org` (EU: `smtp.eu.mailgun.org`) · 587 · user `postmaster@mg.tudominio`, pass = password SMTP del dominio. Requiere dominio verificado (DKIM).

**Amazon SES** — el más barato a volumen
- Host `email-smtp.<región>.amazonaws.com` (ej. `email-smtp.us-east-1.amazonaws.com`) · 587 · user/pass = credenciales SMTP generadas en la consola (NO las de AWS). **Producción requiere salir del sandbox** (ticket a AWS: dominio verificado + request production access).

**SendGrid**
- Host `smtp.sendgrid.net` · 587 · user literal `apikey`, pass = API key (permisos Mail Send).

**Gmail personal (solo pruebas)**
- `smtp.gmail.com` · 587 · pass = **App Password** (requiere 2FA activo). Límite ~500/día. No apto producción.

**Google Workspace**
- `smtp-relay.gmail.com` · 587 · requiere configurar el relay en Admin Console (Routing) restringiendo por IP/dominio; sin relay: `smtp.gmail.com` con cuenta + App Password (desaprobado para prod).

**Microsoft 365 / Outlook** ⚠️ caso Sixmanager
- `smtp.office365.com` · 587 STARTTLS · user = buzón, pass = contraseña del buzón. **PERO**: SMTP AUTH con Basic Auth viene deshabilitado por defecto y Microsoft lo sigue deprecando — por buzón hay que activar "Authenticated SMTP" (Exchange Admin → mailbox → Email apps). Si la política del tenant lo bloquea, la alternativa moderna es **Graph API `sendMail` con OAuth2** (client credentials) — que sería un 4º provider, no SMTP.

**Zoho Mail**
- `smtp.zoho.com` (EU: `.eu`) · 587 · user = dirección completa.

**Mailtrap** (solo desarrollo)
- `sandbox.smtp.mailtrap.io` · 25/465/587 · no sale correo real, todo queda en el inbox virtual.

**Relay interno propio** (ej. Carbonio/Zimbra del cliente)
- Host interno · 25 sin auth (si la app está en la misma red) o 587 con credenciales. Útil cuando el cliente exige que el correo no salga de su infraestructura — relevante para clientes con compliance (salud: Isamédica usa Carbonio).

## B.3 Cuál elegir (guía rápida)

- **Multi-tenant white-label, dominios en CF, volumen bajo/medio** → Opción A (Cloudflare Worker): costo 0, sin terceros, cada empresa usa su dominio propio ya hospedado en CF.
- **Querer métricas de entrega, bounce, templates** → mantener Resend o Mailgun/SES.
- **Cliente que exige todo on-premise / su relay** → Opción B con relay interno.
- **Migración gradual** → las tres opciones conviven: provider se elige por empresa; se puede migrar empresa por empresa sin big-bang.

---

## Resumen de líneas a tocar

| Archivo | Cambio |
|---|---|
| `email_service.py` | +`_send_via_cloudflare` (~50 líneas), reescritura `_send_via_smtp` (~55), `_company_email_settings` +2 claves ×2 providers, dispatcher +1 branch, `send_test_email` parametrizado |
| `templates/admin/email_settings.html` | select 3 opciones + 2 secciones condicionales |
| `blueprints/admin/__init__.py` | persistencia de settings nuevos (mismo patrón encriptado) |
| Nuevo repo/proyecto `rinde-mail` | Worker (~110 líneas JS) + wrangler.toml |
| Nada existente | se rompe: default `resend` intacto, fallback SMTP global intacto |
