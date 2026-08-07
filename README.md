# Rinde Fácil

Aplicación web para rendición de gastos empresariales: registro de gastos, análisis de boletas con IA (OCR), creación de rendiciones, flujos de aprobación, notificaciones y branding por empresa.

## Funcionalidades principales

- Gestión de gastos con adjunto de comprobante (imagen/PDF).
- **Categoría obligatoria** al crear y editar gastos (web y API).
- Los comprobantes subidos desde web/API se guardan con nombre único para evitar colisiones y sobreescritura entre gastos distintos.
- **Validación de archivos por contenido (magic bytes)**: si un agente o usuario sube un PDF con extensión `.png`, el sistema detecta el tipo real y normaliza la extensión automáticamente.
- OCR con IA configurable por empresa: **OpenRouter (cloud)** o **servidor local OpenAI-compatible** (Ollama, LMStudio, vLLM, llama.cpp, LiteLLM). Autocompletado de monto, comercio, fecha y categoría.
- Vista previa embebida del comprobante en el formulario, con apertura ampliada para imágenes y PDF.
- Normalización de montos para formato local (CLP): separador de miles y decimales.
- Soporte de gastos en CLP y USD con conversión a CLP para reportes, políticas, dashboard y aprobación.
- Tipo de gasto `Vehículo particular` con cálculo automático por tramo y boleta opcional de combustible como respaldo.
- Detección de duplicados por hash de imagen y por monto/fecha.
- GPS obligatorio al crear gastos (captura de coordenadas + dirección aproximada).
- Validación antifraude con score combinado (`match`, `partial`, `mismatch`).
- Rendiciones con múltiples gastos. Tipo: solicitud de devolución o tarjeta corporativa.
- Flujo de aprobación configurable por pasos (`rol`, `usuario`, `manager`).
- Solicitud de antecedentes adicionales durante la aprobación, con reenvío al mismo paso del flujo.
- Notificaciones in-app y envío de correos vía Resend para aprobaciones/rechazos.
- **Recuperación de contraseña** vía enlace por correo (token de 30 min, un solo uso).
- **Verificación en dos pasos (MFA)** por email con código OTP. Activable por usuario y **exigible por empresa** desde el panel admin.
- **Login empresarial (SSO)** vía Microsoft Entra ID y Google Workspace (OIDC).
- Panel administrativo: usuarios, centros de costo, flujos, branding, seguridad, OCR/IA, SSO, email y auditoría.
- **Desactivar/reactivar usuarios** (soft delete): preserva auditoría y rendiciones históricas, libera el email.
- Branding por empresa: nombre de app, ícono, logo y dominio por defecto.
- Selector de temas visuales (Executive / Paper / Midnight / Rose).
- Guía funcional de uso integrada para usuarios desde `Mi Perfil`.

## Stack técnico

- Backend: Flask 3, SQLAlchemy, Flask-Login, Flask-Migrate, Flask-Mail, Flask-Limiter.
- Base de datos: PostgreSQL.
- Frontend: Jinja2 + Alpine.js + UnoCSS.
- Geocodificación: OpenStreetMap Nominatim (reverse geocoding de coordenadas).
- OCR IA: OpenRouter (cloud) o servidor local OpenAI-compatible (Ollama, LMStudio, vLLM, llama.cpp, LiteLLM).
- Email: Resend (API REST) con fallback SMTP.
- SSO: OIDC estándar (Microsoft Entra ID, Google Workspace, Auth0, Okta, etc.).
- Exportación: ReportLab (PDF), CSV nativo.
- Procesamiento de primera página PDF para OCR/hash: `pypdfium2`.
- Validación de uploads: magic bytes (sin librería externa).
- Infra: Docker + Docker Compose.

## Requisitos

- Docker + Docker Compose (recomendado).
- Opcional para desarrollo frontend: Node.js 18+ (`npm`).
- Opcional para ejecución local sin Docker: Python 3.12 + PostgreSQL.

## Inicio rápido con Docker

1. Copiar variables de entorno:

```bash
cp .env.example .env
```

2. Ajustar al menos:

- `OPENROUTER_API_KEY` (si quieres OCR IA).
- `SECRET_KEY` y credenciales de BD para tu entorno.

3. Levantar servicios:

```bash
docker compose up -d --build
```

4. Abrir la app:

