# Rinde Fácil

Aplicación web para rendición de gastos empresariales: registro de gastos, análisis de boletas con IA (OCR), creación de informes, flujos de aprobación, notificaciones y branding por empresa.

## Funcionalidades principales

- Gestión de gastos con adjunto de comprobante (imagen/PDF).
- OCR con IA vía OpenRouter al subir la boleta (autocompletado de monto, comercio, fecha y categoría).
- Normalización de montos para formato local (CLP): separador de miles y decimales.
- Detección de duplicados por hash de imagen y por monto/fecha.
- GPS obligatorio al crear gastos (captura de coordenadas + dirección aproximada).
- Validación antifraude con score combinado (`match`, `partial`, `mismatch`):
  comercio↔ubicación + fecha boleta↔rendición + hora boleta↔rendición (margen 20 min).
  Incluye además regla horaria habitual: L-V entre 09:00 y 19:00.
- Informes de rendición con múltiples gastos.
- Flujo de aprobación configurable por pasos (`rol`, `usuario`, `manager`).
- Notificaciones in-app y envío de correos para aprobaciones/rechazos.
- Panel administrativo: usuarios, centros de costo, flujos, auditoría y branding.
- Branding por empresa: nombre de app, logo y dominio por defecto para emails de usuarios.
- Selector de temas visuales (Executive / Paper / Midnight).
- Guía funcional de uso integrada para usuarios desde `Mi Perfil`.

## Stack técnico

- Backend: Flask 3, SQLAlchemy, Flask-Login, Flask-Migrate, Flask-Mail, Flask-Limiter.
- Base de datos: PostgreSQL.
- Frontend: Jinja2 + Alpine.js + UnoCSS.
- Geocodificación: OpenStreetMap Nominatim (reverse geocoding de coordenadas).
- OCR IA: OpenRouter (SDK `openai`).
- Exportación: ReportLab (PDF).
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
- Reglas y señales del score antifraude.
- Buenas prácticas para evitar rechazos.
- Uso de API Keys para agentes IA.

## OCR y comportamiento de gastos

- El análisis se dispara automáticamente al seleccionar un archivo en “Nuevo Gasto”.
- Si OCR no detecta campos, el formulario sigue disponible para carga manual.
- El endpoint de extracción es `POST /expenses/extract-data`.
- Archivos se guardan localmente en `app/static/uploads`.
- OCR intenta extraer fecha en formato regional `DD/MM/YYYY` y hora `HH:MM` cuando exista.

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
- Las API keys heredan el rol/permisos del usuario que las crea y pueden revocarse desde `Mi Perfil`.

### Endpoints principales

- `POST /auth/token`: login API (email/password) y entrega token JWT.
- `GET /me`: datos del usuario autenticado.
- `GET /categories`: categorías activas de la empresa.
- `POST /expenses/analyze`: analiza una boleta (multipart `receipt`) y devuelve campos OCR.
- `GET /expenses`: lista gastos (paginable por `limit` y `offset`).
- `POST /expenses`: crea gasto; acepta imagen/PDF, puede autocompletar con IA y exige `gps_latitude`/`gps_longitude`.
  También acepta `receipt_time` (`HH:MM` o `HH:MM:SS`).
- `GET /reports`: lista rendiciones.
- `POST /reports`: crea rendición a partir de `expense_ids`.
- `GET /reports/{id}`: detalle completo (gastos + decisiones).
- `POST /reports/{id}/submit`: envía rendición al flujo de aprobación.
- `POST /reports/{id}/approve`: aprueba un paso o aprobación final.
- `POST /reports/{id}/reject`: rechaza rendición con motivo.
- `GET /reports/pending-approvals`: mejora recomendada, lista rendiciones pendientes que el usuario actual puede aprobar.

### Ejemplos rápidos

1. Obtener token:

```bash
curl -X POST http://localhost:5001/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@demo.com","password":"admin123"}'
```

1.1. Consumir API con API key de usuario:

```bash
curl -X GET http://localhost:5001/api/v1/me \
  -H "Authorization: Bearer rfk_..."
```

2. Analizar boleta con IA:

```bash
curl -X POST http://localhost:5001/api/v1/expenses/analyze \
  -H "Authorization: Bearer <TOKEN>" \
  -F "receipt=@/ruta/boleta.jpg"
```

3. Crear gasto con imagen (OCR aplicado):

```bash
curl -X POST http://localhost:5001/api/v1/expenses \
  -H "Authorization: Bearer <TOKEN>" \
  -F "description=Traslado cliente Santiago centro" \
  -F "date=2026-03-08" \
  -F "receipt_time=13:25" \
  -F "gps_latitude=-33.4489" \
  -F "gps_longitude=-70.6693" \
  -F "gps_accuracy_m=25.0" \
  -F "receipt=@/ruta/boleta.jpg"
```

`date` soporta `YYYY-MM-DD`, `DD/MM/YYYY` y `DD-MM-YYYY`.

4. Crear rendición:

```bash
curl -X POST http://localhost:5001/api/v1/reports \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"title":"Rendicion Marzo","description":"Semana 1","expense_ids":["<EXPENSE_ID_1>","<EXPENSE_ID_2>"]}'
```

5. Aprobar rendición:

```bash
curl -X POST http://localhost:5001/api/v1/reports/<REPORT_ID>/approve \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"comment":"Aprobado por jefatura"}'
```

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
