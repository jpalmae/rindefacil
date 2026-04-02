# Rinde Fácil

Aplicación web para rendición de gastos empresariales: registro de gastos, análisis de boletas con IA (OCR), creación de rendiciones, flujos de aprobación, notificaciones y branding por empresa.

## Funcionalidades principales

- Gestión de gastos con adjunto de comprobante (imagen/PDF).
- Los comprobantes subidos desde web/API se guardan con nombre único para evitar colisiones y sobreescritura entre gastos distintos.
- OCR con IA vía OpenRouter al subir la boleta (autocompletado de monto, comercio, fecha y categoría), incluyendo PDF convertido internamente a imagen para análisis.
- Vista previa embebida del comprobante en el formulario, con apertura ampliada para imágenes y PDF.
- En `Mis Gastos`, los comprobantes PDF se muestran con acceso directo correcto al visor ampliado.
- Normalización de montos para formato local (CLP): separador de miles y decimales.
- Soporte de gastos en CLP y USD con conversión a CLP para reportes, políticas, dashboard y aprobación.
- Tipo de gasto `Vehículo particular` con cálculo automático por tramo y boleta opcional de combustible como respaldo.
- `Mis Gastos` muestra más contexto por gasto (motivo, cliente/partner y datos de kilometraje cuando aplica).
- Los gastos en borrador pueden editarse mientras no hayan sido enviados a aprobación; si están dentro de una rendición borrador, también pueden quitarse de ella.
- Detección de duplicados por hash de imagen y por monto/fecha.
- GPS obligatorio al crear gastos (captura de coordenadas + dirección aproximada).
- Validación antifraude con score combinado (`match`, `partial`, `mismatch`):
  comercio↔ubicación + fecha boleta↔rendición + hora boleta↔rendición (margen 20 min).
  Incluye además regla horaria habitual: L-V entre 09:00 y 19:00.
- Rendiciones con múltiples gastos.
- Tipo de rendición: solicitud de devolución o tarjeta corporativa.
- En `Rendiciones`, los usuarios pueden filtrar sus propias rendiciones por `Pendientes de devolución`, `Pagadas` y `Tarjeta corporativa`.
- En `Rendiciones -> Finanzas`, los usuarios con permisos financieros pueden subfiltrar por `Pagadas` y `Tarjeta corporativa`.
- Flujo de aprobación configurable por pasos (`rol`, `usuario`, `manager`).
- Solicitud de antecedentes adicionales durante la aprobación, con reenvío al mismo paso del flujo.
- Notificaciones in-app y envío de correos para aprobaciones/rechazos.
- Panel administrativo: usuarios, centros de costo, flujos, auditoría y branding.
- Branding por empresa: nombre de app, ícono, logo y dominio por defecto para emails de usuarios.
- Selector de temas visuales (Executive / Paper / Midnight / Rose).
- Guía funcional de uso integrada para usuarios desde `Mi Perfil`.

## Stack técnico

- Backend: Flask 3, SQLAlchemy, Flask-Login, Flask-Migrate, Flask-Mail, Flask-Limiter.
- Base de datos: PostgreSQL.
- Frontend: Jinja2 + Alpine.js + UnoCSS.
- Geocodificación: OpenStreetMap Nominatim (reverse geocoding de coordenadas).
- OCR IA: OpenRouter (SDK `openai`).
- Exportación: ReportLab (PDF).
- Procesamiento de primera página PDF para OCR/hash: `pypdfium2`.
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
- `employee`: crea gastos y rendiciones propias.
- `manager`: revisa/decide rendiciones según flujo y jerarquía.
- `approver` / `reviewer`: participan en pasos definidos por flujo.
- `admin` / `superadmin`: acceso a administración completa.

Permisos adicionales sobre el usuario:
- `can_view_approved_reports`: permite ver rendiciones `approved` y `paid` de toda la empresa.
- `can_mark_reimbursements_paid`: permite marcar como `paid` las rendiciones aprobadas de tipo `employee_reimbursement`.

Estos permisos son acumulativos y no reemplazan el rol principal. Un mismo usuario puede, por ejemplo, seguir siendo `manager` y además operar como Finanzas.

Vista operativa para Finanzas:
- `Finanzas -> Todas`: rendiciones visibles para el perfil financiero.
- `Finanzas -> Pagadas`: rendiciones de devolución ya marcadas como pagadas.
- `Finanzas -> Tarjeta corporativa`: rendiciones sin devolución al empleado.

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

### Administración

Ruta: `/admin`.

Módulos principales:
- `Usuarios`: crear/editar/eliminar, rol, manager, centro de costo.
- `Permisos de Finanzas`: visibilidad corporativa de rendiciones aprobadas y cierre de devoluciones pagadas.
- `Centros de costo`: código y presupuesto mensual.
- `Flujos`: diseño de pipeline de aprobación.
- `Branding`: nombre app, ícono, logo y dominio por defecto de usuarios.
- `Auditoría`: historial de acciones.

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
- Las API keys heredan el rol/permisos del usuario que las crea y pueden revocarse desde `Mi Perfil`.

### Endpoints principales

- `POST /auth/token`: login API (email/password) y entrega token JWT.
- `GET /me`: datos del usuario autenticado.
- `GET /categories`: categorías activas de la empresa.
- `POST /expenses/analyze`: analiza una boleta (multipart `receipt`) y devuelve campos OCR.
- `GET /expenses`: lista gastos (paginable por `limit` y `offset`).
- `POST /expenses`: crea gasto; acepta imagen/PDF, puede autocompletar con IA y exige `gps_latitude`/`gps_longitude`.
  También acepta `receipt_time` (`HH:MM` o `HH:MM:SS`).
  Soporta además `currency`, `exchange_rate`, `expense_type`, `distance_km`, `fuel_price_per_liter`, `vehicle_efficiency_km_l` y `correction_factor`.
- `GET /reports`: lista rendiciones.
- `POST /reports`: crea rendición a partir de `expense_ids`.
  Acepta `settlement_type` con valores `employee_reimbursement` o `corporate_card`.
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