- [http://localhost:5001](http://localhost:5001)

La imagen ejecuta `flask db upgrade` al iniciar.

## Datos de demo (opcional)

El script `seed.py` recrea la base completa (borra datos actuales) y crea usuarios demo:

```bash
docker compose exec web python seed.py
```

Credenciales demo:

- `admin@demo.com / admin123`
- `user@demo.com / user123`

## Desarrollo local (sin Docker)

1. Crear y activar entorno virtual:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Instalar dependencias:

```bash
pip install -r requirements.txt
npm install
```

3. Variables de entorno:

```bash
cp .env.example .env
```

Define `DATABASE_URL` apuntando a PostgreSQL local.

4. Migraciones:

```bash
flask db upgrade
```

5. Generar CSS:

```bash
npm run css-build
```

Modo watch:

```bash
npm run css-watch
```

6. Ejecutar:

```bash
python run_dev.py
```

## Branding (por empresa)

Ruta: `Admin -> Branding` (`/admin/branding`).

Permite configurar:

- Nombre visible de la app (`brand_app_name`).
- Ícono de la app (`brand_icon_url`) para pestaña/navegación.
- Logo corporativo (PNG/JPG/WEBP/SVG).
- Dominio de correo por defecto para creación de usuarios (`brand_user_default_domain`).

Valor por defecto global: `Rinde Fácil`.

## Mi Perfil y API Keys

Ruta: `Mi Perfil` (`/auth/profile`).

Cada usuario puede crear sus propias API keys para integraciones con agentes IA:

- Generación de API key personal (se muestra solo una vez al crearla).
- Revocación manual de keys activas.
- Registro de último uso (`last_used_at`).
- Herencia de permisos: la key opera con el mismo rol/permisos del usuario creador.

Para consumir la API con una key:

```http
Authorization: Bearer rfk_...
```

### Guía funcional para usuarios

Ruta en la app: `Mi Perfil -> Guía de Uso Completa` (`/auth/user-guide`).

Incluye:
- Flujo completo de rendición paso a paso.
- Roles/perfiles y alcance por tipo de usuario.
- Operación del panel administrativo.
- Configuración y uso de flujos de aprobación.
- Reglas y señales del score antifraude.
- Buenas prácticas para evitar rechazos.
- Uso de API Keys para agentes IA.

## Modelo operativo

La aplicación separa tres conceptos:

- `Gasto`: comprobante individual. Se crea desde `Gastos -> Nuevo Gasto`.
- `Rendición`: agrupación de uno o más gastos. Internamente el modelo se llama `Report`, pero en la UI corresponde a una rendición.
- `Flujo de aprobación`: se ejecuta sobre la rendición, no sobre el gasto individual.

Tipos de gasto soportados:

- `receipt`: gasto normal con monto manual o autocompletado por OCR.
- `mileage`: tramo en vehículo particular. Cada tramo se registra como un gasto independiente y luego se agrupa normalmente en una rendición.

Cada rendición además tiene un `tipo`:

- `employee_reimbursement`: solicitar devolución al empleado.
- `corporate_card`: rendición de tarjeta corporativa, sin devolución de dinero.

Secuencia normal de uso:

1. Crear uno o varios gastos.
2. Ir a `Rendiciones` y agrupar esos gastos.
3. Crear la rendición en estado borrador.
4. Enviar la rendición al flujo de aprobación.

Comportamiento en `Mis Gastos`:

- Un gasto `draft` sin rendición puede `Editar` o `Eliminar`.
- Un gasto `draft` dentro de una rendición `draft` puede `Editar` o `Quitar de Rendición`.
- Si editas un gasto que ya está dentro de una rendición borrador, el total de la rendición se recalcula automáticamente.
- Una vez que la rendición fue enviada, el gasto deja de ser editable.

Durante la aprobación puede ocurrir además este ciclo:

1. El aprobador solicita antecedentes adicionales con comentario obligatorio.
2. La rendición pasa a estado `needs_info`.
3. El solicitante ve el motivo en la rendición, responde y la reenvía.
4. La revisión vuelve al mismo paso del flujo, no al inicio.

Importante:

- Si no existe un flujo activo con pasos configurados para la empresa, la rendición no se envía y se mantiene en borrador.
- Los gastos individuales no se aprueban por separado; el resultado final se refleja en la rendición y en los gastos asociados.
- `needs_info` no es rechazo: significa que faltan antecedentes o contexto para decidir.

## Roles y perfiles

Roles disponibles:
- `employee`: crea gastos y rendiciones propias. Ve solo sus datos.
- `manager`: revisa/decide rendiciones **según flujo y jerarquía**. Ve sus propias rendiciones + las que tiene pendientes de aprobar en el flujo. No ve todas las de la empresa.
- `approver` / `reviewer`: participan en pasos definidos por flujo.
- `admin` / `superadmin`: acceso a administración completa. Ve todas las rendiciones y gastos de la empresa.

Permisos adicionales sobre el usuario:
- `can_view_approved_reports`: permite ver rendiciones `approved` y `paid` de toda la empresa (perfil Finanzas).
- `can_mark_reimbursements_paid`: permite marcar como `paid` las rendiciones aprobadas de tipo `employee_reimbursement`.

Estos permisos son acumulativos y no reemplazan el rol principal. Un mismo usuario puede, por ejemplo, seguir siendo `manager` y además operar como Finanzas.

**Importante sobre managers**: el rol `manager` NO equivale a admin. Un manager ve:
- Sus propias rendiciones y gastos.
- Rendiciones que tiene pendientes de aprobar (según el paso actual del flujo).
- Si además tiene permisos de finanzas: rendiciones aprobadas/pagadas de la empresa.

Un manager **no** ve: todas las rendiciones de la empresa, dashboard corporativo, ni analytics (salvo que tenga permisos de finanzas).

Vista operativa para Finanzas:
- `Finanzas -> Por pagar`: rendiciones aprobadas pendientes de pago.
- `Finanzas -> Pagadas`: rendiciones ya marcadas como pagadas.
- `Finanzas -> Tarjeta corporativa`: rendiciones de tarjeta corporativa aprobadas.
- `Finanzas -> Todas`: todas las visibles para finanzas.

Acceso administrativo (`/admin`) requiere rol `admin` o `superadmin`.

## Flujos de aprobación y administración

### Flujos de aprobación

Ruta: `Admin -> Flujos` (`/admin/flows`).

Cada flujo permite:
- Definir regla de activación (ej. `min_amount`).
- Configurar pasos secuenciales de aprobación.
- Asignar aprobadores por:
  - `role` (rol específico),
  - `user` (usuario específico),
  - `manager` (jefe directo del solicitante).

Notas operativas:

- El flujo se evalúa al enviar la rendición.
- Si existen varios flujos aplicables, el sistema selecciona el de mayor `monto mínimo` y, en empate, el de más pasos.
- Si el flujo no tiene pasos o no existe uno aplicable, la rendición permanece en borrador.
- La aprobación afecta a la rendición y actualiza el estado de los gastos que contiene.
- Cada aprobador ve y gestiona solo el paso actual que realmente le corresponde.
- Un usuario `admin` no interviene automáticamente en pasos previos de otros aprobadores mientras el flujo siga activo.
- El detalle de la rendición muestra `Paso X de Y` para reflejar el avance real del flujo.
- Un aprobador puede pedir antecedentes adicionales sin rechazar la rendición.
- Cuando el solicitante responde, la rendición vuelve al mismo paso pendiente.
- Si un paso configurado como `manager` no tiene destinatario porque el solicitante no posee manager asignado, el sistema lo omite y continúa con el siguiente paso del flujo.

### Administración

Ruta: `/admin`.

Módulos principales:
- `Usuarios`: crear/editar, rol, manager, centro de costo. **Desactivar/reactivar** (soft delete: preserva datos, libera email, revoca API keys y sesiones MFA).
- `Permisos de Finanzas`: visibilidad corporativa de rendiciones aprobadas y cierre de devoluciones pagadas.
- `Centros de costo`: código y presupuesto mensual.
- `Flujos`: diseño de pipeline de aprobación.
- `Branding`: nombre app, ícono, logo y dominio por defecto de usuarios.
- `Notificaciones Email`: configuración de Resend (API key, remitente, eventos a notificar).
- `OCR / IA`: configuración del proveedor OCR (OpenRouter cloud o servidor local OpenAI-compatible). Incluye prompt editable y botón de prueba.
- `Seguridad`: forzar verificación en dos pasos (MFA) para todos los usuarios de la empresa.
- `Login Empresarial (SSO)`: configurar proveedores OIDC (Microsoft Entra ID, Google Workspace). Client secret cifrado.
- `Auditoría`: historial de acciones (incluye eventos de login SSO).

## OCR y comportamiento de gastos

- El análisis se dispara automáticamente al seleccionar un archivo en “Nuevo Gasto”.
- Si OCR no detecta campos, el formulario sigue disponible para carga manual.
- El endpoint de extracción es `POST /expenses/extract-data`.
- Archivos se guardan localmente en `app/static/uploads`.
- Los nombres almacenados incluyen un identificador único por carga, para evitar que dos archivos con el mismo nombre original se sobrescriban.
- OCR intenta extraer fecha en formato regional `DD/MM/YYYY` y hora `HH:MM` cuando exista.
- Si el comprobante es PDF, la app convierte la primera página a imagen para OCR y cálculo de hash.
- El formulario muestra vista previa embebida del comprobante y permite abrirlo en modal ampliado.

## Monedas y tipo de cambio

- La moneda contable base es `CLP`.
- Los gastos pueden registrarse en `CLP` o `USD`.
- En gastos USD se guarda:
  - monto original en USD,
  - tipo de cambio usado,
  - monto equivalente en CLP (`amount_clp`).
- La app intenta completar el tipo de cambio automáticamente con:
  - fuente primaria: `mindicador.cl`
  - fallback: `CMF` si existe `CMF_API_KEY`
- Si no hay respuesta de fuente externa, el usuario puede completar el tipo de cambio manualmente.

## Vehículo particular

- Se registra desde `Nuevo Gasto` como tipo `Vehículo particular`.
- Cada tramo corresponde a un gasto independiente para mantener intacta la lógica actual de gastos y rendiciones.
- Campos operativos por tramo:
  - fecha,
  - descripción del trayecto,
  - kilómetros,
  - precio litro,
  - rendimiento km/l,
  - factor de corrección,
  - GPS,
  - boleta opcional de combustible.
- Fórmula aplicada:

```text
km_ajustados = kilometros + (kilometros * factor_correccion)
monto = (km_ajustados / rendimiento_km_l) * precio_litro
```

- El resultado se guarda como gasto normal y puede entrar en rendición, aprobación, PDF y API igual que cualquier otro gasto.

## GPS obligatorio en gastos

- `Nuevo Gasto` exige geolocalización activa (latitud/longitud obligatorias).
- Se guarda precisión GPS, timestamp de captura y dirección aproximada.
- La app calcula coherencia para alertar posibles fraudes:
  - Comercio vs dirección GPS.
  - Fecha de boleta vs fecha de rendición.
  - Hora de boleta vs hora de rendición (20 min de margen).
  - Horario habitual de operación: L-V 09:00 a 19:00 (fin de semana/fuera de horario suma riesgo).
  - Si cae en fin de semana o fuera de horario, la validación nunca queda en `match` (baja al menos a `partial`).
- `match`: alta coherencia.
- `partial`: coherencia parcial.
- `mismatch`: potencial riesgo.
- Nota: en navegador, geolocalización requiere contexto seguro (HTTPS) o `localhost`.

## API REST (v1)

Base URL:

- `http://localhost:5001/api/v1`

Autenticación:

- Bearer JWT vía `Authorization: Bearer <token>`.
- API Key personal (generada en `Mi Perfil`): `Authorization: Bearer rfk_...`.
- Las API keys heredan exactamente el rol/permisos del usuario que las crea.
- **El acceso a datos vía API es congruente con la web**: un manager ve lo mismo por API que por web.

### Scoping de datos por rol (web = API)

| Recurso | Admin | Finanzas | Manager | Employee |
|---|---|---|---|---|
| Gastos (lista/detalle) | Toda la empresa | Propios + aprob/pagados | Propios | Propios |
| Rendiciones (lista/detalle) | Toda la empresa | Propias + aprob/pagadas | Propias + en revisión | Propias |
| Analytics / Exports | ✅ | ✅ | ❌ | ❌ |
| Usuarios / Cost centers | ✅ | ❌ | ❌ | ❌ |
| Editar/Eliminar gastos | Todos borrador | Propios | Propios | Propios |

### Endpoints completos

**Auth y usuario:**
- `POST /auth/token`: login API (email/password) → JWT.
- `GET /me`: datos del usuario autenticado.

**Gastos:**
- `GET /expenses`: lista (scoping por rol). Paginable con `limit` y `offset`.
- `POST /expenses`: crea gasto. Acepta imagen/PDF, OCR automático, GPS obligatorio, `category_id` obligatorio.
- `GET /expenses/{id}`: detalle completo de un gasto.
- `PUT /expenses/{id}`: editar gasto borrador (parcial: description, merchant, amount, currency, date, category_id, etc.).
- `DELETE /expenses/{id}`: eliminar gasto borrador.
- `POST /expenses/analyze`: OCR de comprobante (multipart `receipt`).
- `GET /expenses/export`: CSV descargable con filtros (admin/finanzas).

**Rendiciones:**
- `GET /reports`: lista (scoping por rol).
- `POST /reports`: crea rendición desde `expense_ids`. Acepta `settlement_type`.
- `GET /reports/{id}`: detalle completo (gastos + decisiones de aprobación).
- `DELETE /reports/{id}`: eliminar rendición borrador (gastos vuelven a draft).
- `POST /reports/{id}/submit`: envía al flujo de aprobación.
- `POST /reports/{id}/approve`: aprueba paso o aprobación final.
- `POST /reports/{id}/reject`: rechaza con motivo.
- `POST /reports/{id}/request-info`: solicita antecedentes adicionales.
- `POST /reports/{id}/mark-paid`: marca como pagada (finanzas).
- `POST /reports/{id}/remove-expense`: quita gasto de rendición borrador.
- `GET /reports/{id}/export`: PDF de la rendición.
- `GET /reports/pending-approvals`: rendiciones pendientes que el usuario puede aprobar.

**Datos maestros (solo admin):**
- `GET /categories`: categorías activas de la empresa.
- `GET /cost-centers`: centros de costo con presupuesto.
- `GET /users`: usuarios activos (nombre, email, rol, centro de costo).

**Analytics y BI (admin + finanzas):**
Todos aceptan `?date_from=`, `?date_to=`, `?status=`, `?limit=`.
- `GET /analytics/summary`: totales globales, counts por estado.
- `GET /analytics/by-category`: gasto por categoría.
- `GET /analytics/by-cost-center`: real vs presupuesto.
- `GET /analytics/by-user`: top gastadores.
- `GET /analytics/by-month`: tendencia mensual.
- `GET /analytics/by-status`: rendiciones por estado.
- `GET /analytics/top-merchants`: top comercios.
- `GET /analytics/fraud-signals`: score antifraude promedio y distribución.

**Sistema:**
- `GET /health`: health check.

### Ejemplos rápidos

1. Obtener token:

```bash
curl -X POST http://localhost:5001/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@demo.com","password":"admin123"}'
```

2. Consumir API con API key:

```bash
curl -X GET http://localhost:5001/api/v1/me \
  -H "Authorization: Bearer rfk_..."
```

3. Crear gasto con imagen (OCR aplicado):

```bash
curl -X POST http://localhost:5001/api/v1/expenses \
  -H "Authorization: Bearer <TOKEN>" \
  -F "description=Traslado cliente" \
  -F "date=2026-03-08" \
  -F "gps_latitude=-33.4489" \
  -F "gps_longitude=-70.6693" \
  -F "category_id=<UUID>" \
  -F "receipt=@/ruta/boleta.jpg"
```

4. Editar un gasto (corregir monto):

```bash
curl -X PUT http://localhost:5001/api/v1/expenses/<EXPENSE_ID> \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"amount": 15000}'
```

5. Analytics: gasto por categoría del trimestre:

```bash
curl -X GET "http://localhost:5001/api/v1/analytics/by-category?date_from=2026-07-01&date_to=2026-09-30" \
  -H "Authorization: Bearer <TOKEN>"
```

6. Exportar gastos aprobados en CSV:

```bash
curl -O -J "http://localhost:5001/api/v1/expenses/export?status=approved&date_from=2026-01-01" \
  -H "Authorization: Bearer <TOKEN>"
```

## Seguridad y autenticación

### Recuperación de contraseña

- Desde el login, enlace "¿Olvidaste tu contraseña?".
- Se envía un enlace único por correo (válido 30 min, un solo uso).
- Anti-enumeración: siempre muestra el mismo mensaje sin importar si el email existe.
- Token hasheado (SHA-256) en DB; los tokens previos se invalidan al emitir uno nuevo.

### Verificación en dos pasos (MFA por email)

- Cada usuario puede activar MFA desde `Mi Perfil -> Verificación en dos pasos`.
- Al iniciar sesión, se envía un código de 6 dígitos por correo (válido 10 min, máx. 5 intentos).
- El admin puede **forzar MFA para toda la empresa** desde `Admin -> Seguridad`.
- Los usuarios que entran por SSO (OIDC) no pasan por el MFA de la app (el IdP gestiona su propio 2FA).
- Requiere Resend configurado para el envío de los códigos.

### Login empresarial (SSO vía OIDC)

Ruta: `Admin -> Login Empresarial (SSO)` (`/admin/oidc-providers`).

- Soporta cualquier IdP que cumpla OIDC: **Microsoft Entra ID**, **Google Workspace**, Auth0, Okta, Keycloak, etc.
- Cada empresa configura sus propios providers (multi-tenant).
- Validación estricta del `id_token`: firma RS256 con JWKS, `iss`, `aud`, `tid` (tenant para Microsoft).
- **Sin auto-provisioning**: los usuarios deben existir previamente en rinde. El login SSO no crea cuentas.
- Anclaje atómico de `oidc_subject` en el primer login (anti race conditions).
- Auditoría de todos los eventos de login SSO (`login.oidc.ok / .unauthorized / .invalid / .error`).
- El login local (email/password) sigue disponible como break-glass.

### OCR / IA configurable por empresa

Ruta: `Admin -> OCR / IA` (`/admin/ocr-settings`).

- Selector de proveedor: **OpenRouter (cloud)** o **Local (OpenAI-compatible)**.
- Para local: soporta Ollama, LMStudio, vLLM, llama.cpp, LiteLLM mediante su API `/v1/chat/completions`.
- Campos por proveedor: `base_url`, `api_key` (cifrado), `model`, `model_fallback`, `timeout`.
- **Prompt editable** con defaults inteligentes (uno para cloud, otro más explícito para modelos locales pequeños).
- Botón "Probar conexión" que envía una imagen sintética y muestra la respuesta del modelo.
- Backward compatible: si no hay config en la empresa, usa `OPENROUTER_*` del `.env`.

## Estructura del proyecto

```text
app/
  blueprints/      # auth, dashboard, expenses, reports, admin, api
  models/          # entidades SQLAlchemy
  services/        # OCR, notificaciones, correo, auditoría, exportación
  templates/       # vistas Jinja
  static/          # css/js/img/uploads
migrations/        # alembic/flask-migrate
docker-compose.yml
Dockerfile
run_dev.py
seed.py
```

## Troubleshooting

### No se ven los estilos CSS

- Ejecuta `npm run css-build`.
- Verifica que exista `app/static/css/uno.css`.
- Recarga dura del navegador (`Cmd+Shift+R` / `Ctrl+F5`).

### OCR no completa datos

- Revisa `OPENROUTER_API_KEY` en `.env`.
- Verifica conectividad saliente a `https://openrouter.ai`.
- Si falla, la carga manual sigue disponible.

### El tipo de cambio no se completa solo

- Revisa conectividad saliente hacia `mindicador.cl`.
- Si usarás fallback institucional, configura `CMF_API_KEY`.
- Mientras no exista respuesta de fuente externa, el tipo de cambio puede completarse manualmente.

### PDF no se analiza o no se previsualiza

- Verifica que el archivo tenga al menos una página legible.
- La app analiza la primera página del PDF para OCR.
- La vista previa depende del visor PDF del navegador. Si falla en un navegador, prueba `Ver grande` o cambia de navegador.

### Error de conexión a DB

- Confirma `DATABASE_URL`.
- Ejecuta `flask db upgrade`.

### Push por SSH a GitHub falla (`Permission denied (publickey)`)

```bash
ssh-add ~/.ssh/id_rsa
```

## Seguridad

- `.env` está excluido del repo.
- No subas credenciales reales en archivos versionados.
- Cambia secretos por defecto antes de producción.
- `SETTINGS_ENCRYPTION_KEY` requerido en producción (cifra API keys de Resend, OCR y OIDC).
- `SECRET_KEY` requerido en producción (firma sesiones y tokens JWT de estado OIDC).
- Client secrets de OIDC se guardan cifrados con Fernet en la base de datos.
- Validación de uploads por magic bytes (no por extensión del filename).
- Rate limiting en endpoints de auth (login, MFA, forgot-password, OIDC callback).
- ProxyFix habilitado para correcto manejo de `X-Forwarded-*` detrás de Cloudflare/proxy.
- Soft delete de usuarios: nunca se elimina un usuario con datos asociados (preserva auditoría).
